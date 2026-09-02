import json
from services.stat_analyzer import StatAnalyzer

analyzer = StatAnalyzer()
analyzer._ensure_loaded()

items = ["Médaillon de l'Iron Solari", "Hydre titanesque", "Convergence de Zeke", "Cotte épineuse", "Rookern kaénique", "Plaque du mort", "Armure de Warmog"]
marginal_gains = {
    "HP_for_AD_EHP": 1.5,
    "HP_for_AP_EHP": 1.5,
    "Armor_EHP": 2.0,
    "MR_EHP": 2.0,
    "AD": 1.5,
    "AP": 1.5,
    "Crit": 0,
    "AS": 0,
    "Lethality": 0,
    "PctArmorPen": 0,
    "MagicPenFlat": 0,
    "PctMagicPen": 0
}

triggers = {
    "need_grievous": False,
    "need_armor_pen": False,
    "need_magic_pen": False,
    "need_tenacity": False,
    "can_adapt_defense": False,
    "tank_count": 0,
    "gold_state": "normal"
}

print("SCORING TANK SUPPORT ITEMS")
print("-" * 50)
for item in items:
    mg = marginal_gains.copy()
    score, eff, reason = analyzer._score_item(
        item_name=item,
        player_class="Tank_Support",
        win_ratio=1.0,
        stat_priority="ARMOR_SUPPORT",
        triggers=triggers,
        already_have=[],
        marginal_gains=mg
    )
    print(f'{item}: {score:.2f} (Eff: {eff:.0f}%) -> {reason}')
