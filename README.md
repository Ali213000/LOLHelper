# LOL HELPER

A **real-time League of Legends coaching assistant and analytical engine** that runs locally on Windows. Uses official Riot APIs, statistical match analysis, GPU-accelerated OCR, and LLM-powered advice — fully compliant with Riot Games ToS and Vanguard Anti-Cheat (no memory reading, no DLL injection).

---

## Features

| Feature | How it works |
|---|---|
| **Data-Driven Core Engine** | Uses Riot's Match-V5 API to ingest high-elo datasets (e.g. 30,000+ matches), applying rigorous statistical models (Cohen's d, A/B testing) to validate contextual item effectiveness. |
| **Champ Select Advisor** | Polls the LCU API every 2s (lecture seule) and produit des suggestions de champion via le scorer algorithmique (`ai/champion_scorer.py`). |
| **Fed Enemy Item Recommender** | Monitors the Live Client API + OCR scoreboard; if an enemy goes 5/0 or KDA ≥ 3.0, triggers a targeted item recommendation based on empirical engine weights. **100 % algorithmique** — aucun appel LLM. |
| **GPU-Accelerated OCR** | EasyOCR on your RTX 5070 Ti scans the scoreboard when you press TAB or every 15s |
| **Overlay en jeu** | Fenêtre transparente toujours au premier plan affichant le plan d'objets : icônes, état de chaque slot (acheté / prévu / hors plan) et niveau de confiance. Click-through actif — la souris du jeu n'est jamais interceptée. Activable/désactivable depuis les Réglages ou l'icône ◉ de la barre de titre. |
| **Text-to-Speech** | Microsoft Neural voices (edge-tts) read advice aloud |
| **System Tray** | Minimize to tray while gaming, restore with one click |

---

## Requirements

- Windows 10 / 11
- Python 3.11+
- NVIDIA GPU (RTX recommended) for GPU-accelerated OCR
- League of Legends installed (for LCU API)
- *(optionnel)* Une clé **Gemini / OpenAI / Anthropic** — voir « État du LLM » ci-dessous

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

### 3. Configure
Copiez `.env.example` vers `.env`, ou renseignez l'onglet **Settings** :
- **Summoner Name** and **Tag** (e.g. `Faker#KR1`)
- **Region** (e.g. `euw1`, `na1`, `kr`)
- **Clé LLM** — facultative, voir ci-dessous

### État du LLM

Le moteur de recommandation d'objets est **entièrement algorithmique**
(`services/stat_analyzer.py` + `data/core_items_prescription.json`, calibrés sur
un jeu de matchs Match-V5 avec split train/test). L'application **n'appelle
aucun LLM** dans son fonctionnement courant.

Le câblage LLM (`ai/llm_client.py`, `ai/prompt_templates.py`,
`CoachingEngine.request_champ_select_advice`) est conservé mais **non branché** :
aucun appelant. Il n'y a donc pas besoin de clé API pour utiliser l'app.

### Overlay en jeu

L'overlay se déclenche sur les événements de partie (mort, retour en base,
rafraîchissement manuel) et se masque tout seul après `OVERLAY_AUTO_HIDE_SECONDS`.

* **Activer / désactiver** : interrupteur dans Réglages → « Overlay en jeu », ou
  icône ◉ dans la barre de titre. Le changement s'applique immédiatement.
* **Aperçu** : bouton « Aperçu » dans les Réglages, sans avoir à lancer une partie.
* **Click-through** : la fenêtre pose `WS_EX_TRANSPARENT`, donc elle ne capte
  aucun clic. `tests/test_overlay.py` vérifie que l'appel est bien effectué —
  il avait été oublié pendant un temps, et l'overlay bloquait alors la souris.

### ⚠️ Locale des assets Data Dragon

`services/stat_analyzer.py` indexe toutes ses tables sur les noms d'objets
**français**, alors que les champions sont manipulés en **anglais** (LCU +
scorer). `download_assets.py` applique cet invariant et refuse d'écrire des
items dans la mauvaise langue. Ne le modifiez pas sans adapter le moteur :
l'erreur ne lève aucune exception, elle vide silencieusement toutes les
correspondances.

### Tests

```bat
.venv\Scripts\python.exe -m pytest
```

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
├── models/
│   ├── build_plan.py           # Plan d'objets (slots, états)
│   └── verdict.py
│
├── data/                       # Tables de champions, prescriptions, stats patch
├── scripts/                    # Collecte Match-V5 + scripts de validation
├── tests/                      # Suite pytest de non-régression
│
└── assets/
    ├── champion_data.json      # en_US — auto-downloaded by setup.bat
    └── item_data.json          # fr_FR — voir « Locale des assets »
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
| `OVERLAY_ENABLED` | `true` | Affiche l'overlay en jeu (bascule aussi depuis la barre de titre) |
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
| Poll official local APIs (**GET only**) | Inject DLLs |
| Capture screenshots via OS API | Modify game files |
| Compute advice locally | Automate gameplay |
| Listen for key presses (read-only) | Inject mouse/keyboard input |
| | Accept queues / hover / lock picks for you |

**Le client LCU est en lecture seule.** `api/lcu_client.py` ne contient aucune
méthode d'écriture (POST/PATCH/PUT). L'auto-accept de file, qui existait
auparavant, a été retiré : accepter une partie à la place du joueur est de
l'automatisation du client. `tests/test_lcu_readonly.py` verrouille l'invariant.

---

## Troubleshooting

**LCU shows disconnected:**
- Make sure the League client is running *before* the app
- Check your install path in Settings (default: `C:\Riot Games\League of Legends`)

**OCR not working:**
- Press TAB while the scoreboard is fully visible (not fading in)
- Check if `OCR_USE_GPU=true` — set to `false` if CUDA errors appear in the log

**No champ select advice:**
- Les suggestions viennent du scorer algorithmique : vérifiez que
  `data/champions_*.json` et `data/draft_config.json` sont présents
- L'analyse démarre à l'entrée en champ select

**TTS not working:**
- edge-tts requires internet access; use `pyttsx3` fallback if offline
- Check Windows audio output isn't muted
