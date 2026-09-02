"""
main.py — LoL Coaching Assistant entry point.

Bootstrap sequence:
  1. Configure logging
  2. Initialise EasyOCR in background (GPU warm-up)
  3. Connect to LCU (non-fatal if client not running yet)
  4. Build the CustomTkinter main window
  5. Start background services (ChampSelect, InGame, TTS)
  6. Run the UI event loop
"""
import logging
import sys
import threading
import time

import customtkinter as ctk

# Silence httpx SSL warnings (LCU uses self-signed certs — expected)
import warnings
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

import config
from core.state_manager import StateManager
from core.event_bus import bus, EventBus
from api.lcu_client import LCUClient
from api.live_client import LiveClientAPI
from ai.llm_client import LLMClient
from ai.coaching_engine import CoachingEngine
from services.champ_select_service import ChampSelectService
from services.ingame_service import InGameService
from services.tts_service import TTSService
from ui.main_window import MainWindow
from ui.splash_screen import SplashScreen


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("lol_helper.log", encoding="utf-8"),
        ],
    )
    # Quieten noisy libraries
    for noisy in ("httpx", "httpcore", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# LCU connection (retried in background)
# ---------------------------------------------------------------------------

def try_connect_lcu(lcu: LCUClient) -> None:
    """Attempt LCU connection with retries. Runs in a daemon thread."""
    while True:
        if lcu.connect():
            bus.emit(EventBus.LCU_CONNECTED)
            return
        time.sleep(5.0)



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    logger = logging.getLogger("main")
    logger.info("Starting %s v%s", config.APP_TITLE, config.APP_VERSION)

    # ---- 2. Initialise shared services ----
    state_manager  = StateManager()
    lcu_client     = LCUClient()
    live_client    = LiveClientAPI()
    llm_client     = LLMClient(provider=config.LLM_PROVIDER)
    coaching_engine = CoachingEngine(llm_client=llm_client)
    tts_service    = TTSService()

    # ---- 3. Build the main window (must happen before services subscribe to bus) ----
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    window = MainWindow(state_manager=state_manager, tts=tts_service, coaching_engine=coaching_engine)

    # ---- 4. Start background services ----
    tts_service.start()

    champ_select_service = ChampSelectService(
        lcu_client=lcu_client,
        coaching_engine=coaching_engine,
        state_manager=state_manager,
    )
    ingame_service = InGameService(
        live_client=live_client,
        coaching_engine=coaching_engine,
        state_manager=state_manager,
    )

    # Try LCU connection in background (League may not be open yet)
    threading.Thread(
        target=try_connect_lcu, args=(lcu_client,), daemon=True, name="LCU-Connect"
    ).start()

    # Start polling services
    champ_select_service.start()
    ingame_service.start()

    logger.info("All services started. Entering UI event loop.")

    # ---- 5. Show splash screen after mainloop starts ----
    # CTkToplevel requires the Tk mainloop to be running — use after(0) to
    # defer creation to the very first event-loop tick.
    def _post_start():
        splash = SplashScreen(window)
        # Splash is just visual now, close it quickly
        splash.mark_ready()

    window.after(0, _post_start)

    # ---- 6. Run UI (blocking) ----
    try:
        window.mainloop()
    finally:
        # Graceful shutdown
        logger.info("Shutting down…")
        champ_select_service.stop()
        ingame_service.stop()
        tts_service.stop()
        lcu_client.disconnect()
        live_client.close()
        logger.info("Goodbye.")


if __name__ == "__main__":
    main()
