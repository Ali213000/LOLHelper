"""
Identification du vis-à-vis de lane.

L'ancienne heuristique prenait l'ennemi le plus proche en NIVEAU, ce qui
désignait régulièrement le mauvais joueur alors que tout le plan d'objets
en dépend.
"""
from ai.coaching_engine import CoachingEngine
from api.live_client import normalize_position
from core.state_manager import PlayerInGameData

find = CoachingEngine._find_lane_opponent


def enemy(champ, position="", level=10):
    return PlayerInGameData(champion_name=champ, team="CHAOS", position=position, level=level)


ME = PlayerInGameData(champion_name="Jinx", team="ORDER", level=10, is_local_player=True)


def test_position_de_la_live_client_api_prioritaire():
    enemies = [
        enemy("Darius", "TOP", level=10),      # même niveau que moi : piégeait l'ancienne version
        enemy("Caitlyn", "ADC", level=13),
    ]
    assert find(enemies, "ADC", ME).champion_name == "Caitlyn"


def test_libelles_du_lcu_acceptes():
    enemies = [enemy("Darius", "TOP"), enemy("Lux", "SUPPORT")]
    # Le LCU fournit "Support", la Live Client API "UTILITY" : même résultat.
    assert find(enemies, "Support", ME).champion_name == "Lux"
    assert find(enemies, "utility", ME).champion_name == "Lux"


def test_repli_sur_la_table_de_roles_si_position_absente():
    enemies = [enemy("Darius", ""), enemy("Caitlyn", "")]
    found = find(enemies, "ADC", ME)
    assert found.champion_name == "Caitlyn", "la table de rôles du scorer doit départager"


def test_repli_final_sur_le_niveau():
    enemies = [enemy("Darius", "", level=18), enemy("Sett", "", level=10)]
    assert find(enemies, "", ME).champion_name == "Sett"


def test_aucun_ennemi():
    assert find([], "ADC", ME) is None


def test_normalisation_des_positions():
    assert normalize_position("MIDDLE") == "MID"
    assert normalize_position("Mid") == "MID"
    assert normalize_position("bottom") == "ADC"
    assert normalize_position("Support") == "SUPPORT"
    assert normalize_position("") == ""
    assert normalize_position("nawak") == ""


def test_la_position_du_joueur_sert_de_repli():
    """
    Si la position du champ select manque (app lancée en cours de partie,
    reconnexion), la Live Client API fournit la position du joueur lui-même.
    Cas réel : Mundo TOP face à Fiddlesticks TOP, Darius étant jungle.
    """
    moi = PlayerInGameData(
        champion_name="Dr. Mundo", team="ORDER", position="TOP",
        level=12, is_local_player=True,
    )
    enemies = [
        enemy("Fiddlesticks", "TOP", level=8),
        enemy("Darius", "JUNGLE", level=6),
        enemy("Anivia", "MID", level=8),
    ]
    # my_position vide : le repli doit lire moi.position
    assert find(enemies, "", moi).champion_name == "Fiddlesticks"


def test_le_jungler_n_est_pas_pris_pour_le_vis_a_vis():
    """L'ancienne heuristique par niveau désignait souvent le jungler."""
    moi = PlayerInGameData(
        champion_name="Dr. Mundo", team="ORDER", position="TOP",
        level=6, is_local_player=True,
    )
    enemies = [
        enemy("Fiddlesticks", "TOP", level=8),
        enemy("Darius", "JUNGLE", level=6),   # même niveau que moi : le piège
    ]
    assert find(enemies, "TOP", moi).champion_name == "Fiddlesticks"
