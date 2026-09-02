import pytest
from models.build_plan import BuildPlan, Slot, SlotState

def test_adc_full_sequence():
    """
    Simule la progression d'un ADC de 0 à 6 objets complets + bottes.
    Vérifie la classification des achats via classify_purchase().
    """
    # Plan initial généré par le coaching engine
    plan = BuildPlan(
        boots=Slot(index=-1, state=SlotState.PLANNED, item_id=3006), # Berserker
        legendary_slots=[
            Slot(index=0, state=SlotState.PLANNED, item_id=3153), # BoRK
            Slot(index=1, state=SlotState.PLANNED, item_id=3031), # IE
            Slot(index=2, state=SlotState.PLANNED, item_id=3046), # Phantom Dancer
            Slot(index=3, state=SlotState.PLANNED, item_id=3072), # Bloodthirster
            Slot(index=4, state=SlotState.PLANNED, item_id=3036), # LDR
            Slot(index=5, state=SlotState.PLANNED, item_id=3026), # GA
        ]
    )

    # 1. Achat des bottes prévu
    assert plan.classify_purchase(3006) == SlotState.OWNED_ON_PLAN
    plan.lock(3006, SlotState.OWNED_ON_PLAN)
    assert plan.boots.state == SlotState.OWNED_ON_PLAN

    # 2. Achat du premier item (prévu)
    assert plan.classify_purchase(3153) == SlotState.OWNED_ON_PLAN
    plan.lock(3153, SlotState.OWNED_ON_PLAN)
    assert plan.legendary_slots[0].state == SlotState.OWNED_ON_PLAN

    # 3. Achat d'un item non prévu (déviation : par exemple Runaan 3085)
    assert plan.classify_purchase(3085) == SlotState.OWNED_OFF_PLAN
    plan.lock(3085, SlotState.OWNED_OFF_PLAN)
    # Runaan a remplacé IE (qui était en index 1)
    assert plan.legendary_slots[1].item_id == 3085
    assert plan.legendary_slots[1].state == SlotState.OWNED_OFF_PLAN

    # 4. Achat de IE plus tard (prévu, mais on a dévié entre temps)
    # IE est toujours dans planned car il a été poussé ou est toujours là 
    # Wait, dans BuildPlan.lock(), "déviation : on écrase le premier slot prévu".
    # Donc IE (index 1) a été écrasé. IE n'est plus dans le plan.
    assert plan.classify_purchase(3031) == SlotState.OWNED_OFF_PLAN

if __name__ == "__main__":
    pytest.main([__file__])
