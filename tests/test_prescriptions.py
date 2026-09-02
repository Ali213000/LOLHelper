"""
Prescriptions d'objets : clé de recherche et lisibilité du plan.

Bug constaté en partie réelle (Dr. Mundo TOP) : la table est indexée sur
l'identifiant Riot ("DrMundo") alors que la Live Client API renvoie le nom
d'affichage ("Dr. Mundo"). La prescription était donc introuvable, sans la
moindre erreur — l'app retombait sur le score mathématique, dont les
confiances basses masquaient ensuite tous les slots derrière des « ? ».
"""
import json
from pathlib import Path

import pytest

from ai.champion_scorer import norm_name

ROOT = Path(__file__).resolve().parent.parent
ENGINE_SRC = (ROOT / "ai" / "coaching_engine.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prescriptions():
    path = ROOT / "data" / "core_items_prescription.json"
    if not path.exists():
        pytest.skip("core_items_prescription.json absent")
    return json.loads(path.read_text(encoding="utf-8"))


# Champions dont le nom d'affichage diffère de l'identifiant Riot.
NOMS_PIEGES = [
    ("Dr. Mundo", "DrMundo"),
    ("Kai'Sa", "Kaisa"),
    ("Cho'Gath", "Chogath"),
    ("Lee Sin", "LeeSin"),
    ("Miss Fortune", "MissFortune"),
    ("Jarvan IV", "JarvanIV"),
    ("Wukong", "MonkeyKing"),
    ("Vel'Koz", "Velkoz"),
]


@pytest.mark.parametrize("affichage,riot_id", NOMS_PIEGES)
def test_normalisation_des_noms(affichage, riot_id):
    assert norm_name(affichage) == riot_id


def test_le_moteur_normalise_avant_de_chercher():
    assert "norm_name(local.champion_name)" in ENGINE_SRC, (
        "la clé de prescription doit être normalisée, sinon 'Dr. Mundo|TOP' "
        "ne trouvera jamais 'DrMundo|TOP'"
    )


def test_les_cles_de_la_table_sont_des_identifiants_riot(prescriptions):
    """Aucune clé ne doit contenir d'espace ni d'apostrophe."""
    for key in prescriptions:
        champ = key.split("|")[0]
        assert norm_name(champ) == champ, (
            f"clé {key!r} : forme non normalisée, elle sera introuvable"
        )


def test_mundo_est_bien_present(prescriptions):
    assert f"{norm_name('Dr. Mundo')}|TOP" in prescriptions


def test_le_premier_slot_n_est_jamais_masque():
    """Le prochain achat doit rester visible même à faible confiance."""
    assert "if i > 0 and conf < 0.45:" in ENGINE_SRC, (
        "le slot 1 doit échapper à l'état UNDETERMINED : c'est la seule "
        "information utile devant la boutique"
    )


def test_la_confiance_de_la_prescription_est_reportee():
    assert "slot.confidence = p_conf" in ENGINE_SRC


def test_les_doublons_de_prescription_sont_filtres():
    assert "prescrits = set(core_items)" in ENGINE_SRC, (
        "un objet prescrit déjà proposé plus loin par le score mathématique "
        "apparaissait deux fois dans la grille"
    )
