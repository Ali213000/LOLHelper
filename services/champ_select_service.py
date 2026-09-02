"""
services/champ_select_service.py — Background thread that polls the LCU API
during champion select and triggers coaching advice when picks change.
"""
import logging
import threading
import time

from api.lcu_client import LCUClient
from ai.coaching_engine import CoachingEngine
from core.event_bus import bus, EventBus
from core.state_manager import StateManager, ChampSelectState

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0  # seconds between LCU polls during champ select


class ChampSelectService:
    """
    Polls the LCU /lol-champ-select/v1/session endpoint in a background thread.
    When the composition changes, fires the CoachingEngine for advice.
    """

    def __init__(
        self,
        lcu_client: LCUClient,
        coaching_engine: CoachingEngine,
        state_manager: StateManager,
    ) -> None:
        self._lcu       = lcu_client
        self._engine    = coaching_engine
        self._state     = state_manager
        self._running   = False
        self._thread: threading.Thread | None = None
        self._champ_map: dict[int, str] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._champ_map = self._lcu.load_champion_map()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ChampSelectService"
        )
        self._thread.start()
        logger.info("ChampSelectService started.")

    def stop(self, timeout: float = 6.0) -> None:
        """Demande l'arrêt et attend la fin de la boucle (au plus *timeout* s)."""
        self._running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("ChampSelectService: thread toujours actif après %.1fs.", timeout)
        self._thread = None
        logger.info("ChampSelectService stopped.")

    # -----------------------------------------------------------------------
    # Main polling loop
    # -----------------------------------------------------------------------

    def _loop(self) -> None:
        was_in_champ_select = False

        while self._running:
            try:
                if not self._lcu.check_still_alive():
                    if was_in_champ_select:
                        self._state.update_champ_select(ChampSelectState(in_champ_select=False))
                        bus.emit(EventBus.CHAMP_SELECT_ENDED)
                        was_in_champ_select = False
                    bus.emit(EventBus.LCU_DISCONNECTED)
                    time.sleep(5.0)
                    if self._lcu.connect():
                        bus.emit(EventBus.LCU_CONNECTED)
                        self._champ_map = self._lcu.load_champion_map()
                    continue

                new_cs_state = self._lcu.build_champ_select_state(self._champ_map)

                if new_cs_state.in_champ_select:
                    if not was_in_champ_select:
                        bus.emit(EventBus.CHAMP_SELECT_STARTED)
                        was_in_champ_select = True
                        # ── First entry: request suggestions immediately ──
                        self._engine.request_champion_suggestions(new_cs_state)

                    self._state.update_champ_select(new_cs_state)
                    bus.emit(EventBus.CHAMP_SELECT_UPDATED, new_cs_state)

                    # Les suggestions de champion/ban sont volontairement désactivées :
                    # l'app se concentre sur les objets. Voir CoachingEngine.

                else:
                    if was_in_champ_select:
                        logger.info("Champ select ended.")
                        self._state.update_champ_select(ChampSelectState(in_champ_select=False))
                        bus.emit(EventBus.CHAMP_SELECT_ENDED)
                        was_in_champ_select = False

            except Exception as exc:
                logger.exception("ChampSelectService loop error: %s", exc)

            time.sleep(_POLL_INTERVAL)
