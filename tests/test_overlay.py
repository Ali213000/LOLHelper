"""
Overlay en jeu : réglage d'activation, click-through, contenu affiché.

Trois bugs sont couverts ici :
  1. `_make_click_through` existait mais n'était JAMAIS appelé — l'overlay
     interceptait donc les clics de la souris en pleine partie ;
  2. l'overlay n'affichait que la chaîne « Nouveau plan généré. », sans le
     moindre objet ;
  3. aucun réglage ne permettait de le désactiver.
"""
import os
import re
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
OVERLAY_SRC = (ROOT / "ui" / "overlay_window.py").read_text(encoding="utf-8")
MAIN_SRC = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
ENGINE_SRC = (ROOT / "ai" / "coaching_engine.py").read_text(encoding="utf-8")
SETTINGS_SRC = (ROOT / "ui" / "settings_panel.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------- réglage

def test_le_reglage_existe_et_est_booleen():
    assert isinstance(config.OVERLAY_ENABLED, bool)


def test_reload_relit_le_reglage(tmp_path, monkeypatch):
    """config.reload() doit refléter une modification de .env sans redémarrage."""
    original = config.ENV_FILE.read_text(encoding="utf-8")
    initial = config.OVERLAY_ENABLED
    try:
        flipped = "false" if initial else "true"
        config.save_setting("OVERLAY_ENABLED", flipped)
        config.reload()
        assert config.OVERLAY_ENABLED is (flipped == "true")
    finally:
        config.ENV_FILE.write_text(original, encoding="utf-8")
        os.environ["OVERLAY_ENABLED"] = "true" if initial else "false"
        config.reload()
    assert config.OVERLAY_ENABLED is initial


def test_le_panneau_reglages_expose_l_interrupteur():
    assert "_overlay_switch" in SETTINGS_SRC
    assert '"OVERLAY_ENABLED"' in SETTINGS_SRC
    assert "_on_overlay_toggle" in SETTINGS_SRC


def test_la_bascule_est_appliquee_a_chaud():
    assert "set_enabled" in MAIN_SRC
    assert "_toggle_overlay" in MAIN_SRC


# ---------------------------------------------------------------- click-through

def test_le_click_through_est_reellement_applique():
    """La fonction doit être APPELÉE, pas seulement définie."""
    assert "def _enable_click_through" in OVERLAY_SRC
    calls = re.findall(r"(?<!def )_enable_click_through\(", OVERLAY_SRC)
    assert calls, "click-through défini mais jamais appelé — l'overlay avalerait les clics"


def test_les_styles_windows_necessaires_sont_poses():
    for flag in ("WS_EX_LAYERED", "WS_EX_TRANSPARENT"):
        assert flag in OVERLAY_SRC


# -------------------------------------------------------------------- contenu

def test_l_overlay_affiche_un_plan_pas_un_texte_bidon():
    assert "def show_plan" in OVERLAY_SRC
    assert "get_item_icon_by_id" in OVERLAY_SRC, "l'overlay doit afficher les icônes d'objets"
    assert "show_plan" in MAIN_SRC, "main_window doit transmettre le plan à l'overlay"


def test_le_placeholder_a_disparu():
    assert "Nouveau plan généré." not in ENGINE_SRC


def test_le_declencheur_est_transmis():
    assert '"trigger": trigger' in ENGINE_SRC
    assert "trigger=trigger" in MAIN_SRC


def test_tous_les_etats_de_slot_ont_un_style():
    from models.build_plan import SlotState
    from ui import overlay_window

    for state in SlotState:
        assert state in overlay_window._SLOT_STYLE, f"{state} sans style dans l'overlay"
