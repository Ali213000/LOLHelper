"""
Garde-fou sur le locale des assets Data Dragon.

services/stat_analyzer.py indexe toutes ses tables sur les noms d'objets
FRANÇAIS, alors que les champions sont manipulés en ANGLAIS (LCU + scorer).
Un téléchargement dans la mauvaise langue ne lève aucune erreur : il vide
simplement toutes les correspondances. Ce test verrouille l'invariant.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    path = ROOT / "assets" / name
    if not path.exists():
        pytest.skip(f"{name} absent — lancer download_assets.py")
    return json.loads(path.read_text(encoding="utf-8"))["data"]


def test_les_objets_sont_en_francais():
    names = {v.get("name", "") for v in _load("item_data.json").values()}
    for expected in ("Coiffe de Rabadon", "Couperet noir", "Bottes"):
        assert expected in names, f"{expected!r} absent : assets/item_data.json n'est pas en fr_FR"
    assert "Rabadon's Deathcap" not in names, "item_data.json est en anglais — stat_analyzer sera muet"


def test_les_champions_sont_en_anglais():
    names = {v.get("name", "") for v in _load("champion_data.json").values()}
    assert "Miss Fortune" in names
    assert names & {"Cho'Gath", "Kai'Sa"}, "champion_data.json ne ressemble pas à du en_US"


def test_le_telechargeur_respecte_ces_locales():
    src = (ROOT / "download_assets.py").read_text(encoding="utf-8")
    assert 'CHAMPION_LOCALE = "en_US"' in src
    assert 'ITEM_LOCALE = "fr_FR"' in src
