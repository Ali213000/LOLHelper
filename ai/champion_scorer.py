"""
ai/champion_scorer.py — Algorithmic Champion Draft Engine.
"""
import math
import logging
import json
import glob
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

STUB_STATS = True  # Set to False when real stats are plugged in

logger = logging.getLogger(__name__)

# =============================================================================
# Constants for Lane Scoring & Bayesian Shrinkage
# =============================================================================
_LANE_SHRINK_K = 300
_LANE_MIN_GAMES = 200
_WR_FLOOR = 0.50
_WR_SPAN = 0.06
_CONF_HOVER = 1.0
_CONF_POOL = 0.6
_CONF_FLAT_SCHEMA = 0.5

# =============================================================================
# Data Models
# =============================================================================

@dataclass
class ScorerDraftState:
    my_role: str                      # "TOP", "JUNGLE", "MID", "ADC", "SUPPORT"
    pick_slot: int                    # 1 to 5
    mode: str                         # "draft", "blind"
    available: list[str]              # List of champion IDs
    allies: list[dict[str, str]]      # [{"id": "Ornn", "role": "TOP"}]
    enemies: list[dict[str, str]]     # [{"id": "Zed", "role": "MID"}]
    bans: list[str]
    rank: str                         # e.g., "PLATINUM"
    lane_opponent: Optional[str] = None
    my_hover: str = ""
    ally_hovers: list[str] = field(default_factory=list)
    ally_ban_intents: list[str] = field(default_factory=list)
    my_recent_picks: dict[str, list[str]] = field(default_factory=dict)

@dataclass
class CandidateScore:
    champion_id: str
    total_score: float
    sub_scores: dict[str, float]
    dominant_reason: str = ""

# =============================================================================
# Initialization & Data Loading
# =============================================================================

ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]

# by_role: role -> {id: attrs}
# by_id: id -> attrs
by_role = {r: {} for r in ROLES}
by_id = {}
config = {}

ALIAS = {
    "Wukong": "MonkeyKing", "Bel'Veth": "Belveth", "Renata Glasc": "Renata",
    "Cho'Gath": "Chogath", "Vel'Koz": "Velkoz", "LeBlanc": "Leblanc",
    "Kai'Sa": "Kaisa", "Kha'Zix": "Khazix", "Rek'Sai": "RekSai",
    "Kog'Maw": "KogMaw", "K'Sante": "KSante", "Dr. Mundo": "DrMundo",
    "Twisted Fate": "TwistedFate", "Aurelion Sol": "AurelionSol",
    "Master Yi": "MasterYi", "Miss Fortune": "MissFortune",
    "Xin Zhao": "XinZhao", "Jarvan IV": "JarvanIV", "Lee Sin": "LeeSin",
    "Tahm Kench": "TahmKench", "Nunu & Willump": "Nunu",
}

def norm_name(name: str) -> str:
    """Normalize champion names to match Riot's internal IDs (e.g. Kha'Zix -> Khazix)."""
    return ALIAS.get(name, name.replace(" ", "").replace("'", "").replace(".", ""))

def load_data(data_dir: Path):
    global config, by_id, by_role
    by_role = {r: {} for r in ROLES}
    by_id = {}
    
    config_path = data_dir / "draft_config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        logger.error(f"Missing config at {config_path}")

    # Load champion files
    for path_str in glob.glob(str(data_dir / "champions_*.json")):
        with open(path_str, "r", encoding="utf-8") as f:
            d = json.load(f)
            role = d.get("role", "").upper()
            if role not in ROLES:
                continue
            for ch in d.get("champions", []):
                cid = ch["id"]
                by_role[role][cid] = ch
                by_id.setdefault(cid, ch)
    
    logger.info(f"Scorer loaded {len(by_id)} unique champions from {data_dir}")

def get_champion(cid: str) -> dict:
    """Safely get champion attributes, falling back to DEFAULT if missing."""
    cid = norm_name(cid)
    ch = by_id.get(cid)
    if ch is None:
        # Construct default axes based on config if available
        axes_keys = config.get("team_thresholds", {}).keys()
        default_axes = {a: 0.4 for a in axes_keys} if axes_keys else {}
        
        default_champ = {
            "id": "UNKNOWN", "roles": ROLES, "archetype": "mage_control",
            "damage_mix": {"ad": 0.5, "ap": 0.5, "true": 0.0},
            "damage_profile": "sustained", "attack_range": 400,
            "axes": default_axes,
            "power_curve": {"early": 0.5, "mid": 0.5, "late": 0.5},
            "flex_score": 0.4, "mechanical_difficulty": 0.5,
        }
        logger.warning(f"Champion absent de la table : {cid}")
        return {**default_champ, "id": cid}
    return ch

# =============================================================================
# Core Scorer Engine
# =============================================================================

class ChampionScorer:
    
    def __init__(self, data_dir: Path):
        load_data(data_dir)
        self.stats = {}  # Dynamic stats
        self._load_stats(data_dir / "patch_stats.json")

    def _load_stats(self, path: Path):
        """Load and parse dynamic stats."""
        if not path.exists():
            logger.warning(f"Stats file {path} not found, S1/S2 will be stubbed.")
            return
            
        with open(path, "r", encoding="utf-8") as f:
            self.stats = json.load(f)
            
        global STUB_STATS
        STUB_STATS = False
        logger.info(f"Loaded patch stats: {self.stats.get('patch', 'unknown')} for {self.stats.get('rank_bracket', 'unknown')}")

    def recommend(self, draft: ScorerDraftState) -> list[CandidateScore]:
        if not config:
            return []
            
        candidats = []
        my_role = draft.my_role.upper()
        
        if my_role not in by_role:
            logger.error(f"Unknown role {my_role}")
            return []
            
        role_champions = by_role[my_role]
        picked = [norm_name(p["id"]) for p in draft.allies + draft.enemies]
        banned = [norm_name(b) for b in draft.bans]
        unavailable = set(picked + banned)

        for cid in draft.available:
            norm_cid = norm_name(cid)
            if norm_cid in unavailable:
                continue
            # Candidat doit avoir les attributs (pas inconnu) et être jouable dans ce rôle
            if norm_cid in role_champions:
                candidats.append(norm_cid)

        if not candidats:
            return []

        # Weights
        w = self._get_weights(draft)
        
        # Calculate needs & threats based on composition
        allies_attrs = [get_champion(a["id"]) for a in draft.allies]
        enemies_attrs = [get_champion(e["id"]) for e in draft.enemies]
        
        besoins = self._calculer_besoins(allies_attrs)
        menaces = self._calculer_menaces(enemies_attrs, allies_attrs)

        resultats = []
        for cid in candidats:
            t = role_champions[cid]
            
            s1 = self._score_meta(t, draft)
            s2 = self._score_lane(t, draft)
            s3 = self._score_team_fit(t, besoins, allies_attrs)
            s4 = self._score_counter_comp(t, menaces)
            
            pen = self._calculer_penalites(t, draft, allies_attrs, enemies_attrs)
            
            # Penalité de complexité
            pen += config.get("complexity", {}).get("lambda", 0.08) * t.get("mechanical_difficulty", 0.5)

            score = w["meta"]*s1 + w["lane"]*s2 + w["team_fit"]*s3 + w["counter_comp"]*s4 - pen
            
            if draft.pick_slot == 1:
                score += config.get("first_pick", {}).get("flex_bonus", 0.10) * t.get("flex_score", 0)

            reason = self._generate_reason(s1, s2, s3, s4, w, t, besoins, menaces)
            
            resultats.append(CandidateScore(
                champion_id=cid,
                total_score=score,
                sub_scores={"meta": s1, "lane": s2, "team": s3, "counter": s4, "pen": pen},
                dominant_reason=reason
            ))

        return self._top3_diversifie(resultats)

    def _get_weights(self, draft: ScorerDraftState) -> dict:
        weights = config.get("weights_by_slot", {})
        if draft.mode == "blind":
            w = weights.get("blind_pick", {})
        elif draft.pick_slot == 1:
            w = weights.get("first_pick", {})
        elif draft.pick_slot in (2, 3):
            w = weights.get("middle", {})
        else:
            w = weights.get("last_pick", {})
            
        w_dict = dict(w)
        if STUB_STATS:
            # Zero out meta/lane and renormalize team_fit/counter_comp
            team = w_dict.get("team_fit", 0.0)
            counter = w_dict.get("counter_comp", 0.0)
            tot = team + counter
            if tot > 0:
                w_dict["meta"] = 0.0
                w_dict["lane"] = 0.0
                w_dict["team_fit"] = team / tot
                w_dict["counter_comp"] = counter / tot
                
        return w_dict

    # -------------------------------------------------------------------------
    # S3 - Team Fit
    # -------------------------------------------------------------------------
    
    def _calculer_besoins(self, allies: list[dict]) -> dict:
        thresholds = config.get("team_thresholds", {})
        current = {k: 0.0 for k in thresholds.keys()}
        
        for ally in allies:
            axes = ally.get("axes", {})
            for k in current.keys():
                current[k] += axes.get(k, 0.0)
                
        needs = {}
        for k, v in thresholds.items():
            needs[k] = max(0.0, v.get("target", 0.0) - current[k])
            
        return needs

    def _score_team_fit(self, candidat: dict, besoins: dict, allies: list[dict]) -> float:
        thresholds = config.get("team_thresholds", {})
        s3_axes = 0.0
        norm_factor = sum(v.get("importance", 1.0) for v in thresholds.values()) or 1.0
        
        cand_axes = candidat.get("axes", {})
        for k, need_val in besoins.items():
            champ_val = cand_axes.get(k, 0.0)
            importance = thresholds[k].get("importance", 1.0)
            s3_axes += (need_val * champ_val * importance)
            
        s3_axes /= norm_factor
        s3_axes = min(1.0, s3_axes)

        # Damage mix projection
        tot_ad = sum(a.get("damage_mix", {}).get("ad", 0.0) for a in allies) + candidat.get("damage_mix", {}).get("ad", 0.0)
        tot_team = len(allies) + 1
        ratio_projete = tot_ad / tot_team if tot_team > 0 else 0.5
        
        mix_cfg = config.get("damage_mix", {})
        ideal = mix_cfg.get("ideal_ad_ratio", 0.55)
        slope = mix_cfg.get("slope", 2.5)
        
        bonus_mix = 1.0 - min(1.0, abs(ideal - ratio_projete) * slope)
        
        return 0.75 * s3_axes + 0.25 * bonus_mix

    # -------------------------------------------------------------------------
    # S4 - Counter Comp
    # -------------------------------------------------------------------------

    def _calculer_menaces(self, enemies: list[dict], allies: list[dict]) -> dict:
        matrix = config.get("threat_matrix", {})
        menaces = {k: 0.0 for k in matrix.keys()}
        
        # Formules simulées de l'énoncé (simplifiées pour la logique Python)
        for e in enemies:
            axes = e.get("axes", {})
            profile = e.get("damage_profile", "")
            mobility = axes.get("mobility", 0)
            
            # Dive
            burst_mult = 1.0 if profile == "burst" else 0.5
            menaces["dive"] += mobility * burst_mult
            
            # Poke
            if e.get("attack_range", 0) > 500:
                menaces["poke"] += axes.get("poke_siege", 0)
                
            # CC
            menaces["hard_cc"] += axes.get("hard_cc", 0)

        # Engage (max + 0.5*others)
        engages = [e.get("axes", {}).get("engage", 0) for e in enemies]
        if engages:
            engages.sort(reverse=True)
            menaces["engage"] = engages[0] + 0.5 * sum(engages[1:])
            
        # AD / AP Heavy
        if enemies:
            tot_ad = sum(e.get("damage_mix", {}).get("ad", 0) for e in enemies) / len(enemies)
            tot_ap = sum(e.get("damage_mix", {}).get("ap", 0) for e in enemies) / len(enemies)
            menaces["ad_heavy"] = 1.0 if tot_ad > matrix.get("ad_heavy", {}).get("trigger_above", 0.75) else 0.0
            menaces["ap_heavy"] = 1.0 if tot_ap > matrix.get("ap_heavy", {}).get("trigger_above", 0.75) else 0.0
            
        # Scaling
        # formula: moyenne(power_curve.late ennemis) - moyenne(power_curve.late allies)
        avg_enemy_late = sum(e.get("power_curve", {}).get("late", 0.5) for e in enemies) / len(enemies) if enemies else 0.5
        avg_ally_late = sum(a.get("power_curve", {}).get("late", 0.5) for a in allies) / len(allies) if allies else 0.5
        menaces["scaling"] = max(0.0, avg_enemy_late - avg_ally_late)
        
        return menaces

    def _score_counter_comp(self, candidat: dict, menaces: dict) -> float:
        matrix = config.get("threat_matrix", {})
        score = 0.0
        total_weight = 0.0
        
        cand_axes = candidat.get("axes", {})
        
        for k, threat_val in menaces.items():
            if threat_val == 0:
                continue
                
            cfg = matrix.get(k, {})
            norm = cfg.get("normalizer", 1.0)
            intensite = min(1.0, threat_val / norm)
            
            if intensite == 0:
                continue
                
            val_score = 0.0
            for attr, weight in cfg.get("valorise", {}).items():
                if attr.startswith("power_curve."):
                    pc_key = attr.split(".")[1]
                    val_score += candidat.get("power_curve", {}).get(pc_key, 0.0) * weight
                else:
                    val_score += cand_axes.get(attr, 0) * weight
            
            # Simplified penalization
            pen_score = 0.0
            for attr, weight in cfg.get("penalise", {}).items():
                if attr == "immobile" and cand_axes.get("mobility", 0) < 0.4:
                    pen_score += weight
                elif attr == "low_range_low_mobility" and candidat.get("attack_range", 0) < 400 and cand_axes.get("mobility", 0) < 0.4:
                    pen_score += weight
                elif attr == "late_scaling":
                    pen_score += candidat.get("power_curve", {}).get("late", 0.0) * weight
                    
            net_contrib = (val_score - pen_score) * intensite
            score += net_contrib
            total_weight += intensite
            
        if total_weight > 0:
            score = max(0.0, score / total_weight)
        else:
            score = 0.5
            
        return score

    # -------------------------------------------------------------------------
    # S1 & S2 - Dynamic Stats
    # -------------------------------------------------------------------------
    
    def _score_meta(self, candidat: dict, draft: ScorerDraftState) -> float:
        if STUB_STATS: return 0.5
        
        cid = candidat["id"]
        key = f"{cid}|{draft.my_role.upper()}"
        
        meta_stats = self.stats.get("meta", {}).get(key)
        if not meta_stats:
            return 0.5
            
        games = meta_stats.get("games", 0)
        wins = meta_stats.get("wins", 0)
        
        shrink_cfg = config.get("bayesian_shrinkage", {})
        k = shrink_cfg.get("meta_k", 500)
        prior = shrink_cfg.get("prior", 0.50)
        
        if games == 0:
            return 0.5
            
        return (wins + k * prior) / (games + k)

    def _score_lane(self, candidat: dict, draft: ScorerDraftState) -> float:
        if STUB_STATS or not draft.lane_opponent:
            return 0.5
            
        cid = candidat["id"]
        opp = norm_name(draft.lane_opponent)
        role = draft.my_role.upper()
        
        key = f"{cid}|{opp}|{role}"
        reverse_key = f"{opp}|{cid}|{role}"
        
        lane_stats = self.stats.get("lane", {}).get(key)
        
        # If not found, try to derive from symmetry
        if not lane_stats:
            rev_stats = self.stats.get("lane", {}).get(reverse_key)
            if rev_stats:
                games = rev_stats.get("games", 0)
                wins = games - rev_stats.get("wins", 0)
                lane_stats = {"games": games, "wins": wins}
                
        if not lane_stats or lane_stats.get("games", 0) < 200:
            return 0.5
            
        games = lane_stats.get("games", 0)
        wins = lane_stats.get("wins", 0)
        
        shrink_cfg = config.get("bayesian_shrinkage", {})
        k = shrink_cfg.get("lane_matchup_k", 300)
        prior = shrink_cfg.get("prior", 0.50)
        
        return (wins + k * prior) / (games + k)

    def _calculer_penalites(self, candidat: dict, draft: ScorerDraftState, allies: list[dict], enemies: list[dict]) -> float:
        return 0.0

    # -------------------------------------------------------------------------
    # Diversity
    # -------------------------------------------------------------------------

    def _cosine_sim(self, axes_a: dict, axes_b: dict) -> float:
        dot = sum(axes_a.get(k, 0)*axes_b.get(k, 0) for k in set(axes_a) | set(axes_b))
        mag_a = math.sqrt(sum(v**2 for v in axes_a.values()))
        mag_b = math.sqrt(sum(v**2 for v in axes_b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _similarite(self, a_id: str, b_id: str) -> float:
        champ_a = get_champion(a_id)
        champ_b = get_champion(b_id)
        
        div_cfg = config.get("diversity", {}).get("similarity_weights", {})
        w_arch = div_cfg.get("same_archetype", 0.5)
        w_cos = div_cfg.get("axes_cosine", 0.3)
        w_dmg = div_cfg.get("damage_mix", 0.2)

        arch_sim = 1.0 if champ_a.get("archetype") == champ_b.get("archetype") else 0.0
        cos_sim = self._cosine_sim(champ_a.get("axes", {}), champ_b.get("axes", {}))
        
        ad_a = champ_a.get("damage_mix", {}).get("ad", 0.0)
        ad_b = champ_b.get("damage_mix", {}).get("ad", 0.0)
        dmg_sim = 1.0 - abs(ad_a - ad_b)

        return (w_arch * arch_sim) + (w_cos * cos_sim) + (w_dmg * dmg_sim)

    def _top3_diversifie(self, scores: list[CandidateScore]) -> list[CandidateScore]:
        if not scores:
            return []
            
        div_cfg = config.get("diversity", {})
        floor_ratio = div_cfg.get("score_floor_ratio", 0.85)
        sim_threshold = div_cfg.get("similarity_threshold", 0.75)
        
        scores.sort(key=lambda x: x.total_score, reverse=True)
        retenus = [scores[0]]
        meilleur = scores[0].total_score

        for cand in scores[1:]:
            if len(retenus) == 3:
                break
            if cand.total_score < meilleur * floor_ratio:
                break
            if any(self._similarite(cand.champion_id, r.champion_id) > sim_threshold for r in retenus):
                continue
            retenus.append(cand)

        for cand in scores[1:]:
            if len(retenus) == 3: break
            if cand not in retenus: 
                logger.debug(f"Diversity fallback: {cand.champion_id} added by raw score.")
                retenus.append(cand)

        return retenus

    def _generate_reason(self, s1, s2, s3, s4, w, candidat, besoins, menaces) -> str:
        weighted = {"meta": s1 * w.get("meta", 0), "lane": s2 * w.get("lane", 0), 
                    "team": s3 * w.get("team_fit", 0), "counter": s4 * w.get("counter_comp", 0)}
        dominant = max(weighted.items(), key=lambda x: x[1])[0]

        axes = candidat.get("axes", {})
        
        if dominant == "meta": 
            return "Solide winrate dans la méta actuelle."
        elif dominant == "lane": 
            return "Domine statistiquement la phase de lane."
        elif dominant == "team":
            # Find the biggest need the champion fulfills
            best_need = ""
            best_val = 0
            for k, need_val in besoins.items():
                val = need_val * axes.get(k, 0)
                if val > best_val:
                    best_val = val
                    best_need = k
                    
            if best_need == "ap_damage": return "Comble le manque de dégâts magiques (AP) de l'équipe."
            if best_need == "ad_damage": return "Comble le manque de dégâts physiques (AD) de l'équipe."
            if best_need == "frontline": return "Apporte la frontline/tankiness dont l'équipe a besoin."
            if best_need == "hard_cc": return "Fournit les contrôles de foule (CC) manquants."
            if best_need == "engage": return "Offre une excellente capacité d'engage pour l'équipe."
            if best_need == "utility": return "Apporte l'utilitaire manquant à la composition."
            return "S'intègre parfaitement à la composition alliée."
            
        else: # counter
            # Find the biggest threat the champion counters
            best_threat = ""
            best_val = 0
            matrix = config.get("threat_matrix", {})
            for k, threat_val in menaces.items():
                if threat_val == 0: continue
                cfg = matrix.get(k, {})
                val_score = sum(axes.get(attr, 0) * weight for attr, weight in cfg.get("valorise", {}).items() if not attr.startswith("power_curve."))
                if val_score * threat_val > best_val:
                    best_val = val_score * threat_val
                    best_threat = k
                    
            if best_threat == "dive": return "Excellente survie face au dive et assassins adverses."
            if best_threat == "poke": return "Permet de contrer efficacement le poke ennemi."
            if best_threat == "hard_cc": return "Très résilient face aux compositions très axées sur les CC."
            if best_threat == "engage": return "Très bon pour disengage ou contrer les assauts ennemis."
            if best_threat == "ad_heavy": return "Permet de punir ou résister à une composition très AD."
            if best_threat == "ap_heavy": return "Permet de punir ou résister à une composition très AP."
            if best_threat == "scaling": return "Permet de punir le scaling adverse avec un fort early."
            return "Répond idéalement aux menaces de la composition adverse."

    # =========================================================================
    # Ban Recommendations
    # =========================================================================

    def recommend_ban(self, state: ScorerDraftState, top_n: int = 3) -> list[CandidateScore]:
        if not config:
            return []

        scored: list[CandidateScore] = []
        
        for cid, ch in by_id.items():
            if self._excluded(cid, state):
                continue
                
            cm, conf_cm = self._counter_me(cid, state)
            pa, conf_pa = self._protects_ally(cid, state)
            mt = self._meta_threat(cid)
            
            w_me = 0.45 * conf_cm
            w_ally = 0.20 * conf_pa * min(1.0, len(state.ally_hovers) / 4)
            w_meta = 1.0 - w_me - w_ally
            
            raw = w_meta * mt + w_me * cm + w_ally * pa
            rd = self._redundancy_discount(cid, ch)
            
            score = raw * rd
            
            # Formatting reason
            br_max = max((self.stats.get("banrate", {}).get(f"{cid}|{r}", 0.0) for r in ch.get("roles", [])), default=0.0)
            reason_parts = []
            if w_me > 0.1: reason_parts.append("me counter")
            if w_ally > 0.05: reason_parts.append("menace alliés")
            
            reason = ""
            if reason_parts:
                reason = " et ".join(reason_parts).capitalize() + f" (banrate: {br_max*100:.1f}%)"
            else:
                reason = f"Menace méta (banrate: {br_max*100:.1f}%)"
            
            scored.append(CandidateScore(
                champion_id=cid,
                total_score=score,
                sub_scores={"meta": mt, "counter": cm, "protect": pa, "rd": rd, "w_meta": w_meta},
                dominant_reason=reason
            ))

        scored.sort(key=lambda x: (-x.total_score, x.champion_id))
        return scored[:top_n]

    def _excluded(self, cid: str, state: ScorerDraftState) -> bool:
        norm_cid = norm_name(cid)
        if norm_cid in [norm_name(h) for h in state.ally_hovers]:
            return True
        if norm_cid == norm_name(state.my_hover):
            return True
        if norm_cid in [norm_name(bi) for bi in state.ally_ban_intents]:
            return True
        if norm_cid in [norm_name(b) for b in state.bans]:
            return True
        return False

    def _redundancy_discount(self, cid: str, ch: dict) -> float:
        br = 0.0
        for r in ch.get("roles", []):
            br = max(br, self.stats.get("banrate", {}).get(f"{cid}|{r}", 0.0))
        p_enemy_bans = min(0.85, br * 1.2)
        return max(0.4, 1.0 - p_enemy_bans)

    def _meta_threat(self, cid: str) -> float:
        ch = get_champion(cid)
        br_sum = 0.0
        pr_sum = 0.0
        for role in ch.get("roles", []):
            key = f"{cid}|{role}"
            br_sum += self.stats.get("banrate", {}).get(key, 0.0)
            pr_sum += self.stats.get("pickrate", {}).get(key, 0.0)
        
        if not br_sum and not pr_sum:
            return 0.05 # Stub fallback
        return min(1.0, (br_sum * pr_sum) * 5.0)

    def _my_likely_picks(self, state: ScorerDraftState) -> tuple[list[str], float]:
        if state.my_hover:
            return [state.my_hover], 1.0
        pool = state.my_recent_picks.get(state.my_role, [])
        if pool:
            return pool[:4], 0.6
        return [], 0.0

    # =========================================================================
    # Ban Recommendations - Lane Scoring
    # =========================================================================

    def _lane_lookup(self, role: str, cid: str, opponent: str):
        """
        Retourne (winrate_ajuste, games, schema_enrichi) pour `cid` face à
        `opponent` dans `role`, ou None si absent / volume insuffisant.
        """
        lane = self.stats.get("lane")
        if not lane:
            return None

        entry = lane.get(role, {}).get(cid, {}).get(opponent)
        if entry is None:
            return None

        # --- Forme plate : float nu, pas de volume ---
        if isinstance(entry, (int, float)):
            if not ChampionScorer._flat_schema_warned:
                logger.warning(
                    "patch_stats.json utilise le schéma lane plat (winrate sans "
                    "'games'). Shrinkage désactivé, confiance forfaitaire à %.1f. "
                    "Ajoute 'games' pour un scoring fiable.", _CONF_FLAT_SCHEMA
                )
                ChampionScorer._flat_schema_warned = True
            return float(entry), None, False

        # --- Forme enrichie : dict avec volume ---
        if isinstance(entry, dict):
            games = int(entry.get("games", 0))
            if games < _LANE_MIN_GAMES:
                return None
            if "wr" in entry:
                wr_raw = float(entry["wr"])
                wins = wr_raw * games
            elif "wins" in entry:
                wins = float(entry["wins"])
            else:
                return None
            wr_adj = (wins + _LANE_SHRINK_K * 0.5) / (games + _LANE_SHRINK_K)
            return wr_adj, games, True

        return None

    @staticmethod
    def _wr_to_threat(wr: float) -> float:
        """Winrate de l'adversaire contre moi → menace normalisée 0-1."""
        return max(0.0, min(1.0, (wr - _WR_FLOOR) / _WR_SPAN))

    @staticmethod
    def _volume_confidence(games_list: list, expected: int) -> float:
        """
        Confiance issue du volume de données.
        `expected` = nombre de matchups qu'on espérait trouver (taille du pool).
        """
        if not games_list:
            return 0.0
        if any(g is None for g in games_list):
            return _CONF_FLAT_SCHEMA
        # saturation : 2000 parties sur un matchup = confiance pleine
        per_matchup = [min(1.0, g / 2000.0) for g in games_list]
        coverage = min(1.0, len(games_list) / max(1, expected))
        return (sum(per_matchup) / len(per_matchup)) * coverage

    def _counter_me(self, cid: str, state: ScorerDraftState) -> tuple[float, float]:
        """
        À quel point le champion ennemi `cid` me pose problème en lane.
        Retourne (score 0-1, confiance 0-1).
        """
        role = getattr(state, "my_role", None)
        if not role:
            return 0.0, 0.0

        cand = get_champion(cid)
        if cand is None or role not in cand.get("roles", []):
            return 0.0, 0.0

        # --- Choix du pool : hover prioritaire, historique en repli ---
        hover = getattr(state, "my_hover", None)
        if hover:
            pool = [hover]
            base_conf = _CONF_HOVER
        else:
            recent = getattr(state, "my_recent_picks", None) or {}
            pool = list(recent.get(role, []))[:4]
            base_conf = _CONF_POOL

        if not pool:
            return 0.0, 0.0

        threats, volumes = [], []
        for mine in pool:
            hit = self._lane_lookup(role, cid, mine)
            if hit is None:
                continue
            wr_adj, games, _enriched = hit
            threats.append(self._wr_to_threat(wr_adj))
            volumes.append(games)

        if not threats:
            return 0.0, 0.0

        # Le pire matchup pèse plus que la moyenne
        score = 0.6 * max(threats) + 0.4 * (sum(threats) / len(threats))
        confidence = base_conf * self._volume_confidence(volumes, expected=len(pool))

        return score, confidence

    def _protects_ally(self, cid: str, state: ScorerDraftState) -> tuple[float, float]:
        """
        À quel point bannir `cid` protège un allié dont la présélection est
        connue. Retourne (score 0-1, confiance 0-1).
        """
        hovers = getattr(state, "ally_hovers", None) or []
        if not hovers:
            return 0.0, 0.0

        cand = get_champion(cid)
        if cand is None:
            return 0.0, 0.0
        cand_roles = set(cand.get("roles", []))

        # Rôles alliés si le LCU les fournit : {champion: role}
        # In ChampSelectState we don't have ally_hover_roles yet, we will just use intersection
        ally_roles = getattr(state, "ally_hover_roles", None) or {}

        threats, volumes = [], []
        for ally in hovers:
            if ally == getattr(state, "my_hover", None):
                continue  # traité par _counter_me

            known = ally_roles.get(ally)
            if known:
                roles_to_test = [known] if known in cand_roles else []
            else:
                ally_attrs = get_champion(ally)
                if ally_attrs is None:
                    continue
                roles_to_test = list(cand_roles & set(ally_attrs.get("roles", [])))

            best_here, best_games = None, None
            for role in roles_to_test:
                hit = self._lane_lookup(role, cid, ally)
                if hit is None:
                    continue
                wr_adj, games, _enriched = hit
                t = self._wr_to_threat(wr_adj)
                if best_here is None or t > best_here:
                    best_here, best_games = t, games

            if best_here is not None:
                threats.append(best_here)
                volumes.append(best_games)

        if not threats:
            return 0.0, 0.0

        score = max(threats)
        confidence = self._volume_confidence(volumes, expected=len(hovers))

        return score, confidence
