"""
Détection des bottes par tag DDragon plutôt que par sous-chaîne française.

L'ancienne heuristique cherchait "bottes"/"tabi"/"mercure"/"symbiote"/"zphyr"
dans le nom : elle ratait "Coques en acier" (Plated Steelcaps) et "zphyr"
était une faute de frappe qui ne matchait jamais rien.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ITEMS_PATH = ROOT / "assets" / "item_data.json"


@pytest.fixture(scope="module")
def items():
    if not ITEMS_PATH.exists():
        pytest.skip("assets/item_data.json absent")
    return json.loads(ITEMS_PATH.read_text(encoding="utf-8"))["data"]


def _boots_ids(items):
    return {int(i) for i, v in items.items() if "Boots" in v.get("tags", []) and i.isdigit()}


@pytest.mark.parametrize("item_id,label", [
    (3047, "Coques en acier (Plated Steelcaps)"),
    (3111, "Sandales de Mercure"),
    (3006, "Jambières du berzerker"),
    (3020, "Chaussures de sorcier"),
])
def test_les_bottes_courantes_sont_reconnues(items, item_id, label):
    assert item_id in _boots_ids(items), f"{label} non détecté comme bottes"


def test_l_ancienne_heuristique_ratait_plated_steelcaps(items):
    """Documente le bug corrigé : le nom FR ne contient aucun mot-clé cherché."""
    name = items["3047"]["name"].lower()
    anciens_motifs = ("bottes", "chaussures", "tabi", "mercure", "symbiote", "zphyr")
    assert not any(m in name for m in anciens_motifs), \
        "si ce test casse, l'ancienne heuristique aurait pu marcher — revoir le correctif"


def test_un_objet_non_bottes_n_est_pas_detecte(items):
    assert 3089 not in _boots_ids(items)  # Coiffe de Rabadon


def test_le_pool_de_candidats_legendaires_exclut_les_bottes():
    """
    Les bottes ont un slot dédié (plan.boots) : si elles restent dans
    cache.valid_items, elles occupent EN PLUS un slot légendaire.
    """
    from services.image_cache import ImageCache

    cache = ImageCache()
    candidats = [
        n for n in sorted(cache.valid_items)
        if not cache.is_boots(cache.get_item_id_by_name(n))
    ]
    assert candidats, "pool de candidats vide"
    assert not any(cache.is_boots(cache.get_item_id_by_name(n)) for n in candidats)
    # Zephyr est un légendaire (tag NonbootsMovement), il doit rester candidat.
    assert not cache.is_boots(3172)
