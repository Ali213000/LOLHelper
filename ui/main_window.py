"""
ui/main_window.py — Main application window.

Dark-mode CustomTkinter dashboard with sidebar navigation (Champ Select,
In-Game, Settings), a polished titlebar, statusbar with phase badge, and
system tray integration.
Wires the EventBus to UI updates — all bus callbacks marshal to the
main thread via CTk's after() to keep Tkinter happy.
"""
import logging
import sys
import threading

import customtkinter as ctk

import config
import ui.theme as T
from core.event_bus import bus, EventBus
from core.state_manager import StateManager, ChampSelectState, InGameState
from services.image_cache import ImageCache
from ui.champ_select_panel import ChampSelectPanel
from ui.ingame_panel import InGamePanel
from ui.overlay_window import OverlayWindow
from ui.settings_panel import SettingsPanel
from ui.widgets import ToastNotification, ClockLabel
from services.tts_service import TTSService

logger = logging.getLogger(__name__)


class MainWindow(ctk.CTk):
    """
    The primary application window.
    All UI is built here; EventBus subscriptions route data to panels.
    """

    def __init__(self, state_manager: StateManager, tts: TTSService, coaching_engine=None) -> None:
        super().__init__()

        self._state            = state_manager
        self._tts              = tts
        self._coaching_engine  = coaching_engine

        # ---- Window setup ----
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(config.APP_TITLE)
        self.geometry("920x700")
        self.minsize(760, 580)
        self.configure(fg_color=T.BG_ROOT)

        # Custom icon (silently ignored if file missing)
        try:
            self.iconbitmap("assets/icon.ico")
        except Exception:
            pass

        # ---- Construction de l'UI ----
        # ORDRE IMPORTANT : en pack Tk, chaque widget rogne la cavité restante.
        # Les barres horizontales (haut/bas) doivent être placées AVANT les
        # colonnes (gauche/droite), sinon la barre de statut ne reçoit qu'une
        # cavité résiduelle à droite et ampute le contenu de sa largeur.
        self._build_titlebar()
        self._build_statusbar()
        self._build_sidebar()
        self._build_content()

        # Overlay (toplevel séparé)
        self._overlay = OverlayWindow(self)
        self._sync_overlay_button()

        # ---- Subscribe to events ----
        self._subscribe_events()

        # ---- System tray ----
        self._setup_tray()

        # ---- Protocol ----
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -----------------------------------------------------------------------
    # UI Construction
    # -----------------------------------------------------------------------

    def _build_titlebar(self) -> None:
        bar = ctk.CTkFrame(
            self, height=T.TITLEBAR_H, fg_color=T.BG_SIDEBAR, corner_radius=0
        )
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Logo mark
        ctk.CTkLabel(
            bar,
            text="⚔",
            font=ctk.CTkFont("Segoe UI", 22),
            text_color=T.PURPLE_PRIMARY,
        ).pack(side="left", padx=(18, 6), pady=0)

        # App name
        ctk.CTkLabel(
            bar,
            text="LOL COACHING ASSISTANT",
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=T.TEXT_PRIMARY,
        ).pack(side="left", pady=0)

        # Version badge
        ctk.CTkLabel(
            bar,
            text=f" v{config.APP_VERSION}",
            font=T.font_xs(),
            text_color=T.TEXT_FAINT,
        ).pack(side="left", padx=(4, 0), pady=0)

        # ── Right-side controls ──
        def _icon_btn(text: str, cmd, hover: str = T.BG_HOVER) -> ctk.CTkButton:
            return ctk.CTkButton(
                bar,
                text=text,
                width=36,
                height=32,
                fg_color="transparent",
                hover_color=hover,
                text_color=T.TEXT_SECONDARY,
                font=ctk.CTkFont("Segoe UI", 16),
                corner_radius=T.INNER_RADIUS,
                command=cmd,
            )

        _icon_btn("↺", self._restart).pack(side="right", padx=(0, 6), pady=12)
        _icon_btn("—", self._minimize_to_tray).pack(side="right", padx=4, pady=12)

        # Bascule rapide de l'overlay (état reflété par l'icône)
        self._overlay_btn = _icon_btn("◉", self._toggle_overlay)
        self._overlay_btn.pack(side="right", padx=4, pady=12)
        T.attach_tooltip(self._overlay_btn, "Activer / désactiver l'overlay en jeu")

        # Séparateur vertical avant les boutons fenêtre
        ctk.CTkFrame(bar, width=1, height=20, fg_color=T.BORDER_DEFAULT).pack(
            side="right", padx=T.SP_2, pady=18
        )

        # Gold separator line at the bottom of the titlebar
        ctk.CTkFrame(self, height=1, fg_color=T.PURPLE_PRIMARY, corner_radius=0).pack(
            fill="x", side="top"
        )

    def _build_sidebar(self) -> None:
        self._sidebar = ctk.CTkFrame(
            self, width=T.SIDEBAR_W, fg_color=T.BG_SIDEBAR, corner_radius=0
        )
        self._sidebar.pack(fill="y", side="left")
        self._sidebar.pack_propagate(False)

        # Top spacer
        ctk.CTkFrame(self._sidebar, height=20, fg_color="transparent").pack()

        # ── Navigation ──
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_active_bars: dict[str, ctk.CTkFrame] = {}

        nav_items = [
            ("⚔   Draft", "champ_select"),
            ("▶   En jeu",      "ingame"),
            ("⚙   Réglages",     "settings"),
        ]

        for label, key in nav_items:
            # Outer row to hold the active bar + button
            row = ctk.CTkFrame(self._sidebar, height=T.NAV_BTN_H, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=2)
            row.pack_propagate(False)

            # Barre d'état active (3 px à gauche).
            # height est OBLIGATOIRE : un CTkFrame vaut 200 px par défaut, et
            # pack_propagate(False) figeait cette valeur — chaque ligne de nav
            # faisait donc 200 px de haut, chassant le reste de la barre latérale.
            active_bar = ctk.CTkFrame(
                row, width=3, height=T.NAV_BTN_H,
                fg_color="transparent", corner_radius=2,
            )
            active_bar.pack(side="left", fill="y", padx=(0, 4))
            active_bar.pack_propagate(False)

            btn = ctk.CTkButton(
                row,
                text=label,
                font=T.font_sm(),
                anchor="w",
                fg_color="transparent",
                hover_color=T.BG_HOVER,
                text_color=T.TEXT_SECONDARY,
                height=T.NAV_BTN_H,
                corner_radius=T.INNER_RADIUS,
                command=lambda k=key: self._switch_tab(k),
            )
            btn.pack(fill="x", expand=True)

            self._nav_buttons[key]     = btn
            self._nav_active_bars[key] = active_bar

        # ── Separator ──
        T.make_divider(self._sidebar).pack(fill="x", padx=14, pady=18)

        # ── Connection indicators ──
        ctk.CTkLabel(
            self._sidebar,
            text="CONNEXIONS",
            font=T.font_caps(),
            text_color=T.TEXT_FAINT,
        ).pack(anchor="w", padx=16, pady=(0, 6))

        self._lcu_indicator    = self._connection_dot(self._sidebar, "Client LoL")
        self._ingame_indicator = self._connection_dot(self._sidebar, "Partie en cours")
        self._ocr_indicator    = self._connection_dot(self._sidebar, "OCR prêt")

        # ── Footer: clock ──────────────────────────────────────────────────
        # Spacer pushes clock to the bottom
        ctk.CTkFrame(self._sidebar, fg_color="transparent").pack(fill="y", expand=True)
        T.make_divider(self._sidebar).pack(fill="x", padx=14, pady=(0, 6))
        ClockLabel(self._sidebar).pack(anchor="center", pady=(0, 10))

    def _connection_dot(self, parent, label: str) -> ctk.CTkLabel:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=3)

        dot = ctk.CTkLabel(
            row, text="●",
            font=ctk.CTkFont("Segoe UI", T.INDICATOR_DOT),
            text_color=T.TEXT_INVISIBLE,
        )
        dot.pack(side="left")

        ctk.CTkLabel(
            row, text=f"  {label}",
            font=T.font_xs(),
            text_color=T.TEXT_MUTED,
        ).pack(side="left")

        return dot

    def _build_content(self) -> None:
        self._content = ctk.CTkFrame(self, fg_color=T.BG_ROOT, corner_radius=0)
        self._content.pack(fill="both", expand=True, side="left")

        self._panels: dict[str, ctk.CTkFrame] = {
            "champ_select": ChampSelectPanel(self._content),
            "ingame":       InGamePanel(self._content),
            "settings":     SettingsPanel(
                self._content,
                on_settings_changed=self._on_settings_changed,
                on_overlay_preview=self._preview_overlay,
            ),
        }

        # Wire regen button to request new suggestions via the coaching engine
        self._panels["champ_select"].set_regen_callback(self._on_regen_suggestions)

        self._current_tab: str = ""
        self._switch_tab("champ_select")

    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(
            self, height=T.STATUSBAR_H, fg_color=T.BG_SIDEBAR, corner_radius=0
        )
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        # Thin top border
        ctk.CTkFrame(bar, height=1, fg_color=T.BORDER_DEFAULT).place(
            x=0, y=0, relwidth=1.0
        )

        self._status_label = ctk.CTkLabel(
            bar,
            text="En attente du client League…",
            font=T.font_xs(),
            text_color=T.TEXT_MUTED,
        )
        self._status_label.pack(side="left", padx=16)

        # Phase badge (right side)
        self._phase_badge = ctk.CTkFrame(
            bar,
            fg_color=T.TEXT_INVISIBLE,
            corner_radius=6,
        )
        self._phase_badge.pack(side="right", padx=12, pady=7)

        self._phase_label = ctk.CTkLabel(
            self._phase_badge,
            text="  REPOS  ",
            font=T.font_caps(),
            text_color=T.TEXT_FAINT,
        )
        self._phase_label.pack(padx=6, pady=2)

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------

    def _switch_tab(self, key: str) -> None:
        if self._current_tab == key:
            return

        # Hide old panel
        if self._current_tab and self._current_tab in self._panels:
            self._panels[self._current_tab].pack_forget()

        # Reset all nav button styles
        for k, btn in self._nav_buttons.items():
            btn.configure(fg_color="transparent", text_color=T.TEXT_SECONDARY)
            self._nav_active_bars[k].configure(fg_color="transparent")

        # Activate selected
        self._panels[key].pack(fill="both", expand=True)
        self._nav_buttons[key].configure(
            fg_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY
        )
        self._nav_active_bars[key].configure(fg_color=T.PURPLE_PRIMARY)
        self._current_tab = key

    # -----------------------------------------------------------------------
    # Event Bus subscriptions
    # -----------------------------------------------------------------------

    def _subscribe_events(self) -> None:
        bus.subscribe(EventBus.LCU_CONNECTED,         self._on_lcu_connected)
        bus.subscribe(EventBus.LCU_DISCONNECTED,      self._on_lcu_disconnected)
        bus.subscribe(EventBus.LIVE_CLIENT_CONNECTED, self._on_live_connected)
        bus.subscribe(EventBus.LIVE_CLIENT_LOST,      self._on_live_lost)
        bus.subscribe(EventBus.CHAMP_SELECT_UPDATED,  self._on_champ_select_updated)
        bus.subscribe(EventBus.CHAMP_SELECT_STARTED,  self._on_champ_select_started)
        bus.subscribe(EventBus.CHAMP_SELECT_ENDED,    self._on_champ_select_ended)
        bus.subscribe(EventBus.GAME_STARTED,          self._on_game_started)
        bus.subscribe(EventBus.GAME_ENDED,            self._on_game_ended)
        bus.subscribe(EventBus.LIVE_CLIENT_UPDATED,   self._on_live_client_updated)
        bus.subscribe(EventBus.CHAMP_ADVICE_READY,       self._on_champ_advice)
        bus.subscribe(EventBus.CHAMP_SUGGESTIONS_READY,  self._on_champ_suggestions)
        bus.subscribe(EventBus.CHAMP_BAN_SUGGESTIONS_READY, self._on_ban_suggestions)
        bus.subscribe(EventBus.ITEM_ADVICE_READY,         self._on_item_advice)

    # ---- Connection events ----

    def _on_lcu_connected(self) -> None:
        self.after(0, lambda: (
            self._lcu_indicator.configure(text_color=T.TEAL_ACCENT),
            self._set_status("Client League connecté."),
            self._panels["champ_select"].afficher_attente(
                "Client connecté — les conseils démarreront à la sélection."
            ),
            self._panels["ingame"].afficher_attente(
                "Client connecté — lance une partie depuis le client League."
            ),
        ))
        self.after(0, lambda: ToastNotification(
            self, "Client League connecté", style="success"
        ))

    def _on_lcu_disconnected(self) -> None:
        self.after(0, lambda: (
            self._lcu_indicator.configure(text_color=T.TEXT_INVISIBLE),
            self._set_status("Client League déconnecté."),
        ))
        self.after(0, lambda: ToastNotification(
            self, "Client League déconnecté", style="danger"
        ))

    def _on_live_connected(self) -> None:
        self.after(0, lambda: (
            self._ingame_indicator.configure(text_color=T.TEAL_ACCENT),
        ))

    def _on_live_lost(self) -> None:
        self.after(0, lambda: (
            self._ingame_indicator.configure(text_color=T.TEXT_INVISIBLE),
        ))

    # ---- Game phase events ----

    def _on_champ_select_started(self) -> None:
        self.after(0, lambda: (
            self._switch_tab("champ_select"),
            self._set_phase("DRAFT", T.PURPLE_PRIMARY, T.PURPLE_DIM),
            self._panels["champ_select"].afficher_draft(),
            self._panels["ingame"].afficher_attente(
                "Draft en cours — le plan arrivera au début de la partie."
            ),
        ))

    def _on_champ_select_ended(self) -> None:
        self.after(0, lambda: (
            self._set_phase("REPOS", T.TEXT_FAINT, T.TEXT_INVISIBLE),
            self._panels["champ_select"].afficher_attente(),
        ))

    def _on_game_started(self) -> None:
        self.after(0, lambda: (
            self._switch_tab("ingame"),
            self._ingame_indicator.configure(text_color=T.TEAL_ACCENT),
            self._set_phase("EN JEU", T.TEAL_ACCENT, T.TEAL_DIM),
        ))
        self.after(0, lambda: ToastNotification(
            self, "Partie démarrée — passage à l'onglet En jeu", style="info"
        ))

    def _on_game_ended(self) -> None:
        self.after(0, lambda: (
            self._ingame_indicator.configure(text_color=T.TEXT_INVISIBLE),
            self._set_phase("APRÈS-PARTIE", T.GOLD_ACCENT, T.GOLD_DIM),
            self._panels["ingame"].set_no_game(),
            self._panels["ingame"].afficher_attente(
                "Partie terminée. Lance-en une nouvelle depuis le client."
            ),
        ))
        self.after(0, lambda: ToastNotification(
            self, "Partie terminée", style="warning"
        ))

    # ---- Data update events ----

    def _on_champ_select_updated(self, state: ChampSelectState) -> None:
        def _update():
            self._panels["champ_select"].update_draft(state)
        self.after(0, _update)

    def _on_live_client_updated(self, state: InGameState) -> None:
        local = state.local_player
        if local is None:
            return

        def _update():
            panel: InGamePanel = self._panels["ingame"]
            
            # Find all allies
            ally_names = [local.champion_name] # Always put local player first
            for p in state.all_players:
                if p.team == local.team and p.champion_name != local.champion_name:
                    ally_names.append(p.champion_name)
                    
            selected = panel.get_selected_ally()
            if selected not in ally_names:
                selected = local.champion_name
                
            # Find the selected player's items
            selected_player = next((p for p in state.all_players if p.champion_name == selected), local)
            
            # Calculate Gold Diff
            cache = ImageCache()
            selected_val = sum(cache.get_item_gold_value(it) for it in selected_player.items)
            
            enemy_gold_diffs = []
            for p in state.all_players:
                if p.team != local.team:
                    en_val = sum(cache.get_item_gold_value(it) for it in p.items)
                    diff = selected_val - en_val
                    enemy_gold_diffs.append((p.champion_name, diff))

            panel.update_player_stats(
                champion=local.champion_name,
                kills=local.kills,
                deaths=local.deaths,
                assists=local.assists,
                level=local.level,
                gold=local.gold,
                game_time_seconds=state.game_time_seconds,
                items=local.items,
                enemy_gold_diffs=enemy_gold_diffs,
                allies=ally_names,
            )
            if state.fed_enemies:
                top_fed = state.fed_enemies[0]
                panel.show_fed_alert(
                    top_fed.champion_name,
                    f"{top_fed.kills}/{top_fed.deaths}/{top_fed.assists}",
                )
            else:
                panel.hide_fed_alert()

        self.after(0, _update)

    # ---- AI advice events ----

    def _on_champ_advice(self, text: str) -> None:
        streaming = not text.endswith((".", "!", "?", "\n"))
        self.after(0, lambda: self._panels["champ_select"].set_advice(text, streaming=streaming))

    def _on_champ_suggestions(self, payload: dict) -> None:
        suggestions = payload.get("suggestions", [])
        reasons     = payload.get("reasons", [])
        self.after(0, lambda: self._panels["champ_select"].set_suggestions(suggestions, reasons))

    def _on_ban_suggestions(self, payload: dict) -> None:
        # Note: the BanSuggestionsWidget internally uses a thread-safe Queue, 
        # so we don't strictly need .after(), but doing it for consistency.
        self.after(0, lambda: self._panels["champ_select"].set_ban_suggestions(payload))

    def _on_regen_suggestions(self) -> None:
        """Called when user clicks the regen button on the suggestion card."""
        if self._coaching_engine is None:
            return
        state = self._state.get().champ_select
        if state.in_champ_select:
            # Reset debounce hash so forced regen always goes through
            self._coaching_engine._last_suggestions_hash = ""
            self._coaching_engine.request_champion_suggestions(state)

    def _on_item_advice(self, payload: dict) -> None:
        advice    = payload.get("advice", "")
        plan      = payload.get("plan")
        streaming = payload.get("streaming", False)
        is_adc    = payload.get("is_adc", False)
        trigger   = payload.get("trigger", "")
        champion  = payload.get("champion", "")

        def _update():
            panel: InGamePanel = self._panels["ingame"]

            if streaming:
                panel.set_status_text(advice)
            else:
                if plan:
                    panel.set_build_plan(plan, is_adc)
                    panel.set_status_text(advice)
                else:
                    panel.clear_build_slots()
                    panel.set_status_text(advice)

                # L'overlay affiche le plan lui-même (icônes + états), pas un
                # simple résumé texte.
                if plan:
                    self._overlay.show_plan(plan, trigger=trigger, champion=champion)
                else:
                    self._overlay.show_advice(advice)
                self._tts.speak(advice)

        self.after(0, _update)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    def _set_phase(self, text: str, text_color: str, bg_color: str) -> None:
        """Update the phase badge text and colours."""
        self._phase_badge.configure(fg_color=bg_color)
        self._phase_label.configure(
            text=f"  {text}  ", text_color=text_color
        )

    def _on_settings_changed(self, settings: dict) -> None:
        """Appelé quand les réglages changent. Recharge les services concernés."""
        if "TTS_ENABLED" in settings:
            self._tts.set_enabled(settings["TTS_ENABLED"].lower() == "true")

        if "OVERLAY_ENABLED" in settings:
            enabled = settings["OVERLAY_ENABLED"].lower() == "true"
            self._overlay.set_enabled(enabled)
            self._sync_overlay_button()

        self._overlay.apply_settings()
        self._sync_overlay_button()
        self._set_status("Réglages enregistrés.")

    def _preview_overlay(self) -> None:
        """Bouton « Aperçu » du panneau Réglages."""
        self._overlay.preview()

    def _toggle_overlay(self) -> None:
        """Bascule rapide depuis la barre de titre."""
        new_state = not self._overlay.is_enabled()
        config.save_setting("OVERLAY_ENABLED", "true" if new_state else "false")
        config.reload()
        self._overlay.set_enabled(new_state)
        self._sync_overlay_button()
        ToastNotification(
            self,
            f"Overlay {'activé' if new_state else 'désactivé'}",
            style="success" if new_state else "warning",
        )

    def _sync_overlay_button(self) -> None:
        """Aligne l'icône de la barre de titre sur l'état réel de l'overlay."""
        btn = getattr(self, "_overlay_btn", None)
        if btn is None:
            return
        on = self._overlay.is_enabled()
        btn.configure(
            text="◉" if on else "◎",
            text_color=T.PURPLE_PRIMARY if on else T.TEXT_FAINT,
        )

    def mark_ocr_ready(self) -> None:
        """Called from main.py after EasyOCR warm-up completes."""
        self.after(0, lambda: self._ocr_indicator.configure(text_color=T.TEAL_ACCENT))

    # -----------------------------------------------------------------------
    # System tray
    # -----------------------------------------------------------------------

    def _setup_tray(self) -> None:
        try:
            import pystray
            from PIL import Image as PILImage, ImageDraw

            img = PILImage.new("RGB", (64, 64), color="#c8a840")
            d = ImageDraw.Draw(img)
            d.text((16, 20), "LoL", fill="white")

            menu = pystray.Menu(
                pystray.MenuItem("Show", self._show_from_tray, default=True),
                pystray.MenuItem("Quit", self._quit),
            )
            self._tray_icon = pystray.Icon(
                config.APP_TITLE, img, config.APP_TITLE, menu
            )
        except ImportError:
            self._tray_icon = None
            logger.warning("pystray not installed — system tray unavailable.")

    def _minimize_to_tray(self) -> None:
        self.withdraw()
        if self._tray_icon:
            threading.Thread(
                target=self._tray_icon.run, daemon=True, name="TrayIcon"
            ).start()

    def _show_from_tray(self) -> None:
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.deiconify)

    def _quit(self) -> None:
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.destroy)

    def _on_close(self) -> None:
        self.destroy()

    def _restart(self) -> None:
        """Restart the application by spawning a new process then exiting.

        os.execv is unreliable on Windows (doesn't truly replace the process),
        so we spawn an independent child process first and then close cleanly.
        """
        import subprocess
        logger.info("Restarting application…")
        try:
            subprocess.Popen(
                [sys.executable] + sys.argv,
                close_fds=True,
            )
        except Exception as exc:
            logger.error("Failed to spawn restart process: %s", exc)
            return
        self.destroy()
