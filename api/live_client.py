"""
api/live_client.py — Riot Live Client Data API wrapper.

Polls https://127.0.0.1:2999/liveclientdata/allgamedata while a game is active.
This API is officially documented by Riot and is TOS-compliant for local use.

SSL note: the game client uses a self-signed cert, so verify=False is required.
"""
import logging
from typing import Optional

import httpx
import collections

from core.state_manager import InGameState, PlayerInGameData, LiveStats

logger = logging.getLogger(__name__)

_BASE_URL = "https://127.0.0.1:2999"
_ENDPOINT = "/liveclientdata/allgamedata"
_SSL_VERIFY = False  # Self-signed cert from the game client


# ---------------------------------------------------------------------------
# Live Client API client
# ---------------------------------------------------------------------------

class LiveClientAPI:
    """
    Polls the Live Client Data API to get real-time in-game information.

    Thread-safe: create once, call fetch_game_state() from any thread.
    """

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=_BASE_URL,
            verify=_SSL_VERIFY,
            timeout=3.0,
        )

    def close(self) -> None:
        self._client.close()

    # -----------------------------------------------------------------------
    # Core fetch
    # -----------------------------------------------------------------------

    def is_game_active(self) -> bool:
        """Quick probe — returns True only if the game is running."""
        try:
            resp = self._client.get(_ENDPOINT)
            return resp.status_code == 200
        except (httpx.RequestError, httpx.ConnectError):
            return False

    def fetch_raw(self) -> Optional[dict]:
        """Fetch full game data JSON or return None on failure."""
        try:
            resp = self._client.get(_ENDPOINT)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("Live Client returned HTTP %d", resp.status_code)
            return None
        except (httpx.RequestError, httpx.ConnectError):
            return None

    # -----------------------------------------------------------------------
    # Parsed state builder
    # -----------------------------------------------------------------------

    def fetch_game_state(self) -> Optional[InGameState]:
        """
        Fetch and parse the full game state into an InGameState.
        Returns None if the game is not active.
        """
        raw = self.fetch_raw()
        if raw is None:
            return None

        game_data = raw.get("gameData", {})
        game_time = game_data.get("gameTime", 0.0)

        all_players_raw = raw.get("allPlayers", [])
        active_player_raw = raw.get("activePlayer", {})
        active_riot_id = active_player_raw.get("riotId", "")
        active_summoner_name = active_player_raw.get("summonerName", "")

        all_players: list[PlayerInGameData] = []
        for p in all_players_raw:
            player = _parse_player(p, active_riot_id, active_summoner_name)
            all_players.append(player)

        local_player = next((p for p in all_players if p.is_local_player), None)

        # Parse runes from activePlayer.fullRunes
        rune_keystone = ""
        rune_primary  = ""
        rune_secondary = ""
        full_runes = active_player_raw.get("fullRunes", {})
        keystones = full_runes.get("keystoneRune", {})
        if keystones:
            rune_keystone = keystones.get("displayName", "")
        general_runes = full_runes.get("generalRunes", [])
        primary_slots  = full_runes.get("primaryRuneTree", {})
        secondary_slots = full_runes.get("secondaryRuneTree", {})
        if primary_slots:
            rune_primary = primary_slots.get("displayName", "")
        if secondary_slots:
            rune_secondary = secondary_slots.get("displayName", "")

        raw_stats = active_player_raw.get("championStats", {})
        current_gold = float(raw_stats.get("currentGold", 0))

        live_stats = LiveStats(
            armor=float(raw_stats.get("armor", 0)),
            magic_resist=float(raw_stats.get("magicResist", 0)),
            max_health=float(raw_stats.get("maxHealth", 0)),
            current_health=float(raw_stats.get("currentHealth", 0)),
            attack_damage=float(raw_stats.get("attackDamage", 0)),
            ability_power=float(raw_stats.get("abilityPower", 0)),
            ability_haste=float(raw_stats.get("abilityHaste", 0)),
            crit_chance=float(raw_stats.get("critChance", 0)),
            attack_speed=float(raw_stats.get("attackSpeed", 0)),
            tenacity=float(raw_stats.get("tenacity", 0)),
            life_steal=float(raw_stats.get("lifeSteal", 0)),
        )

        # Current gold (from same stats block)
        if local_player:
            local_player.gold = int(current_gold)

        # Identify fed enemies
        if local_player:
            fed_enemies = [
                p for p in all_players
                if p.team != local_player.team and p.is_fed and not p.is_local_player
            ]
        else:
            fed_enemies = []

        # Threat vector
        threat = collections.defaultdict(float)
        events = raw.get("events", {}).get("Events", [])
        
        # VictimName is usually the summoner name. We'll match against active_summoner_name.
        for ev in events:
            if ev.get("EventName") == "ChampionKill":
                victim = ev.get("VictimName", "")
                if victim == active_summoner_name or (local_player and victim == local_player.champion_name):
                    killer = ev.get("KillerName", "")
                    ev_time = ev.get("EventTime", 0.0)
                    # Decay: half-life of 300s (5 min)
                    recency = 0.5 ** ((game_time - ev_time) / 300)
                    
                    if killer:
                        threat[killer] += 1.0 * recency
                    for a in ev.get("Assisters", []):
                        threat[a] += 0.4 * recency

        return InGameState(
            in_game=True,
            game_time_seconds=game_time,
            local_player=local_player,
            all_players=all_players,
            fed_enemies=fed_enemies,
            threat_vector=dict(threat),
            live_stats=live_stats,
        )

    def get_active_player_items(self) -> list[int]:
        """Return item IDs for the active (local) player."""
        raw = self.fetch_raw()
        if not raw:
            return []
        active = raw.get("activePlayer", {})
        items = active.get("items", [])
        return [item.get("itemID", 0) for item in items if item.get("itemID")]

    def get_active_player_gold(self) -> float:
        """Return current gold for the active player."""
        raw = self.fetch_raw()
        if not raw:
            return 0.0
        active = raw.get("activePlayer", {})
        stats = active.get("championStats", {})
        return stats.get("currentGold", 0.0)


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

# Positions : la Live Client API renvoie TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY (parfois ""),
# le LCU renvoie des libellés (Top/Jungle/Mid/ADC/Support). On ramène tout au même jeu.
_POSITION_ALIASES = {
    "top": "TOP",
    "jungle": "JUNGLE", "jgl": "JUNGLE",
    "mid": "MID", "middle": "MID",
    "adc": "ADC", "bottom": "ADC", "bot": "ADC",
    "support": "SUPPORT", "utility": "SUPPORT", "supp": "SUPPORT",
}


def normalize_position(raw: str) -> str:
    """Ramène une position LCU ou Live-Client à TOP/JUNGLE/MID/ADC/SUPPORT ('' si inconnue)."""
    return _POSITION_ALIASES.get((raw or "").strip().lower(), "")


def _parse_player(raw_player: dict, active_riot_id: str, active_summoner_name: str) -> PlayerInGameData:
    """Convert a single raw player dict to a PlayerInGameData dataclass."""
    riot_id = raw_player.get("riotId", "")
    summoner_name = raw_player.get("summonerName", "")
    scores = raw_player.get("scores", {})
    items_raw = raw_player.get("items", [])

    is_local = False
    if active_riot_id and riot_id == active_riot_id:
        is_local = True
    elif active_summoner_name and summoner_name == active_summoner_name:
        is_local = True

    return PlayerInGameData(
        riot_id=riot_id,
        champion_name=raw_player.get("championName", ""),
        team=raw_player.get("team", ""),
        position=normalize_position(raw_player.get("position", "")),
        level=raw_player.get("level", 1),
        kills=int(scores.get("kills", 0)),
        deaths=int(scores.get("deaths", 0)),
        assists=int(scores.get("assists", 0)),
        cs=int(scores.get("creepScore", 0)),
        items=[item.get("itemID", 0) for item in items_raw if item.get("itemID")],
        is_local_player=is_local,
        is_dead=raw_player.get("isDead", False),
        respawn_timer=float(raw_player.get("respawnTimer", 0.0)),
    )
