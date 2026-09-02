"""
Valeur conditionnelle des objets anti-critique.

Signalé en partie : Présage de Randuin recommandé face à Fiddlesticks, Darius,
Anivia, Ezreal et Braum — aucun porteur de coups critiques. Le moteur n'avait
tout simplement aucune mesure du critique ADVERSE : il notait Randuin sur ses
seules stats brutes (75 armure + 350 PV pour 2700 or) sans voir qu'une part du
prix paie une passive inutile ici.
"""
import pytest

from core.state_manager import PlayerInGameData
from services.stat_analyzer import StatAnalyzer

RANDUIN = "Présage de Randuin"

_BASE_TRIGGERS = {
    "need_grievous": False, "gw_source": "", "need_armor_pen": False,
    "need_magic_pen": False, "need_tenacity": False, "can_adapt_defense": False,
    "enemy_fed_name": "", "need_antishield": False, "need_armor": False,
    "need_mr": False, "need_qss": False, "qss_cc_source": "",
    "adapt_threshold": 0, "cc_count": 0, "shield_count": 0, "tank_count": 0,
    "gold_state": "even", "need_anticrit": False, "need_antiauto": False,
}


@pytest.fixture(scope="module")
def analyzer():
    a = StatAnalyzer()
    a._ensure_loaded()
    return a


def _equipe(noms, items=None):
    return [
        PlayerInGameData(champion_name=n, team="CHAOS", level=14, items=list(items or []))
        for n in noms
    ]


SANS_CRIT = ["Fiddlesticks", "Darius", "Anivia", "Ezreal", "Braum"]
AVEC_CRIT = ["Fiddlesticks", "Darius", "Anivia", "Jinx", "Braum"]


# ------------------------------------------------------- détection du critique

def test_aucune_menace_de_crit_dans_la_partie_signalee(analyzer):
    t = analyzer._check_triggers(_equipe(SANS_CRIT), "Tank", 0.5, "hp", [])
    assert t["need_anticrit"] is False


def test_un_tireur_a_crit_declenche_le_besoin(analyzer):
    t = analyzer._check_triggers(_equipe(AVEC_CRIT), "Tank", 0.5, "hp", [])
    assert t["need_anticrit"] is True


def test_un_objet_de_crit_en_inventaire_suffit(analyzer):
    """Même sans champion marqué crit_viable, l'achat trahit l'intention."""
    lame_infinie = 3031
    ennemis = _equipe(SANS_CRIT)
    ennemis[3].items = [lame_infinie]
    t = analyzer._check_triggers(ennemis, "Tank", 0.5, "hp", [])
    assert t["need_anticrit"] is True


# --------------------------------------------------- pénalisation du score

def test_randuin_est_penalise_sans_menace_de_crit(analyzer):
    tr_sans = dict(_BASE_TRIGGERS, need_anticrit=False)
    tr_avec = dict(_BASE_TRIGGERS, need_anticrit=True)
    sans, _, why = analyzer._score_item(RANDUIN, "Tank", 0.5, "Armor_EHP", tr_sans, [])
    avec, _, _ = analyzer._score_item(RANDUIN, "Tank", 0.5, "Armor_EHP", tr_avec, [])
    assert sans < avec, "Randuin devrait valoir moins face à une équipe sans crit"
    assert "inutilis" in why, f"la raison doit expliquer la décote, obtenu : {why!r}"


def test_la_part_conditionnelle_est_declaree(analyzer):
    conds = analyzer._conditions.get(RANDUIN)
    assert conds, "Randuin doit déclarer une part de prix conditionnelle"
    assert any(c["trigger"] == "need_anticrit" for c in conds)
    assert all(0 < c["conditional_share"] < 1 for c in conds)


def test_un_objet_inconditionnel_n_est_pas_penalise(analyzer):
    """
    La décote ne vise que les objets dont une passive dépend d'un contexte.
    Cœur gelé n'en déclare aucune : son prix est intégralement utile.
    (Cotte épineuse, elle, est bien conditionnelle — à l'anti-soin.)
    """
    tr = dict(_BASE_TRIGGERS, need_anticrit=False, need_antiauto=True)
    _, _, why = analyzer._score_item("Cœur gelé", "Tank", 0.5, "Armor_EHP", tr, [])
    assert "inutilis" not in why


# ------------------------------- l'anti-soin déjà acheté relâche le déclencheur

def test_posseder_un_anti_soin_desactive_le_declencheur(analyzer):
    """
    La liste de contrôle était écrite à la main et contenait des noms disparus :
    acheter Thornmail ne coupait donc pas la demande d'anti-soin.
    """
    ennemis = _equipe(SANS_CRIT)   # Fiddlesticks + Darius soignent
    sans = analyzer._check_triggers(
        ennemis, "Tank", 0.5, "hp", [], lane_opponent_name="Fiddlesticks"
    )
    avec = analyzer._check_triggers(
        ennemis, "Tank", 0.5, "hp", ["Cotte épineuse"], lane_opponent_name="Fiddlesticks"
    )
    assert sans["need_grievous"] is True
    assert avec["need_grievous"] is False
