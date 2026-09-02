"""
Poids des composantes de draft, calibrés sur leur pouvoir prédictif mesuré.

team_fit et counter_comp portaient ensemble 0.55 du score de pick sur des poids
écrits à la main. Contrairement à meta et aux duels de voie, leur pouvoir
prédictif n'avait jamais été vérifié : on leur faisait confiance parce qu'elles
semblaient raisonnables.

Test direct sur 5 056 matchs — pour chaque équipe on calcule ce que le scoreur
aurait attribué à ses cinq champions, et on regarde si l'écart entre les deux
camps prédit l'issue :

    team_fit       49.2 % -> 50.8 %   +1.7 %   z = 1.07   non significatif
    counter_comp   51.4 % -> 48.6 %   -2.9 %   z = -1.82  sens inverse

Aucune des deux ne prédit la victoire, et counter_comp pointe à l'envers. Ni
l'une ni l'autre n'est annulée pour autant — à ce volume un effet réel jusqu'à
~3.2 points reste indétectable — mais elles ne peuvent plus dominer le score.

Les poids sont désormais proportionnels aux tailles d'effet mesurées, pondérés
par la disponibilité de l'adversaire de voie selon le rang de pick : en premier
pick on ne le connaît pas, en dernier si.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "data" / "draft_config.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mesures():
    return json.loads((ROOT / "data" / "draft_measures.json").read_text(encoding="utf-8"))


def test_le_pouvoir_predictif_est_enregistre(mesures):
    c = mesures.get("composantes_effet")
    assert c and {"team_fit", "counter_comp"} <= set(c)
    assert mesures.get("composantes_matchs", 0) > 4000


def test_aucune_heuristique_ne_predit_la_victoire(mesures):
    """
    Le constat qui justifie la rebalance. S'il devient faux avec plus de
    données, ce test échoue et les poids doivent être recalculés.
    """
    for nom in ("team_fit", "counter_comp"):
        assert abs(mesures["composantes_effet"][nom]["z"]) < 2.24, (
            f"{nom} est devenu significatif (Bonferroni sur 2 tests) : "
            "relancer scripts/calibrate_draft_config.py"
        )


def test_les_poids_somment_a_un(config):
    for slot, w in config["weights_by_slot"].items():
        assert abs(sum(w.values()) - 1.0) < 0.02, f"{slot} : {sum(w.values())}"


def test_les_composantes_mesurees_dominent(config):
    """
    meta et les duels sont établis (2.2 et 3.2 points d'écart-type) ; team_fit
    et counter_comp ne le sont pas. Dès que l'adversaire de voie est connu, les
    premières doivent peser plus que les secondes.
    """
    for slot in ("middle", "last_pick"):
        w = config["weights_by_slot"][slot]
        assert w["meta"] + w["lane"] > w["team_fit"] + w["counter_comp"], slot


def test_le_duel_pese_selon_sa_disponibilite(config):
    """
    En blind pick l'adversaire est inconnu : donner du poids à « lane » le
    gaspillerait sur une valeur neutre. Il croît avec le rang de pick.
    """
    w = config["weights_by_slot"]
    assert w["blind_pick"]["lane"] == 0.0
    assert (w["first_pick"]["lane"] < w["middle"]["lane"] < w["last_pick"]["lane"])


def test_counter_comp_est_la_plus_faible(config):
    """
    Seule composante mesurée à effet négatif : elle ne peut plus dominer.

    La comparaison exclut « lane », dont le poids est réduit par l'indisponibilité
    de l'adversaire en début de draft et non par sa taille d'effet — en premier
    pick il tombe à 0.06 alors que c'est la composante la mieux établie.
    """
    for slot, w in config["weights_by_slot"].items():
        assert w["counter_comp"] < w["meta"], slot
        assert w["counter_comp"] < w["team_fit"], slot


def test_aucune_composante_n_est_annulee(config):
    """
    Absence de preuve n'est pas preuve d'absence : à ce volume un effet réel
    jusqu'à ~3.2 points resterait indétectable.
    """
    for slot, w in config["weights_by_slot"].items():
        for nom in ("meta", "team_fit", "counter_comp"):
            assert w[nom] > 0.0, f"{slot}/{nom} annulé sans preuve"


def test_la_provenance_des_poids_est_tracee(config):
    assert "measure_component_power" in config.get("weights_source", "")
