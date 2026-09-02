"""
Déclencheur anti-soin (Grievous Wounds / « Hémorragie » en français).

Constaté en partie réelle : Dr. Mundo contre Fiddlesticks top et Darius jungle,
deux champions qui se soignent, sans jamais aucune proposition d'anti-soin.
Quatre causes empilées, toutes couvertes ici.
"""
import json
from pathlib import Path

import pytest

from services.stat_analyzer import StatAnalyzer, _HEALING_CHAMPION_WEIGHTS
from core.state_manager import PlayerInGameData

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def analyzer():
    a = StatAnalyzer()
    a._ensure_loaded()
    return a


def _enemies():
    return [
        PlayerInGameData(champion_name="Fiddlesticks", team="CHAOS", position="TOP", level=8),
        PlayerInGameData(champion_name="Darius", team="CHAOS", position="JUNGLE", level=6),
        PlayerInGameData(champion_name="Anivia", team="CHAOS", position="MID", level=8),
        PlayerInGameData(champion_name="Ezreal", team="CHAOS", position="ADC", level=7),
        PlayerInGameData(champion_name="Braum", team="CHAOS", position="SUPPORT", level=7),
    ]


# ------------------------------------------------- 1. les soigneurs manquants

def test_fiddlesticks_est_reconnu_comme_soigneur():
    """Le drain du W est un levier majeur : son absence tuait le déclencheur."""
    assert _HEALING_CHAMPION_WEIGHTS.get("Fiddlesticks", 0) >= 0.7


@pytest.mark.parametrize("champ", ["Viego", "Master Yi", "Kayn", "Gwen", "Ekko", "Udyr"])
def test_les_autres_oublis_sont_couverts(champ):
    assert champ in _HEALING_CHAMPION_WEIGHTS


# --------------------------------------- 2. les objets anti-soin sont réels

def test_les_objets_anti_soin_existent_vraiment(analyzer):
    """Les listes écrites à la main contenaient des objets fantômes."""
    for jeu in (analyzer._gw_tank, analyzer._gw_ad, analyzer._gw_ap):
        assert jeu, "liste anti-soin vide"
        for nom in jeu:
            assert nom in analyzer._item_db, f"{nom!r} n'existe pas dans item_data.json"


def test_thornmail_et_bramble_sont_disponibles_pour_un_tank(analyzer):
    assert "Cotte épineuse" in analyzer._gw_tank      # Thornmail
    assert "Armure roncière" in analyzer._gw_tank     # Bramble Vest


def test_les_objets_retenus_infligent_bien_de_l_hemorragie(analyzer):
    for nom in analyzer._gw_tank | analyzer._gw_ad | analyzer._gw_ap:
        desc = (analyzer._item_db[nom].get("description", "") or "").lower()
        assert "morragie" in desc, f"{nom!r} ne confère pas d'Hémorragie"


# ------------------------------- 3. collisions de noms entre modes de jeu

def test_la_version_faille_de_l_invocateur_gagne(analyzer):
    """
    216 noms sont partagés par plusieurs IDs (Arena 223xxx, Swarm 773xxx…).
    Sans arbitrage, la dernière variante lue écrasait la version Faille — pour
    94 objets, dont Thornmail, avec de mauvais tags et une autre description.
    """
    raw = json.loads((ROOT / "assets" / "item_data.json").read_text(encoding="utf-8"))["data"]
    for nom, retenu in analyzer._item_db.items():
        variantes = [v for v in raw.values() if v.get("name", "").strip() == nom]
        if len(variantes) < 2:
            continue
        sr = [v for v in variantes if v.get("maps", {}).get("11", False)]
        if sr:
            assert retenu.get("maps", {}).get("11", False), (
                f"{nom!r} : une variante hors Faille a été retenue"
            )


# --------------------------------------------- 4. le déclencheur se déclenche

def test_le_vis_a_vis_de_lane_pese_plus_lourd(analyzer):
    sans = analyzer._check_triggers(_enemies(), "Tank", 0.5, "hp", [])
    avec = analyzer._check_triggers(
        _enemies(), "Tank", 0.5, "hp", [], lane_opponent_name="Fiddlesticks"
    )
    assert not sans["need_grievous"]
    assert avec["need_grievous"], (
        "Fiddlesticks (0.75) + Darius (0.60) = 1.35 < 1.50 ; le bonus de "
        "vis-à-vis doit faire passer le seuil"
    )


def test_le_cas_reel_du_joueur(analyzer):
    t = analyzer._check_triggers(
        _enemies(), "Tank", 0.5, "hp", [], lane_opponent_name="Fiddlesticks"
    )
    assert t["need_grievous"]
    assert "Fiddlesticks" in t["gw_source"] and "Darius" in t["gw_source"]


# Le comportement « l'anti-soin résiste à la prescription et aux achats » est
# vérifié dans tests/test_plan_assemblage.py, sur un plan réellement calculé.
