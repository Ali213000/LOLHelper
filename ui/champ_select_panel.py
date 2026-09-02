"""
ui/champ_select_panel.py — Champ Select tab panel — v3

Layout:
  ┌─────────────────────────────────────────────────────────┐
  │  BAN PHASE   [×][×][×][×][×]   [×][×][×][×][×]        │
  ├─────────────────────────────────────────────────────────┤
  │  PICK PHASE  [1][2][3][4][5]   [1][2][3][4][5]        │
  │  (my slot = gold border)                                │
  ├─────────────────────────────────────────────────────────┤
  │  AI SUGGESTIONS   [◀]  1/3  [▶]   [🔄 Régénérer]      │
  │  [Icon 80px] NOM DU CHAMPION                            │
  │              Raison courte                              │
  ├─────────────────────────────────────────────────────────┤
  │  ⚡ AI COACHING ADVICE  (texte streaming)               │
  └─────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import queue
import hashlib
from PIL import Image, ImageDraw
import customtkinter as ctk

import ui.theme as T
from ui.widgets import GlowBorderFrame, AnimatedBadge
from services.image_cache import ImageCache

_ALLY_COLOR  = T.TEAL_ACCENT
_ENEMY_COLOR = T.RED_DANGER
_BAN_COLOR   = "#4a1a1a"
_SLOT_PICK_SIZE = 54
_SLOT_BAN_SIZE  = 30
_PICK_COUNT  = 5
_BAN_COUNT   = 5


class ChampSelectPanel(ctk.CTkFrame):
    """Full champ-select tab: live draft board + AI suggestions + advice."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._suggestions: list[str] = []
        self._reasons:     list[str] = []
        self._sug_index: int = 0
        self._build()

    # =========================================================================
    # Layout construction
    # =========================================================================

    def _build(self) -> None:
        # ── Ban phase card ────────────────────────────────────────────────
        ban_card = T.make_card(self, height=68)
        ban_card.pack(fill="x", padx=T.PADX_CARD, pady=(T.PADY_CARD_TOP, 4))
        ban_card.pack_propagate(False)

        ban_inner = ctk.CTkFrame(ban_card, fg_color="transparent")
        ban_inner.pack(fill="both", expand=True, padx=12, pady=8)

        T.make_caps_label(ban_inner, "BANS", T.TEXT_FAINT).pack(side="left", padx=(0, 8))

        ally_ban_row = ctk.CTkFrame(ban_inner, fg_color="transparent")
        ally_ban_row.pack(side="left")
        self._ally_ban_slots: list[ctk.CTkLabel] = []
        for _ in range(_BAN_COUNT):
            lbl = self._make_ban_slot(ally_ban_row, is_enemy=False)
            lbl.pack(side="left", padx=2)
            self._ally_ban_slots.append(lbl)

        T.make_divider(ban_inner, vertical=True).pack(side="left", fill="y", padx=10)

        enemy_ban_row = ctk.CTkFrame(ban_inner, fg_color="transparent")
        enemy_ban_row.pack(side="left")
        self._enemy_ban_slots: list[ctk.CTkLabel] = []
        for _ in range(_BAN_COUNT):
            lbl = self._make_ban_slot(enemy_ban_row, is_enemy=True)
            lbl.pack(side="left", padx=2)
            self._enemy_ban_slots.append(lbl)

        # ── Ban Suggestions Widget ─────────────────────────────────────────
        self._ban_sug_widget = BanSuggestionsWidget(self)
        self._ban_sug_widget.pack(fill="x", padx=T.PADX_CARD, pady=(0, 4))

        # ── Pick phase card ───────────────────────────────────────────────
        pick_card = T.make_card(self, height=100)
        pick_card.pack(fill="x", padx=T.PADX_CARD, pady=(0, 4))
        pick_card.pack_propagate(False)

        pick_inner = ctk.CTkFrame(pick_card, fg_color="transparent")
        pick_inner.pack(fill="both", expand=True, padx=12, pady=8)

        # Ally picks
        ally_pick_col = ctk.CTkFrame(pick_inner, fg_color="transparent")
        ally_pick_col.pack(side="left", fill="both", expand=True)
        T.make_caps_label(ally_pick_col, "ALLIES", _ALLY_COLOR).pack(anchor="w", pady=(0, 4))
        ally_slots_row = ctk.CTkFrame(ally_pick_col, fg_color="transparent")
        ally_slots_row.pack(anchor="w")

        self._ally_pick_slots:  list[ctk.CTkLabel] = []
        self._ally_name_labels: list[ctk.CTkLabel] = []
        self._my_pick_slot_idx: int = -1   # which ally slot belongs to me

        for i in range(_PICK_COUNT):
            col_frame = ctk.CTkFrame(ally_slots_row, fg_color="transparent")
            col_frame.pack(side="left", padx=3)
            icon_lbl, name_lbl = self._make_pick_slot(col_frame, is_enemy=False)
            icon_lbl.pack()
            name_lbl.pack()
            self._ally_pick_slots.append(icon_lbl)
            self._ally_name_labels.append(name_lbl)

        T.make_divider(pick_inner, vertical=True).pack(side="left", fill="y", padx=10)

        # Enemy picks
        enemy_pick_col = ctk.CTkFrame(pick_inner, fg_color="transparent")
        enemy_pick_col.pack(side="left", fill="both", expand=True)
        T.make_caps_label(enemy_pick_col, "ENEMIES", _ENEMY_COLOR).pack(anchor="w", pady=(0, 4))
        enemy_slots_row = ctk.CTkFrame(enemy_pick_col, fg_color="transparent")
        enemy_slots_row.pack(anchor="w")

        self._enemy_pick_slots:  list[ctk.CTkLabel] = []
        self._enemy_name_labels: list[ctk.CTkLabel] = []

        for _ in range(_PICK_COUNT):
            col_frame = ctk.CTkFrame(enemy_slots_row, fg_color="transparent")
            col_frame.pack(side="left", padx=3)
            icon_lbl, name_lbl = self._make_pick_slot(col_frame, is_enemy=True)
            icon_lbl.pack()
            name_lbl.pack()
            self._enemy_pick_slots.append(icon_lbl)
            self._enemy_name_labels.append(name_lbl)

        # ── Suggestion card ───────────────────────────────────────────────
        self._sug_card = GlowBorderFrame(
            self,
            animate=True,
            glow_color=T.GOLD_ACCENT,
            dim_color=T.BORDER_DEFAULT,
            border_width=1,
        )
        self._sug_card.pack(fill="x", padx=T.PADX_CARD, pady=(0, 4))

        # Header row
        sug_hdr = ctk.CTkFrame(self._sug_card, fg_color="transparent")
        sug_hdr.pack(fill="x", padx=14, pady=(10, 0))

        ctk.CTkLabel(
            sug_hdr,
            text="💡  SUGGESTIONS IA",
            font=T.font_title(),
            text_color=T.GOLD_ACCENT,
        ).pack(side="left")

        # Nav controls (right)
        nav_frame = ctk.CTkFrame(sug_hdr, fg_color="transparent")
        nav_frame.pack(side="right")

        self._regen_btn = ctk.CTkButton(
            nav_frame,
            text="🔄",
            width=32, height=28,
            fg_color="transparent",
            hover_color=T.BG_HOVER,
            text_color=T.TEXT_MUTED,
            font=ctk.CTkFont("Segoe UI", 14),
            corner_radius=T.INNER_RADIUS,
            command=self._on_regen,
        )
        self._regen_btn.pack(side="right", padx=(4, 0))

        self._next_btn = ctk.CTkButton(
            nav_frame,
            text="▶",
            width=28, height=28,
            fg_color=T.BG_CARD_INNER,
            hover_color=T.BG_HOVER,
            text_color=T.TEXT_PRIMARY,
            font=ctk.CTkFont("Segoe UI", 12),
            corner_radius=T.INNER_RADIUS,
            command=self._next_suggestion,
        )
        self._next_btn.pack(side="right", padx=2)

        self._sug_badge = ctk.CTkLabel(
            nav_frame,
            text="—/3",
            font=T.font_xs(),
            text_color=T.TEXT_FAINT,
        )
        self._sug_badge.pack(side="right", padx=4)

        self._prev_btn = ctk.CTkButton(
            nav_frame,
            text="◀",
            width=28, height=28,
            fg_color=T.BG_CARD_INNER,
            hover_color=T.BG_HOVER,
            text_color=T.TEXT_PRIMARY,
            font=ctk.CTkFont("Segoe UI", 12),
            corner_radius=T.INNER_RADIUS,
            command=self._prev_suggestion,
        )
        self._prev_btn.pack(side="right", padx=2)

        T.make_divider(self._sug_card).pack(fill="x", padx=14, pady=(8, 8))

        # Content row: icon + name/reason
        sug_content = ctk.CTkFrame(self._sug_card, fg_color="transparent")
        sug_content.pack(fill="x", padx=14, pady=(0, 12))

        self._sug_icon = ctk.CTkLabel(sug_content, text="", fg_color="transparent")
        self._sug_icon.pack(side="left", padx=(0, 14))

        sug_text_col = ctk.CTkFrame(sug_content, fg_color="transparent")
        sug_text_col.pack(side="left", fill="both", expand=True)

        self._sug_name = ctk.CTkLabel(
            sug_text_col,
            text="En attente…",
            font=ctk.CTkFont("Segoe UI", 22, "bold"),
            text_color=T.GOLD_ACCENT,
        )
        self._sug_name.pack(anchor="w")

        self._sug_reason = ctk.CTkLabel(
            sug_text_col,
            text="Les suggestions IA arrivent dès le début de la phase de draft.",
            font=T.font_sm(),
            text_color=T.TEXT_SECONDARY,
            wraplength=300,
            justify="left",
        )
        self._sug_reason.pack(anchor="w", pady=(4, 0))

        # ── Advice card ───────────────────────────────────────────────────
        self._advice_card = GlowBorderFrame(
            self,
            animate=True,
            glow_color=T.PURPLE_PRIMARY,
            dim_color=T.BORDER_DEFAULT,
            border_width=1,
        )
        self._advice_card.pack(
            fill="both", expand=True,
            padx=T.PADX_CARD, pady=(0, T.PADY_CARD_TOP),
        )

        advice_hdr = ctk.CTkFrame(self._advice_card, fg_color="transparent")
        advice_hdr.pack(fill="x", padx=14, pady=(12, 0))

        ctk.CTkLabel(
            advice_hdr,
            text="⚡  AI COACHING ADVICE",
            font=T.font_title(),
            text_color=T.PURPLE_PRIMARY,
        ).pack(side="left")

        self._badge = AnimatedBadge(advice_hdr)
        self._badge.pack(side="right", padx=4)

        T.make_divider(self._advice_card).pack(fill="x", padx=14, pady=8)

        self._advice_box = ctk.CTkTextbox(
            self._advice_card,
            font=T.font_md(),
            text_color=T.TEXT_PRIMARY,
            fg_color="transparent",
            border_width=0,
            wrap="word",
            state="disabled",
        )
        self._advice_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # =========================================================================
    # Slot factories
    # =========================================================================

    def _make_ban_slot(self, parent, is_enemy: bool) -> ctk.CTkLabel:
        """Create a single ban slot (30×30 image label with dark bg)."""
        lbl = ctk.CTkLabel(
            parent,
            text="✕" if not is_enemy else "✕",
            fg_color=T.BG_CARD_INNER,
            text_color=T.TEXT_FAINT,
            width=_SLOT_BAN_SIZE,
            height=_SLOT_BAN_SIZE,
            font=T.font_xs(),
            corner_radius=4,
        )
        return lbl

    def _make_pick_slot(
        self,
        parent,
        is_enemy: bool,
        is_mine: bool = False,
    ) -> tuple[ctk.CTkLabel, ctk.CTkLabel]:
        """Create a 54×54 pick slot icon + name label below."""
        border_color = T.GOLD_ACCENT if is_mine else (T.RED_DANGER if is_enemy else T.BORDER_DEFAULT)
        bg = T.BG_INPUT if is_mine else T.BG_CARD_INNER

        frame = ctk.CTkFrame(
            parent,
            fg_color=bg,
            border_color=border_color,
            border_width=2 if is_mine else 1,
            corner_radius=T.INNER_RADIUS,
            width=_SLOT_PICK_SIZE,
            height=_SLOT_PICK_SIZE,
        )
        frame.pack_propagate(False)
        frame.pack()

        icon_lbl = ctk.CTkLabel(frame, text="", fg_color="transparent")
        icon_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # Store frame ref on label for later border updates
        icon_lbl._slot_frame = frame  # type: ignore[attr-defined]

        name_lbl = ctk.CTkLabel(
            parent,
            text="",
            font=T.font_xs(),
            text_color=T.TEXT_MUTED,
            width=_SLOT_PICK_SIZE,
        )
        return icon_lbl, name_lbl

    # =========================================================================
    # Public update API (called by main_window from event bus)
    # =========================================================================

    def update_draft(
        self,
        state,   # ChampSelectState
    ) -> None:
        """Refresh the entire draft board from the new state."""
        cache = ImageCache()

        # ── Bans ──────────────────────────────────────────────────────────
        # Split draft_actions into ally bans / enemy bans (in order)
        ally_bans:  list[str] = []
        enemy_bans: list[str] = []

        for action in state.draft_actions:
            if action.is_ban and action.is_locked and action.champion_name:
                if action.is_ally:
                    ally_bans.append(action.champion_name)
                else:
                    enemy_bans.append(action.champion_name)

        self._fill_ban_row(self._ally_ban_slots,  ally_bans,  cache, is_enemy=False)
        self._fill_ban_row(self._enemy_ban_slots, enemy_bans, cache, is_enemy=True)

        # ── Pick slots ─────────────────────────────────────────────────────
        # Get ally pick slots from draft_actions (ordered)
        ally_picks:  list[tuple[str, bool]] = []   # (name, is_my_action)
        enemy_picks: list[str] = list(state.enemy_champion_names)

        for action in state.draft_actions:
            if action.is_ban:
                continue
            if action.is_ally:
                ally_picks.append((action.champion_name, action.is_my_action))

        # Pad to 5
        while len(ally_picks)  < _PICK_COUNT:
            ally_picks.append(("", False))
        while len(enemy_picks) < _PICK_COUNT:
            enemy_picks.append("")

        for i in range(_PICK_COUNT):
            champ, is_mine = ally_picks[i]
            self._update_pick_slot(
                self._ally_pick_slots[i],
                self._ally_name_labels[i],
                champ, cache,
                is_enemy=False,
                is_mine=is_mine,
            )

        for i in range(_PICK_COUNT):
            self._update_pick_slot(
                self._enemy_pick_slots[i],
                self._enemy_name_labels[i],
                enemy_picks[i], cache,
                is_enemy=True,
                is_mine=False,
            )

        # Highlight my current pick turn slot
        my_champ = state.my_champion_name
        if state.is_my_pick_turn:
            for i, (_, is_mine) in enumerate(ally_picks):
                if is_mine:
                    frame = self._ally_pick_slots[i]._slot_frame  # type: ignore[attr-defined]
                    try:
                        frame.configure(border_color=T.GOLD_ACCENT, border_width=2)
                    except Exception:
                        pass

    def _fill_ban_row(
        self,
        slots: list[ctk.CTkLabel],
        names: list[str],
        cache: "ImageCache",
        is_enemy: bool,
    ) -> None:
        for i, slot in enumerate(slots):
            if i < len(names) and names[i]:
                img = cache.get_champion_icon_round(names[i], size=_SLOT_BAN_SIZE, glow=False)
                slot.configure(
                    text="",
                    image=img if img else None,
                    fg_color=_BAN_COLOR,
                    text_color=T.TEXT_FAINT,
                )
            else:
                slot.configure(
                    text="✕",
                    image=None,
                    fg_color=T.BG_CARD_INNER,
                    text_color=T.TEXT_FAINT,
                )

    def _update_pick_slot(
        self,
        icon_lbl: ctk.CTkLabel,
        name_lbl: ctk.CTkLabel,
        champ: str,
        cache: "ImageCache",
        is_enemy: bool,
        is_mine: bool,
    ) -> None:
        frame = icon_lbl._slot_frame  # type: ignore[attr-defined]
        if champ:
            size = _SLOT_PICK_SIZE - 6
            ring = T.GOLD_ACCENT if is_mine else (_ENEMY_COLOR if is_enemy else _ALLY_COLOR)
            img = cache.get_champion_icon_round(champ, size=size, ring_color=ring, glow=is_mine)
            icon_lbl.configure(image=img if img else None, text="")
            name_lbl.configure(text=champ[:8], text_color=T.GOLD_ACCENT if is_mine else T.TEXT_MUTED)
            bc = T.GOLD_ACCENT if is_mine else (T.RED_DANGER if is_enemy else T.TEAL_ACCENT)
            try:
                frame.configure(fg_color=T.BG_INPUT if is_mine else T.BG_CARD_INNER, border_color=bc)
            except Exception:
                pass
        else:
            icon_lbl.configure(image=None, text="")
            name_lbl.configure(text="")
            bc = T.GOLD_ACCENT if is_mine else (T.RED_DANGER if is_enemy else T.BORDER_DEFAULT)
            try:
                frame.configure(fg_color=T.BG_CARD_INNER, border_color=bc, border_width=1)
            except Exception:
                pass

    # =========================================================================
    # Setters for AI
    # =========================================================================

    def set_ban_suggestions(self, payload: dict) -> None:
        """Called by main thread or background thread. Queue-safe."""
        self._ban_sug_widget.enqueue(payload)

    def set_suggestions(self, suggestions: list[str], reasons: list[str]) -> None:
        """Called from main_window when CHAMP_SUGGESTIONS_READY fires."""
        self._suggestions = suggestions
        self._reasons     = reasons
        self._sug_index   = 0
        self._render_suggestion()

    def _render_suggestion(self) -> None:
        if not self._suggestions:
            self._sug_name.configure(text="En attente…", text_color=T.TEXT_MUTED)
            self._sug_reason.configure(text="Génération des suggestions en cours…")
            self._sug_icon.configure(image=None)
            self._sug_badge.configure(text="—/3")
            return

        idx = self._sug_index
        name   = self._suggestions[idx] if idx < len(self._suggestions) else ""
        reason = self._reasons[idx]     if idx < len(self._reasons)     else ""

        cache = ImageCache()
        img = cache.get_champion_icon_round(name, size=80, glow=True)

        self._sug_icon.configure(image=img if img else None)
        self._sug_name.configure(text=name or "—", text_color=T.GOLD_ACCENT)
        self._sug_reason.configure(text=reason or "")
        self._sug_badge.configure(text=f"{idx + 1}/{len(self._suggestions)}")

    def _next_suggestion(self) -> None:
        if self._suggestions:
            self._sug_index = (self._sug_index + 1) % len(self._suggestions)
            self._render_suggestion()

    def _prev_suggestion(self) -> None:
        if self._suggestions:
            self._sug_index = (self._sug_index - 1) % len(self._suggestions)
            self._render_suggestion()

    def _on_regen(self) -> None:
        """Signal the main window to request new suggestions."""
        self._sug_name.configure(text="Génération…", text_color=T.TEXT_MUTED)
        self._sug_reason.configure(text="Nouvelle tier list en cours…")
        self._sug_badge.configure(text="…/3")
        self._suggestions = []
        self._reasons = []
        # Fire the regenerate callback if one was registered
        if callable(self._regen_callback):
            self._regen_callback()

    def set_regen_callback(self, cb) -> None:
        """Register a callback to fire when the 🔄 button is pressed."""
        self._regen_callback = cb

    # Initialize with a no-op
    _regen_callback = lambda self: None  # noqa: E731

    # =========================================================================
    # Advice box
    # =========================================================================

    def set_advice(self, text: str, streaming: bool = False) -> None:
        self._advice_box.configure(state="normal")
        self._advice_box.delete("1.0", "end")
        self._advice_box.insert("end", text)
        self._advice_box.configure(state="disabled")
        self._advice_box.see("end")
        if streaming:
            self._badge.set_live()
        else:
            self._badge.set_done()

    def set_waiting(self) -> None:
        self.set_advice("En attente du début de la phase de sélection…")
        self._badge.set_idle()
        self._suggestions = []
        self._reasons = []
        self._render_suggestion()

# =============================================================================
# Ban Suggestions Widget (Queue-Safe Polling)
# =============================================================================

class BanSuggestionsWidget(ctk.CTkFrame):
    """Component handling background Ban suggestions updates securely via Queue."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._queue = queue.Queue()
        self._last_sig = ""
        self._ban_images = []  # Keep references to CTkImages to avoid GC collection

        self._build()
        self._poll()

    def _build(self):
        from ui.widgets import GlowBorderFrame
        self._card = GlowBorderFrame(
            self, animate=True, glow_color=T.RED_DANGER, dim_color=T.BORDER_DEFAULT, border_width=1
        )
        self._card.pack(fill="x", expand=True)
        
        hdr = ctk.CTkFrame(self._card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            hdr, text="🚫  BAN SUGGESTIONS", font=T.font_title(), text_color=T.RED_DANGER
        ).pack(side="left")
        
        T.make_divider(self._card).pack(fill="x", padx=14, pady=(8, 8))
        
        self._slots_frame = ctk.CTkFrame(self._card, fg_color="transparent")
        self._slots_frame.pack(fill="x", padx=14, pady=(0, 12))

        self._slots = []
        for _ in range(3):
            col = ctk.CTkFrame(self._slots_frame, fg_color="transparent")
            col.pack(side="left", expand=True, fill="both", padx=4)
            icon = ctk.CTkLabel(col, text="✕", font=T.font_md(), text_color=T.TEXT_FAINT, width=64, height=64, fg_color=T.BG_INPUT, corner_radius=8)
            icon.pack(anchor="center")
            name = ctk.CTkLabel(col, text="---", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=T.TEXT_PRIMARY)
            name.pack(anchor="center", pady=(4, 0))
            reason = ctk.CTkLabel(col, text="---", font=T.font_sm(), text_color=T.TEXT_SECONDARY, wraplength=120)
            reason.pack(anchor="center")
            self._slots.append((icon, name, reason))

    def enqueue(self, payload: dict):
        self._queue.put(payload)

    def _poll(self):
        try:
            while True:
                payload = self._queue.get_nowait()
                self._render(payload)
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _render(self, payload: dict):
        sig = hashlib.md5(str(payload).encode()).hexdigest()
        if sig == self._last_sig:
            return
        self._last_sig = sig

        suggestions = payload.get("suggestions", [])
        reasons = payload.get("reasons", [])
        
        # Clear old images
        self._ban_images.clear()

        for i, slot in enumerate(self._slots):
            icon_lbl, name_lbl, reason_lbl = slot
            if i < len(suggestions):
                cid = suggestions[i]
                rsn = reasons[i] if i < len(reasons) else ""
                
                # Build rounded image via PIL on main thread
                from services.image_cache import ImageCache
                raw_img = ImageCache()._load_pil_champion(cid)
                img = self._create_rounded_image(raw_img, cid)
                self._ban_images.append(img)
                
                icon_lbl.configure(image=img, text="")
                name_lbl.configure(text=cid)
                reason_lbl.configure(text=rsn)
            else:
                icon_lbl.configure(image="", text="✕")
                name_lbl.configure(text="---")
                reason_lbl.configure(text="---")

    def _create_rounded_image(self, raw_img: Image.Image, fallback_name: str, size: int = 64) -> ctk.CTkImage:
        try:
            if raw_img:
                im = raw_img.convert("RGBA")
            else:
                raise ValueError("No image")
        except Exception:
            im = Image.new("RGBA", (size, size), T.BG_INPUT)
            d = ImageDraw.Draw(im)
            initials = fallback_name[:2].upper()
            d.text((size//2, size//2), initials, fill=T.TEXT_FAINT, anchor="mm")
            
        im = im.resize((size, size), Image.Resampling.LANCZOS)
        
        # Make rounded mask
        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, size, size), radius=8, fill=255)
        
        im.putalpha(mask)
        return ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
