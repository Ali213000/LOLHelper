# LOL HELPER

A **real-time League of Legends coaching assistant and analytical engine** that runs locally on Windows. Uses official Riot APIs, statistical match analysis, GPU-accelerated OCR, and LLM-powered advice — fully compliant with Riot Games ToS and Vanguard Anti-Cheat (no memory reading, no DLL injection).

---

## Features

| Feature | How it works |
|---|---|
| **Data-Driven Core Engine** | Uses Riot's Match-V5 API to ingest high-elo datasets (e.g. 30,000+ matches), applying rigorous statistical models (Cohen's d, A/B testing) to validate contextual item effectiveness. |
| **Champ Select Advisor** | Polls the LCU API every 2s, sends champion composition to Gemini/OpenAI/Claude, streams advice in real-time |
| **Fed Enemy Item Recommender** | Monitors the Live Client API + OCR scoreboard; if an enemy goes 5/0 or KDA ≥ 3.0, triggers a targeted item recommendation based on empirical engine weights. |
| **GPU-Accelerated OCR** | EasyOCR on your RTX 5070 Ti scans the scoreboard when you press TAB or every 15s |
| **Streaming Overlay** | Transparent, always-on-top overlay shows advice as it's typed — click-through enabled |
| **Text-to-Speech** | Microsoft Neural voices (edge-tts) read advice aloud |
| **System Tray** | Minimize to tray while gaming, restore with one click |

---

## Requirements

- Windows 10 / 11
- Python 3.11+
- NVIDIA GPU (RTX recommended) for GPU-accelerated OCR
- League of Legends installed (for LCU API)
- A **Gemini API key** (free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey))

---

## Quick Start

### 1. Run Setup
```bat
setup.bat
```
This will:
- Create a Python virtual environment
- Install PyTorch with **CUDA 12.1** support (for RTX 5070 Ti)
- Install all dependencies
- Download League champion/item data from Data Dragon

### 2. Launch the App
```bat
.venv\Scripts\python.exe main.py
```

### 3. Configure API Keys
Open the **Settings** tab and enter:
- **Gemini API Key** — [Get free key](https://aistudio.google.com/apikey) (2.5 Flash has a generous free tier)
- **Summoner Name** and **Tag** (e.g. `Faker#KR1`)
- **Region** (e.g. `euw1`, `na1`, `kr`)

---

## Project Structure

```
lol-helper/
├── main.py                    # Entry point
├── config.py                  # All settings (loaded from .env)
├── setup.bat                  # One-click setup
├── requirements.txt
│
├── core/
│   ├── state_manager.py       # Thread-safe game state
│   └── event_bus.py           # Pub/sub event system
│
├── api/
│   ├── lcu_client.py          # LCU API (champ select, lockfile auth)
│   └── live_client.py         # Live Client Data API (real-time stats)
│
├── vision/
│   ├── screen_capture.py      # mss screenshots + TAB key listener
│   ├── ocr_pipeline.py        # EasyOCR GPU pipeline
│   └── scoreboard_parser.py   # KDA/CS extraction + fed detection
│
├── ai/
│   ├── llm_client.py          # LangChain wrapper (Gemini/OpenAI/Claude)
│   ├── prompt_templates.py    # All prompt templates
│   └── coaching_engine.py     # Orchestration + debouncing
│
├── services/
│   ├── champ_select_service.py
│   ├── ingame_service.py
│   └── tts_service.py
│
├── ui/
│   ├── main_window.py         # Main dashboard
│   ├── overlay_window.py      # Transparent in-game overlay
│   ├── champ_select_panel.py
│   ├── ingame_panel.py
│   └── settings_panel.py
│
└── assets/
    ├── champion_data.json      # Auto-downloaded by setup.bat
    └── item_data.json
```

---

## Configuration

All settings are stored in `.env` (auto-created on first run). Edit via the **Settings** tab in the UI or directly in the file.

| Setting | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` / `openai` / `claude` |
| `GEMINI_API_KEY` | — | Your Google AI Studio key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model to use |
| `OCR_USE_GPU` | `true` | Set `false` if no NVIDIA GPU |
| `OCR_SCAN_INTERVAL_SECONDS` | `15` | Auto-scan interval in seconds |
| `OVERLAY_OPACITY` | `0.88` | 0.0–1.0 |
| `OVERLAY_AUTO_HIDE_SECONDS` | `12` | Seconds before overlay hides |
| `TTS_ENABLED` | `true` | Enable voice coaching |
| `TTS_VOICE` | `en-US-GuyNeural` | Any edge-tts voice |

---

## ToS Compliance

This tool is **fully compliant** with Riot Games' Terms of Service and Vanguard:

| ✅ What we DO | ❌ What we DON'T do |
|---|---|
| Read the LCU lockfile for auth | Read game memory |
| Poll official local APIs | Inject DLLs |
| Capture screenshots via OS API | Modify game files |
| Send API data to an external AI | Automate gameplay |
| Listen for key presses (read-only) | Inject mouse/keyboard input |

---

## Troubleshooting

**LCU shows disconnected:**
- Make sure the League client is running *before* the app
- Check your install path in Settings (default: `C:\Riot Games\League of Legends`)

**OCR not working:**
- Press TAB while the scoreboard is fully visible (not fading in)
- Check if `OCR_USE_GPU=true` — set to `false` if CUDA errors appear in the log

**No champ select advice:**
- You must have a Gemini/OpenAI/Claude API key set in Settings
- The advice fires only when at least one champion is hovered on either side

**TTS not working:**
- edge-tts requires internet access; use `pyttsx3` fallback if offline
- Check Windows audio output isn't muted
