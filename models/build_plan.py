from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

class SlotState(Enum):
    OWNED_ON_PLAN  = "green"     # acheté, était recommandé
    OWNED_OFF_PLAN = "grey"      # acheté, hors plan → déclenche recalcul
    PLANNED        = "normal"    # recommandation à venir
    PENDING        = "dashed"    # prévu mais pas encore accessible
    UNDETERMINED   = "loading"   # en cours de décision (UI pulse)
    EMPTY          = "empty"

@dataclass
class Slot:
    index: int
    state: SlotState
    item_id: Optional[int] = None
    reason: str = ""             # "prescrit", "anti-soin", "départage"
    confidence: float = 0.0
    affordable: bool = False     # l'or actuel suffit-il ?
    alternatives: list[int] = field(default_factory=list)

@dataclass
class BuildPlan:
    legendary_slots: list[Slot]  # 5, ou 6 si ADC quête finie
    boots: Slot
    boots_tier: int = 1              # 1, 2 ou 3
    boots_in_quest_slot: bool = False    # True → capacité +1
    recalc_count: int = 0
    
    def get_capacity(self, role: str, quest_done: bool) -> int:
        return 7 if (role == "ADC" and quest_done) else 6
        
    def classify_purchase(self, item_id: int) -> SlotState:
        """Détermine si un achat dévie du plan ou s'il s'agit d'un réordonnancement."""
        planned = {s.item_id for s in self.legendary_slots if s.state == SlotState.PLANNED and s.item_id is not None}
        
        # Cas spécial pour les bottes (tier 2 ou 3)
        if self.boots.item_id == item_id:
            return SlotState.OWNED_ON_PLAN
            
        if item_id in planned:
            return SlotState.OWNED_ON_PLAN       # vert, même hors ordre
            
        return SlotState.OWNED_OFF_PLAN          # gris, vraie déviation

    def lock(self, item_id: int, state: SlotState):
        """Verrouille un achat dans le plan."""
        # 1. Chercher si c'est les bottes
        if self.boots.item_id == item_id or state == SlotState.OWNED_ON_PLAN and self.boots.state == SlotState.PLANNED:
            # Simplification: Si c'est l'achat des bottes
            if self.boots.item_id == item_id:
                self.boots.state = state
                return

        # 2. Chercher dans les légendaires (réordonnancement)
        for s in self.legendary_slots:
            if s.item_id == item_id and s.state == SlotState.PLANNED:
                s.state = state
                return
                
        # 3. Déviation (item non prévu) : on écrase le premier slot prévu
        # ou le prochain slot libre si tout est déjà rempli
        for s in self.legendary_slots:
            if s.state in (SlotState.PLANNED, SlotState.UNDETERMINED, SlotState.EMPTY):
                s.item_id = item_id
                s.state = state
                return
