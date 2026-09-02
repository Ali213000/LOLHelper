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

@pytest.mark.parametrize("champ,mini", [
    ("Zac", 0.95), ("Vladimir", 0.95), ("Soraka", 0.95),
    ("Briar", 0.80), ("Sona", 0.70), ("Nami", 0.60),
    ("Tryndamere", 0.60), ("Nasus", 0.60), ("Aatrox", 0.50),
])
def test_les_soigneurs_mesures_pesent_leur_poids(champ, mini):
    """Poids issus de 75 900 participants, plus d'une estimation."""
    assert _HEALING_CHAMPION_WEIGHTS.get(champ, 0) >= mini


@pytest.mark.parametrize("champ", ["Darius", "Zed", "Riven", "Master Yi", "Viego"])
def test_les_faux_soigneurs_ont_ete_ecartes(champ):
    """
    Ces champions figuraient dans la table estimée à 0.55–0.80. La mesure les
    situe au niveau de la médiane de leur poste, voire en dessous : Master Yi
    soigne 66 PV/min de MOINS que le jungleur médian, Darius dépasse la médiane
    top de 10 PV/min. Aucun ne justifie de l'anti-soin par sa seule présence.
    """
    assert _HEALING_CHAMPION_WEIGHTS.get(champ, 0) == 0


def test_fiddlesticks_reste_non_mesure():
    """
    Moins de 30 parties sur 7 810 : aucune mesure possible. Il est donc absent
    de la table plutôt que porté par un jugement. Le conseil d'anti-soin qu'il
    avait motivé reposait sur une valeur que rien n'appuie.
    """
    assert "Fiddlesticks" not in _HEALING_CHAMPION_WEIGHTS


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

def _soigneurs():
    """Composition qui soigne réellement, d'après les mesures."""
    return [
        PlayerInGameData(champion_name="Aatrox", team="CHAOS", position="TOP", level=15),
        PlayerInGameData(champion_name="Nasus", team="CHAOS", position="JUNGLE", level=14),
        PlayerInGameData(champion_name="Sona", team="CHAOS", position="UTILITY", level=13),
        PlayerInGameData(champion_name="Ezreal", team="CHAOS", position="ADC", level=14),
        PlayerInGameData(champion_name="Anivia", team="CHAOS", position="MID", level=15),
    ]


def test_le_vis_a_vis_de_lane_pese_plus_lourd(analyzer):
    """Le champion qu'on affronte en boucle décide des échanges."""
    sans = analyzer._check_triggers(_soigneurs(), "Tank", 0.5, "hp", [])
    avec = analyzer._check_triggers(
        _soigneurs(), "Tank", 0.5, "hp", [], lane_opponent_name="Aatrox"
    )
    assert not sans["need_grievous"]
    assert avec["need_grievous"], (
        "Aatrox 0.58 + Nasus 0.64 + Sona 0.77 = 1.99, sous le seuil de 2.00 ; "
        "le bonus de vis-à-vis doit faire basculer"
    )


def test_la_partie_du_joueur_ne_declenche_plus(analyzer):
    """
    Documente un revirement. Le conseil d'anti-soin donné sur cette partie
    reposait sur Fiddlesticks 0.75 et Darius 0.60, deux valeurs estimées. La
    mesure ne retient ni l'un ni l'autre : Fiddlesticks manque de données,
    Darius ne dépasse pas sa médiane de poste. La composition ne justifie donc
    pas d'anti-soin.
    """
    t = analyzer._check_triggers(
        _enemies(), "Tank", 0.5, "hp", [], lane_opponent_name="Fiddlesticks"
    )
    assert t["need_grievous"] is False


# Le comportement « l'anti-soin résiste à la prescription et aux achats » est
# vérifié dans tests/test_plan_assemblage.py, sur un plan réellement calculé.
