"""
Scoreur de draft : composantes vivantes, pénalités de composition, bans.

Symptôme rapporté : « ça me propose quasiment à chaque fois les mêmes champions,
pareil pour les bans conseillés ». Trois causes, toutes structurelles.

  · S1 « meta » était une constante. patch_stats.json ne contient AUCUNE clé
    « meta » ; la fonction renvoyait 0.5 pour tout le monde alors qu'elle pèse
    0.40 en premier pick et 0.50 en blind. Aucune force de champion n'entrait
    dans le calcul, seule la composition départageait.

  · _calculer_penalites renvoyait 0.0. draft_config.json spécifie pourtant
    redondance, équilibre des dégâts et scaling, avec leurs constantes et leurs
    commentaires : rien n'était appliqué. Rien n'empêchait d'entasser une
    cinquième source du même axe ni de composer une équipe entièrement AD.

  · Les bans ne dépendaient d'aucun contexte. Sans champion survolé, _counter_me
    a une confiance nulle et le score se réduisait à banrate × pickrate, valeur
    statique : le même classement à chaque partie.

Les importances d'axes ont par ailleurs été recalibrées sur mesure (voir
scripts/calibrate_draft_config.py) : sur 14 axes testés, 2 seulement survivent
à la correction de Bonferroni.
"""
import json
from pathlib import Path

import pytest

from ai import champion_scorer as cs
from ai.champion_scorer import ChampionScorer, ScorerDraftState

ROOT = Path(__file__).resolve().parent.parent
ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]


@pytest.fixture(scope="module")
def scorer():
    return ChampionScorer(ROOT / "data")


def _draft(role, allies=(), enemies=(), **kw):
    pris = {a["id"] for a in allies} | {e["id"] for e in enemies}
    return ScorerDraftState(
        my_role=role, pick_slot=kw.pop("pick_slot", 3), mode="draft",
        available=[c for c in cs.by_role[role] if c not in pris],
        allies=list(allies), enemies=list(enemies), bans=[],
        rank="PLATINUM", **kw,
    )


# ------------------------------------------------------------ S1 meta

def test_les_mesures_de_draft_sont_chargees(scorer):
    assert len(scorer.mesures.get("meta", {})) > 200


def test_la_force_du_champion_n_est_plus_constante(scorer):
    """Le coeur du symptôme : S1 valait 0.5 pour tous les candidats."""
    d = _draft("MID")
    scores = {
        cid: scorer._score_meta(cs.by_role["MID"][cid], d)
        for cid in list(cs.by_role["MID"])[:25]
    }
    assert len(set(round(v, 4) for v in scores.values())) > 5


@pytest.mark.parametrize("poste", ["UTILITY", "BOTTOM", "MIDDLE"])
def test_les_graphies_de_poste_de_riot_trouvent_les_mesures(scorer, poste):
    """
    La Live Client API dit UTILITY et BOTTOM là où les mesures disent SUPPORT
    et ADC. Sans alias, S1 retombait sur 0.5 — la constante qu'on vient de
    supprimer — pour deux postes sur cinq.
    """
    equivalent = {"UTILITY": "SUPPORT", "BOTTOM": "ADC", "MIDDLE": "MID"}[poste]
    cid = next(iter(cs.by_role[equivalent]))
    ch = cs.by_role[equivalent][cid]
    assert (scorer._score_meta(ch, _draft(equivalent, )) ==
            scorer._score_meta(ch, ScorerDraftState(
                my_role=poste, pick_slot=3, mode="draft", available=[],
                allies=[], enemies=[], bans=[], rank="PLATINUM")))


def test_la_force_mesuree_reste_dans_son_echelle(scorer):
    """
    L'étendue brute va de 32 % à 62 %, mais l'écart-type VRAI entre champions
    n'est que de 2.2 points : le reste est du bruit d'échantillonnage. La force
    normalisée doit rester bornée plutôt que refléter cette étendue.
    """
    for cid in list(cs.by_id)[:40]:
        f = scorer._force_mesuree(cid, cs.by_id[cid].get("roles", []))
        assert -1.0 <= f <= 1.0


# ------------------------------------------------------- pénalités

def test_les_penalites_ne_sont_plus_nulles(scorer):
    d = _draft("MID", allies=[{"id": "Ornn", "role": "TOP"}, {"id": "Vi", "role": "JUNGLE"}],
               enemies=[{"id": "Darius", "role": "TOP"}])
    valeurs = [r.sub_scores["pen"] for r in scorer.recommend(d)]
    assert valeurs and max(valeurs) > 0.0


def test_une_equipe_monochrome_ad_est_penalisee(scorer):
    """
    Mesuré : les équipes sous 40 % d'AP gagnent 48.5 %, contre ~50.5 % au-dessus.
    C'est le seul effet net de l'équilibre AD/AP dans les données.
    """
    allies_ad = [{"id": c, "role": r} for c, r in
                 (("Darius", "TOP"), ("Vi", "JUNGLE"), ("Jinx", "ADC"))]
    d = _draft("MID", allies=allies_ad)
    attrs = [cs.get_champion(a["id"]) for a in allies_ad]

    ad_pur = max(cs.by_role["MID"].values(),
                 key=lambda c: c.get("damage_mix", {}).get("ad", 0))
    ap_pur = max(cs.by_role["MID"].values(),
                 key=lambda c: c.get("damage_mix", {}).get("ap", 0))
    pen_ad = scorer._calculer_penalites(ad_pur, d, attrs, [])
    pen_ap = scorer._calculer_penalites(ap_pur, d, attrs, [])
    assert pen_ad > pen_ap


def test_la_redondance_penalise_un_axe_deja_sature(scorer):
    seuils = cs.config["team_thresholds"]
    axe = "frontline"
    cible = seuils[axe]["target"]
    sature = [{"axes": {axe: cible}, "damage_mix": {"ad": 0.5},
               "power_curve": {"late": 0.5}} for _ in range(3)]
    vide = [{"axes": {axe: 0.0}, "damage_mix": {"ad": 0.5},
             "power_curve": {"late": 0.5}} for _ in range(3)]
    cand = {"axes": {axe: 1.0}, "damage_mix": {"ad": 0.5}, "power_curve": {"late": 0.5}}
    d = _draft("TOP")
    assert (scorer._calculer_penalites(cand, d, sature, []) >
            scorer._calculer_penalites(cand, d, vide, []))


# ------------------------------------------------------------- bans

def test_les_bans_dependent_du_poste_joue(scorer):
    """
    Sans champion survolé, le score de ban se réduisait à une valeur statique et
    renvoyait les trois mêmes noms quel que soit le poste. Le poste, lui, est
    toujours connu.
    """
    listes = {
        role: [b.champion_id for b in scorer.recommend_ban(_draft(role), 3)]
        for role in ROLES
    }
    distincts = {tuple(v) for v in listes.values()}
    assert len(distincts) == len(ROLES), f"listes identiques : {listes}"


def test_un_ban_conseille_joue_la_voie_concernee(scorer):
    """Bannir un support quand on joue top n'aide pas la voie."""
    for role in ROLES:
        for b in scorer.recommend_ban(_draft(role), 3):
            assert role in cs.get_champion(b.champion_id).get("roles", []), \
                f"{b.champion_id} conseille en {role} sans y jouer"


def test_le_taux_de_ban_n_est_pas_impute_a_un_poste_marginal(scorer):
    """
    Riot ne bannit pas « Sylas ADC » : le taux de ban est global et recopié sur
    les cinq lignes du champion. L'additionner au pickrate d'un poste marginal
    donnait à Sylas une présence de 0.16 en ADC, où il est joué 0.2 % du temps.
    """
    assert scorer._meta_threat("Sylas", "MID") > scorer._meta_threat("Sylas", "ADC")


# ------------------------------------------------- calibration des axes

def test_les_importances_viennent_des_mesures():
    config = json.loads((ROOT / "data" / "draft_config.json").read_text(encoding="utf-8"))
    assert "team_thresholds_source" in config
    seuils = config["team_thresholds"]
    mesures = [v for v in seuils.values() if "verdict" in v]
    assert len(mesures) == len(seuils), "des axes n'ont pas été calibrés"


def test_les_axes_mesures_pesent_plus_que_les_non_concluants():
    """
    Sur 14 axes testés simultanément, seuls sustain (+5.0 %, z=3.15) et
    dive_resistance (+4.7 %, z=2.99) survivent à la correction de Bonferroni.
    Les axes que la config plaçait en tête — frontline et engage à importance
    1.00, hard_cc à 0.80 — ne sont pas concluants à ce volume.
    """
    seuils = json.loads(
        (ROOT / "data" / "draft_config.json").read_text(encoding="utf-8")
    )["team_thresholds"]
    mesures = [v["importance"] for v in seuils.values() if v.get("verdict") == "mesure"]
    autres = [v["importance"] for v in seuils.values() if v.get("verdict") != "mesure"]
    assert mesures and autres
    assert min(mesures) > max(autres)


def test_les_axes_non_concluants_ne_sont_pas_annules():
    """
    À ce volume un effet réel allant jusqu'à 3.5 points reste indétectable :
    les mettre à zéro affirmerait ce que la mesure n'établit pas.
    """
    seuils = json.loads(
        (ROOT / "data" / "draft_config.json").read_text(encoding="utf-8")
    )["team_thresholds"]
    for axe, cfg in seuils.items():
        assert cfg["importance"] > 0.0, f"{axe} annulé sans preuve d'absence d'effet"
