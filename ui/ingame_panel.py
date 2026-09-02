"""
ui/ingame_panel.py — In-Game tab panel.

Displays live stats from the Live Client API and a 6-slot visual build planner
that fills progressively as the AI recommends complete items.

Visual improvements (v3):
  - Item slots use make_item_icon() → rounded corners + gold border when filled
  - 'Pop' animation when a slot is filled (brief scale effect via border flash)
  - CounterLabel for GOLD (animated numeric scroll)
  - KDA colour adaptive (teal / white / red based on ratio)
  - Fed alert includes circular champion icon (24×24)
  - Game time text turns orange after 25 min (late game phase)
  - GlowBorderFrame for the item recommendation card
"""
import customtkinter as ctk

import ui.theme as T
from ui.widgets import GlowBorderFrame, AnimatedBadge, CounterLabel, UndeterminedSlot
from services.image_cache import ImageCache
from core.event_bus import bus, EventBus
from models.build_plan import BuildPlan, SlotState

_SLOT_COUNT = 6
_LATE_GAME_SECONDS = 25 * 60   # 25 minutes


class InGamePanel(ctk.CTkFrame):
    """Tab panel shown during an active game."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._alert_pulse_active = False
        self._build()
        self.afficher_attente()

    def _construire_attente(self) -> ctk.CTkFrame:
        """Écran affiché tant qu'aucune partie n'est en cours."""
        cadre = ctk.CTkFrame(self, fg_color="transparent")

        centre = ctk.CTkFrame(cadre, fg_color="transparent")
        centre.place(relx=0.5, rely=0.45, anchor="center")

        ctk.CTkLabel(
            centre, text="⚔", font=ctk.CTkFont("Segoe UI", 46),
            text_color=T.BORDER_DEFAULT,
        ).pack(pady=(0, T.SP_3))
        ctk.CTkLabel(
            centre, text="EN ATTENTE D'UNE PARTIE", font=T.font_title(),
            text_color=T.TEXT_MUTED,
        ).pack()
        self._attente_detail = ctk.CTkLabel(
            centre,
            text="Le plan d'objets apparaîtra dès le début de la partie.",
            font=T.font_sm(), text_color=T.TEXT_FAINT,
        )
        self._attente_detail.pack(pady=(T.SP_2, 0))
        return cadre

    def afficher_attente(self, detail: str = "") -> None:
        """Bascule sur l'écran d'attente et masque les données de partie."""
        if detail:
            self._attente_detail.configure(text=detail)
        if self._contenu.winfo_manager():
            self._contenu.pack_forget()
        if not self._attente.winfo_manager():
            self._attente.pack(fill="both", expand=True)

    def afficher_partie(self) -> None:
        """Bascule sur les données de partie."""
        if self._attente.winfo_manager():
            self._attente.pack_forget()
        if not self._contenu.winfo_manager():
            self._contenu.pack(fill="both", expand=True)

    def _build(self) -> None:
        # Deux états exclusifs : l'attente et la partie. Les cartes de partie
        # vivent dans _contenu, qu'on masque d'un bloc hors partie plutôt que
        # de laisser traîner des chiffres périmés.
        self._attente = self._construire_attente()
        self._contenu = ctk.CTkFrame(self, fg_color="transparent")

        # ================================================================
        # Stats row — individual mini-cards
        # ================================================================
        stats_outer = ctk.CTkFrame(self._contenu, fg_color="transparent")
        stats_outer.pack(fill="x", padx=T.PADX_CARD, pady=(T.PADY_CARD_TOP, 8))
        for _col in range(5):
            stats_outer.grid_columnconfigure(_col, weight=1, uniform="stat")
        self._stat_col = 0

        def stat_card(icon: str, label: str, value: str = "—",
                      value_color: str = T.TEXT_PRIMARY,
                      animated: bool = False):
            """Create a fixed-size stat mini-card. Returns (card, value_label)."""
            card = T.make_card(stats_outer, width=1, height=90)
            card.grid(row=0, column=self._stat_col, sticky="nsew",
                      padx=(0, 6) if self._stat_col < 4 else 0)
            card.grid_propagate(False)
            self._stat_col += 1

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=12, pady=10)

            # Icon + label row
            hdr_row = ctk.CTkFrame(inner, fg_color="transparent")
            hdr_row.pack(anchor="w")
            ctk.CTkLabel(
                hdr_row, text=icon,
                font=ctk.CTkFont("Segoe UI", 10),
                text_color=T.TEXT_MUTED,
            ).pack(side="left")
            ctk.CTkLabel(
                hdr_row, text=f"  {label}",
                font=T.font_caps(),
                text_color=T.TEXT_MUTED,
            ).pack(side="left")

            if animated:
                lbl = CounterLabel(
                    inner, text=value,
                    font=T.font_lg(),
                    text_color=value_color,
                )
            else:
                lbl = ctk.CTkLabel(
                    inner, text=value,
                    font=T.font_lg(),
                    text_color=value_color,
                )
            lbl.pack(anchor="w", pady=(4, 0))
            return card, lbl

        _, self._champ_lbl  = stat_card("⚔", "CHAMPION", "—",    T.PURPLE_PRIMARY)
        self._champ_lbl.configure(compound="left", padx=4)
        _, self._kda_lbl    = stat_card("☠", "KDA",      "0/0/0", T.TEXT_PRIMARY)
        _, self._level_lbl  = stat_card("★", "NIVEAU",    "1",     T.GOLD_ACCENT)
        _, self._gold_lbl   = stat_card("◈", "OR",     "0",     T.GOLD_ACCENT, animated=True)
        self._time_card, self._time_lbl = stat_card("⏱", "TEMPS", "0:00", T.TEAL_ACCENT)

        # ================================================================
        # Current build row
        # ================================================================
        build_card = T.make_card(self._contenu)
        build_card.pack(fill="x", padx=T.PADX_CARD, pady=(0, 8))

        hdr = ctk.CTkFrame(build_card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 4))
        T.make_caps_label(hdr, "⬡  BUILD ACTUEL", T.TEXT_MUTED).pack(side="left")

        self._items_container = ctk.CTkFrame(build_card, width=1, height=1, fg_color="transparent")
        self._items_container.pack(anchor="w", padx=14, pady=(0, 10))
        self._item_labels: list[ctk.CTkLabel] = []

        # État vide : sans partie en cours, la carte restait un grand vide.
        self._build_empty = ctk.CTkLabel(
            build_card,
            text="Aucune partie en cours — lance une partie pour voir ton inventaire.",
            font=T.font_sm(),
            text_color=T.TEXT_FAINT,
            justify="left",
        )
        self._build_empty.pack(anchor="w", padx=14, pady=(0, 12))

        # ================================================================
        # Advantage / Gold Diff Row (inside build_card)
        # ================================================================
        T.make_divider(build_card).pack(fill="x", padx=14, pady=(0, 5))
        
        adv_hdr = ctk.CTkFrame(build_card, fg_color="transparent")
        adv_hdr.pack(fill="x", padx=14, pady=(4, 0))
        T.make_caps_label(adv_hdr, "⚖  AVANTAGE OR (OBJETS)", T.TEXT_MUTED).pack(side="left")

        self._ally_selector = ctk.CTkOptionMenu(
            adv_hdr,
            values=["(Moi)"],
            width=120,
            height=24,
            font=T.font_xs(),
            fg_color=T.BG_INPUT,
            button_color=T.BG_HOVER,
            button_hover_color=T.PURPLE_PRIMARY,
        )
        self._ally_selector.pack(side="right")

        self._adv_container = ctk.CTkFrame(build_card, fg_color="transparent")
        self._adv_container.pack(fill="x", padx=14, pady=(4, 10))
        
        self._adv_widgets: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        for i in range(5):
            col = ctk.CTkFrame(self._adv_container, fg_color="transparent")
            col.pack(side="left", padx=(0, 15))
            
            icon_lbl = ctk.CTkLabel(col, text="", fg_color="transparent")
            icon_lbl.pack(pady=(0, 2))
            
            diff_lbl = ctk.CTkLabel(col, text="—", font=T.font_xs())
            diff_lbl.pack()
            
            self._adv_widgets.append((icon_lbl, diff_lbl))

        # ================================================================
        # Fed enemy alert banner (hidden by default)
        # ================================================================
        self._alert_frame = ctk.CTkFrame(
            self._contenu,
            fg_color=T.RED_DIM,
            corner_radius=T.CARD_RADIUS,
            border_color=T.RED_DANGER,
            border_width=2,
        )
        self._alert_frame.pack(fill="x", padx=T.PADX_CARD, pady=(0, 8))
        self._alert_frame.pack_forget()

        alert_inner = ctk.CTkFrame(self._alert_frame, fg_color="transparent")
        alert_inner.pack(fill="x", padx=14, pady=(10, 10))

        # Header with icon placeholder
        alert_hdr = ctk.CTkFrame(alert_inner, fg_color="transparent")
        alert_hdr.pack(anchor="w", fill="x")

        ctk.CTkLabel(
            alert_hdr,
            text="⚠  ENNEMI EN FEU",
            font=T.font_title(),
            text_color=T.RED_DANGER,
        ).pack(side="left")

        self._alert_icon = ctk.CTkLabel(alert_hdr, text="", fg_color="transparent")
        self._alert_icon.pack(side="right", padx=(0, 4))

        self._alert_champ_lbl = ctk.CTkLabel(
            alert_inner,
            text="",
            font=T.font_md(),
            text_color=T.RED_DANGER,
        )
        self._alert_champ_lbl.pack(anchor="w", pady=(3, 0))

        # ================================================================
        # Item Recommendation — GlowBorderFrame + 6 slots (3×2 grid)
        # ================================================================
        self._rec_card = GlowBorderFrame(
            self._contenu,
            animate=True,
            glow_color=T.PURPLE_PRIMARY,
            dim_color=T.BORDER_DEFAULT,
            border_width=1,
        )
        self._rec_card.pack(
            fill="both", expand=True,
            padx=T.PADX_CARD, pady=(0, T.PADY_CARD_TOP),
        )

        rec_hdr = ctk.CTkFrame(self._rec_card, fg_color="transparent")
        rec_hdr.pack(fill="x", padx=14, pady=(12, 0))

        ctk.CTkLabel(
            rec_hdr,
            text="⬡  PLAN D'OBJETS",
            font=T.font_title(),
            text_color=T.PURPLE_PRIMARY,
        ).pack(side="left")

        self._refresh_btn = ctk.CTkButton(
            rec_hdr,
            text="⟳ Actualiser",
            font=T.font_sm(),
            width=80,
            height=24,
            corner_radius=4,
            fg_color=T.PURPLE_PRIMARY,
            hover_color=T.PURPLE_HOVER,
            text_color="#000000",
            command=lambda: bus.emit(EventBus.FORCE_ITEM_REFRESH),
        )
        self._refresh_btn.pack(side="right", padx=(0, 10))

        self._item_badge = AnimatedBadge(rec_hdr)
        self._item_badge.pack(side="right")

        T.make_divider(self._rec_card).pack(fill="x", padx=14, pady=(8, 10))

        # Dynamic Build Plan UI Container
        self._plan_container = ctk.CTkFrame(self._rec_card, width=1, height=1, fg_color="transparent")
        self._plan_container.pack(fill="x", padx=14, pady=(0, 8))

        # Legendary items (left side)
        self._legendary_frame = ctk.CTkFrame(self._plan_container, width=1, height=1, fg_color="transparent")
        self._legendary_frame.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Boots (right side)
        self._boots_frame = ctk.CTkFrame(self._plan_container, width=1, height=1, fg_color="transparent")
        self._boots_frame.pack(side="right")
        
        # We will keep references to the widgets to destroy/recreate them dynamically
        self._legendary_widgets = []
        self._boots_widget = None

        self._verdicts_frame = ctk.CTkFrame(self._rec_card, width=1, height=1, fg_color="transparent")

        # Status / streaming text below slots
        self._status_lbl = ctk.CTkLabel(
            self._rec_card,
            text="En attente d'une partie…",
            font=T.font_sm(),
            text_color=T.TEXT_FAINT,
            wraplength=480,
            justify="left",
        )
        self._status_lbl.pack(fill="x", padx=14, pady=(0, 10))

    # -----------------------------------------------------------------------
    # Public update API
    # -----------------------------------------------------------------------

    def update_player_stats(
        self,
        champion: str,
        kills: int,
        deaths: int,
        assists: int,
        level: int,
        gold: float,
        game_time_seconds: float,
        items: list[int],
        enemy_gold_diffs: list[tuple[str, float]] | None = None,
        allies: list[str] | None = None,
    ) -> None:
        if enemy_gold_diffs is None:
            enemy_gold_diffs = []

        self.afficher_partie()

        # Une partie tourne : on retire le message d'état vide.
        if self._build_empty is not None and self._build_empty.winfo_manager():
            self._build_empty.pack_forget()

        cache = ImageCache()

        # Champion
        champ_img = cache.get_champion_icon_round(champion, size=26, glow=False)
        self._champ_lbl.configure(text=f"  {champion or '—'}", image=champ_img)

        # KDA with adaptive colour
        kda_color = self._kda_color(kills, deaths, assists)
        self._kda_lbl.configure(
            text=f"{kills}/{deaths}/{assists}",
            text_color=kda_color,
        )

        self._level_lbl.configure(text=str(level))

        # Animated gold counter
        if isinstance(self._gold_lbl, CounterLabel):
            self._gold_lbl.set_value(int(gold))
        else:
            self._gold_lbl.configure(text=f"{gold:,.0f}")

        # Game time — late-game colour shift
        mins = int(game_time_seconds) // 60
        secs = int(game_time_seconds) % 60
        time_color = T.GOLD_ACCENT if game_time_seconds >= _LATE_GAME_SECONDS else T.TEAL_ACCENT
        self._time_lbl.configure(text=f"{mins}:{secs:02d}", text_color=time_color)

        # Current build row — rounded item icons
        for lbl in self._item_labels:
            lbl.destroy()
        self._item_labels.clear()

        if not items:
            lbl = ctk.CTkLabel(
                self._items_container,
                text="Aucun objet",
                font=T.font_sm(),
                text_color=T.TEXT_MUTED,
            )
            lbl.pack(side="left")
            self._item_labels.append(lbl)
        else:
            for item_id in items:
                img = cache.get_item_icon_by_id(item_id, size=30, is_filled=True)
                lbl = ctk.CTkLabel(
                    self._items_container,
                    text="",
                    image=img,
                    fg_color="transparent",
                )
                lbl.pack(side="left", padx=2)
                self._item_labels.append(lbl)


        # Update Ally Selector
        if allies:
            current = self._ally_selector.get()
            self._ally_selector.configure(values=allies)
            if current not in allies and allies:
                self._ally_selector.set(allies[0])

        # Update Advantage
        for i in range(5):
            icon_lbl, diff_lbl = self._adv_widgets[i]
            if i < len(enemy_gold_diffs):
                champ, diff = enemy_gold_diffs[i]
                img = cache.get_champion_icon_round(champ, size=24, glow=False)
                icon_lbl.configure(image=img)
                if diff > 0:
                    diff_lbl.configure(text=f"+{diff}g", text_color=T.TEAL_ACCENT)
                elif diff < 0:
                    diff_lbl.configure(text=f"{diff}g", text_color=T.RED_DANGER)
                else:
                    diff_lbl.configure(text="0g", text_color=T.TEXT_FAINT)
            else:
                icon_lbl.configure(image="")
                diff_lbl.configure(text="—", text_color=T.TEXT_FAINT)

    @staticmethod
    def _kda_color(kills: int, deaths: int, assists: int) -> str:
        ratio = (kills + assists) / max(deaths, 1)
        if ratio >= 3.0:
            return T.TEAL_ACCENT
        if ratio >= 1.5:
            return T.TEXT_PRIMARY
        return T.RED_DANGER

    def show_fed_alert(self, champion: str, kda: str) -> None:
        cache = ImageCache()
        icon = cache.get_champion_icon_round(champion, size=28, ring_color=T.RED_DANGER, glow=False)
        self._alert_icon.configure(image=icon if icon else None)
        self._alert_frame.pack(
            fill="x", padx=T.PADX_CARD, pady=(0, 8),
            before=self._slots_frame.master,
        )
        self._alert_champ_lbl.configure(text=f"{champion}  —  KDA: {kda}")
        if not self._alert_pulse_active:
            self._alert_pulse_active = True
            self._pulse_alert()

    def hide_fed_alert(self) -> None:
        self._alert_pulse_active = False
        self._alert_frame.pack_forget()

    def get_selected_ally(self) -> str:
        return self._ally_selector.get()

    def _pulse_alert(self) -> None:
        if not self._alert_pulse_active:
            return
        cur = self._alert_frame.cget("border_color")
        nxt = T.RED_DIM if cur == T.RED_DANGER else T.RED_DANGER
        try:
            self._alert_frame.configure(border_color=nxt)
        except Exception:
            return
        self.after(700, self._pulse_alert)

    def _create_slot_widget(self, parent, index: int, slot_data) -> ctk.CTkFrame:
        """Create a single slot widget based on SlotState."""
        if slot_data.state == SlotState.UNDETERMINED:
            w = UndeterminedSlot(parent, index, height=90, width=90)
            w.pack_propagate(False)
            w.start()
            return w
            
        # Determine colors based on state
        if slot_data.state == SlotState.OWNED_ON_PLAN:
            bg, border = T.BG_CARD_INNER, T.TEAL_ACCENT
        elif slot_data.state == SlotState.OWNED_OFF_PLAN:
            bg, border = T.BG_INPUT, T.TEXT_FAINT
        elif slot_data.state == SlotState.PENDING:
            bg, border = T.BG_CARD_INNER, T.TEXT_MUTED  # Dashed effect not easily supported natively, use muted
        else: # PLANNED / EMPTY
            bg, border = T.BG_INPUT, T.PURPLE_PRIMARY

        frame = ctk.CTkFrame(parent, fg_color=bg, border_color=border, border_width=1 if slot_data.state != SlotState.OWNED_ON_PLAN else 2, corner_radius=T.INNER_RADIUS, height=90, width=90)
        frame.pack_propagate(False)

        cache = ImageCache()
        img = None
        item_name = ""
        if slot_data.item_id:
            item_name = cache.get_item_name_by_id(slot_data.item_id) or f"ID:{slot_data.item_id}"
            img = cache.get_item_icon_rounded(item_name, size=52, is_filled=(slot_data.state in (SlotState.OWNED_ON_PLAN, SlotState.OWNED_OFF_PLAN)))

        icon_lbl = ctk.CTkLabel(frame, text="?" if not img else "", image=img if img else None, fg_color="transparent")
        icon_lbl.pack(pady=(8, 2))

        name_lbl = ctk.CTkLabel(
            frame,
            text=item_name or f"Slot {index + 1}",
            font=T.font_xs(),
            text_color=T.TEXT_PRIMARY if item_name else T.TEXT_FAINT,
            wraplength=80,
            justify="center",
        )
        name_lbl.pack(pady=(0, 8))
        return frame

    def set_build_plan(self, plan: BuildPlan, is_adc: bool = False) -> None:
        """Render the complete BuildPlan to the UI."""
        self._item_badge.set_done()
        self._verdicts_frame.pack_forget()
        self._plan_container.pack(fill="x", padx=14, pady=(0, 8))
        
        # 1. Clear existing
        for w in self._legendary_widgets:
            if isinstance(w, UndeterminedSlot): w.stop()
            w.destroy()
        self._legendary_widgets.clear()
        
        if self._boots_widget:
            if isinstance(self._boots_widget, UndeterminedSlot): self._boots_widget.stop()
            self._boots_widget.destroy()
            self._boots_widget = None
            
        # 2. Draw legendary slots
        # For a clean grid, let's just pack them horizontally (wrap if > 3)
        # Using grid inside _legendary_frame
        for col in range(3):
            self._legendary_frame.columnconfigure(col, weight=1)
            
        display_slots = list(plan.legendary_slots)
        if not is_adc:
            # If not ADC, they only have 6 slots total. Put boots in the main grid.
            display_slots.insert(1, plan.boots)
            display_slots = display_slots[:6]
            
        for i, s_data in enumerate(display_slots):
            w = self._create_slot_widget(self._legendary_frame, i, s_data)
            w.grid(row=i // 3, column=i % 3, padx=5, pady=5, sticky="nsew")
            self._legendary_widgets.append(w)
            
        # 3. Draw boots slot
        if is_adc:
            self._boots_widget = self._create_slot_widget(self._boots_frame, -1, plan.boots)
            self._boots_widget.pack(padx=5, pady=5)
        
        # Pulse the newest planned item if it just resolved?
        # A simple pop effect could be added here if needed.

    def clear_build_slots(self) -> None:
        """Reset all slots to empty state."""
        self._verdicts_frame.pack_forget()
        self._plan_container.pack(fill="x", padx=14, pady=(0, 8))
        for w in self._legendary_widgets:
            if isinstance(w, UndeterminedSlot): w.stop()
            w.destroy()
        self._legendary_widgets.clear()
        if self._boots_widget:
            if isinstance(self._boots_widget, UndeterminedSlot): self._boots_widget.stop()
            self._boots_widget.destroy()
            self._boots_widget = None
        self._item_badge.set_idle()

    def render_verdicts(self, verdicts: list[dict]) -> None:
        self._plan_container.pack_forget()
        self._verdicts_frame.pack(fill="x", padx=14, pady=(0, 8))
        
        for widget in self._verdicts_frame.winfo_children():
            widget.destroy()
            
        cache = ImageCache()
        
        for v in verdicts:
            reason = v.get("reason", "")
            if reason in ("prescrit", "net"):
                box = ctk.CTkFrame(self._verdicts_frame, fg_color=T.BG_CARD_INNER, border_color=T.PURPLE_PRIMARY, border_width=2, corner_radius=T.INNER_RADIUS)
                box.pack(fill="x", pady=5)
                
                img = cache.get_item_icon_rounded(v["item"], size=52, is_filled=True)
                lbl_icon = ctk.CTkLabel(box, image=img if img else None, text="" if img else "?")
                lbl_icon.pack(side="left", padx=10, pady=10)
                
                text_frame = ctk.CTkFrame(box, fg_color="transparent")
                text_frame.pack(side="left", fill="both", expand=True, padx=5, pady=10)
                
                title = "Core Item Prescrit" if reason == "prescrit" else "Recommandation Principale"
                ctk.CTkLabel(text_frame, text=title, font=T.font_xs(), text_color=T.PURPLE_PRIMARY).pack(anchor="w")
                ctk.CTkLabel(text_frame, text=v["item"], font=T.font_md(), text_color=T.TEXT_PRIMARY).pack(anchor="w")
                
            elif reason == "équivalents":
                row = ctk.CTkFrame(self._verdicts_frame, fg_color="transparent")
                row.pack(fill="x", pady=5)
                row.columnconfigure((0, 1), weight=1)
                
                for idx, item in enumerate(v.get("tied_items", [])[:2]):
                    box = ctk.CTkFrame(row, fg_color=T.BG_CARD_INNER, border_color=T.PURPLE_DIM, border_width=1, corner_radius=T.INNER_RADIUS)
                    box.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx==0 else 5, 5 if idx==0 else 0))
                    
                    img = cache.get_item_icon_rounded(item, size=40, is_filled=True)
                    ctk.CTkLabel(box, image=img if img else None, text="" if img else "?").pack(pady=(10, 2))
                    ctk.CTkLabel(box, text=item, font=T.font_xs(), text_color=T.TEXT_PRIMARY, wraplength=100, justify="center").pack(pady=(0, 10))
                    
            elif reason == "léger avantage":
                # Top item full width, alt item smaller
                box = ctk.CTkFrame(self._verdicts_frame, fg_color=T.BG_CARD_INNER, border_color=T.PURPLE_DIM, border_width=1, corner_radius=T.INNER_RADIUS)
                box.pack(fill="x", pady=5)
                
                img = cache.get_item_icon_rounded(v["item"], size=40, is_filled=True)
                lbl_icon = ctk.CTkLabel(box, image=img if img else None, text="" if img else "?")
                lbl_icon.pack(side="left", padx=10, pady=10)
                
                text_frame = ctk.CTkFrame(box, fg_color="transparent")
                text_frame.pack(side="left", fill="both", expand=True, padx=5, pady=10)
                
                ctk.CTkLabel(text_frame, text="Léger Avantage", font=T.font_xs(), text_color=T.TEXT_FAINT).pack(anchor="w")
                ctk.CTkLabel(text_frame, text=v["item"], font=T.font_sm(), text_color=T.TEXT_PRIMARY).pack(anchor="w")
                
                if v.get("alt"):
                    ctk.CTkLabel(text_frame, text=f"Alternative: {v['alt']}", font=T.font_xs(), text_color=T.TEXT_FAINT).pack(anchor="w")

    def set_status_text(self, text: str) -> None:
        self._status_lbl.configure(
            text=text,
            text_color=T.TEXT_SECONDARY if text else T.TEXT_FAINT,
        )

    # Backward-compat
    def set_item_advice(self, text: str, streaming: bool = False) -> None:
        self.set_status_text(text)
        if streaming:
            self._item_badge.set_live()
        else:
            self._item_badge.set_idle()

    def set_no_game(self) -> None:
        """Fin de partie : on vide tout et on repasse en attente."""
        self.afficher_attente()

        # L'avantage en or et le sélecteur d'allié gardaient les valeurs de la
        # partie précédente, affichées à côté de compteurs remis à zéro.
        for icone, diff in self._adv_widgets:
            icone.configure(image=None, text="")
            diff.configure(text="—", text_color=T.TEXT_FAINT)
        try:
            self._ally_selector.configure(values=["(Moi)"])
            self._ally_selector.set("(Moi)")
        except Exception:
            pass

        if self._build_empty is not None and not self._build_empty.winfo_manager():
            self._build_empty.pack(anchor="w", padx=14, pady=(0, 12))
        self._champ_lbl.configure(text="—", image=None)
        self._kda_lbl.configure(text="0/0/0", text_color=T.TEXT_PRIMARY)
        self._level_lbl.configure(text="1")
        if isinstance(self._gold_lbl, CounterLabel):
            self._gold_lbl.set_value(0)
        else:
            self._gold_lbl.configure(text="0")
        self._time_lbl.configure(text="0:00", text_color=T.TEAL_ACCENT)

        for lbl in self._item_labels:
            lbl.destroy()
        self._item_labels.clear()
        vide = ctk.CTkLabel(
            self._items_container,
            text="Aucun objet",
            font=T.font_sm(),
            text_color=T.TEXT_MUTED,
        )
        vide.pack(side="left")
        self._item_labels.append(vide)   # suivi, sinon il s'empile à chaque appel

        self.hide_fed_alert()
        self.clear_build_slots()
        self.set_status_text("En attente d'une partie…")
