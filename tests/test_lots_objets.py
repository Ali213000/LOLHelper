"""
Lot d'objets mesuré par champion et par poste.

Le moteur note les objets à partir de leurs statistiques. Cela échoue dès que la
valeur de l'objet vit dans sa passive : Éclipse n'expose que « 60 AD » et perd
contre une Hydre titanesque (40 AD + 600 PV), alors que Pantheon l'achète dans
59 % de ses parties. Aucun réglage d'affinité ne corrige cela — on ne peut pas
pondérer une statistique absente.

Deux mécanismes censés couvrir ce trou étaient hors service :

  · core_items_prescription mesurait la confiance comme la fréquence du COUPLE
    exact de deux objets, et exigeait 0.35. Pantheon TOP tombait à 0.333 et
    FiddleSticks à 0.21 — non parce que leurs objets sont incertains, mais parce
    qu'ils les achètent dans un ordre variable. Kai'Sa (0.678) et Lillia (0.594)
    passaient : ce sont exactement les deux cas qui fonctionnaient.

  · situational_frequencies ne couvrait que 134 couples champion|poste avec UN
    objet chacun, indexés « FiddleSticks » quand le runtime cherche
    « Fiddlesticks ».

Le lot mesure chaque objet séparément — statistique bien plus stable qu'un
couple — sur 273 couples et 7 objets en médiane.
"""
import json
from pathlib import Path

import pytest

from data import item_pool

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------- résolution

def test_le_lot_est_disponible():
    assert item_pool.disponible(), "champion_item_pools.json absent ou vide"


@pytest.mark.parametrize("champion,poste", [
    ("Fiddlesticks", "JUNGLE"),      # Data Dragon dit « Fiddlesticks »
    ("FiddleSticks", "JUNGLE"),      # Match-V5 dit « FiddleSticks »
    ("Kai'Sa", "ADC"),
    ("Kaisa", "ADC"),
    ("Pantheon", "SUPPORT"),
    ("Pantheon", "UTILITY"),         # la Live Client API dit « UTILITY »
    ("Kai'Sa", "BOTTOM"),            # et « BOTTOM » pour l'ADC
])
def test_les_graphies_de_production_trouvent_le_lot(champion, poste):
    assert item_pool.lot(champion, poste).get("objets")


def test_un_poste_inconnu_se_rabat_sur_le_plus_joue():
    """Mieux vaut un lot mesuré ailleurs qu'aucun lot du tout."""
    assert item_pool.lot("Lillia", "TOP").get("objets")


def test_un_champion_inconnu_ne_donne_aucun_avis():
    """Sans mesure, le lot doit se taire — pas pénaliser à l'aveugle."""
    assert item_pool.merite("Chalumeau", "TOP", None, "Coiffe de Rabadon") == 1.0


# --------------------------------------------------------- mérite

def test_le_merite_separe_le_coeur_du_reste():
    """
    Le classement par statistiques mettait Hydre titanesque et Estropieur devant
    Éclipse et Couperet noir, faute de voir les passives.
    """
    coeur = [item_pool.merite("Pantheon", "SUPPORT", None, n)
             for n in ("Éclipse", "Couperet noir")]
    hors = [item_pool.merite("Pantheon", "SUPPORT", None, n)
            for n in ("Hydre titanesque", "Estropieur")]
    assert min(coeur) > max(hors) * 2


def test_luden_n_est_pas_un_objet_de_fiddlesticks():
    """Signalé en partie : Écho de Luden absent de ses 191 parties mesurées."""
    luden = item_pool.merite("Fiddlesticks", "JUNGLE", None, "Écho de Luden")
    zhonya = item_pool.merite("Fiddlesticks", "JUNGLE", None, "Sablier de Zhonya")
    assert luden == item_pool.PENALITE_HORS_LOT
    assert zhonya > 1.0


def test_le_merite_reste_borne():
    for cle, donnees in json.loads(
        (ROOT / "data" / "champion_item_pools.json").read_text(encoding="utf-8")
    )["pools"].items():
        champ, poste = cle.rsplit("|", 1)
        for o in donnees["objets"]:
            m = item_pool.merite(champ, poste, o["id"], o["nom"])
            assert item_pool.PENALITE_HORS_LOT <= m <= 1.25, f"{cle} {o['nom']} → {m}"


# ------------------------------------------- intégration au moteur

def test_le_garde_fou_mort_a_disparu():
    """
    situational_frequencies couvrait 134 couples avec un objet chacun, sous une
    graphie que la recherche ne trouvait pas : le laisser en place donnait
    l'illusion d'un filtrage.
    """
    src = (ROOT / "services" / "stat_analyzer.py").read_text(encoding="utf-8")
    assert "self._situational_freqs" not in src


def test_un_declencheur_actif_protege_un_objet_hors_lot():
    """
    Un anti-soin face à des soigneurs reste le bon achat même si le champion ne
    l'achète jamais d'habitude : le contexte prime sur l'habitude.
    """
    from services.stat_analyzer import StatAnalyzer

    a = StatAnalyzer()
    a._ensure_loaded()
    anti_soin = next(iter(a._gw_ad))
    declencheurs = {"need_grievous": True, "need_armor_pen": False,
                    "need_magic_pen": False, "need_tenacity": False}
    assert a._repond_a_un_declencheur(anti_soin, declencheurs, "Fighter")
    assert not a._repond_a_un_declencheur("Coiffe de Rabadon", declencheurs, "Fighter")


def test_aucun_declencheur_ne_protege_sans_besoin():
    from services.stat_analyzer import StatAnalyzer

    a = StatAnalyzer()
    a._ensure_loaded()
    anti_soin = next(iter(a._gw_ad))
    assert not a._repond_a_un_declencheur(anti_soin, {}, "Fighter")
