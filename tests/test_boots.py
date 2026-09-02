"""
Choix des bottes.

La règle « 3 CC durs ou plus → Sandales de Mercure » ne se déclenchait presque
jamais : la liste de référence, recopiée à l'identique dans deux fichiers, ne
comptait que 31 champions et oubliait des porteurs de CC dur évidents
(Anivia, Braum, Nautilus, Thresh…).
"""
from data.hard_cc import HARD_CC_CHAMPIONS, count_hard_cc


def test_une_seule_liste_partagee():
    from ai.boot_optimizer import _CC_CHAMPIONS as depuis_boots
    from services.stat_analyzer import _CC_CHAMPIONS as depuis_analyzer

    assert depuis_boots is HARD_CC_CHAMPIONS
    assert depuis_analyzer is HARD_CC_CHAMPIONS


def test_les_oublis_constates_sont_couverts():
    for champ in ("Anivia", "Braum", "Nautilus", "Thresh", "Malzahar", "Lux"):
        assert champ in HARD_CC_CHAMPIONS, f"{champ} a du CC dur et doit compter"


def test_les_ralentisseurs_purs_ne_comptent_pas():
    """La tenacité ne réduit pas les ralentissements : ils n'entrent pas au compte."""
    for champ in ("Darius", "Ezreal", "Nasus", "Zed"):
        assert champ not in HARD_CC_CHAMPIONS


def test_le_cas_reel_du_joueur():
    """Fiddlesticks + Anivia + Braum = 3 CC durs → seuil Mercure atteint."""
    equipe = ["Darius", "Fiddlesticks", "Anivia", "Ezreal", "Braum"]
    assert count_hard_cc(equipe) == 3


def test_les_noms_sont_en_anglais():
    """Le LCU et la Live Client API renvoient les noms anglais."""
    assert "Fiddlesticks" in HARD_CC_CHAMPIONS
    assert "Épouvantail" not in HARD_CC_CHAMPIONS
