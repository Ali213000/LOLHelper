"""
core/event_bus.py — Lightweight publish-subscribe event bus.

Uses Python's threading.Event and callback registration pattern so services
running in background threads can safely signal the UI thread via queued calls.
No Qt dependency here — stays framework-agnostic so tests can run headlessly.
"""
import threading
import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger(__name__)


class EventBus:
    """
    Simple thread-safe pub/sub bus.

    Events are defined as string constants on this class.
    Subscribers register a callable; publishers call emit().
    Callbacks are invoked in the publisher's thread, so UI subscribers
    must marshal to the main thread themselves (e.g. via CTk's after()).
    """

    # -----------------------------------------------------------------------
    # Event name constants
    # -----------------------------------------------------------------------

    # Connection events
    LCU_CONNECTED         = "lcu_connected"
    LCU_DISCONNECTED      = "lcu_disconnected"
    LIVE_CLIENT_CONNECTED = "live_client_connected"
    LIVE_CLIENT_LOST      = "live_client_lost"

    # Game-phase transitions
    CHAMP_SELECT_STARTED  = "champ_select_started"
    CHAMP_SELECT_ENDED    = "champ_select_ended"
    GAME_STARTED          = "game_started"
    GAME_ENDED            = "game_ended"

    # Data-ready events (carry payload dicts)
    CHAMP_SELECT_UPDATED  = "champ_select_updated"   # payload: ChampSelectState
    LIVE_CLIENT_UPDATED   = "live_client_updated"    # payload: InGameState
    OCR_SCAN_COMPLETE     = "ocr_scan_complete"      # payload: list[PlayerScoreEntry]

    # AI advice ready
    CHAMP_ADVICE_READY       = "champ_advice_ready"      # payload: str
    CHAMP_SUGGESTIONS_READY  = "champ_suggestions_ready" # payload: {"suggestions": list[str], "reasons": list[str]}
    CHAMP_BAN_SUGGESTIONS_READY = "champ_ban_suggestions_ready" # payload: {"suggestions": list[str], "reasons": list[str]}
    FORCE_ITEM_REFRESH       = "force_item_refresh"      # manual user refresh
    ITEM_ADVICE_READY        = "item_advice_ready"        # payload: dict{advice, champion}

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event: str, callback: Callable[..., Any]) -> None:
        """Register *callback* to be called whenever *event* is emitted."""
        with self._lock:
            self._subscribers[event].append(callback)
        logger.debug("EventBus: %s subscribed to '%s'", callback.__qualname__, event)

    def unsubscribe(self, event: str, callback: Callable[..., Any]) -> None:
        """Remove *callback* from *event*'s subscriber list (no-op if not found)."""
        with self._lock:
            try:
                self._subscribers[event].remove(callback)
            except ValueError:
                pass

    def emit(self, event: str, payload: Any = None) -> None:
        """
        Invoke all subscribers for *event* with *payload*.
        Exceptions inside callbacks are caught and logged so one bad subscriber
        cannot break others.
        """
        with self._lock:
            callbacks = list(self._subscribers.get(event, []))

        for cb in callbacks:
            try:
                if payload is not None:
                    cb(payload)
                else:
                    cb()
            except Exception:
                logger.exception(
                    "EventBus: exception in subscriber '%s' for event '%s'",
                    cb.__qualname__, event
                )

    def clear(self) -> None:
        """Remove all subscribers (useful for testing)."""
        with self._lock:
            self._subscribers.clear()


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------
bus = EventBus()
