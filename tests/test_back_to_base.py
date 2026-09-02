"""
Détection du retour en base.

Bug corrigé : `_just_respawned` ne repassait à False qu'après un achat
>= 350 or. Tant qu'il restait à True, la branche principale de détection
(`not self._just_respawned`) était neutralisée pour le reste de la partie.
"""
from core.state_manager import InGameState, PlayerInGameData
from services.ingame_service import InGameService


class _EngineSpy:
    def __init__(self):
        self.calls = []

    def request_death_or_back_advice(self, **kwargs):
        self.calls.append(kwargs)


class _StateStub:
    def get(self):
        return type("S", (), {"champ_select": type("C", (), {"my_position": "ADC"})()})()


def _service():
    spy = _EngineSpy()
    svc = InGameService.__new__(InGameService)   # sans thread ni bus
    svc._engine = spy
    svc._state = _StateStub()
    svc._reset_tracking()
    return svc, spy


def _state(gold, dead=False):
    me = PlayerInGameData(champion_name="Jinx", team="ORDER", gold=gold,
                          is_dead=dead, is_local_player=True, level=10)
    return InGameState(in_game=True, local_player=me, all_players=[me])


def test_achat_simple_declenche_le_conseil():
    svc, spy = _service()
    svc._check_death_and_back(_state(1500))      # baseline
    svc._check_death_and_back(_state(200))       # -1300 or
    assert len(spy.calls) == 1
    assert spy.calls[0]["trigger"] == "retour en base"


def test_le_retour_reste_detecte_apres_une_mort_sans_achat():
    """Le scénario qui était cassé : mort → respawn sans acheter → back plus tard."""
    svc, spy = _service()
    svc._check_death_and_back(_state(300))
    svc._check_death_and_back(_state(300, dead=True))     # mort
    assert spy.calls and spy.calls[-1]["trigger"] == "mort"
    svc._check_death_and_back(_state(400))                # respawn, pas d'achat
    svc._check_death_and_back(_state(900))                # farm
    svc._check_death_and_back(_state(100))                # -800 : vrai retour en base
    assert spy.calls[-1]["trigger"] == "retour en base après mort"
    assert len([c for c in spy.calls if "retour" in c["trigger"]]) == 1


def test_le_libelle_repasse_a_la_normale_ensuite():
    svc, spy = _service()
    svc._check_death_and_back(_state(300))
    svc._check_death_and_back(_state(300, dead=True))
    svc._check_death_and_back(_state(1000))
    svc._check_death_and_back(_state(100))       # 1er back après mort
    svc._check_death_and_back(_state(1200))      # re-farm
    svc._check_death_and_back(_state(50))        # 2e back
    assert spy.calls[-1]["trigger"] == "retour en base"


def test_gagner_de_l_or_ne_declenche_rien():
    svc, spy = _service()
    svc._check_death_and_back(_state(100))
    svc._check_death_and_back(_state(2000))
    assert spy.calls == []


def test_petite_depense_sous_le_seuil_ignoree():
    svc, spy = _service()
    svc._check_death_and_back(_state(500))
    svc._check_death_and_back(_state(300))       # -200, sous le seuil de 350
    assert spy.calls == []
