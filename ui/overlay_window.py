"""
ui/overlay_window.py — Transparent, always-on-top advice overlay.

A frameless, click-through Tkinter window that slides in from the screen edge
to display coaching advice. Auto-hides after a configurable timeout.
Click-through is implemented via Windows API (ctypes) so the game mouse is
never intercepted.
"""
import ctypes
import logging
import threading
from typing import Optional

import customtkinter as ctk

import config
import ui.theme as T

logger = logging.getLogger(__name__)

# Windows layered window constants for click-through
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE       = -20


def _make_click_through(hwnd: int) -> None:
    """Enable click-through on a Windows HWND via ctypes."""
    try:
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        )
    except Exception as exc:
        logger.warning("Could not enable click-through: %s", exc)


class OverlayWindow:
    """
    A minimal, non-interactive overlay window.

    Call show_advice(text) from any thread to display text in the overlay.
    The window slides in, stays for OVERLAY_AUTO_HIDE_SECONDS, then slides out.
    """

    _SLIDE_STEPS   = 20      # animation frames
    _SLIDE_DELAY   = 15      # ms between frames
    _OVERLAY_W     = 480
    _OVERLAY_H     = 260
    _PADDING       = 20      # screen edge padding

    def __init__(self, root: ctk.CTk) -> None:
        self._root   = root
        self._win: Optional[ctk.CTkToplevel] = None
        self._label: Optional[ctk.CTkLabel]  = None
        self._hide_job: Optional[str]        = None
        self._lock = threading.Lock()

    def _create_window(self) -> None:
        """Create the overlay toplevel (must be called from main thread)."""
        if self._win is not None:
            return

        win = ctk.CTkToplevel(self._root)
        win.overrideredirect(True)          # No title bar / frame
        win.attributes("-topmost", True)    # Always on top
        win.attributes("-alpha", config.OVERLAY_OPACITY)
        win.withdraw()  # Start hidden

        # Position
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        pos = config.OVERLAY_POSITION

        if "right" in pos:
            x = sw - self._OVERLAY_W - self._PADDING
        else:
            x = self._PADDING

        if "bottom" in pos:
            y = sh - self._OVERLAY_H - self._PADDING
        else:
            y = self._PADDING

        win.geometry(f"{self._OVERLAY_W}x{self._OVERLAY_H}+{x}+{y}")

        # Make click-through after the window is mapped
        win.update_idletasks()
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, win.title())
        except Exception:
            hwnd = None

        # Background frame
        frame = ctk.CTkFrame(
            win,
            corner_radius=T.CARD_RADIUS,
            fg_color=T.BG_CARD,
            border_color=T.PURPLE_PRIMARY,
            border_width=1,
        )
        frame.pack(fill="both", expand=True, padx=4, pady=4)

        # Header label
        ctk.CTkLabel(
            frame,
            text="⚔  COACHING ASSISTANT",
            font=T.font_title(),
            text_color=T.PURPLE_PRIMARY,
        ).pack(anchor="w", padx=14, pady=(10, 0))

        # Divider
        T.make_divider(frame).pack(fill="x", padx=14, pady=4)

        # Advice text
        self._label = ctk.CTkLabel(
            frame,
            text="",
            font=T.font_md(),
            text_color=T.TEXT_PRIMARY,
            wraplength=self._OVERLAY_W - 40,
            justify="left",
            anchor="nw",
        )
        self._label.pack(fill="both", expand=True, padx=14, pady=(4, 14))

        self._win = win

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def show_advice(self, text: str) -> None:
        """Display advice text. Safe to call from any thread."""
        self._root.after(0, lambda: self._show(text))

    def update_text(self, text: str) -> None:
        """Update the text on an already-visible overlay (for streaming)."""
        self._root.after(0, lambda: self._update_label(text))

    def hide(self) -> None:
        """Hide the overlay immediately."""
        self._root.after(0, self._hide)

    # -----------------------------------------------------------------------
    # Internal (main-thread only)
    # -----------------------------------------------------------------------

    def _show(self, text: str) -> None:
        self._create_window()
        assert self._win is not None

        if self._label:
            self._label.configure(text=text)

        # Cancel any pending auto-hide
        if self._hide_job:
            self._root.after_cancel(self._hide_job)

        self._win.deiconify()
        self._win.attributes("-topmost", True)

        # Schedule auto-hide
        hide_ms = config.OVERLAY_AUTO_HIDE_SECONDS * 1000
        self._hide_job = self._root.after(hide_ms, self._hide)

    def _update_label(self, text: str) -> None:
        if self._label and self._win and self._win.winfo_ismapped():
            self._label.configure(text=text)
            # Reset the hide timer on each token update
            if self._hide_job:
                self._root.after_cancel(self._hide_job)
            hide_ms = config.OVERLAY_AUTO_HIDE_SECONDS * 1000
            self._hide_job = self._root.after(hide_ms, self._hide)

    def _hide(self) -> None:
        if self._win:
            self._win.withdraw()
        self._hide_job = None
