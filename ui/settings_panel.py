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

    def __init__(self, master, on_settings_changed=None, on_overlay_preview=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_changed = on_settings_changed
        self._on_overlay_preview = on_overlay_preview
        self._build()

    # -----------------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------------

    def _build(self) -> None:
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=6, pady=6)

        # ── Riot / Summoner ──────────────────────────────────────────────
        section = self._section(scroll, "⚔  INVOCATEUR & API RIOT")
        self._summoner_name = self._row(section, "Nom d'invocateur",  config.SUMMONER_NAME)
        self._summoner_tag  = self._row(section, "Tag  (#)",       config.SUMMONER_TAG)
        self._region        = self._row(section, "Région",         config.REGION,
                                        hint="ex. euw1, na1, kr")
        self._riot_key      = self._row(section, "Clé API Riot",   config.RIOT_API_KEY, secret=True)
        self._lol_path      = self._row(section, "Dossier d'installation LoL", config.LOL_INSTALL_PATH)

        # ── AI Provider ─────────────────────────────────────────────────
        section = self._section(scroll, "◆  FOURNISSEUR IA")

        # Provider dropdown
        prov_lbl, prov_widget = self._row_frame(section, "Fournisseur LLM")
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

        self._gemini_key    = self._row(section, "Clé API Gemini",   config.GEMINI_API_KEY,    secret=True)
        self._gemini_model  = self._row(section, "Modèle Gemini",     config.GEMINI_MODEL)
        self._openai_key    = self._row(section, "Clé API OpenAI",   config.OPENAI_API_KEY,    secret=True)
        self._openai_model  = self._row(section, "Modèle OpenAI",     config.OPENAI_MODEL)
        self._anthropic_key = self._row(section, "Clé API Anthropic",config.ANTHROPIC_API_KEY, secret=True)

        # ── Overlay ──────────────────────────────────────────────────────
        section = self._section(
            scroll, "▣  OVERLAY EN JEU",
            subtitle="Fenêtre transparente affichant le plan d'objets par-dessus le jeu.",
        )

        ov_lbl, ov_widget = self._row_frame(section, "Afficher l'overlay")
        self._overlay_switch = ctk.CTkSwitch(
            ov_widget,
            text="",
            progress_color=T.PURPLE_PRIMARY,
            button_color=T.TEXT_PRIMARY,
            button_hover_color=T.PURPLE_GLOW,
            command=self._on_overlay_toggle,
        )
        if config.OVERLAY_ENABLED:
            self._overlay_switch.select()
        self._overlay_switch.pack(side="left")

        self._overlay_state_lbl = ctk.CTkLabel(
            ov_widget, text="", font=T.font_xs(), text_color=T.TEXT_MUTED
        )
        self._overlay_state_lbl.pack(side="left", padx=(T.SP_3, 0))

        ctk.CTkButton(
            ov_widget,
            text="Aperçu",
            width=88,
            height=26,
            font=T.font_xs(),
            fg_color=T.BG_INPUT,
            hover_color=T.BG_HOVER,
            text_color=T.TEXT_SECONDARY,
            corner_radius=T.INNER_RADIUS,
            command=self._preview_overlay,
        ).pack(side="right")

        self._opacity   = self._row(section, "Opacité",    str(config.OVERLAY_OPACITY),
                                    hint="0.0 – 1.0")
        self._auto_hide = self._row(section, "Masquage auto", str(config.OVERLAY_AUTO_HIDE_SECONDS),
                                    hint="secondes")

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
        section = self._section(scroll, "♪  SYNTHÈSE VOCALE")

        tts_lbl, tts_widget = self._row_frame(section, "Activer la voix")
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

        self._tts_voice = self._row(section, "Voix",       config.TTS_VOICE)
        self._tts_rate  = self._row(section, "Débit",        config.TTS_RATE,
                                    hint="ex. +10%, -5%")

        # ── Save ─────────────────────────────────────────────────────────
        ctk.CTkFrame(scroll, height=12, fg_color="transparent").pack()

        self._save_btn = ctk.CTkButton(
            scroll,
            text="✓  ENREGISTRER",
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

        self._refresh_overlay_state()

    # -----------------------------------------------------------------------
    # Widget factories
    # -----------------------------------------------------------------------

    def _section(self, parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
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

        if subtitle:
            ctk.CTkLabel(
                card, text=subtitle, font=T.font_xs(),
                text_color=T.TEXT_MUTED, anchor="w", justify="left",
            ).pack(fill="x", padx=14, pady=(0, 2))

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
            "OVERLAY_ENABLED":           "true" if self._overlay_switch.get() else "false",
            "OVERLAY_OPACITY":           self._opacity.get(),
            "OVERLAY_AUTO_HIDE_SECONDS": self._auto_hide.get(),
            "OVERLAY_POSITION":          self._position_var.get(),
            "TTS_ENABLED":               "true" if self._tts_switch.get() else "false",
            "TTS_VOICE":                 self._tts_voice.get(),
            "TTS_RATE":                  self._tts_rate.get(),
        }
        # NB : on écrit aussi les valeurs vides — un `if value:` empêchait
        # d'effacer un champ (une clé API, par exemple) depuis l'interface.
        for key, value in mappings.items():
            config.save_setting(key, value)
        config.reload()

        # Animated feedback: button turns green for 2 s
        self._save_btn.configure(
            fg_color=T.TEAL_DIM,
            text="✓  ENREGISTRÉ",
        )
        self._status_label.configure(
            text="Réglages enregistrés. Certains changements demandent un redémarrage."
        )
        self.after(2000, self._reset_save_btn)

        if self._on_changed:
            self._on_changed(mappings)

    # -----------------------------------------------------------------------
    # Overlay
    # -----------------------------------------------------------------------

    def _on_overlay_toggle(self) -> None:
        """Applique la bascule immédiatement, sans attendre la sauvegarde."""
        enabled = bool(self._overlay_switch.get())
        config.save_setting("OVERLAY_ENABLED", "true" if enabled else "false")
        config.reload()
        self._refresh_overlay_state()
        if self._on_changed:
            self._on_changed({"OVERLAY_ENABLED": "true" if enabled else "false"})

    def _refresh_overlay_state(self) -> None:
        enabled = bool(self._overlay_switch.get())
        self._overlay_state_lbl.configure(
            text="activé" if enabled else "désactivé",
            text_color=T.STATE_OK if enabled else T.TEXT_FAINT,
        )

    def _preview_overlay(self) -> None:
        if self._on_overlay_preview:
            self._on_overlay_preview()

    def _reset_save_btn(self) -> None:
        self._save_btn.configure(fg_color=T.PURPLE_PRIMARY, text="✓  ENREGISTRER")
