"""
ui/splash_screen.py — Hextech-styled loading screen.

Displayed while EasyOCR warms up (GPU model load, ~5-10s).
Destroyed automatically when mark_ready() is called or timeout fires.
"""
import customtkinter as ctk

import ui.theme as T
from ui.widgets import HextechProgressBar


class SplashScreen:
    """
    Borderless centered loading window shown during OCR warm-up.

    Usage:
        splash = SplashScreen(root)
        # ... when OCR is ready:
        splash.mark_ready()
    """

    _W = 520
    _H = 260

    def __init__(self, root: ctk.CTk):
        self._root = root
        self._ready = False

        win = ctk.CTkToplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(fg_color=T.BG_ROOT)

        # Centre on screen
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw - self._W) // 2
        y = (sh - self._H) // 2
        win.geometry(f"{self._W}x{self._H}+{x}+{y}")
        self._win = win

        # ── Gold border frame ──────────────────────────────────────────────
        border = ctk.CTkFrame(
            win,
            fg_color=T.BG_CARD,
            corner_radius=T.CARD_RADIUS,
            border_color=T.PURPLE_PRIMARY,
            border_width=2,
        )
        border.pack(fill="both", expand=True, padx=2, pady=2)

        # ── Logo / title ───────────────────────────────────────────────────
        ctk.CTkLabel(
            border,
            text="⚔",
            font=ctk.CTkFont("Segoe UI", 36),
            text_color=T.PURPLE_PRIMARY,
        ).pack(pady=(28, 4))

        ctk.CTkLabel(
            border,
            text="LOL COACHING ASSISTANT",
            font=ctk.CTkFont("Segoe UI", 16, "bold"),
            text_color=T.TEXT_PRIMARY,
        ).pack()

        ctk.CTkLabel(
            border,
            text=f"Initializing AI Vision Engine…",
            font=T.font_sm(),
            text_color=T.TEXT_MUTED,
        ).pack(pady=(6, 0))

        # ── Progress bar ───────────────────────────────────────────────────
        bar_frame = ctk.CTkFrame(border, fg_color="transparent")
        bar_frame.pack(fill="x", padx=40, pady=(16, 6))

        self._bar = HextechProgressBar(
            bar_frame,
            bar_width=self._W - 80,
            bar_height=10,
            segments=24,
        )
        self._bar.pack(fill="x")
        self._bar.set_indeterminate(True)

        self._status = ctk.CTkLabel(
            border,
            text="Loading…",
            font=T.font_xs(),
            text_color=T.TEXT_FAINT,
        )
        self._status.pack(pady=(0, 20))

        # Force draw before returning
        win.update_idletasks()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        """Update the status text from any thread via root.after."""
        self._root.after(0, lambda: self._status.configure(text=text))

    def mark_ready(self) -> None:
        """Fill bar to 100% then close after 400 ms."""
        if self._ready:
            return
        self._ready = True
        self._root.after(0, self._finish)

    def _finish(self) -> None:
        try:
            self._bar.set_indeterminate(False)
            self._bar.set_progress(1.0)
            self._status.configure(text="Ready!", text_color=T.TEAL_ACCENT)
        except Exception:
            pass
        self._root.after(500, self._destroy)

    def _destroy(self) -> None:
        try:
            self._win.destroy()
        except Exception:
            pass
