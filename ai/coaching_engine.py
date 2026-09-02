"""
ai/coaching_engine.py — Orchestrates when to call the LLM and which prompt to use.

Sits between the services (which detect game events) and the LLM client
(which generates text). Handles debouncing to prevent API spam.
"""
import logging
import time
import threading
import json
from pathlib import Path

from ai.llm_client import LLMClient
from ai import prompt_templates as pt
from ai.champion_scorer import ChampionScorer, ScorerDraftState
from core.event_bus import bus, EventBus
from core.state_manager import ChampSelectState, InGameState
from services.image_cache import ImageCache
from services.stat_analyzer import StatAnalyzer
from data.champion_affinity import ChampionAffinity
from ai.boot_optimizer import BootOptimizer
from models.build_plan import BuildPlan, Slot, SlotState

logger = logging.getLogger(__name__)

# Minimal EN→FR display name overrides for common champions.
# The LCU / DataDragon names are English; this maps them to French display names.
_CHAMP_FR: dict[str, str] = {
    "Aurelion Sol": "Aurelion Sol",
    "Bel'Veth": "Bel'Veth",
    "Cho'Gath": "Cho'Gath",
    "Dr. Mundo": "Dr. Mundo",
    "Jarvan IV": "Jarvan IV",
    "K'Sante": "K'Santé",
    "Kai'Sa": "Kaïsa",
    "Kha'Zix": "Kha'Zix",
    "Kog'Maw": "Kog'Maw",
    "LeBlanc": "LeBlanc",
    "Lee Sin": "Lee Sin",
    "Master Yi": "Master Yi",
    "Miss Fortune": "Miss Fortune",
    "Nunu & Willump": "Nunu & Willump",
    "Rek'Sai": "Rek'Sai",
    "Tahm Kench": "Tahm Kench",
    "Twisted Fate": "Twisted Fate",
    "Vel'Koz": "Vel'Koz",
    "Xin Zhao": "Xin Zhao",
}


def champ_en_to_fr(name: str) -> str:
    """Return French display name for a champion, falling back to the English name."""
    return _CHAMP_FR.get(name, name)


class CoachingEngine:
    """
    Orchestrates coaching advice generation.

    All LLM calls are non-blocking (background threads). Results are
    emitted via the EventBus so UI components can subscribe and update.
    """

    # Cooldown periods
    _CHAMP_SELECT_COOLDOWN   = 8.0    # seconds
    _ITEM_ADVICE_COOLDOWN    = 30.0   # seconds per fed enemy
    _DEATH_BACK_COOLDOWN     = 25.0   # seconds between death/back advice
    _SUGGESTIONS_COOLDOWN    = 3.0    # debounce for champion suggestions

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client
        self._scorer = ChampionScorer(Path("data"))
        self._aff = ChampionAffinity("data/champion_item_profiles.json", "data")
        self._boot_optimizer = BootOptimizer(self._aff)
        
        # Tags de champions (utilisés pour compter les tanks adverses).
        # Chargés hors du thread principal : ce __init__ tourne AVANT la création
        # de la fenêtre, un appel réseau bloquant ici gèle le démarrage de l'app.
        self._champion_tags: dict[str, list[str]] = self._load_champion_tags_local()
        threading.Thread(
            target=self._refresh_champion_tags, daemon=True, name="DDragonTags"
        ).start()

        # Debounce tracking
        self._last_champ_advice_time  = 0.0
        self._last_champ_advice_hash  = ""
        self._last_item_advice_time   = 0.0
        self._last_item_advice_target = ""
        self._last_death_back_time    = 0.0
        self._last_suggestions_hash   = ""
        self._last_suggestions_time   = 0.0
        self._last_ban_hash           = ""
        self._last_ban_time           = 0.0
        self._last_build_plan         = None
        # Tout ce que l'app a recommandé durant la partie en cours. Un objet
        # acheté sort de la liste des recommandations : son absence ne prouve
        # donc rien. Sans cette mémoire, acheter l'objet conseillé l'affichait
        # en écart (gris) au lieu de conforme (vert).
        self._recommended_ids: set[int] = set()

        self._lock = threading.Lock()
        self._core_prescriptions = self._load_core_prescriptions()

    @staticmethod
    def _load_champion_tags_local() -> dict[str, list[str]]:
        """Tags depuis assets/champion_data.json (déjà téléchargé par setup.bat)."""
        try:
            with open("assets/champion_data.json", encoding="utf-8") as f:
                data = json.load(f).get("data", {})
            return {name: info.get("tags", []) for name, info in data.items()}
        except Exception as e:
            logger.warning("Tags champions indisponibles en local: %s", e)
            return {}

    def _refresh_champion_tags(self) -> None:
        """Rafraîchit les tags depuis DDragon en tâche de fond (jamais bloquant)."""
        try:
            import requests
            ver = requests.get(
                "https://ddragon.leagueoflegends.com/api/versions.json", timeout=5
            ).json()[0]
            champs_data = requests.get(
                f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json",
                timeout=10,
            ).json()["data"]
            tags = {name: info.get("tags", []) for name, info in champs_data.items()}
            if tags:
                self._champion_tags = tags
                logger.info("Tags DDragon rafraîchis (%d champions).", len(tags))
        except Exception as e:
            logger.warning("Rafraîchissement des tags DDragon échoué (on garde le local): %s", e)

    def _load_core_prescriptions(self) -> dict:
        path = Path("data/core_items_prescription.json")
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Erreur chargement core_items_prescription: {e}")
        return {}

    # -----------------------------------------------------------------------
    # Champ Select
    # -----------------------------------------------------------------------

    def request_champ_select_advice(self, state: ChampSelectState) -> None:
        """
        Trigger champ-select coaching if the state has meaningfully changed
        and the cooldown has elapsed.
        """
        if not state.my_champion_name and not state.enemy_champion_names:
            return  # Not enough info yet

        state_hash = (
            state.my_champion_name
            + "|" + "|".join(sorted(x for x in state.ally_champion_names if x))
            + "|" + "|".join(sorted(x for x in state.enemy_champion_names if x))
        )

        with self._lock:
            if state_hash == self._last_champ_advice_hash:
                return  # Identical state, no need to ask LLM again

            now = time.monotonic()
            if now - self._last_champ_advice_time < self._CHAMP_SELECT_COOLDOWN:
                logger.debug("Champ-select advice debounced.")
                return
                
            self._last_champ_advice_hash = state_hash
            self._last_champ_advice_time = now

        system, user = pt.champ_select_prompt(
            my_champion=state.my_champion_name,
            allies=state.ally_champion_names,
            enemies=state.enemy_champion_names,
            my_position=state.my_position,
        )
        logger.info("Requesting champ-select advice for %s vs %s",
                    state.my_champion_name, state.enemy_champion_names)

        accumulated: list[str] = []

        def on_token(token: str) -> None:
            accumulated.append(token)
            bus.emit(EventBus.CHAMP_ADVICE_READY, "".join(accumulated))

        def on_done(full_text: str) -> None:
            logger.info("Champ-select advice complete (%d chars).", len(full_text))
            
            # Fallback for abruptly interrupted streams (e.g. LLM safety filters or timeouts)
            if full_text and not full_text.strip().endswith((".", "!", "?", "]", ")", "»", '"', "—", "-")):
                full_text += "\n[⚠️ Le modèle IA a interrompu la génération avant la fin]"
                
            # Final emit with complete text
            bus.emit(EventBus.CHAMP_ADVICE_READY, full_text)

        def on_error(err: str) -> None:
            bus.emit(EventBus.CHAMP_ADVICE_READY, f"⚠️ LLM Error: {err}")

        self._llm.complete_async(system, user, on_token=on_token, on_done=on_done, on_error=on_error)

    # -----------------------------------------------------------------------
    # Champion Suggestions (Tier List x3) - Algorithmic
    # -----------------------------------------------------------------------

    def request_champion_suggestions(self, state: ChampSelectState) -> None:
        """
        Non-blocking. Generates 3 champion recommendations using the pure-Python Scorer.
        Debounced by enemy/ally/ban composition hash (3s cooldown).
        """
        suggestions_hash = (
            state.my_position
            + "|".join(sorted(state.enemy_champion_names))
            + "|".join(sorted(state.ally_champion_names))
            + "|".join(sorted(state.banned_champion_names))
        )

        with self._lock:
            now = time.monotonic()
            if (
                suggestions_hash == self._last_suggestions_hash
                and now - self._last_suggestions_time < self._SUGGESTIONS_COOLDOWN
            ):
                logger.debug("Champion suggestions debounced.")
                return
            self._last_suggestions_hash = suggestions_hash
            self._last_suggestions_time = now

        def _run_scorer():
            try:
                # Map roles from French UI labels to the ENUM used in scorer
                pos_map = {
                    "Top": "TOP", "Jungle": "JUNGLE", "Mid": "MID", 
                    "ADC": "ADC", "Support": "SUPPORT"
                }
                my_role = pos_map.get(state.my_position, "UNKNOWN")

                allies = [{"id": name, "role": "UNKNOWN"} for name in state.ally_champion_names if name]
                enemies = [{"id": name, "role": "UNKNOWN"} for name in state.enemy_champion_names if name]
                bans = [name for name in state.banned_champion_names if name]
                
                # Determine pick slot roughly (1 to 5)
                # Count locked picks + my turn to estimate
                pick_slot = min(5, len(state.ally_champion_names) + 1)

                draft = ScorerDraftState(
                    my_role=my_role,
                    pick_slot=pick_slot,
                    mode="draft",
                    available=[], # Populated below
                    allies=allies,
                    enemies=enemies,
                    bans=bans,
                    rank="PLATINUM" # Mock rank for now
                )
                
                # Import the global from module for available pool
                import ai.champion_scorer as cs
                draft.available = list(cs.by_id.keys())

                cands = self._scorer.recommend(draft)
                
                suggestions = [c.champion_id for c in cands]
                reasons = [c.dominant_reason for c in cands]
                
                logger.info(f"Algorithmic suggestions generated: {suggestions}")
                bus.emit(EventBus.CHAMP_SUGGESTIONS_READY, {
                    "suggestions": suggestions,
                    "reasons": reasons,
                })
            except Exception as e:
                logger.exception(f"Error generating algorithmic suggestions: {e}")
                bus.emit(EventBus.CHAMP_SUGGESTIONS_READY, {
                    "suggestions": [],
                    "reasons": [],
                })

        # Run in background thread to not block UI
        threading.Thread(target=_run_scorer, daemon=True).start()

    # -----------------------------------------------------------------------
    # Ban Suggestions - Algorithmic
    # -----------------------------------------------------------------------

    def request_ban_suggestions(self, state: ChampSelectState) -> None:
        """
        Non-blocking. Generates 3 ban recommendations using the pure-Python Scorer.
        Debounced by state hovers and intents.
        """
        suggestions_hash = (
            state.my_hover
            + "|".join(sorted(state.ally_hovers))
            + "|".join(sorted(state.ally_ban_intents))
            + "|".join(sorted(state.banned_champion_names))
            + "|".join(sorted(state.ally_champion_names))
            + "|".join(sorted(state.enemy_champion_names))
        )

        with self._lock:
            now = time.monotonic()
            if (
                suggestions_hash == self._last_ban_hash
                and now - self._last_ban_time < self._SUGGESTIONS_COOLDOWN
            ):
                return
            self._last_ban_hash = suggestions_hash
            self._last_ban_time = now

        def _run_scorer():
            try:
                pos_map = {
                    "Top": "TOP", "Jungle": "JUNGLE", "Mid": "MID", 
                    "ADC": "ADC", "Support": "SUPPORT"
                }
                my_role = pos_map.get(state.my_position, "UNKNOWN")

                allies = [{"id": name, "role": "UNKNOWN"} for name in state.ally_champion_names if name]
                enemies = [{"id": name, "role": "UNKNOWN"} for name in state.enemy_champion_names if name]
                bans = [name for name in state.banned_champion_names if name]
                
                pick_slot = min(5, len(state.ally_champion_names) + 1)

                draft = ScorerDraftState(
                    my_role=my_role,
                    pick_slot=pick_slot,
                    mode="draft",
                    available=[],
                    allies=allies,
                    enemies=enemies,
                    bans=bans,
                    rank="PLATINUM",
                    my_hover=state.my_hover,
                    ally_hovers=state.ally_hovers,
                    ally_ban_intents=state.ally_ban_intents,
                    my_recent_picks=state.my_recent_picks
                )
                
                import ai.champion_scorer as cs
                draft.available = list(cs.by_id.keys())
                
                cands = self._scorer.recommend_ban(draft)
                
                suggestions = [c.champion_id for c in cands]
                reasons = [c.dominant_reason for c in cands]
                
                if suggestions:
                    logger.info(f"Ban suggestions generated: {suggestions}")
                    bus.emit(EventBus.CHAMP_BAN_SUGGESTIONS_READY, {
                        "suggestions": suggestions,
                        "reasons": reasons,
                    })
            except Exception as e:
                logger.exception(f"Error generating ban suggestions: {e}")

        threading.Thread(target=_run_scorer, daemon=True).start()


    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _find_lane_opponent(enemies, my_position: str, local):
        """
        Identifie le vis-à-vis de lane.

        1. position renvoyée par la Live Client API (fiable quand présente) ;
        2. sinon, table de rôles du scorer (data/champions_<role>.json) ;
        3. sinon seulement, écart de niveau — l'ancien comportement, qui
           désignait l'ennemi le plus proche en niveau et non le vis-à-vis.
        """
        if not enemies:
            return None

        from api.live_client import normalize_position
        # La position du champ select peut manquer (app lancée en cours de
        # partie, reconnexion…). La Live Client API expose la position du joueur
        # lui-même : c'est la source la plus fiable une fois en jeu.
        want = normalize_position(my_position) or getattr(local, "position", "")

        if want:
            by_pos = [p for p in enemies if p.position == want]
            if len(by_pos) == 1:
                return by_pos[0]
            if by_pos:
                return min(by_pos, key=lambda p: abs(p.level - local.level))

            try:
                import ai.champion_scorer as cs
                if not any(cs.by_role.values()):
                    # Les tables ne sont peuplées que par load_data() ; on peut
                    # arriver ici avant qu'un ChampionScorer ait été construit.
                    cs.load_data(Path("data"))
                role_pool = cs.by_role.get(want, {})
                if role_pool:
                    by_role = [
                        p for p in enemies
                        if cs.norm_name(p.champion_name) in role_pool
                    ]
                    if len(by_role) == 1:
                        return by_role[0]
                    if by_role:
                        return min(by_role, key=lambda p: abs(p.level - local.level))
            except Exception:
                logger.debug("Table de rôles indisponible pour le lane matching.", exc_info=True)

        logger.debug("Vis-à-vis de lane non identifié (position=%r) — repli sur le niveau.", my_position)
        return min(enemies, key=lambda p: abs(p.level - local.level))

    def request_death_or_back_advice(
        self,
        game_state: InGameState,
        trigger: str,
        my_position: str = "",
        primary_threat_name: str = "",
        force: bool = False,
    ) -> None:
        """
        Déclenche le plan d'objets sur mort / retour en base.

        Non bloquant : l'analyse complète (StatAnalyzer) part dans un thread
        dédié. Elle était auparavant exécutée en ligne dans la boucle de
        polling d'InGameService, ce qui décalait sa cadence de 5 s.
        """
        local = game_state.local_player
        if local is None:
            return

        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_death_back_time < self._DEATH_BACK_COOLDOWN:
                logger.debug("Death/back advice debounced.")
                return
            self._last_death_back_time = now

        threading.Thread(
            target=self._build_and_emit_plan,
            args=(game_state, trigger, my_position),
            daemon=True,
            name="BuildPlan",
        ).start()

    def _build_and_emit_plan(
        self, game_state: InGameState, trigger: str, my_position: str
    ) -> None:
        """Calcule le plan d'objets et l'émet sur le bus. Tourne hors thread de polling."""
        try:
            self._compute_plan(game_state, my_position, trigger)
        except Exception:
            logger.exception("Échec du calcul du plan d'objets (trigger=%s).", trigger)

    def _compute_plan(self, game_state: InGameState, my_position: str,
                      trigger: str = "") -> None:
        local = game_state.local_player
        if local is None:
            return

        enemies = [p for p in game_state.all_players if p.team != local.team]
        enemy_team_names = [p.champion_name for p in enemies]

        lane_opp = self._find_lane_opponent(enemies, my_position, local)

        lane_opp_name  = lane_opp.champion_name if lane_opp else "inconnu"

        # --- Run Full Build Plan Analysis ---
        cache          = ImageCache()

        # Convert item IDs back to names for the AI
        my_item_names = [cache.get_item_name_by_id(i) for i in local.items]

        # Objets légendaires candidats. Les bottes sont exclues : elles ont leur
        # propre slot (plan.boots), sinon elles occupent en double un slot
        # légendaire — ce que le plan affichait jusqu'ici.
        candidate_items = sorted(
            n for n in cache.valid_items
            if not cache.is_boots(cache.get_item_id_by_name(n))
        )
        analyzer       = StatAnalyzer()
        
        is_adc = my_position.upper() == "ADC"
        # L'inventaire compte 6 emplacements. Pour un non-ADC les bottes en
        # occupent un, donc 5 légendaires : en générer 6 revenait à en faire
        # calculer un que l'UI jetait silencieusement. Un ADC revend souvent ses
        # bottes en fin de partie, d'où 6 légendaires + un emplacement bottes.
        target_capacity = 6 if is_adc else 5
        
        # Load previous plan for hysteresis
        prev_plan = self._last_build_plan
        prev_items = []
        prev_items_ids: list[int] = []
        if prev_plan:
            prev_items_ids = [s.item_id for s in prev_plan.legendary_slots if s.item_id]
            prev_items = [cache.get_item_name_by_id(i) for i in prev_items_ids]
            
        # Get raw plan from analyzer with hysteresis
        raw_plan = analyzer.plan_with_confidence(game_state, lane_opp, candidate_items, n_slots=target_capacity, prev_plan_items=prev_items)
        
        # --- Create BuildPlan Object ---
        
        # Bottes : détectées par tag DDragon 'Boots' sur l'ID de l'objet.
        # L'ancien matching par sous-chaîne française ratait "Coques en acier"
        # (Plated Steelcaps) et contenait une faute ("zphyr").
        owned_boot_id = next((i for i in local.items if cache.is_boots(i)), None)
        has_boots = owned_boot_id is not None
        
        lane_opp_type = "UNKNOWN"
        if lane_opp_name:
            prof = self._aff.profile(lane_opp_name)
            mix = prof.get("damage_mix") or {}
            if mix.get("ap", 0) >= 0.60: lane_opp_type = "AP"
            elif mix.get("ad", 0) >= 0.60: lane_opp_type = "AD"
            else: lane_opp_type = "HYBRID"
        enemy_tank_count = sum(1 for e in enemy_team_names if "Tank" in self._champion_tags.get(e, []))
        
        boot_name = None
        if not has_boots:
            boot_name = self._boot_optimizer.recommend_boots(game_state, lane_opp_name, lane_opp_type, enemy_tank_count)
            
        boot_slot = Slot(
            index=-1,
            state=SlotState.EMPTY if not boot_name else SlotState.PLANNED,
            item_id=cache.get_item_id_by_name(boot_name) if boot_name else None,
            reason="bottes requises" if boot_name else ""
        )
        
        # Populate legendary slots
        legendary_slots = []
        for i, (item_name, conf, margin, trigger_locked) in enumerate(raw_plan):
            # Le PROCHAIN objet est toujours révélé : c'est la seule information
            # dont le joueur a besoin devant la boutique. Le masquer parce que la
            # confiance est basse laissait un « ? » et aucune décision possible.
            # L'incertitude est communiquée par le score, pas par l'absence.
            state = SlotState.PLANNED
            if i > 0 and conf < 0.45:
                state = SlotState.UNDETERMINED
                
            slot = Slot(
                index=i,
                state=state,
                item_id=cache.get_item_id_by_name(item_name),
                confidence=conf,
                reason="anti-soin" if trigger_locked else "math",
            )
            if trigger_locked:
                slot.state = SlotState.PLANNED
            legendary_slots.append(slot)
            
        plan = BuildPlan(legendary_slots=legendary_slots, boots=boot_slot)
        
        # ── Objets déjà achetés ───────────────────────────────────────────
        # Ils occupent leurs PROPRES emplacements. Auparavant, BuildPlan.lock()
        # écrasait le premier emplacement recommandé : acheter un objet effaçait
        # donc la recommandation suivante — c'est ainsi que le conseil anti-soin
        # disparaissait dès l'achat de Cœuracier.
        candidate_set = set(candidate_items)
        owned_ids: list[int] = []
        for iid in local.items:
            if cache.is_boots(iid):
                continue                       # les bottes ont leur emplacement
            nom = cache.get_item_name_by_id(iid)
            if nom in candidate_set and iid not in owned_ids:
                owned_ids.append(iid)

        # ── Prescription empirique ────────────────────────────────────────
        # Toujours consultée : même quand tous ses objets sont déjà achetés,
        # elle sert à savoir s'ils étaient conformes au plan (vert) ou non (gris).
        from ai.champion_scorer import norm_name
        champ_key = norm_name(local.champion_name)
        key = f"{champ_key}|{my_position.upper()}"
        prescription_data = self._core_prescriptions.get(key)
        if not prescription_data:
            possible_keys = [k for k in self._core_prescriptions if k.startswith(f"{champ_key}|")]
            if possible_keys:
                best_key = max(
                    possible_keys,
                    key=lambda k: self._core_prescriptions[k].get("global", {}).get("samples", 0),
                )
                prescription_data = self._core_prescriptions[best_key]

        core_items: list[int] = []
        if prescription_data:
            tank_cat = "tank_hi" if enemy_tank_count >= 2 else "tank_lo"
            ctx = {"lane_opp_type": lane_opp_type, "enemy_tank_count": tank_cat}
            order = prescription_data.get("strata_order", [])
            best_stratum = prescription_data.get("global", {})
            if order:
                parts = [ctx.get(f, "") for f in order]
                for depth in range(len(parts), 0, -1):
                    k = "|".join(parts[:depth])
                    if k in prescription_data.get("strata", {}):
                        best_stratum = prescription_data["strata"][k]
                        break
            if best_stratum and best_stratum.get("confidence", 0) > 0.35:
                core_items = list(best_stratum.get("items", []))
                p_conf = best_stratum.get("confidence", 0)

        # ── Recommandations restantes ─────────────────────────────────────
        # Un emplacement remporté par un seuil binaire actif (anti-soin) prime
        # sur la prescription, qui est statistique et aveugle au contexte.
        restants = [s for s in legendary_slots if s.item_id not in owned_ids]
        a_prescrire = [i for i in core_items if i not in owned_ids]
        for slot in restants:
            if not a_prescrire:
                break
            if slot.reason == "anti-soin":
                continue
            slot.item_id = a_prescrire.pop(0)
            slot.state = SlotState.PLANNED
            slot.reason = "prescrit"
            slot.confidence = p_conf

        # Dédoublonnage : le score mathématique proposait parfois déjà un objet
        # prescrit ailleurs dans la liste.
        vus: set[int] = set(owned_ids)
        propres = []
        for slot in restants:
            if slot.item_id is not None:
                if slot.item_id in vus:
                    continue
                vus.add(slot.item_id)
            propres.append(slot)

        # ── Assemblage : achetés d'abord, puis la suite du plan ────────────
        # Remise à zéro en début de partie (aucun légendaire en inventaire).
        if not owned_ids:
            self._recommended_ids = set()

        reference = (
            set(core_items)
            | {s.item_id for s in legendary_slots if s.item_id}
            | set(prev_items_ids)
            | self._recommended_ids
        )

        # Un anti-soin acheté face à des soigneurs est conforme au plan, même si
        # l'app n'a pas eu l'occasion de le conseiller (démarrage en cours de
        # partie) : il ne disparaît de la liste que parce qu'il est déjà acquis.
        from services.stat_analyzer import _HEALING_CHAMPION_WEIGHTS
        if any(_HEALING_CHAMPION_WEIGHTS.get(n, 0) for n in enemy_team_names):
            anti_soin = analyzer._gw_ad | analyzer._gw_ap | analyzer._gw_tank
            reference |= {
                cache.get_item_id_by_name(n) for n in anti_soin
                if cache.get_item_id_by_name(n)
            }
        finaux: list[Slot] = []
        for iid in owned_ids:
            finaux.append(Slot(
                index=len(finaux),
                state=SlotState.OWNED_ON_PLAN if iid in reference else SlotState.OWNED_OFF_PLAN,
                item_id=iid,
                confidence=1.0,
                reason="acheté",
            ))
        for slot in propres:
            if len(finaux) >= target_capacity:
                break
            slot.index = len(finaux)
            finaux.append(slot)
        plan.legendary_slots = finaux
        self._recommended_ids.update(
            s.item_id for s in finaux
            if s.item_id and s.state is SlotState.PLANNED
        )

        if owned_boot_id:
            plan.boots.item_id = owned_boot_id
            plan.boots.state = SlotState.OWNED_ON_PLAN

        # Save plan
        self._last_build_plan = plan
        
        planned = sum(1 for s in plan.legendary_slots if s.state == SlotState.PLANNED)
        owned = sum(
            1 for s in plan.legendary_slots
            if s.state in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN)
        )
        advice_str = f"{owned} objet(s) en inventaire · {planned} prévu(s)"

        bus.emit(EventBus.ITEM_ADVICE_READY, {
            "advice": advice_str,
            "champion": lane_opp_name,
            "trigger": trigger,
            "streaming": False,
            "plan": plan,
            "is_adc": is_adc
        })
