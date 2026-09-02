from typing import Optional
from core.state_manager import InGameState
from data.champion_affinity import ChampionAffinity

# Constants for common boots (using the exact French names from ImageCache)
BOOTS_TABI = "Coques en acier"
BOOTS_MERCURY = "Sandales de Mercure"
BOOTS_SORC = "Chaussures de sorcier"
BOOTS_LUCIDITY = "Bottes de lucidité"
BOOTS_ZERKER = "Jambières du berzerker"
BOOTS_SWIFT = "Bottes de célérité"

# Liste de champions avec du CC dur (reprise de stat_analyzer)
_CC_CHAMPIONS = {
    "Leona", "Nautilus", "Amumu", "Malphite", "Annie", "Ashe", "Zac",
    "Alistar", "Thresh", "Blitzcrank", "Morgana", "Lissandra", "Rammus",
    "Galio", "Vi", "Jarvan IV", "Cho'Gath", "Ornn", "Sion", "Veigar",
    "Twisted Fate", "Poppy", "Rell", "Sejuani", "Fiddlesticks", "Volibear",
    "Urgot", "Nunu et Willump", "Wukong", "Kennen", "Ryze",
}

class BootOptimizer:
    def __init__(self, affinity: ChampionAffinity):
        self.affinity = affinity
        
    def recommend_boots(self, game_state: InGameState, lane_opp_name: Optional[str], lane_opp_type: str, enemy_tank_count: int) -> Optional[str]:
        """
        Recommande les meilleures bottes en fonction du contexte de la partie.
        """
        if not game_state.local_player:
            return None
            
        local_champ = game_state.local_player.champion_name
        prof = self.affinity.profile(local_champ)
        local_flags = prof.get("flags", {})
        
        enemy_team = [p.champion_name for p in game_state.all_players if p.team != game_state.local_player.team]
        
        # --- 1. Calcul des menaces ---
        # Calcul du CC dur dans l'équipe ennemie
        hard_cc_count = sum(1 for e in enemy_team if e in _CC_CHAMPIONS)
        
        # Vérifier si l'adversaire de lane est un attaquant de base
        lane_opp_auto_based = False
        if lane_opp_name:
            opp_prof = self.affinity.profile(lane_opp_name)
            lane_opp_auto_based = opp_prof.get("flags", {}).get("auto_based", False) or opp_prof.get("flags", {}).get("on_hit", False)
            
        # --- 2. Règles Défensives (Priorité Absolue) ---
        # Si on est contre beaucoup de CC dur (3+), Mercure est obligatoire pour survivre
        if hard_cc_count >= 3:
            return BOOTS_MERCURY
            
        # Les Tabi ne contrant que les attaques de base, on cible les auto_based (Tryndamere, Yasuo, Irela, etc.)
        if lane_opp_auto_based:
            return BOOTS_TABI
            
        # --- 3. Règles Offensives (Si pas de menace écrasante) ---
        # Tireurs et Auto-attackers
        if local_flags.get("on_hit", False) or local_flags.get("crit_viable", False):
            return BOOTS_ZERKER
            
        # Mages et Assassins AP
        local_mix = prof.get("damage_mix", {})
        if local_mix.get("ap", 0) >= 0.70:
            if enemy_tank_count >= 2:
                # Contre des tanks, la pénétration magique flat est moins efficace que Lucidité pour DPS constant
                return BOOTS_LUCIDITY
            return BOOTS_SORC
            
        # Tanks et Supports
        if "Tank" in prof.get("archetype", "") or "Support" in prof.get("archetype", ""):
            return BOOTS_LUCIDITY
            
        # Par défaut pour les combattants AD
        return BOOTS_TABI
