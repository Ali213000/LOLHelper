"""
Régression : _score_item levait un NameError sur la Coiffe de Rabadon.

La branche de pénalité early-Rabadon retombait sur `getattr(ls, ...)` alors
que `ls` n'est pas un paramètre de _score_item. Elle plantait dès que
`marginal_gains` était vide — ce qui faisait échouer tout le plan d'objets.
"""
import pytest

from services.stat_analyzer import StatAnalyzer

TRIGGERS = {
    "need_grievous": False, "gw_source": "", "need_armor_pen": False,
    "need_magic_pen": False, "need_tenacity": False, "can_adapt_defense": False,
    "enemy_fed_name": "", "need_antishield": False, "need_armor": False,
    "need_mr": False, "need_qss": False, "qss_cc_source": "",
    "adapt_threshold": 0, "cc_count": 0, "shield_count": 0, "tank_count": 0,
    "gold_state": "even",
}


@pytest.fixture(scope="module")
def analyzer():
    a = StatAnalyzer()
    a._ensure_loaded()
    return a


@pytest.mark.parametrize("marginal_gains", [None, {}])
def test_rabadon_sans_marginal_gains_ne_plante_pas(analyzer, marginal_gains):
    score, gold_eff, reason = analyzer._score_item(
        "Coiffe de Rabadon", "Mage", 0.5, "AP", dict(TRIGGERS), [],
        marginal_gains=marginal_gains,
    )
    assert isinstance(score, float)


def test_la_penalite_early_rabadon_s_applique_toujours(analyzer):
    """AP faible → score pénalisé par rapport à un AP élevé."""
    faible, _, _ = analyzer._score_item(
        "Coiffe de Rabadon", "Mage", 0.5, "AP", dict(TRIGGERS), [],
        marginal_gains={"_current_ap": 50},
    )
    eleve, _, _ = analyzer._score_item(
        "Coiffe de Rabadon", "Mage", 0.5, "AP", dict(TRIGGERS), [],
        marginal_gains={"_current_ap": 400},
    )
    assert faible < eleve


def test_un_autre_objet_reste_scorable(analyzer):
    score, _, _ = analyzer._score_item(
        "Couperet noir", "Fighter", 0.5, "AD", dict(TRIGGERS), [],
    )
    assert isinstance(score, float)
