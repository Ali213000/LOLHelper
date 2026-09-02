"""
Détection des menaces adverses : boucliers, armure, résistance magique.

La Live Client API n'expose `championStats` (armure, RM) QUE pour le joueur
local. Pour les adversaires on ne dispose que du champion, du niveau et des
identifiants d'objets : les résistances doivent être reconstituées.

Trois défauts corrigés ici :
  1. _estimate_enemy_stats recevait des IDENTIFIANTS d'objets mais les cherchait
     dans un dictionnaire indexé par nom — l'équipement adverse n'était donc
     jamais compté. Un Malphite niveau 16 avec Randuin, Thornmail et Cœur gelé
     ressortait à 88 d'armure au lieu de 337.
  2. La pénétration d'armure comptait les champions taggés Tank/Fighter au lieu
     de lire leur armure, alors que le pendant magique lisait déjà la RM.
  3. Les boucliers utilisaient un comptage brut à seuil 2 : une Lulu, si
     spécialisée soit-elle, ne déclenchait jamais le Crochet de serpent.
"""
import pytest

from core.state_manager import PlayerInGameData
from services.stat_analyzer import StatAnalyzer, _SHIELD_CHAMPION_WEIGHTS

RANDUIN, THORNMAIL, COEUR_GELE = 3143, 3075, 3110
SANDALES, RAYONNEMENT = 3111, 6664


@pytest.fixture(scope="module")
def analyzer():
    a = StatAnalyzer()
    a._ensure_loaded()
    return a


def _eq(specs):
    return [
        PlayerInGameData(champion_name=n, team="CHAOS", level=lv, items=list(it))
        for n, lv, it in specs
    ]


# ------------------------------------------ reconstitution des résistances

def test_les_objets_adverses_sont_comptes(analyzer):
    """Le bug central : les identifiants n'étaient pas résolus."""
    _, nu, _ = analyzer._estimate_enemy_stats("Malphite", 16, [])
    _, equipe, _ = analyzer._estimate_enemy_stats(
        "Malphite", 16, [RANDUIN, THORNMAIL, COEUR_GELE]
    )
    assert equipe > nu + 200, (
        f"trois objets d'armure devraient ajouter 225 : {nu:.0f} -> {equipe:.0f}"
    )


def test_les_noms_d_objets_marchent_aussi(analyzer):
    """Tolérance : certains appels internes passent des noms."""
    _, par_id, _ = analyzer._estimate_enemy_stats("Malphite", 16, [THORNMAIL])
    _, par_nom, _ = analyzer._estimate_enemy_stats("Malphite", 16, ["Cotte épineuse"])
    assert par_id == pytest.approx(par_nom)


def test_les_statistiques_de_base_sont_celles_du_champion(analyzer):
    """Plus de moyenne de classe : Data Dragon donne les valeurs exactes."""
    _, armure_tank, _ = analyzer._estimate_enemy_stats("Malphite", 16, [])
    _, armure_mage, _ = analyzer._estimate_enemy_stats("Anivia", 16, [])
    assert armure_tank > armure_mage + 20


def test_la_croissance_suit_la_formule_du_jeu(analyzer):
    """stat = base + croissance x (n-1) x (0.7025 + 0.0175 x (n-1))."""
    st = analyzer._champ_stats["Malphite"]
    attendu = analyzer._croissance(st["armor"], st["armorperlevel"], 16)
    _, obtenu, _ = analyzer._estimate_enemy_stats("Malphite", 16, [])
    assert obtenu == pytest.approx(attendu)
    # Niveau 1 : aucune croissance appliquée
    assert analyzer._croissance(40, 5, 1) == 40


# ------------------------------------------------ pénétration d'armure

def test_un_seul_tank_tres_epais_suffit(analyzer):
    """L'ancienne règle exigeait DEUX champions taggés tank."""
    sp = [("Malphite", 16, [RANDUIN, THORNMAIL, COEUR_GELE]), ("Ezreal", 14, []),
          ("Lulu", 13, []), ("Anivia", 14, []), ("Zed", 14, [])]
    t = analyzer._check_triggers(_eq(sp), "Marksman", 0.5, "AD", [], my_champion_name="Jinx")
    assert t["enemy_max_armor"] > 300
    assert t["need_armor_pen"] is True


def test_une_equipe_fragile_ne_declenche_pas(analyzer):
    sp = [(n, 12, []) for n in ("Ezreal", "Lulu", "Anivia", "Zed", "Ahri")]
    t = analyzer._check_triggers(_eq(sp), "Marksman", 0.5, "AD", [], my_champion_name="Jinx")
    assert t["need_armor_pen"] is False


# --------------------------------------------- pénétration magique

@pytest.mark.parametrize("champion,classe", [
    ("Anivia", "Mage"),
    ("Diana", "Assassin"),        # assassin AP
    ("Mordekaiser", "Fighter"),   # bruiser AP
])
def test_tout_profil_ap_peut_avoir_de_la_penetration_magique(analyzer, champion, classe):
    """La règle était gated sur player_class == 'Mage'."""
    sp = [("Malphite", 16, [SANDALES, RAYONNEMENT, COEUR_GELE]), ("Ornn", 16, [SANDALES]),
          ("Braum", 15, [SANDALES]), ("Ezreal", 14, []), ("Lulu", 13, [])]
    t = analyzer._check_triggers(_eq(sp), classe, 0.5, "AP", [], my_champion_name=champion)
    assert t["need_magic_pen"] is True


def test_un_profil_ad_pur_n_en_recoit_pas(analyzer):
    sp = [("Malphite", 16, [SANDALES, RAYONNEMENT, COEUR_GELE]), ("Ornn", 16, [SANDALES]),
          ("Braum", 15, [SANDALES]), ("Ezreal", 14, []), ("Lulu", 13, [])]
    t = analyzer._check_triggers(_eq(sp), "Marksman", 0.5, "AD", [], my_champion_name="Jinx")
    assert t["need_magic_pen"] is False


def test_sans_resistance_magique_en_face_rien_ne_se_declenche(analyzer):
    sp = [(n, 12, []) for n in ("Ezreal", "Lulu", "Zed", "Ahri", "Kaisa")]
    t = analyzer._check_triggers(_eq(sp), "Mage", 0.5, "AP", [], my_champion_name="Anivia")
    assert t["need_magic_pen"] is False


# ------------------------------------------------------------ boucliers

def test_une_enchanteresse_dediee_suffit(analyzer):
    """Le cas signalé : une Lulu qui protège beaucoup."""
    sp = [("Lulu", 13, []), ("Darius", 13, []), ("Ezreal", 13, []),
          ("Anivia", 13, []), ("Fiddlesticks", 13, [])]
    t = analyzer._check_triggers(_eq(sp), "Assassin", 0.5, "AD", [], my_champion_name="Zed")
    assert t["need_antishield"] is True
    assert "Lulu" in t["shield_source"]


def test_deux_boucliers_moyens_se_cumulent(analyzer):
    sp = [("Lulu", 13, []), ("Karma", 13, []), ("Darius", 13, []),
          ("Ezreal", 13, []), ("Anivia", 13, [])]
    t = analyzer._check_triggers(_eq(sp), "Assassin", 0.5, "AD", [], my_champion_name="Zed")
    assert t["shield_weight"] >= 1.5
    assert t["need_antishield"] is True


def test_un_bouclier_marginal_ne_suffit_pas(analyzer):
    """Le petit bouclier d'ultime de Kai'Sa ne vaut pas un Crochet de serpent."""
    sp = [("Kai'Sa", 13, []), ("Darius", 13, []), ("Anivia", 13, []),
          ("Lee Sin", 13, []), ("Nautilus", 13, [])]
    t = analyzer._check_triggers(_eq(sp), "Assassin", 0.5, "AD", [], my_champion_name="Zed")
    assert t["need_antishield"] is False


@pytest.mark.parametrize("noms,attendu", [
    (["Tahm Kench", "Shen"], True),      # 1.35 — deux vrais porteurs
    (["Lux", "Orianna"], True),          # 1.30 — pile au seuil
    (["Braum", "Riven"], False),         # 1.15 — boucliers secondaires
    (["Malphite", "Diana"], False),      # 0.85 — passives marginales
    (["Sett", "Sion"], False),           # 1.00
])
def test_le_seuil_separe_les_vrais_porteurs(analyzer, noms, attendu):
    """Calibré sur le jugement en partie : Tahm Kench + Shen doit passer."""
    sp = [(n, 13, []) for n in noms] + [(f, 13, []) for f in ("Darius", "Nasus", "Yorick")][:5 - len(noms)]
    t = analyzer._check_triggers(_eq(sp), "Assassin", 0.5, "AD", [], my_champion_name="Zed")
    assert t["need_antishield"] is attendu, (
        f"{' + '.join(noms)} : poids {t['shield_weight']}, attendu {attendu}"
    )


def test_aucun_bouclier_en_face(analyzer):
    sp = [(n, 13, []) for n in ("Darius", "Garen", "Nasus", "Zed", "Yorick")]
    t = analyzer._check_triggers(_eq(sp), "Assassin", 0.5, "AD", [], my_champion_name="Zed")
    assert t["shield_weight"] == 0.0
    assert t["need_antishield"] is False


@pytest.mark.parametrize("champion", ["Lulu", "Karma", "Janna", "Shen", "Tahm Kench"])
def test_les_boucliers_de_reference_sont_ponderes(champion):
    assert _SHIELD_CHAMPION_WEIGHTS.get(champion, 0) >= 0.55


def test_les_poids_sont_des_fractions():
    for nom, poids in _SHIELD_CHAMPION_WEIGHTS.items():
        assert 0 < poids <= 1.0, f"{nom} : poids hors ]0,1]"
