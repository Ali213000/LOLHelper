"""
services/ingame_service.py — Background thread for in-game tracking.

Responsibilities:
  1. Poll the Live Client Data API every 5 seconds.
  2. Detect death (isDead True) → trigger item advice.
  3. Detect back to base (gold drop after respawn) → trigger item advice.
  4. Trigger OCR scoreboard scans every 15 seconds OR when TAB is pressed.
  5. Emit events so the UI can update live stats.
"""
import logging
import threading
import time

from api.live_client import LiveClientAPI
from ai.coaching_engine import CoachingEngine
from core.event_bus import bus, EventBus
from core.state_manager import StateManager, InGameState

logger = logging.getLogger(__name__)

_LIVE_CLIENT_POLL_INTERVAL = 5.0   # seconds
_BACK_GOLD_THRESHOLD       = 350.0 # gold drop that signals a shop visit


class InGameService:
    """
    Monitors the active game via Live Client API and periodic OCR.
    Detects death and back-to-base events to trigger item advice.
    """

    def __init__(
        self,
        live_client: LiveClientAPI,
        coaching_engine: CoachingEngine,
        state_manager: StateManager,
    ) -> None:
        self._live   = live_client
        self._engine = coaching_engine
        self._state  = state_manager

        bus.subscribe(EventBus.FORCE_ITEM_REFRESH, self._on_force_refresh)

        self._running = False
        self._thread: threading.Thread | None = None

        self._last_ocr_time = 0.0

        # Death / back detection state
        self._prev_is_dead: bool  = False
        self._prev_gold:    float = 0.0
        self._just_respawned: bool = False   # True for one cycle after respawn

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="InGameService"
        )
        self._thread.start()
        logger.info("InGameService started.")

    def stop(self, timeout: float = 6.0) -> None:
        """Demande l'arrêt et attend la fin de la boucle (au plus *timeout* s)."""
        self._running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("InGameService: thread toujours actif après %.1fs.", timeout)
        self._thread = None
        logger.info("InGameService stopped.")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _my_position(self) -> str:
        """Read my_position from the champ select state (set during draft)."""
        try:
            return self._state.get().champ_select.my_position
        except Exception:
            return ""

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def _loop(self) -> None:
        was_in_game = False

        while self._running:
            try:
                game_state = self._live.fetch_game_state()

                # ---- Game ended or not started ----
                if game_state is None:
                    if was_in_game:
                        logger.info("Game ended (Live Client gone).")
                        bus.emit(EventBus.GAME_ENDED)
                        was_in_game = False
                        self._state.update_game_phase("NONE")
                        self._reset_tracking()
                    time.sleep(_LIVE_CLIENT_POLL_INTERVAL)
                    continue

                # ---- Game just started ----
                if not was_in_game:
                    logger.info("Game started.")
                    bus.emit(EventBus.GAME_STARTED)
                    was_in_game = True
                    self._reset_tracking()
                    
                    # Ask for starter items immediately
                    self._engine.request_death_or_back_advice(
                        game_state=game_state,
                        trigger="début de partie",
                        my_position=self._my_position(),
                    )

                self._state.update_in_game(game_state)
                bus.emit(EventBus.LIVE_CLIENT_UPDATED, game_state)

                # ---- Death / back detection ----
                self._check_death_and_back(game_state)

            except Exception as exc:
                logger.exception("InGameService loop error: %s", exc)

            time.sleep(_LIVE_CLIENT_POLL_INTERVAL)

    def _reset_tracking(self) -> None:
        self._prev_is_dead    = False
        self._prev_gold       = 0.0
        self._just_respawned  = False

    def _on_force_refresh(self) -> None:
        """User manually clicked the Refresh button in the UI."""
        game_state = self._state.get().in_game
        if game_state and game_state.local_player:
            logger.info("Manual item refresh requested by user.")
            
            # Find the most fed enemy to pass to the engine
            enemies = [p for p in game_state.all_players if p.team != game_state.local_player.team]
            fed_enemies = [p for p in enemies if p.is_fed]
            primary_threat = fed_enemies[0].champion_name if fed_enemies else ""
            
            self._engine.request_death_or_back_advice(
                game_state=game_state,
                trigger="manuel",
                my_position=self._my_position(),
                primary_threat_name=primary_threat,
                force=True
            )

    # -----------------------------------------------------------------------
    # Death / back detection
    # -----------------------------------------------------------------------

    def _check_death_and_back(self, game_state: InGameState) -> None:
        local = game_state.local_player
        if local is None:
            return

        curr_is_dead = local.is_dead
        curr_gold    = float(local.gold)

        # Find the most fed enemy to pass to the engine
        enemies = [p for p in game_state.all_players if p.team != local.team]
        fed_enemies = [p for p in enemies if p.is_fed]
        
        primary_threat_name = ""
        if fed_enemies:
            # Pick the most fed enemy by KDA or Kills
            most_fed = max(fed_enemies, key=lambda p: p.kda_ratio + p.kills)
            primary_threat_name = most_fed.champion_name

        # --- Detect death (False → True) ---
        if curr_is_dead and not self._prev_is_dead:
            logger.info("Player died — requesting death item advice.")
            self._engine.request_death_or_back_advice(
                game_state=game_state,
                trigger="mort",
                my_position=self._my_position(),
                primary_threat_name=primary_threat_name,
            )
            self._just_respawned = False

        # --- Detect respawn (True → False) ---
        if not curr_is_dead and self._prev_is_dead:
            logger.info("Player respawned — will detect back-to-base on gold drop.")
            self._just_respawned = True
            self._prev_gold = curr_gold   # reset baseline to gold at respawn

        # --- Detect back to base (chute de gold entre deux relevés) ---
        # Un seul chemin : _just_respawned ne conditionne plus la DÉTECTION,
        # seulement le libellé. Avant, il restait bloqué à True tant qu'aucun
        # achat >= seuil n'était vu, ce qui neutralisait la branche principale.
        if not curr_is_dead and not self._prev_is_dead and self._prev_gold > 0:
            gold_spent = self._prev_gold - curr_gold
            if gold_spent >= _BACK_GOLD_THRESHOLD:
                trigger = (
                    "retour en base après mort" if self._just_respawned else "retour en base"
                )
                logger.info(
                    "Player spent %.0f gold (%s) — requesting item advice.", gold_spent, trigger
                )
                self._engine.request_death_or_back_advice(
                    game_state=game_state,
                    trigger=trigger,
                    my_position=self._my_position(),
                    primary_threat_name=primary_threat_name,
                )
                self._just_respawned = False

        self._prev_is_dead = curr_is_dead
        self._prev_gold    = curr_gold
