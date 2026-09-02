"""
ui/widgets.py — Reusable premium custom widgets for the LoL Coaching Assistant.

Widgets:
  GlowBorderFrame     — Card frame with an animated breathing gold border
  AnimatedBadge       — Pulsing status badge (LIVE / DONE / IDLE)
  ToastNotification   — Slide-up/slide-down notification popup
  CounterLabel        — Numeric label that animates changes frame-by-frame
  HextechProgressBar  — Segmented Canvas progress bar (hextech style)
  ClockLabel          — Self-updating local time label (HH:MM:SS)
"""
from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from typing import Optional

import customtkinter as ctk

import ui.theme as T


# ---------------------------------------------------------------------------
# GlowBorderFrame — card with breathing animated border
# ---------------------------------------------------------------------------

class GlowBorderFrame(ctk.CTkFrame):
    """
    A CTkFrame whose border colour pulses between a dim and a bright gold,
    creating a subtle 'breathing glow' effect.

    Usage:
        card = GlowBorderFrame(parent, animate=True)
        card.stop_glow()   # when panel is hidden
        card.start_glow()  # when panel is shown again
    """

    _PERIOD_MS  = 1800   # full breath cycle (ms)
    _STEPS      = 36     # frames per cycle
    _STEP_MS    = _PERIOD_MS // _STEPS

    def __init__(
        self,
        parent,
        animate: bool = True,
        glow_color: str = T.PURPLE_PRIMARY,
        dim_color:  str = T.BORDER_DEFAULT,
        **kwargs,
    ):
        kwargs.setdefault("fg_color",      T.BG_CARD)
        kwargs.setdefault("corner_radius", T.CARD_RADIUS)
        kwargs.setdefault("border_width",  1)
        kwargs.setdefault("border_color",  dim_color)
        super().__init__(parent, **kwargs)

        self._glow_color = glow_color
        self._dim_color  = dim_color
        self._glow_rgb   = _hex_to_rgb_f(glow_color)
        self._dim_rgb    = _hex_to_rgb_f(dim_color)
        self._step       = 0
        self._job: Optional[str] = None
        self._active     = False

        if animate:
            self.start_glow()

    def start_glow(self) -> None:
        if self._active:
            return
        self._active = True
        self._tick()

    def stop_glow(self) -> None:
        self._active = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self.configure(border_color=self._dim_color)

    def _tick(self) -> None:
        if not self._active:
            return
        # Sine wave: 0 → 1 → 0 over _STEPS steps
        t = (1 - math.cos(2 * math.pi * self._step / self._STEPS)) / 2
        r = int(self._dim_rgb[0] + (self._glow_rgb[0] - self._dim_rgb[0]) * t)
        g = int(self._dim_rgb[1] + (self._glow_rgb[1] - self._dim_rgb[1]) * t)
        b = int(self._dim_rgb[2] + (self._glow_rgb[2] - self._dim_rgb[2]) * t)
        color = f"#{r:02x}{g:02x}{b:02x}"
        try:
            self.configure(border_color=color)
        except Exception:
            return
        self._step = (self._step + 1) % self._STEPS
        self._job = self.after(self._STEP_MS, self._tick)


# ---------------------------------------------------------------------------
# AnimatedBadge — pulsing text badge
# ---------------------------------------------------------------------------

class AnimatedBadge(ctk.CTkFrame):
    """
    A rounded badge that pulses its text colour when in 'live' mode.

    States:
        set_live()   — gold pulsing (streaming)
        set_done()   — teal static (complete)
        set_idle()   — dim static (waiting)
    """

    _PULSE_MS = 600

    def __init__(self, parent, **kwargs):
        super().__init__(
            parent,
            fg_color=T.BG_CARD_INNER,
            corner_radius=4,
            **kwargs,
        )
        self._label = ctk.CTkLabel(
            self, text="IDLE",
            font=T.font_xs(),
            text_color=T.TEXT_FAINT,
        )
        self._label.pack(padx=8, pady=2)
        self._pulsing = False
        self._job: Optional[str] = None

    def set_live(self) -> None:
        self._pulsing = True
        self._label.configure(text="● LIVE")
        self.configure(fg_color=T.PURPLE_DIM)
        if not self._job:
            self._pulse()

    def set_done(self) -> None:
        self._pulsing = False
        self._cancel()
        self._label.configure(text="✓ DONE", text_color=T.TEAL_ACCENT)
        self.configure(fg_color=T.TEAL_DIM)

    def set_idle(self) -> None:
        self._pulsing = False
        self._cancel()
        self._label.configure(text="IDLE", text_color=T.TEXT_FAINT)
        self.configure(fg_color=T.BG_CARD_INNER)

    def _pulse(self) -> None:
        if not self._pulsing:
            return
        cur = self._label.cget("text_color")
        nxt = T.PURPLE_GLOW if cur == T.TEXT_FAINT else T.TEXT_FAINT
        try:
            self._label.configure(text_color=nxt)
        except Exception:
            return
        self._job = self.after(self._PULSE_MS, self._pulse)

    def _cancel(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


# ---------------------------------------------------------------------------
# ToastNotification — slide-up notification
# ---------------------------------------------------------------------------

class ToastNotification:
    """
    A borderless popup that slides up from the bottom-right corner,
    stays visible for `duration_ms`, then slides back down.

    Styles: "success" (teal) | "warning" (gold) | "danger" (red) | "info" (navy)

    Usage (from main thread or via root.after):
        ToastNotification(root, "✅ LCU Connected", style="success")
    """

    _W  = 320
    _H  = 56
    _MARGIN  = 20
    _FRAMES  = 18
    _STEP_MS = 14

    _STYLES = {
        "success": (T.TEAL_DIM,    T.TEAL_ACCENT,  "✅"),
        "warning": (T.PURPLE_DIM,  T.PURPLE_PRIMARY,"⚠"),
        "danger":  (T.RED_DIM,     T.RED_DANGER,    "🔴"),
        "info":    (T.BG_CARD,     T.TEXT_SECONDARY,"ℹ"),
    }

    def __init__(
        self,
        root: ctk.CTk,
        message: str,
        style: str = "info",
        duration_ms: int = 3000,
    ):
        self._root = root
        self._duration_ms = duration_ms
        bg, accent, icon = self._STYLES.get(style, self._STYLES["info"])

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()

        self._x_final = sw - self._W - self._MARGIN
        self._y_show  = sh - self._H - self._MARGIN - 60   # 60 = taskbar approx
        self._y_hide  = sh + self._H

        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{self._W}x{self._H}+{self._x_final}+{self._y_hide}")
        win.configure(bg=bg)
        self._win = win

        # Border
        border = tk.Frame(win, bg=accent, padx=1, pady=1)
        border.pack(fill="both", expand=True)

        inner = tk.Frame(border, bg=bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(
            inner, text=f"  {icon}  {message}",
            bg=bg, fg=accent,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="both", expand=True, padx=8)

        # Slide in
        self._slide_in()

    def _slide_in(self, frame: int = 0) -> None:
        t = frame / self._FRAMES
        t_ease = 1 - (1 - t) ** 3    # ease-out cubic
        y = int(self._y_hide + (self._y_show - self._y_hide) * t_ease)
        try:
            self._win.geometry(f"{self._W}x{self._H}+{self._x_final}+{y}")
        except Exception:
            return
        if frame < self._FRAMES:
            self._root.after(self._STEP_MS, lambda: self._slide_in(frame + 1))
        else:
            self._root.after(self._duration_ms, self._slide_out)

    def _slide_out(self, frame: int = 0) -> None:
        t = frame / self._FRAMES
        t_ease = t ** 2   # ease-in quadratic
        y = int(self._y_show + (self._y_hide - self._y_show) * t_ease)
        try:
            self._win.geometry(f"{self._W}x{self._H}+{self._x_final}+{y}")
        except Exception:
            return
        if frame < self._FRAMES:
            self._root.after(self._STEP_MS, lambda: self._slide_out(frame + 1))
        else:
            try:
                self._win.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CounterLabel — animated numeric counter
# ---------------------------------------------------------------------------

class CounterLabel(ctk.CTkLabel):
    """
    A CTkLabel that smoothly animates numeric value changes.
    Non-numeric values are set immediately without animation.

    Usage:
        lbl = CounterLabel(parent, font=T.font_lg(), text_color=T.GOLD_ACCENT)
        lbl.set_value(4230)   # sets to 4230
        lbl.set_value(4890)   # animates 4230 → 4890 over 20 frames
    """

    _FRAMES  = 20
    _STEP_MS = 25   # 20 × 25ms = 500ms total

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("text", "0")
        super().__init__(parent, **kwargs)
        self._current: float = 0
        self._target:  float = 0
        self._job: Optional[str] = None
        self._frame: int = 0

    def set_value(self, value, suffix: str = "") -> None:
        """Set a new value. Animates if numeric, immediate if text."""
        self._suffix = suffix
        try:
            target = float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            # Non-numeric: set immediately
            self.configure(text=str(value))
            return

        self._start   = self._current
        self._target  = target
        self._frame   = 0
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._tick()

    def _tick(self) -> None:
        self._frame += 1
        t = self._frame / self._FRAMES
        t_ease = 1 - (1 - t) ** 2   # ease-out quad
        val = self._start + (self._target - self._start) * min(t_ease, 1.0)
        self._current = val

        # Format: integer if target is int-like
        if self._target == int(self._target):
            text = f"{int(round(val)):,}{self._suffix}"
        else:
            text = f"{val:.1f}{self._suffix}"

        try:
            self.configure(text=text)
        except Exception:
            return

        if self._frame < self._FRAMES:
            self._job = self.after(self._STEP_MS, self._tick)
        else:
            self._current = self._target


# ---------------------------------------------------------------------------
# HextechProgressBar — segmented CTkFrame bar (no Canvas, CTkToplevel-safe)
# ---------------------------------------------------------------------------

class HextechProgressBar(ctk.CTkFrame):
    """
    A segmented progress bar styled after the LoL hextech aesthetic.
    Implemented with CTkFrame children — no tk.Canvas, works inside CTkToplevel.

    - N segments separated by 2px gaps
    - Filled segments animate from left to right
    - Color shifts from gold → teal as progress increases

    Usage:
        bar = HextechProgressBar(parent, bar_width=400, bar_height=10, segments=24)
        bar.set_progress(0.68)        # 0.0 → 1.0
        bar.set_indeterminate(True)   # bouncing animation
    """

    _ANIM_MS = 40

    def __init__(self, parent, bar_width: int = 400, bar_height: int = 10,
                 segments: int = 24, **kwargs):
        kwargs.setdefault("fg_color", T.BG_ROOT)
        kwargs.setdefault("corner_radius", 0)
        super().__init__(parent, width=bar_width, height=bar_height, **kwargs)
        self.pack_propagate(False)

        self._segments = segments
        self._progress = 0.0
        self._indeterminate = False
        self._ind_pos = 0.0
        self._ind_dir = 1
        self._job: Optional[str] = None

        # Build segment frames
        self._segs: list[ctk.CTkFrame] = []
        for i in range(segments):
            seg = ctk.CTkFrame(
                self,
                height=bar_height,
                fg_color=T.BG_CARD_INNER,
                corner_radius=2,
            )
            seg.pack(side="left", fill="y", expand=True, padx=(0, 2))
            self._segs.append(seg)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_progress(self, value: float) -> None:
        self._indeterminate = False
        self._cancel()
        self._progress = max(0.0, min(1.0, value))
        self._redraw_static()

    def set_indeterminate(self, active: bool) -> None:
        self._indeterminate = active
        if active:
            self._cancel()
            self._ind_pos = 0.0
            self._ind_dir = 1
            self._animate()
        else:
            self._cancel()
            self._redraw_static()

    # ── Drawing ─────────────────────────────────────────────────────────────

    def _redraw_static(self) -> None:
        filled = round(self._progress * self._segments)
        for i, seg in enumerate(self._segs):
            color = self._seg_color(i / self._segments) if i < filled else T.BG_CARD_INNER
            try:
                seg.configure(fg_color=color)
            except Exception:
                pass

    def _redraw_indeterminate(self) -> None:
        window = max(2, self._segments // 4)
        center = self._ind_pos * (self._segments - 1)
        for i, seg in enumerate(self._segs):
            dist = abs(i - center)
            if dist <= window / 2:
                t = 1 - (dist / (window / 2))
                color = self._seg_color(t)
            else:
                color = T.BG_CARD_INNER
            try:
                seg.configure(fg_color=color)
            except Exception:
                pass

    def _seg_color(self, t: float) -> str:
        gold = _hex_to_rgb_f(T.PURPLE_PRIMARY)
        teal = _hex_to_rgb_f(T.TEAL_ACCENT)
        r = int(gold[0] + (teal[0] - gold[0]) * t)
        g = int(gold[1] + (teal[1] - gold[1]) * t)
        b = int(gold[2] + (teal[2] - gold[2]) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _animate(self) -> None:
        if not self._indeterminate:
            return
        self._ind_pos += self._ind_dir * 0.05
        if self._ind_pos >= 1.0:
            self._ind_pos, self._ind_dir = 1.0, -1
        elif self._ind_pos <= 0.0:
            self._ind_pos, self._ind_dir = 0.0, 1
        self._redraw_indeterminate()
        self._job = self.after(self._ANIM_MS, self._animate)

    def _cancel(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


# ---------------------------------------------------------------------------
# ClockLabel — auto-updating local time
# ---------------------------------------------------------------------------

class ClockLabel(ctk.CTkLabel):
    """
    A self-updating label showing local time (HH:MM).
    Updates every 30 seconds.
    """

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("font", T.font_xs())
        kwargs.setdefault("text_color", T.TEXT_FAINT)
        super().__init__(parent, text=self._time_str(), **kwargs)
        self._update()

    def _time_str(self) -> str:
        import datetime
        return datetime.datetime.now().strftime("%H:%M")

    def _update(self) -> None:
        try:
            self.configure(text=self._time_str())
        except Exception:
            return
        self.after(30_000, self._update)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb_f(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16))

def _lerp_hex(a: str, b: str, t: float) -> str:
    ca = tuple(int(a.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    cb = tuple(int(b.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))


# ---------------------------------------------------------------------------
# UndeterminedSlot — animated thinking slot for the build plan
# ---------------------------------------------------------------------------

class UndeterminedSlot(ctk.CTkFrame):
    PULSE_MS = 60

    def __init__(self, master, index, **kw):
        kw.setdefault("fg_color", "#1B2129")
        kw.setdefault("corner_radius", T.INNER_RADIUS)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", T.BORDER_DEFAULT)
        super().__init__(master, **kw)
        self.index = index
        self._phase = 0
        self._running = False
        
        # We add an icon label and a name label to match standard slot structure
        self.icon_lbl = ctk.CTkLabel(self, text="?", fg_color="transparent", text_color="#5A6472", font=T.font_lg())
        self.icon_lbl.pack(pady=(8, 2))
        
        self.name_lbl = ctk.CTkLabel(
            self,
            text=f"Slot {index + 1}",
            font=T.font_xs(),
            text_color=T.TEXT_FAINT,
            wraplength=115,
            justify="center",
        )
        self.name_lbl.pack(pady=(0, 8))

    def start(self):
        if self._running: return
        self._running = True
        self._pulse()

    def _pulse(self):
        if not self._running: return
        # Sine wave interpolation
        t = (math.sin(self._phase * 0.08) + 1) / 2
        try:
            self.configure(fg_color=_lerp_hex("#1B2129", "#2E3A48", t))
        except Exception:
            return
        self._phase += 1
        self._after_id = self.after(self.PULSE_MS, self._pulse)

    def stop(self):
        self._running = False
        if hasattr(self, "_after_id"):
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            del self._after_id

    def resolve_animated(self, item_name: str, icon=None):
        self.stop()
        self.configure(fg_color=T.BG_INPUT, border_color=T.PURPLE_PRIMARY)
        self.icon_lbl.configure(text="" if icon else "?", image=icon if icon else None)
        self.name_lbl.configure(text=item_name, text_color=T.TEXT_PRIMARY)

