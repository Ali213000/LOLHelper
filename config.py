"""
config.py — Central configuration for the LoL Coaching Assistant.

All user-configurable settings live here. Values are loaded from a .env file
(auto-created on first run) and can also be changed through the Settings UI,
which writes back to .env automatically.
"""
import os
from pathlib import Path
from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).parent.resolve()
ENV_FILE = APP_DIR / ".env"

# Create .env if it doesn't exist yet
if not ENV_FILE.exists():
    ENV_FILE.touch()

load_dotenv(ENV_FILE)

# ---------------------------------------------------------------------------
# Riot / LCU
# ---------------------------------------------------------------------------
# Official Riot API key from https://developer.riotgames.com/
RIOT_API_KEY: str = os.getenv("RIOT_API_KEY", "")

# Summoner info (Riot ID format: "Name#TAG")
SUMMONER_NAME: str = os.getenv("SUMMONER_NAME", "")
SUMMONER_TAG: str  = os.getenv("SUMMONER_TAG", "EUW")
REGION: str        = os.getenv("REGION", "euw1")          # e.g. euw1, na1, kr
REGIONAL_CLUSTER: str = os.getenv("REGIONAL_CLUSTER", "europe")  # europe / americas / asia

# Path to League of Legends installation (used to locate lockfile).
# Leave blank to auto-detect from common install locations.
LOL_INSTALL_PATH: str = os.getenv(
    "LOL_INSTALL_PATH",
    r"C:\Riot Games\League of Legends"
)

# ---------------------------------------------------------------------------
# LLM Provider
# ---------------------------------------------------------------------------
# Options: "gemini" | "openai" | "claude"
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")

# Google Gemini
GEMINI_API_KEY: str  = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str    = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# OpenAI
OPENAI_API_KEY: str  = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str    = os.getenv("OPENAI_MODEL", "gpt-4o")

# Anthropic Claude
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

# ---------------------------------------------------------------------------
# OCR / Vision
# ---------------------------------------------------------------------------
# Set to True if a CUDA-capable GPU is available (EasyOCR will use it)
OCR_USE_GPU: bool = os.getenv("OCR_USE_GPU", "true").lower() == "true"

# How many seconds between automatic scoreboard OCR scans in-game
OCR_SCAN_INTERVAL_SECONDS: int = int(os.getenv("OCR_SCAN_INTERVAL_SECONDS", "15"))

# Fraction of screen height captured as the scoreboard region (top portion)
SCOREBOARD_TOP_FRACTION: float = float(os.getenv("SCOREBOARD_TOP_FRACTION", "0.22"))

# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------
# Affiche ou non l'overlay en jeu (désactivable depuis l'onglet Réglages)
OVERLAY_ENABLED: bool = os.getenv("OVERLAY_ENABLED", "true").lower() == "true"

OVERLAY_OPACITY: float = float(os.getenv("OVERLAY_OPACITY", "0.88"))
OVERLAY_AUTO_HIDE_SECONDS: int = int(os.getenv("OVERLAY_AUTO_HIDE_SECONDS", "12"))
# Corner: "top-right" | "top-left" | "bottom-right" | "bottom-left"
OVERLAY_POSITION: str = os.getenv("OVERLAY_POSITION", "top-right")

# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
TTS_ENABLED: bool = os.getenv("TTS_ENABLED", "true").lower() == "true"
# edge-tts voice name; see: `edge-tts --list-voices`
TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-GuyNeural")
TTS_RATE: str  = os.getenv("TTS_RATE", "+0%")   # e.g. "+20%", "-10%"

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
APP_THEME: str  = os.getenv("APP_THEME", "dark")
APP_TITLE: str  = "LoL Coaching Assistant"
APP_VERSION: str = "1.0.0"

# ---------------------------------------------------------------------------
# Helper: persist a single setting back to .env
# ---------------------------------------------------------------------------
def save_setting(key: str, value: str) -> None:
    """Persist a key=value pair to the .env file and update the live env."""
    set_key(str(ENV_FILE), key, value)
    os.environ[key] = value


def reload() -> None:
    """Relit .env et rafraîchit les constantes modifiables à chaud.

    Le panneau Réglages écrit dans .env puis appelle cette fonction : sans elle,
    les modules qui lisent config.X gardent les valeurs du démarrage.
    """
    global OVERLAY_ENABLED, OVERLAY_OPACITY, OVERLAY_AUTO_HIDE_SECONDS
    global OVERLAY_POSITION, TTS_ENABLED, TTS_VOICE, TTS_RATE, LLM_PROVIDER

    load_dotenv(ENV_FILE, override=True)
    OVERLAY_ENABLED = os.getenv("OVERLAY_ENABLED", "true").lower() == "true"
    OVERLAY_OPACITY = float(os.getenv("OVERLAY_OPACITY", "0.88"))
    OVERLAY_AUTO_HIDE_SECONDS = int(os.getenv("OVERLAY_AUTO_HIDE_SECONDS", "12"))
    OVERLAY_POSITION = os.getenv("OVERLAY_POSITION", "top-right")
    TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() == "true"
    TTS_VOICE = os.getenv("TTS_VOICE", "en-US-GuyNeural")
    TTS_RATE = os.getenv("TTS_RATE", "+0%")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
