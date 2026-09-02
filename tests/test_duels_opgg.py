"""
Duels de voie mesurés par OP.GG, et échelle commune des composantes de score.

S2 « lane » renvoyait 0.5 pour tout le monde : la clé « lane » de
patch_stats.json ne contient que les cinq noms de rôles, jamais de duels, alors
que la composante pèse jusqu'à 0.25 du score de pick.

Notre propre collecte ne peut pas combler ce trou. Sur 7 550 matchs on compte
8 611 appariements distincts à 4.4 parties chacun ; il faudrait un million de
matchs pour couvrir 84 % des duels réellement vécus à +/- 5 points. Les données
viennent donc du serveur MCP officiel d'OP.GG (pas de scraping, pas de clé) et
couvrent les 3 meilleurs et 3 pires contres par champion et par poste — 12.6 %
des duels vécus, mais ce sont les extrêmes, les seuls qui changent une décision.

Un second défaut, révélé en branchant le premier : les quatre composantes sont
combinées par des poids qui supposent des échelles comparables, or team_fit et
counter_comp occupent [0, 1] quand un taux de victoire lissé ne quitte pas
[0.46, 0.54]. Les composantes mesurées pesaient trente fois moins que leur poids
nominal. Elles passent désormais par une règle commune exprimée en points de
victoire.
"""
import json
from pathlib import Path

import pytest

from ai import champion_scorer as cs
from ai.champion_scorer import ChampionScorer, ScorerDraftState, _sur_echelle
from data import matchup_db

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def scorer():
    return ChampionScorer(ROOT / "data")


@pytest.fixture(scope="module")
def un_duel():
    """
    Un duel present dans le fichier, choisi dynamiquement.

    Les duels dependent du palier collecte : epingler « Lee Sin contre Aatrox »
    faisait echouer la suite des qu'on recollectait a un autre niveau, alors que
    le comportement teste n'a rien de specifique a cette paire.
    """
    entrees = json.loads(
        (ROOT / "data" / "opgg_matchups.json").read_text(encoding="utf-8"))["entrees"]
    for cle, v in entrees.items():
        champ, poste = cle.rsplit("|", 1)
        for d in v.get("duels", []):
            if d["parties"] >= 100:
                return champ, d["adversaire"], poste, d
    pytest.skip("aucun duel exploitable dans le fichier")


# ------------------------------------------------------ base de duels

def test_la_base_de_duels_est_disponible():
    assert matchup_db.disponible()


def test_la_source_est_citee():
    """
    Les données viennent d'un tiers : l'attribution doit voyager avec elles.
    """
    d = json.loads((ROOT / "data" / "opgg_matchups.json").read_text(encoding="utf-8"))
    assert "OP.GG" in d.get("source", "")
    assert d.get("attribution")


def test_un_duel_connu_est_retrouve(un_duel):
    champ, adversaire, poste, attendu = un_duel
    d = matchup_db.duel(champ, adversaire, poste)
    assert d and d["parties"] == attendu["parties"]
    assert 0.0 < d["victoire"] < 1.0


def test_le_duel_inverse_est_symetrique(un_duel):
    """
    OP.GG ne liste que six duels par champion : un appariement peut n'exister
    que d'un seul côté. Le lire à l'envers doit rendre le complément, pas rien.
    """
    champ, adversaire, poste, _ = un_duel
    direct = matchup_db.duel(champ, adversaire, poste)
    inverse = matchup_db.duel(adversaire, champ, poste)
    assert direct and inverse
    assert direct["parties"] == inverse["parties"]
    assert abs(direct["victoire"] + inverse["victoire"] - 1.0) < 0.01
    assert inverse["sens"] == "inverse"


def test_un_appariement_non_couvert_rend_none():
    """
    Inconnu n'est pas neutre. Rendre None laisse l'appelant retomber sur sa
    valeur neutre plutôt que d'inventer un avantage.
    """
    assert matchup_db.duel("Chalumeau", "Tournevis", "MID") is None


@pytest.mark.parametrize("poste", ["UTILITY", "BOTTOM", "MIDDLE"])
def test_les_graphies_de_poste_de_riot_sont_acceptees(poste):
    equivalent = {"UTILITY": "SUPPORT", "BOTTOM": "ADC", "MIDDLE": "MID"}[poste]
    entrees = json.loads(
        (ROOT / "data" / "opgg_matchups.json").read_text(encoding="utf-8"))["entrees"]
    cle = next(k for k in entrees if k.endswith(f"|{equivalent}"))
    champ = cle.rsplit("|", 1)[0]
    assert matchup_db.stats_poste(champ, poste) == matchup_db.stats_poste(champ, equivalent)


@pytest.fixture(scope="module")
def duel_favorable():
    """Un duel nettement favorable, choisi dans le fichier du palier collecte."""
    entrees = json.loads(
        (ROOT / "data" / "opgg_matchups.json").read_text(encoding="utf-8"))["entrees"]
    for cle, v in entrees.items():
        champ, poste = cle.rsplit("|", 1)
        if champ not in cs.by_role.get(poste, {}):
            continue
        for d in v.get("duels", []):
            adv = cs.norm_name(d["adversaire"])
            if d["victoire"] > 0.54 and d["parties"] >= 300 and adv in cs.by_role[poste]:
                return champ, adv, poste
    pytest.skip("aucun duel favorable assez fourni")


# ------------------------------------------------- échelle commune

def test_l_echelle_commune_ouvre_les_composantes_mesurees():
    """
    Un taux lissé vit dans [0.46, 0.54] ; sans remise à l'échelle il ne pouvait
    pas peser face à team_fit qui occupe [0, 1].
    """
    assert _sur_echelle(0.50) == pytest.approx(0.5)
    assert _sur_echelle(0.55) > 0.9
    assert _sur_echelle(0.45) < 0.1
    # On plafonne plutôt que d'extrapoler au-delà du domaine mesuré.
    assert _sur_echelle(0.90) == 1.0
    assert _sur_echelle(0.10) == 0.0


def test_l_echelle_preserve_la_taille_d_effet():
    """
    Normaliser chaque composante sur son propre écart-type corrigeait l'échelle
    mais écrasait l'information de taille d'effet : la force par champion (2.2
    points) aurait pesé autant qu'un duel (6 points). Mesuré, l'effet était une
    chute de diversité de 78 à 58 champions. Le dénominateur est donc commun.
    """
    ecart_meta = _sur_echelle(0.522) - _sur_echelle(0.478)      # +/- 1 sd de meta
    ecart_duel = _sur_echelle(0.56) - _sur_echelle(0.44)        # duel typique
    assert ecart_duel > ecart_meta * 2


def test_le_duel_deplace_le_classement(scorer, duel_favorable):
    """
    Avec S2 constante, un duel mesure ne changeait rien : le candidat qui gagne
    nettement son duel doit remonter au classement.
    """
    champion, adversaire, poste = duel_favorable
    def rang(avec_duel):
        reel = ChampionScorer._score_lane
        if not avec_duel:
            ChampionScorer._score_lane = lambda self, c, d: 0.5
        try:
            d = ScorerDraftState(
                my_role=poste, pick_slot=4, mode="draft",
                available=[c for c in cs.by_role[poste] if c != adversaire],
                allies=[], enemies=[{"id": adversaire, "role": poste}],
                bans=[], rank="", lane_opponent=adversaire)
            besoins = scorer._calculer_besoins([])
            menaces = scorer._calculer_menaces([cs.get_champion(adversaire)], [])
            w = scorer._get_weights(d)
            scores = []
            for cid, t in cs.by_role[poste].items():
                if cid == adversaire:
                    continue
                scores.append((cid,
                    w["meta"] * scorer._score_meta(t, d)
                    + w["lane"] * scorer._score_lane(t, d)
                    + w["team_fit"] * scorer._score_team_fit(t, besoins, [])
                    + w["counter_comp"] * scorer._score_counter_comp(t, menaces)
                    - scorer._calculer_penalites(t, d, [], [])))
            scores.sort(key=lambda x: -x[1])
            return {c: i for i, (c, _) in enumerate(scores)}
        finally:
            ChampionScorer._score_lane = reel

    avant, apres = rang(False), rang(True)
    assert apres[champion] < avant[champion], "duel favorable ignore"


def test_un_duel_defavorable_fait_reculer(scorer):
    """Un duel mesure sous 50 % doit tirer la composante vers le bas."""
    entrees = json.loads(
        (ROOT / "data" / "opgg_matchups.json").read_text(encoding="utf-8"))["entrees"]
    for cle, v in entrees.items():
        champ, poste = cle.rsplit("|", 1)
        if champ not in cs.by_role.get(poste, {}):
            continue
        for d in v.get("duels", []):
            if d["victoire"] < 0.47 and d["parties"] >= 300:
                etat = ScorerDraftState(
                    my_role=poste, pick_slot=4, mode="draft",
                    available=list(cs.by_role[poste]), allies=[],
                    enemies=[{"id": d["adversaire"], "role": poste}], bans=[],
                    rank="", lane_opponent=d["adversaire"])
                assert scorer._score_lane(cs.by_role[poste][champ], etat) < 0.5
                return
    pytest.skip("aucun duel defavorable assez fourni")


def test_sans_adversaire_connu_la_composante_est_neutre(scorer):
    etat = ScorerDraftState(
        my_role="MID", pick_slot=1, mode="draft", available=list(cs.by_role["MID"]),
        allies=[], enemies=[], bans=[], rank="PLATINUM", lane_opponent=None)
    assert scorer._score_lane(cs.by_role["MID"]["Ahri"], etat) == 0.5


# --------------------------------------- adversaire de voie deviné

def test_les_postes_adverses_sont_deduits():
    """
    Riot ne communique pas les postes de l'équipe adverse en sélection. Sans
    inférence, lane_opponent restait None et TOUTE la composante de duel était
    neutre en production — les données avaient beau être bonnes, elles
    n'atteignaient jamais le score.
    """
    postes = matchup_db.deviner_postes(
        ["Darius", "Lee Sin", "Ahri", "Jinx", "Thresh"])
    assert postes == {"TOP": "Darius", "JUNGLE": "Lee Sin", "MID": "Ahri",
                      "ADC": "Jinx", "SUPPORT": "Thresh"}


def test_l_affectation_est_globale_et_non_champion_par_champion():
    """
    Sylas est joué 45 % en jungle et 45 % en mid : son poste dépend de ce que
    jouent les autres. Un argmax indépendant placerait deux ennemis sur la même
    voie et en laisserait une vide.
    """
    sans_jungler = matchup_db.deviner_postes(
        ["Ornn", "Sylas", "Ahri", "Caitlyn", "Lulu"])
    avec_jungler = matchup_db.deviner_postes(
        ["Gnar", "Viego", "Sylas", "Ezreal", "Nautilus"])
    assert sans_jungler["JUNGLE"] == "Sylas"
    assert avec_jungler["MID"] == "Sylas"
    for postes in (sans_jungler, avec_jungler):
        assert len(set(postes.values())) == 5, "un champion affecté deux fois"


def test_l_adversaire_de_voie_est_trouve():
    eq = ["Darius", "Lee Sin", "Ahri", "Jinx", "Thresh"]
    assert matchup_db.adversaire_de_voie(eq, "MID") == "Ahri"
    assert matchup_db.adversaire_de_voie(eq, "UTILITY") == "Thresh"
    assert matchup_db.adversaire_de_voie([], "MID") is None


def test_le_moteur_transmet_l_adversaire_de_voie():
    """La déduction doit atteindre le scoreur, pas rester dans son module."""
    import inspect

    from ai.coaching_engine import CoachingEngine

    src = inspect.getsource(CoachingEngine)
    assert "deviner_postes" in src
    assert "lane_opponent=lane_opp" in src


def test_le_pool_est_un_signal_d_affichage():
    """
    Le pool du joueur est marqué à l'écran mais n'entre PAS dans le score :
    l'effet de la maîtrise n'est mesurable sur aucune de nos données, et le
    scoreur ne contient plus aucune pondération non vérifiée.
    """
    import inspect

    from ai import champion_scorer

    src = inspect.getsource(champion_scorer.ChampionScorer.recommend)
    assert "my_recent_picks" not in src, (
        "le pool influence le classement alors qu'aucune mesure ne le justifie")
