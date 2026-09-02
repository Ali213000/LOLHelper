"""
ui/theme.py — Centralized design system for the LoL Coaching Assistant.

Palette inspired by the official League of Legends client UI:
  • Deep navy backgrounds  (#07101e → #1a2d45)
  • Gold / amber primary accent  (#c8a840)
  • Electric cyan secondary accent  (#0bc4e3)
  • Steel-blue borders and dividers  (#1e3a5f)

Import this module in every UI file instead of hardcoding colour literals.
"""
import customtkinter as ctk

# ---------------------------------------------------------------------------
# Colour Palette — LoL Client-Inspired
# ---------------------------------------------------------------------------

# ── Primary accent: Gold / amber (LoL logo, "JOUER" button, borders) ──────
PURPLE_PRIMARY  = "#c8a840"   # renamed alias kept for compat — this is Gold
PURPLE_HOVER    = "#a88a28"   # darker gold on hover
PURPLE_DIM      = "#5a4810"   # very dim gold (badge backgrounds)
PURPLE_GLOW     = "#f0cc60"   # bright gold glow / highlight

# ── Secondary accent: Electric cyan (notification dots, active pips) ───────
TEAL_ACCENT     = "#0bc4e3"   # cyan highlight
TEAL_DIM        = "#065870"   # dim cyan

# ── Danger / fed enemy ─────────────────────────────────────────────────────
RED_DANGER      = "#e84057"   # LoL red (death / danger)
RED_DIM         = "#6b1020"   # dim red background

# ── In-game gold counter (kept distinct from accent gold) ──────────────────
GOLD_ACCENT     = "#d4a843"   # in-game gold / XP
GOLD_DIM        = "#6b4e10"   # dim gold backgrounds

# ---------------------------------------------------------------------------
# Background layers — deep navy, like the LoL client
# ---------------------------------------------------------------------------
BG_ROOT         = "#07101e"   # outermost window background
BG_SIDEBAR      = "#0a1828"   # sidebar, titlebar, statusbar
BG_CARD         = "#0f1e30"   # main content cards
BG_CARD_INNER   = "#152538"   # nested cards, item slots
BG_INPUT        = "#1a2d45"   # text entries, option menus
BG_HOVER        = "#1e3650"   # nav hover state

# ---------------------------------------------------------------------------
# Text colours
# ---------------------------------------------------------------------------
TEXT_PRIMARY    = "#e8dfc0"   # warm off-white (matches LoL UI text)
TEXT_SECONDARY  = "#8fa8c0"   # steel-blue grey
TEXT_MUTED      = "#4a6680"   # subdued labels
TEXT_FAINT      = "#2e4a65"   # placeholders / disabled
TEXT_INVISIBLE  = "#162030"   # separator-level (barely visible)

# ---------------------------------------------------------------------------
# Border colours — steel blue tones
# ---------------------------------------------------------------------------
BORDER_DEFAULT  = "#1e3a5f"   # standard card border
BORDER_ACCENT   = PURPLE_PRIMARY          # gold accent border
BORDER_DANGER   = RED_DANGER

# ---------------------------------------------------------------------------
# Typography — 5 semantic sizes (Segoe UI, always available on Windows)
# ---------------------------------------------------------------------------
FONT_CAPS   = ("Segoe UI", 10, "bold")   # ALL-CAPS section labels
FONT_XS     = ("Segoe UI", 10)           # footnotes, version, muted
FONT_SM     = ("Segoe UI", 12)           # body small
FONT_MD     = ("Segoe UI", 13)           # body standard
FONT_LG     = ("Segoe UI", 16, "bold")   # stat values
FONT_XL     = ("Segoe UI", 20, "bold")   # hero / champion name
FONT_TITLE  = ("Segoe UI", 14, "bold")   # panel section titles

# ---------------------------------------------------------------------------
# Spacing & sizing constants
# ---------------------------------------------------------------------------
TITLEBAR_H      = 56
SIDEBAR_W       = 185
STATUSBAR_H     = 32
NAV_BTN_H       = 48
CARD_RADIUS     = 10
INNER_RADIUS    = 6
PADX_CARD       = 16
PADY_CARD_TOP   = 14
INDICATOR_DOT   = 13   # font size for ● indicators

# ---------------------------------------------------------------------------
# Helper factories (avoid repeating ctk.CTkFont everywhere)
# ---------------------------------------------------------------------------

def font_caps() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_CAPS)

def font_xs() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_XS)

def font_sm() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_SM)

def font_md() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_MD)

def font_lg() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_LG)

def font_xl() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_XL)

def font_title() -> ctk.CTkFont:
    return ctk.CTkFont(*FONT_TITLE)


# ---------------------------------------------------------------------------
# Reusable widget factories
# ---------------------------------------------------------------------------

def make_card(parent, **kwargs) -> ctk.CTkFrame:
    """Standard content card — navy background, subtle gold border."""
    return ctk.CTkFrame(
        parent,
        fg_color=kwargs.pop("fg_color", BG_CARD),
        corner_radius=kwargs.pop("corner_radius", CARD_RADIUS),
        border_color=kwargs.pop("border_color", BORDER_DEFAULT),
        border_width=kwargs.pop("border_width", 1),
        **kwargs,
    )


def make_divider(parent, vertical: bool = False) -> ctk.CTkFrame:
    """Thin steel-blue separator line."""
    if vertical:
        return ctk.CTkFrame(parent, width=1, fg_color=BORDER_DEFAULT)
    return ctk.CTkFrame(parent, height=1, fg_color=BORDER_DEFAULT)


def make_caps_label(
    parent, text: str, color: str = PURPLE_PRIMARY, **kwargs
) -> ctk.CTkLabel:
    """ALL-CAPS bold section header label."""
    return ctk.CTkLabel(
        parent,
        text=text,
        font=font_caps(),
        text_color=color,
        **kwargs,
    )


def make_section_title(parent, text: str) -> ctk.CTkLabel:
    """Larger bold title for a panel section."""
    return ctk.CTkLabel(
        parent,
        text=text,
        font=font_title(),
        text_color=PURPLE_PRIMARY,
    )
