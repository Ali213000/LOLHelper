"""
ui/overlay_window.py — Overlay de conseil, transparent et toujours au premier plan.

Fenêtre sans bordure qui apparaît en fondu au bord de l'écran pour afficher le
plan d'objets en cours. Elle se masque seule après OVERLAY_AUTO_HIDE_SECONDS.

Le click-through est activé via l'API Windows (ctypes) : la souris du jeu n'est
jamais interceptée. C'est indispensable — sans lui, l'overlay avale les clics.
"""
import ctypes
import logging
from typing import Optional

import customtkinter as ctk

import config
import ui.theme as T
from models.build_plan import SlotState

logger = logging.getLogger(__name__)

# Constantes Windows pour la fenêtre en couche / transparente aux clics
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20

# Apparence des états de slot dans l'overlay
_SLOT_STYLE = {
    SlotState.OWNED_ON_PLAN: (T.STATE_OK, "✓"),
    SlotState.OWNED_OFF_PLAN: (T.STATE_DRIFT, "•"),
    SlotState.PLANNED: (T.PURPLE_PRIMARY, ""),
    SlotState.PENDING: (T.TEXT_MUTED, ""),
    SlotState.UNDETERMINED: (T.TEAL_DIM, "?"),
    SlotState.EMPTY: (T.TEXT_FAINT, ""),
}


def _enable_click_through(win) -> bool:
    """Rend la fenêtre transparente aux clics. Retourne True si appliqué."""
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
        )
        return True
    except Exception as exc:
        logger.warning("Click-through indisponible : %s", exc)
        return False


class OverlayWindow:
    """
    Overlay non interactif affichant le plan d'objets.

    show_plan() / show_advice() sont sûrs depuis n'importe quel thread.
    """

    _FADE_STEPS = 12
    _FADE_DELAY = 16          # ms entre deux images
    _PADDING = 24             # marge par rapport au bord de l'écran
    _ICON = 44
    _MAX_SLOTS = 7            # bottes + 6 légendaires

    def __init__(self, root: ctk.CTk) -> None:
        self._root = root
        self._win: Optional[ctk.CTkToplevel] = None
        self._hide_job: Optional[str] = None
        self._fade_job: Optional[str] = None
        self._slot_widgets: list = []
        self._enabled: bool = config.OVERLAY_ENABLED
        self._click_through_ok = False

    # ------------------------------------------------------------------
    # API publique (sûre depuis tout thread)
    # ------------------------------------------------------------------

    def show_plan(self, plan, trigger: str = "", champion: str = "") -> None:
        """Affiche un BuildPlan complet avec les icônes d'objets."""
        self._root.after(0, lambda: self._show_plan(plan, trigger, champion))

    def show_advice(self, text: str) -> None:
        """Affiche un simple message texte (repli quand aucun plan n'est fourni)."""
        self._root.after(0, lambda: self._show_text(text))

    def hide(self) -> None:
        self._root.after(0, self._fade_out)

    def set_enabled(self, enabled: bool) -> None:
        """Active/désactive l'overlay. Le masque immédiatement si désactivé."""
        self._enabled = enabled
        if not enabled:
            self._root.after(0, self._hide_now)
        logger.info("Overlay %s.", "activé" if enabled else "désactivé")

    def is_enabled(self) -> bool:
        return self._enabled

    def apply_settings(self) -> None:
        """Reprend opacité / position / activation depuis config, à chaud."""
        self._enabled = config.OVERLAY_ENABLED
        if self._win is not None:
            self._position_window()
            if not self._enabled:
                self._hide_now()

    def preview(self) -> None:
        """Affiche un aperçu — utilisé par le bouton de test des réglages."""
        self._root.after(
            0,
            lambda: self._show_text(
                "Aperçu de l'overlay — c'est ici qu'apparaîtra ton plan d'objets.",
                force=True,
            ),
        )

    # ------------------------------------------------------------------
    # Construction (thread principal uniquement)
    # ------------------------------------------------------------------

    def _ensure_window(self) -> None:
        if self._win is not None:
            return

        win = ctk.CTkToplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(fg_color=T.BG_ROOT)
        win.withdraw()

        shell = ctk.CTkFrame(
            win,
            corner_radius=T.CARD_RADIUS,
            fg_color=T.BG_CARD,
            border_color=T.PURPLE_PRIMARY,
            border_width=1,
        )
        shell.pack(fill="both", expand=True, padx=3, pady=3)

        # -- En-tête --
        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.pack(fill="x", padx=T.SP_3, pady=(T.SP_3, 0))

        ctk.CTkLabel(
            header, text="⚔", font=ctk.CTkFont("Segoe UI", 15),
            text_color=T.PURPLE_PRIMARY,
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="  PLAN D'OBJETS", font=T.font_caps(),
            text_color=T.PURPLE_PRIMARY,
        ).pack(side="left")

        self._trigger_label = ctk.CTkLabel(
            header, text="", font=T.font_xs(), text_color=T.TEXT_MUTED
        )
        self._trigger_label.pack(side="right")

        T.make_divider(shell).pack(fill="x", padx=T.SP_3, pady=T.SP_2)

        # -- Rangée de slots --
        self._slots_row = ctk.CTkFrame(shell, fg_color="transparent")
        self._slots_row.pack(fill="x", padx=T.SP_3, pady=(0, T.SP_2))

        # -- Ligne de texte (repli / message) --
        self._text_label = ctk.CTkLabel(
            shell, text="", font=T.font_sm(), text_color=T.TEXT_SECONDARY,
            wraplength=460, justify="left", anchor="w",
        )
        self._text_label.pack(fill="x", padx=T.SP_3, pady=(0, T.SP_3))

        win.update_idletasks()
        self._click_through_ok = _enable_click_through(win)
        self._win = win

    def _position_window(self) -> None:
        if self._win is None:
            return
        win = self._win
        win.update_idletasks()
        w = max(win.winfo_reqwidth(), 320)
        h = max(win.winfo_reqheight(), 90)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        pos = config.OVERLAY_POSITION

        x = sw - w - self._PADDING if "right" in pos else self._PADDING
        y = sh - h - self._PADDING if "bottom" in pos else self._PADDING
        win.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def _clear_slots(self) -> None:
        for w in self._slot_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._slot_widgets = []

    def _slot_tile(self, parent, item_id, state, caption: str, confidence: float):
        color, mark = _SLOT_STYLE.get(state, (T.TEXT_FAINT, ""))
        filled = state in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN)

        tile = ctk.CTkFrame(
            parent,
            width=self._ICON + 2 * T.SP_2,
            height=self._ICON + 60,
            fg_color=T.BG_CARD_INNER,
            border_color=color,
            border_width=2 if filled else 1,
            corner_radius=T.INNER_RADIUS,
        )
        tile.pack_propagate(False)

        img = None
        if item_id:
            try:
                from services.image_cache import ImageCache
                img = ImageCache().get_item_icon_by_id(
                    item_id, size=self._ICON, is_filled=filled
                )
            except Exception:
                img = None

        ctk.CTkLabel(
            tile,
            text="" if img else "·",
            image=img,
            fg_color="transparent",
            text_color=T.TEXT_FAINT,
            width=self._ICON,
            height=self._ICON,
        ).pack(padx=T.SP_2, pady=(T.SP_2, 2))

        label = caption if not mark else f"{mark} {caption}"
        ctk.CTkLabel(
            tile,
            text=label,
            font=T.font_caps(),
            text_color=color,
        ).pack(padx=T.SP_2, pady=(0, 2))

        if confidence:
            ctk.CTkLabel(
                tile,
                text=f"{confidence:.0%}",
                font=T.font_xs(),
                text_color=T.TEXT_MUTED,
            ).pack(pady=(0, T.SP_2))
        else:
            ctk.CTkFrame(
                tile, width=1, height=2, fg_color="transparent"
            ).pack(pady=(0, T.SP_2))

        return tile

    def _show_plan(self, plan, trigger: str, champion: str) -> None:
        if not self._enabled or plan is None:
            return
        self._ensure_window()
        self._clear_slots()

        entries = []
        if plan.boots is not None and plan.boots.item_id:
            entries.append((plan.boots.item_id, plan.boots.state, "BOT", 0.0))
        for slot in plan.legendary_slots:
            if slot.state == SlotState.EMPTY and not slot.item_id:
                continue
            entries.append(
                (slot.item_id, slot.state, f"#{slot.index + 1}", slot.confidence)
            )
        entries = entries[: self._MAX_SLOTS]

        for item_id, state, caption, conf in entries:
            tile = self._slot_tile(self._slots_row, item_id, state, caption, conf)
            tile.pack(side="left", padx=(0, T.SP_2))
            self._slot_widgets.append(tile)

        bits = []
        if trigger:
            bits.append(trigger)
        if champion and champion != "inconnu":
            bits.append(f"vs {champion}")
        self._trigger_label.configure(text="  ·  ".join(bits))

        planned = sum(1 for _, st, _, _ in entries if st == SlotState.PLANNED)
        owned = sum(
            1
            for _, st, _, _ in entries
            if st in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN)
        )
        self._text_label.configure(
            text=f"{owned} objet(s) en inventaire · {planned} prévu(s)"
        )
        self._reveal()

    def _show_text(self, text: str, force: bool = False) -> None:
        if not self._enabled and not force:
            return
        self._ensure_window()
        self._clear_slots()
        self._trigger_label.configure(text="")
        self._text_label.configure(text=text)
        self._reveal()

    # ------------------------------------------------------------------
    # Fondu entrant / sortant
    # ------------------------------------------------------------------

    def _reveal(self) -> None:
        self._position_window()
        if self._win is None:
            return
        self._cancel_jobs()
        self._win.deiconify()
        self._win.attributes("-topmost", True)
        self._fade(step=0, direction=1)

        hide_ms = max(1, config.OVERLAY_AUTO_HIDE_SECONDS) * 1000
        self._hide_job = self._root.after(hide_ms, self._fade_out)

    def _fade(self, step: int, direction: int) -> None:
        if self._win is None:
            return
        target = config.OVERLAY_OPACITY
        ratio = step / self._FADE_STEPS
        alpha = target * (ratio if direction > 0 else 1.0 - ratio)
        try:
            self._win.attributes("-alpha", max(0.0, min(target, alpha)))
        except Exception:
            return

        if step < self._FADE_STEPS:
            self._fade_job = self._root.after(
                self._FADE_DELAY, lambda: self._fade(step + 1, direction)
            )
        else:
            self._fade_job = None
            if direction < 0:
                self._hide_now()

    def _fade_out(self) -> None:
        if self._win is None or not self._win.winfo_ismapped():
            return
        self._cancel_jobs()
        self._fade(step=0, direction=-1)

    def _hide_now(self) -> None:
        self._cancel_jobs()
        if self._win is not None:
            try:
                self._win.withdraw()
            except Exception:
                pass

    def _cancel_jobs(self) -> None:
        for attr in ("_hide_job", "_fade_job"):
            job = getattr(self, attr)
            if job:
                try:
                    self._root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
