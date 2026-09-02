"""
Base des effets d'objets conditionnels (data/item_conditions.json).

Elle remplace des ensembles écrits à la main en français, qui avaient dérivé
trois fois sans qu'aucune erreur ne soit levée : « Plaques de l'épineux »,
« Chaîne de Chempunk » et « Voile de mercure » n'existaient plus, et les
déclencheurs correspondants ne partaient donc jamais.

L'indexation par identifiant et l'avertissement au chargement évitent que
l'histoire se répète.
"""
import json
from pathlib import Path

import pytest

from data import item_conditions as IC
from services.stat_analyzer import StatAnalyzer

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def analyzer():
    a = StatAnalyzer()
    a._ensure_loaded()
    return a


@pytest.fixture(scope="module")
def base():
    return json.loads((ROOT / "data" / "item_conditions.json").read_text(encoding="utf-8"))


# ------------------------------------------------------- intégrité de la base

def test_tous_les_identifiants_existent(analyzer, base):
    """Le point de tout l'exercice : plus aucun objet fantôme."""
    connus = set(analyzer._item_id_to_name)
    manquants = [
        f"{e['id']} ({e.get('nom_indicatif')})"
        for e in base["objets"] if e["id"] not in connus
    ]
    assert not manquants, f"identifiants absents de Data Dragon : {manquants}"


def test_les_parts_sont_des_fractions(base):
    for e in base["objets"]:
        for c in e["conditions"]:
            assert 0 < c["part"] < 1, f"{e['nom_indicatif']} : part hors [0,1]"


def test_chaque_declencheur_est_calcule(analyzer, base):
    """Un déclencheur déclaré mais jamais calculé = décote permanente injustifiée."""
    from core.state_manager import PlayerInGameData

    ennemis = [PlayerInGameData(champion_name="Darius", team="CHAOS", level=10)]
    produits = set(analyzer._check_triggers(ennemis, "Tank", 0.5, "hp", []))
    declares = {c["declencheur"] for e in base["objets"] for c in e["conditions"]}
    assert declares <= produits, (
        f"déclencheurs déclarés mais non calculés : {sorted(declares - produits)}"
    )


def test_la_base_couvre_les_grandes_familles(base):
    declares = {c["declencheur"] for e in base["objets"] for c in e["conditions"]}
    for attendu in ("need_grievous", "need_anticrit", "need_antiauto",
                    "need_qss", "need_antishield", "need_armor_pen",
                    "need_magic_pen", "need_tenacity"):
        assert attendu in declares, f"{attendu} absent de la base"


# ------------------------------------------------------------- résolution

def test_le_chargement_resout_vers_les_noms_courants(analyzer):
    assert analyzer._conditions, "aucune condition chargée"
    for nom in analyzer._conditions:
        assert nom in analyzer._item_db, f"{nom!r} ne correspond à aucun objet"


def test_un_identifiant_inconnu_est_signale(caplog):
    """L'échec doit être bruyant, contrairement aux anciennes listes."""
    with caplog.at_level("WARNING"):
        IC.charger({})          # aucun objet connu → tout est introuvable
    assert any("introuvable" in r.message.lower() for r in caplog.records)


# --------------------------------------------------------- cumul des effets

def test_un_objet_peut_porter_plusieurs_conditions(analyzer):
    """Rappel mortel : anti-soin ET pénétration d'armure."""
    conds = analyzer._conditions.get("Rappel mortel")
    assert conds and len(conds) == 2
    assert {c["trigger"] for c in conds} == {"need_grievous", "need_armor_pen"}


def test_les_conditions_se_cumulent():
    conds = [
        {"trigger": "a", "conditional_share": 0.22},
        {"trigger": "b", "conditional_share": 0.25},
    ]
    ratio_aucun, inactifs = IC.ratio_effectif(conds, {"a": False, "b": False})
    ratio_un, _ = IC.ratio_effectif(conds, {"a": True, "b": False})
    ratio_tous, vides = IC.ratio_effectif(conds, {"a": True, "b": True})

    assert ratio_tous == 1.0 and not vides
    assert ratio_aucun < ratio_un < ratio_tous
    assert len(inactifs) == 2
    assert ratio_aucun == pytest.approx(0.78 * 0.75, abs=1e-6)


def test_la_decote_a_un_plancher():
    """Privé de ses effets, un objet garde ses statistiques brutes."""
    enormes = [{"trigger": f"t{i}", "conditional_share": 0.5} for i in range(5)]
    ratio, _ = IC.ratio_effectif(enormes, {})
    assert ratio == IC.RATIO_MINIMUM


# ------------------------------------------- effet réel sur les scores

@pytest.mark.parametrize("objet,declencheur,classe,priorite", [
    ("Présage de Randuin", "need_anticrit", "Tank", "Armor_EHP"),
    ("Cœur gelé", "need_antiauto", "Tank", "Armor_EHP"),
    ("Bâton du vide", "need_magic_pen", "Mage", "AP"),
    ("Ceinture de mercure", "need_qss", "Tank", "MR_EHP"),
])
def test_chaque_objet_vaut_moins_sans_son_declencheur(
    analyzer, objet, declencheur, classe, priorite
):
    base = dict.fromkeys(
        ["need_grievous", "need_armor_pen", "need_magic_pen", "need_tenacity",
         "can_adapt_defense", "need_antishield", "need_armor", "need_mr",
         "need_qss", "need_anticrit", "need_antiauto"], False)
    base.update({"gw_source": "", "enemy_fed_name": "", "qss_cc_source": "",
                 "adapt_threshold": 0, "cc_count": 0, "shield_count": 0,
                 "tank_count": 0, "gold_state": "even"})
    sans, _, why = analyzer._score_item(objet, classe, 0.5, priorite, dict(base), [])
    avec, _, _ = analyzer._score_item(
        objet, classe, 0.5, priorite, dict(base, **{declencheur: True}), []
    )
    assert sans < avec, f"{objet} devrait être décoté sans {declencheur}"
    assert "inutilis" in why
