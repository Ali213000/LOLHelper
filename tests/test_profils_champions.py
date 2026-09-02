"""
Profils d'affinité champion/objet : résolution des noms et couverture.

Trois défauts signalés en partie réelle, tous ramenés à la même cause — des
identifiants Riot confrontés à des noms d'affichage.

  · Kai'Sa recevait un Couperet noir. Sa surcharge (on_hit vrai, crit faux)
    était cherchée sous « Kai'Sa » alors que le fichier l'indexe sous
    « Kaisa » : elle héritait donc du défaut ADC à coups critiques, qui
    pénalise les objets on-hit à 0.45 — soit exactement ses objets de base.

  · 15 champions sur 173 n'avaient aucun profil, donc aucun filtrage d'objets.

  · Un Pantheon support recevait des objets de combattant : le poste n'était
    jamais transmis à analyze(), et l'objet de quête de support n'avait aucun
    emplacement alors qu'il figure dans 88 % de ses parties.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def affinite():
    from data.champion_affinity import ChampionAffinity

    return ChampionAffinity(
        str(ROOT / "data" / "champion_item_profiles.json"), str(ROOT / "data")
    )


@pytest.fixture(scope="module")
def champions():
    chemin = ROOT / "assets" / "champion_data.json"
    if not chemin.exists():
        pytest.skip("champion_data.json absent")
    return [v["name"] for v in json.loads(chemin.read_text(encoding="utf-8"))["data"].values()]


# ------------------------------------------------------- couverture

def test_tous_les_champions_ont_un_profil(affinite, champions):
    """Sans profil, un champion tourne SANS filtrage d'objets."""
    sans = [c for c in champions if not (affinite.profile(c).get("affinity") or {})]
    assert not sans, f"champions sans profil : {sans}"


def test_les_nouveaux_champions_heritent_de_leurs_tags(affinite):
    """
    Le fichier annonce que « les nouveaux champions fonctionnent sans
    intervention ». Un champion absent des tables doit donc recevoir un
    archétype déduit de ses tags Data Dragon.
    """
    for champ in ("Aurora", "Yunara", "Vex"):
        prof = affinite.profile(champ)
        assert prof.get("archetype"), f"{champ} sans archétype"
        assert prof.get("affinity"), f"{champ} sans affinité"


# ------------------------------------------- résolution des surcharges

def test_toutes_les_surcharges_declarees_sont_appliquees(affinite, champions):
    declarees = json.loads(
        (ROOT / "data" / "champion_item_profiles.json").read_text(encoding="utf-8")
    )["champion_overrides"]
    appliquees = sum(1 for c in champions if affinite.profile(c).get("source") == "override")
    assert appliquees == len(declarees), (
        f"{appliquees} surcharges appliquées sur {len(declarees)} déclarées — "
        "la résolution par nom d'affichage en ignore une partie"
    )


@pytest.mark.parametrize("champion,attendu", [
    ("Kai'Sa", True), ("Vayne", True), ("Kog'Maw", True),
    ("Master Yi", True), ("Bel'Veth", True),
])
def test_les_champions_on_hit_sont_reconnus(affinite, champion, attendu):
    """
    Le drapeau décide si les objets à effet d'impact sont valorisés ou pénalisés
    à 0.45. Se tromper inverse tout le classement d'un ADC on-hit.
    """
    assert affinite.profile(champion)["flags"].get("on_hit") is attendu


def test_kaisa_n_est_pas_traitee_comme_une_adc_a_crit(affinite):
    prof = affinite.profile("Kai'Sa")
    assert prof["flags"].get("crit_viable") is False
    assert prof["affinity"].get("on_hit", 0) > prof["affinity"].get("crit", 1)


# --------------------------------------------------- poste et support

def test_le_poste_atteint_le_moteur_de_score():
    """
    plan_with_confidence ne transmettait pas my_position à analyze() : toute la
    branche support était morte en production.
    """
    import inspect
    from services.stat_analyzer import StatAnalyzer

    sig = inspect.signature(StatAnalyzer.plan_with_confidence)
    assert "my_position" in sig.parameters
    src = inspect.getsource(StatAnalyzer.plan_with_confidence)
    assert "my_position=my_position" in src


@pytest.mark.parametrize("champion,attendu", [
    ("Pantheon", 3877),      # AD → Mélodie du sang, 88 % de ses parties
    ("Soraka", 3870),        # enchanteresse qui soigne → Rêve éveillé
    ("Leona", 3876),         # engage → Traîneau du solstice
    ("Brand", 3871),         # mage → Pique de Zaz'Zak
])
def test_l_objet_de_support_suit_le_profil(champion, attendu):
    from ai.coaching_engine import CoachingEngine
    from ai.llm_client import LLMClient

    moteur = CoachingEngine(llm_client=LLMClient(provider="gemini"))
    assert moteur._objet_de_support(champion) == attendu


def test_occuper_le_poste_de_support_ne_fait_pas_un_enchanteur(affinite):
    """
    Basculer aveuglément la classe faisait conseiller à Pantheon un objet joué
    dans 10 % de ses parties pour 33 % de victoires.
    """
    pantheon = affinite.profile("Pantheon")
    soraka = affinite.profile("Soraka")
    assert (pantheon.get("affinity") or {}).get("heal_shield_power", 0) == 0
    assert (soraka.get("affinity") or {}).get("heal_shield_power", 0) > 0
