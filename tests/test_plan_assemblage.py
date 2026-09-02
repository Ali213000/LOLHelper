"""
Assemblage du plan d'objets — tests de comportement.

Reproduit la partie qui a révélé les bugs : Dr. Mundo TOP niveau 16, Cœuracier
et Armure de Warmog achetés, Sandales de Mercure aux pieds, face à Fiddlesticks
(top) et Darius (jungle), deux champions qui se soignent.

Deux symptômes, une seule cause : BuildPlan.lock() écrasait le premier
emplacement recommandé avec l'objet acheté. Résultat, les achats n'apparaissaient
pas comme tels ET la recommandation anti-soin disparaissait à chaque achat.
"""
import pytest

from core.event_bus import bus, EventBus
from core.state_manager import InGameState, LiveStats, PlayerInGameData
from models.build_plan import SlotState
from services.image_cache import ImageCache

WARMOG = 3083
COEURACIER = 3084
SANDALES_MERCURE = 3111
BOUCLIER_DORAN = 1054
BALISE = 3340


def _etat(items):
    moi = PlayerInGameData(
        champion_name="Dr. Mundo", team="ORDER", position="TOP", level=16,
        kills=3, deaths=2, assists=1, gold=1200, items=list(items),
        is_local_player=True,
    )
    ennemis = [
        PlayerInGameData(champion_name="Fiddlesticks", team="CHAOS", position="TOP", level=15),
        PlayerInGameData(champion_name="Darius", team="CHAOS", position="JUNGLE", level=14),
        PlayerInGameData(champion_name="Anivia", team="CHAOS", position="MID", level=15),
        PlayerInGameData(champion_name="Ezreal", team="CHAOS", position="ADC", level=14),
        PlayerInGameData(champion_name="Braum", team="CHAOS", position="SUPPORT", level=13),
    ]
    return InGameState(
        in_game=True, game_time_seconds=1500.0, local_player=moi,
        all_players=[moi] + ennemis,
        live_stats=LiveStats(armor=95, magic_resist=52, max_health=3400,
                             current_health=3400, attack_damage=110,
                             ability_power=0, attack_speed=0.9, crit_chance=0.0),
    )


def _etat_soigneurs(items):
    """
    Même joueur, mais face à une composition qui soigne d'après les MESURES.
    L'ancienne (Fiddlesticks + Darius) reposait sur des poids estimés que les
    données n'appuient pas : Fiddlesticks manque d'occurrences et Darius ne
    dépasse pas la médiane de son poste.
    """
    etat = _etat(items)
    for p, champ in zip(etat.all_players[1:], ("Aatrox", "Nasus", "Sona", "Ezreal", "Anivia")):
        p.champion_name = champ
    return etat


def _plan(items, etat=None):
    from ai.coaching_engine import CoachingEngine
    from ai.llm_client import LLMClient

    recu = []
    cb = lambda p: recu.append(p)
    bus.subscribe(EventBus.ITEM_ADVICE_READY, cb)
    try:
        CoachingEngine(llm_client=LLMClient(provider="gemini"))._compute_plan(
            etat if etat is not None else _etat(items), "TOP", "test"
        )
    finally:
        bus.unsubscribe(EventBus.ITEM_ADVICE_READY, cb)
    assert recu, "aucun plan émis"
    return recu[0]["plan"]


@pytest.fixture(scope="module")
def plan_partie_reelle():
    return _plan([COEURACIER, SANDALES_MERCURE, WARMOG, BOUCLIER_DORAN, BALISE])


def _ids(plan):
    return [s.item_id for s in plan.legendary_slots]


# ------------------------------------------- les achats sont bien reconnus

def test_les_objets_achetes_apparaissent(plan_partie_reelle):
    ids = _ids(plan_partie_reelle)
    assert WARMOG in ids, "Armure de Warmog achetée mais absente du plan"
    assert COEURACIER in ids, "Cœuracier acheté mais absent du plan"


def test_ils_sont_marques_comme_possedes(plan_partie_reelle):
    etats = {
        s.item_id: s.state for s in plan_partie_reelle.legendary_slots
        if s.item_id in (WARMOG, COEURACIER)
    }
    for iid, state in etats.items():
        assert state in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN), (
            f"objet {iid} en inventaire mais affiché comme {state.name}"
        )


def test_ils_sont_conformes_au_plan_donc_en_vert(plan_partie_reelle):
    """Warmog + Cœuracier est la prescription de Mundo : ce ne sont pas des écarts."""
    for s in plan_partie_reelle.legendary_slots:
        if s.item_id in (WARMOG, COEURACIER):
            assert s.state is SlotState.OWNED_ON_PLAN


def test_les_bottes_portees_sont_reconnues(plan_partie_reelle):
    assert plan_partie_reelle.boots.item_id == SANDALES_MERCURE
    assert plan_partie_reelle.boots.state is SlotState.OWNED_ON_PLAN


def test_les_bottes_ne_prennent_pas_de_slot_legendaire(plan_partie_reelle):
    cache = ImageCache()
    for s in plan_partie_reelle.legendary_slots:
        assert not cache.is_boots(s.item_id)


def test_ni_le_bouclier_de_doran_ni_la_balise(plan_partie_reelle):
    ids = _ids(plan_partie_reelle)
    assert BOUCLIER_DORAN not in ids
    assert BALISE not in ids


# ----------------------------------- l'achat n'efface pas la recommandation

@pytest.fixture(scope="module")
def plan_face_aux_soigneurs():
    items = [COEURACIER, SANDALES_MERCURE, WARMOG, BOUCLIER_DORAN, BALISE]
    return _plan(items, etat=_etat_soigneurs(items))


def test_l_anti_soin_survit_aux_achats(plan_face_aux_soigneurs):
    """
    Le symptôme d'origine : acheter Cœuracier écrasait l'emplacement anti-soin.
    Face à une composition qui soigne, la recommandation doit rester présente.
    """
    raisons = [s.reason for s in plan_face_aux_soigneurs.legendary_slots]
    assert "anti-soin" in raisons, (
        "aucun emplacement anti-soin alors que deux soigneurs sont en face"
    )


def test_l_anti_soin_est_a_acheter_pas_deja_possede(plan_face_aux_soigneurs):
    slot = next(s for s in plan_face_aux_soigneurs.legendary_slots if s.reason == "anti-soin")
    assert slot.state is SlotState.PLANNED
    assert slot.item_id is not None


def test_aucun_doublon(plan_partie_reelle):
    ids = [i for i in _ids(plan_partie_reelle) if i is not None]
    assert len(ids) == len(set(ids)), f"objets en double dans le plan : {ids}"


def test_la_capacite_de_l_inventaire_est_respectee(plan_partie_reelle):
    """Non-ADC : 5 légendaires + 1 emplacement bottes = les 6 cases du jeu."""
    assert len(plan_partie_reelle.legendary_slots) <= 5


# ------------------------------------------------ progression des achats

def test_les_achats_s_accumulent_sans_perdre_le_conseil():
    """À chaque nouvel achat, l'objet rejoint les possédés et le plan continue."""
    sans = _plan([SANDALES_MERCURE, BOUCLIER_DORAN])
    un = _plan([SANDALES_MERCURE, WARMOG, BOUCLIER_DORAN])
    deux = _plan([SANDALES_MERCURE, WARMOG, COEURACIER, BOUCLIER_DORAN])

    def possedes(p):
        return sum(
            1 for s in p.legendary_slots
            if s.state in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN)
        )

    assert possedes(sans) == 0
    assert possedes(un) == 1
    assert possedes(deux) == 2
    for p in (sans, un, deux):
        planifies = [s for s in p.legendary_slots if s.state is SlotState.PLANNED]
        assert planifies, "plus aucune recommandation à acheter"


THORNMAIL = 3075


def test_l_anti_soin_achete_est_affiche_comme_conforme():
    """
    Acheter l'objet conseillé le faisait passer en écart (gris) : une fois
    acquis, il sort de la liste des recommandations, et son absence était
    interprétée comme une déviation.
    """
    items = [COEURACIER, SANDALES_MERCURE, WARMOG, THORNMAIL, BOUCLIER_DORAN]
    plan = _plan(items, etat=_etat_soigneurs(items))
    slot = next(s for s in plan.legendary_slots if s.item_id == THORNMAIL)
    assert slot.state is SlotState.OWNED_ON_PLAN


def test_tous_les_achats_conformes_restent_verts():
    items = [COEURACIER, SANDALES_MERCURE, WARMOG, THORNMAIL, BOUCLIER_DORAN]
    plan = _plan(items, etat=_etat_soigneurs(items))
    possedes = [
        s for s in plan.legendary_slots
        if s.state in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN)
    ]
    assert len(possedes) == 3
    assert all(s.state is SlotState.OWNED_ON_PLAN for s in possedes)


def test_le_plan_continue_apres_quatre_objets():
    plan = _plan([COEURACIER, SANDALES_MERCURE, WARMOG, THORNMAIL, BOUCLIER_DORAN])
    assert [s for s in plan.legendary_slots if s.state is SlotState.PLANNED], (
        "il reste des emplacements à remplir, le plan doit continuer à conseiller"
    )
