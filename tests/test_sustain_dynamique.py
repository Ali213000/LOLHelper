"""
Modèle de soin dynamique — mesuré sur 75 900 participants.

Le poids d'anti-soin d'un adversaire n'est plus une constante par champion :
il dépend de son build et de l'état de la partie. Un champion sans soin dans
son kit devient une cible par son seul équipement.
"""
import json
from pathlib import Path

import pytest

from data import sustain

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def modele():
    chemin = ROOT / "data" / "sustain_model.json"
    if not chemin.exists():
        pytest.skip("modèle absent — lancer scripts/build_sustain_model.py")
    return json.loads(chemin.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def objet_id(modele):
    par_nom = {v["nom"]: int(k) for k, v in modele["objets"].items()}

    def chercher(nom):
        if nom not in par_nom:
            pytest.skip(f"{nom} absent du modèle")
        return par_nom[nom]

    return chercher


# --------------------------------------------------------- intégrité

def test_le_modele_est_charge():
    assert sustain.disponible()


def test_les_objets_sont_indexes_par_identifiant(modele):
    """Les noms dérivent à chaque patch — trois listes s'y sont déjà perdues."""
    for cle in modele["objets"]:
        assert cle.isdigit(), f"clé {cle!r} : les objets doivent être indexés par ID"


def test_les_champions_portent_leur_poste_et_leur_effectif(modele):
    for nom, v in modele["champions"].items():
        assert v["poste"] in modele["references_par_poste"]
        assert v["n"] >= 20, f"{nom} mesuré sur trop peu de parties"


# ------------------------------------------------- effet du build

def test_un_objet_de_soin_rend_un_champion_ciblable(objet_id):
    """Le cas signalé : n'importe qui devient cible avec Soif-de-sang."""
    sds = objet_id("Soif-de-sang")
    assert sustain.poids("Zed", []) == 0.0
    assert sustain.poids("Zed", [sds]) > 0.30


def test_les_objets_se_cumulent(objet_id):
    sds, bork = objet_id("Soif-de-sang"), objet_id("Lame du roi déchu")
    un = sustain.poids("Samira", [sds])
    deux = sustain.poids("Samira", [sds, bork])
    assert deux > un > sustain.poids("Samira", [])


def test_un_objet_sans_soin_ne_compte_pas(modele):
    """
    Force de la nature, Jak'Sho et Terminus mesuraient +72 à +86 PV/min sans
    aucune mécanique de soin : ce sont des objets de tank achetés par des
    joueurs qui encaissent longtemps. Pure corrélation.
    """
    noms = {v["nom"] for v in modele["objets"].values()}
    for faux in ("Force de la nature", "Jak'Sho, le Protéiforme", "Terminus"):
        assert faux not in noms


def test_les_resurrections_sont_exclues(modele):
    """L'Ange gardien gonfle totalHeal mais l'Hémorragie ne l'empêche pas."""
    noms = {v["nom"] for v in modele["objets"].values()}
    assert "Ange gardien" not in noms
    assert "Gage de Sterak" not in noms      # bouclier, non réductible


# --------------------------------------------- effet de la domination

def test_la_domination_augmente_le_soin():
    faible = sustain.poids("Aatrox", [], kda=0.5, ratio_or=0.7)
    moyen = sustain.poids("Aatrox", [], kda=1.8, ratio_or=1.0)
    fort = sustain.poids("Aatrox", [], kda=5.0, ratio_or=1.4)
    assert faible < moyen < fort


def test_or_et_kda_comptent_tous_les_deux():
    """
    Mesuré : à or constant le KDA fait encore varier le soin de x1.6, et à KDA
    constant l'or ajoute x1.34. Les deux signaux sont indépendants.
    """
    ref = sustain.facteur_domination(1.0, 1.8)
    assert sustain.facteur_domination(1.0, 5.0) > ref      # KDA seul
    assert sustain.facteur_domination(1.4, 1.8) > ref      # or seul
    assert sustain.facteur_domination(1.4, 5.0) > sustain.facteur_domination(1.0, 5.0)


def test_le_poids_reste_borne():
    enorme = [i for i in range(1, 8000)]
    assert 0.0 <= sustain.poids("Soraka", enorme, kda=99, ratio_or=9) <= 1.0


# ------------------------------------------------------ cohérence

def test_les_trois_gros_soigneurs_declenchent_seuls():
    """Seuls Vladimir, Soraka et Zac dépassent 750 PV/min d'excès."""
    for champ in ("Vladimir", "Soraka", "Zac"):
        assert sustain.poids(champ, []) >= 0.80


def test_un_bruiser_nourri_avec_objet_de_soin_declenche(objet_id):
    """Le seuil spécialiste de 0.80 vise exactement ce cas."""
    hydre = objet_id("Hydre vorace")
    assert sustain.poids("Aatrox", [hydre], kda=5.0, ratio_or=1.4) >= 0.80


def test_le_detail_explique_le_poids(objet_id):
    d = sustain.detail("Aatrox", [objet_id("Hydre vorace")], kda=5.0, ratio_or=1.4)
    assert d["base_par_min"] > 0
    assert d["facteur_domination"] > 1.0
    assert d["objets"] and d["apport_objets"] > 0
    assert d["poids"] == sustain.poids("Aatrox", [objet_id("Hydre vorace")], 5.0, 1.4)
