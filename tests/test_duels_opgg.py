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


def test_un_duel_connu_est_retrouve():
    d = matchup_db.duel("Lee Sin", "Aatrox", "JUNGLE")
    assert d and d["parties"] > 100
    assert 0.0 < d["victoire"] < 1.0


def test_le_duel_inverse_est_symetrique():
    """
    OP.GG ne liste que six duels par champion : un appariement peut n'exister
    que d'un seul côté. Le lire à l'envers doit rendre le complément, pas rien.
    """
    direct = matchup_db.duel("Lee Sin", "Aatrox", "JUNGLE")
    inverse = matchup_db.duel("Aatrox", "Lee Sin", "JUNGLE")
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


def test_le_duel_deplace_le_classement(scorer):
    """
    Avec S2 constante, un duel mesuré ne changeait rien. Lissandra contre Yasuo
    est mesurée à 56 % sur 3 126 parties : elle doit remonter.
    """
    def rang(avec_duel):
        reel = ChampionScorer._score_lane
        if not avec_duel:
            ChampionScorer._score_lane = lambda self, c, d: 0.5
        try:
            d = ScorerDraftState(
                my_role="MID", pick_slot=4, mode="draft",
                available=[c for c in cs.by_role["MID"] if c != "Yasuo"],
                allies=[], enemies=[{"id": "Yasuo", "role": "MID"}],
                bans=[], rank="PLATINUM", lane_opponent="Yasuo")
            besoins = scorer._calculer_besoins([])
            menaces = scorer._calculer_menaces([cs.get_champion("Yasuo")], [])
            w = scorer._get_weights(d)
            scores = []
            for cid, t in cs.by_role["MID"].items():
                if cid == "Yasuo":
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
    assert apres["Lissandra"] < avant["Lissandra"], "duel favorable ignore"


def test_un_duel_defavorable_fait_reculer(scorer):
    """Kai'Sa est mesuree a 46 % contre Jinx sur 18 498 parties."""
    d = matchup_db.duel("Kai'Sa", "Jinx", "ADC")
    assert d and d["victoire"] < 0.5
    etat = ScorerDraftState(
        my_role="ADC", pick_slot=4, mode="draft", available=list(cs.by_role["ADC"]),
        allies=[], enemies=[{"id": "Jinx", "role": "ADC"}], bans=[],
        rank="PLATINUM", lane_opponent="Jinx")
    assert scorer._score_lane(cs.by_role["ADC"]["Kaisa"], etat) < 0.5


def test_sans_adversaire_connu_la_composante_est_neutre(scorer):
    etat = ScorerDraftState(
        my_role="MID", pick_slot=1, mode="draft", available=list(cs.by_role["MID"]),
        allies=[], enemies=[], bans=[], rank="PLATINUM", lane_opponent=None)
    assert scorer._score_lane(cs.by_role["MID"]["Ahri"], etat) == 0.5
