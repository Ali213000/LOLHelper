"""
ui/settings_panel.py — Settings tab panel.

Allows the user to configure API keys, LLM provider, LoL install path,
overlay settings, and TTS options. Changes are saved to .env via config.save_setting().

Improvements vs v1:
  - Each section lives in its own card (CTkFrame with border)
  - Cleaner label / entry alignment using grid layout
  - Save button gives animated feedback (green for 2 s, then resets)
  - All colours from theme.py
"""
import customtkinter as ctk

import config
import ui.theme as T


class SettingsPanel(ctk.CTkFrame):
    """Settings tab with all user-configurable options."""

    def __init__(self, master, on_settings_changed=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_changed = on_settings_changed
        self._build()

    # -----------------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------------

    def _build(self) -> None:
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=6, pady=6)

        # ── Riot / Summoner ──────────────────────────────────────────────
        section = self._section(scroll, "⚔  SUMMONER & RIOT API")
        self._summoner_name = self._row(section, "Summoner Name",  config.SUMMONER_NAME)
        self._summoner_tag  = self._row(section, "Tag  (#)",       config.SUMMONER_TAG)
        self._region        = self._row(section, "Region",         config.REGION,
                                        hint="e.g. euw1, na1, kr")
        self._riot_key      = self._row(section, "Riot API Key",   config.RIOT_API_KEY, secret=True)
        self._lol_path      = self._row(section, "LoL Install Path", config.LOL_INSTALL_PATH)

        # ── AI Provider ─────────────────────────────────────────────────
        section = self._section(scroll, "🤖  AI PROVIDER")

        # Provider dropdown
        prov_lbl, prov_widget = self._row_frame(section, "LLM Provider")
        self._provider_var = ctk.StringVar(value=config.LLM_PROVIDER.capitalize())
        ctk.CTkOptionMenu(
            prov_widget,
            values=["Gemini", "OpenAI", "Claude"],
            variable=self._provider_var,
            font=T.font_sm(),
            fg_color=T.BG_INPUT,
            button_color=T.PURPLE_DIM,
            button_hover_color=T.PURPLE_PRIMARY,
            dropdown_fg_color=T.BG_CARD_INNER,
            width=240,
        ).pack(side="left")

        self._gemini_key    = self._row(section, "Gemini API Key",   config.GEMINI_API_KEY,    secret=True)
        self._gemini_model  = self._row(section, "Gemini Model",     config.GEMINI_MODEL)
        self._openai_key    = self._row(section, "OpenAI API Key",   config.OPENAI_API_KEY,    secret=True)
        self._openai_model  = self._row(section, "OpenAI Model",     config.OPENAI_MODEL)
        self._anthropic_key = self._row(section, "Anthropic API Key",config.ANTHROPIC_API_KEY, secret=True)

        # ── Overlay ──────────────────────────────────────────────────────
        section = self._section(scroll, "🖥  OVERLAY")
        self._opacity   = self._row(section, "Opacity",    str(config.OVERLAY_OPACITY),
                                    hint="0.0 – 1.0")
        self._auto_hide = self._row(section, "Auto-Hide", str(config.OVERLAY_AUTO_HIDE_SECONDS),
                                    hint="seconds")

        pos_lbl, pos_widget = self._row_frame(section, "Position")
        self._position_var = ctk.StringVar(value=config.OVERLAY_POSITION)
        ctk.CTkOptionMenu(
            pos_widget,
            values=["top-right", "top-left", "bottom-right", "bottom-left"],
            variable=self._position_var,
            font=T.font_sm(),
            fg_color=T.BG_INPUT,
            button_color=T.PURPLE_DIM,
            button_hover_color=T.PURPLE_PRIMARY,
            dropdown_fg_color=T.BG_CARD_INNER,
            width=240,
        ).pack(side="left")

        # ── TTS ──────────────────────────────────────────────────────────
        section = self._section(scroll, "🔊  TEXT-TO-SPEECH")

        tts_lbl, tts_widget = self._row_frame(section, "Enable TTS")
        self._tts_switch = ctk.CTkSwitch(
            tts_widget,
            text="",
            progress_color=T.PURPLE_PRIMARY,
            button_color=T.TEXT_PRIMARY,
            button_hover_color=T.PURPLE_GLOW,
        )
        if config.TTS_ENABLED:
            self._tts_switch.select()
        self._tts_switch.pack(side="left")

        self._tts_voice = self._row(section, "Voice",       config.TTS_VOICE)
        self._tts_rate  = self._row(section, "Rate",        config.TTS_RATE,
                                    hint="e.g. +10%, -5%")

        # ── Save ─────────────────────────────────────────────────────────
        ctk.CTkFrame(scroll, height=12, fg_color="transparent").pack()

        self._save_btn = ctk.CTkButton(
            scroll,
            text="💾  SAVE SETTINGS",
            font=T.font_title(),
            fg_color=T.PURPLE_PRIMARY,
            hover_color=T.PURPLE_HOVER,
            height=46,
            corner_radius=T.INNER_RADIUS,
            command=self._save,
        )
        self._save_btn.pack(padx=12, pady=(0, 8), fill="x")

        self._status_label = ctk.CTkLabel(
            scroll,
            text="",
            font=T.font_sm(),
            text_color=T.TEAL_ACCENT,
        )
        self._status_label.pack(pady=(0, 20))

    # -----------------------------------------------------------------------
    # Widget factories
    # -----------------------------------------------------------------------

    def _section(self, parent, title: str) -> ctk.CTkFrame:
        """Create a titled card container for a group of settings."""
        # Outer card with border
        card = ctk.CTkFrame(
            parent,
            fg_color=T.BG_CARD,
            corner_radius=T.CARD_RADIUS,
            border_color=T.BORDER_DEFAULT,
            border_width=1,
        )
        card.pack(fill="x", padx=6, pady=(10, 0))

        # Section title row
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=14, pady=(10, 4))

        ctk.CTkLabel(
            title_row,
            text=title,
            font=T.font_title(),
            text_color=T.PURPLE_PRIMARY,
        ).pack(side="left")

        T.make_divider(card).pack(fill="x", padx=14, pady=(4, 8))

        # Content frame (inside the card, with some padding)
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(fill="x", padx=14, pady=(0, 12))

        return content

    def _row_frame(self, parent, label: str) -> tuple:
        """Create a label + right-side container row. Returns (label, widget_frame)."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)

        lbl = ctk.CTkLabel(
            row,
            text=label,
            font=T.font_sm(),
            text_color=T.TEXT_SECONDARY,
            width=180,
            anchor="w",
        )
        lbl.pack(side="left")

        widget_frame = ctk.CTkFrame(row, fg_color="transparent")
        widget_frame.pack(side="left", fill="x", expand=True)

        return lbl, widget_frame

    def _row(
        self,
        parent,
        label: str,
        default: str = "",
        secret: bool = False,
        hint: str = "",
    ) -> ctk.CTkEntry:
        """Labeled entry row. Returns the CTkEntry widget."""
        _, widget_frame = self._row_frame(parent, label)

        entry = ctk.CTkEntry(
            widget_frame,
            show="*" if secret else "",
            font=T.font_sm(),
            fg_color=T.BG_INPUT,
            border_color=T.BORDER_DEFAULT,
            text_color=T.TEXT_PRIMARY,
            placeholder_text=hint,
            placeholder_text_color=T.TEXT_FAINT,
            width=280,
        )
        if default:
            entry.insert(0, default)
        entry.pack(side="left")

        # Focus highlight: switch border to purple on focus
        entry.bind("<FocusIn>",  lambda e: entry.configure(border_color=T.PURPLE_PRIMARY))
        entry.bind("<FocusOut>", lambda e: entry.configure(border_color=T.BORDER_DEFAULT))

        return entry

    # -----------------------------------------------------------------------
    # Save with animated feedback
    # -----------------------------------------------------------------------

    def _save(self) -> None:
        mappings = {
            "SUMMONER_NAME":             self._summoner_name.get(),
            "SUMMONER_TAG":              self._summoner_tag.get(),
            "REGION":                    self._region.get(),
            "RIOT_API_KEY":              self._riot_key.get(),
            "LOL_INSTALL_PATH":          self._lol_path.get(),
            "LLM_PROVIDER":              self._provider_var.get().lower(),
            "GEMINI_API_KEY":            self._gemini_key.get(),
            "GEMINI_MODEL":              self._gemini_model.get(),
            "OPENAI_API_KEY":            self._openai_key.get(),
            "OPENAI_MODEL":              self._openai_model.get(),
            "ANTHROPIC_API_KEY":         self._anthropic_key.get(),
            "OVERLAY_OPACITY":           self._opacity.get(),
            "OVERLAY_AUTO_HIDE_SECONDS": self._auto_hide.get(),
            "OVERLAY_POSITION":          self._position_var.get(),
            "TTS_ENABLED":               "true" if self._tts_switch.get() else "false",
            "TTS_VOICE":                 self._tts_voice.get(),
            "TTS_RATE":                  self._tts_rate.get(),
        }
        for key, value in mappings.items():
            if value:
                config.save_setting(key, value)

        # Animated feedback: button turns green for 2 s
        self._save_btn.configure(
            fg_color=T.TEAL_DIM,
            text="✓  SAVED!",
        )
        self._status_label.configure(
            text="✓ Settings saved. Restart may be required for some changes."
        )
        self.after(2000, self._reset_save_btn)

        if self._on_changed:
            self._on_changed(mappings)

    def _reset_save_btn(self) -> None:
        self._save_btn.configure(fg_color=T.PURPLE_PRIMARY, text="💾  SAVE SETTINGS")
