"""
CLIENT EN LECTURE SEULE — ce module ne fait que des GET.
Aucune méthode n'écrit vers le client Riot (pas de POST/PATCH/PUT) : accepter
une file, hover un champion ou verrouiller un pick à la place du joueur relève
de l'automatisation du client et sort du cadre annoncé dans le README.
Ne pas réintroduire de helper d'écriture ici.

api/lcu_client.py — League Client Update (LCU) API wrapper.

The LCU is a local REST server that runs while the League client is open.
Authentication uses HTTP Basic Auth with credentials extracted from the
'lockfile' in the League installation directory.

TOS compliance: read-only endpoints only. We NEVER inject, write memory,
or perform any action that would alter game behaviour.
"""
import base64
import logging
from pathlib import Path
from typing import Optional

import httpx

import config
from core.state_manager import ChampSelectState, DraftAction

logger = logging.getLogger(__name__)

# LCU always uses a self-signed TLS cert — we skip verification
_SSL_VERIFY = False

# Common League install locations to search if config path is empty / wrong
_COMMON_INSTALL_PATHS = [
    r"C:\Riot Games\League of Legends",
    r"C:\Program Files\Riot Games\League of Legends",
    r"D:\Riot Games\League of Legends",
    r"D:\Program Files\Riot Games\League of Legends",
    r"E:\Riot Games\League of Legends",
    r"F:\Riot Games\League of Legends",
    r"G:\Riot Games\League of Legends",
    r"H:\Riot Games\League of Legends",
]


# ---------------------------------------------------------------------------
# Lockfile parser
# ---------------------------------------------------------------------------

def _find_lockfile() -> Optional[Path]:
    """Search for the League lockfile in known install locations."""
    candidates = [config.LOL_INSTALL_PATH] + _COMMON_INSTALL_PATHS
    for folder in candidates:
        p = Path(folder) / "lockfile"
        if p.exists():
            return p
    return None


def parse_lockfile(lockfile_path: Path) -> dict:
    """
    Parse the LCU lockfile.
    Format: name:pid:port:password:protocol
    Returns dict with keys: name, pid, port, password, protocol
    """
    content = lockfile_path.read_text(encoding="utf-8")
    parts = content.strip().split(":")
    if len(parts) != 5:
        raise ValueError(f"Unexpected lockfile format: {content!r}")
    return {
        "name": parts[0],
        "pid": parts[1],
        "port": int(parts[2]),
        "password": parts[3],
        "protocol": parts[4],
    }


def _build_auth_header(password: str) -> str:
    """Build the HTTP Basic Auth header value (user is always 'riot')."""
    token = base64.b64encode(f"riot:{password}".encode()).decode()
    return f"Basic {token}"


# ---------------------------------------------------------------------------
# LCU Client
# ---------------------------------------------------------------------------

class LCUClient:
    """
    Thin wrapper around LCU HTTP endpoints.

    Usage:
        client = LCUClient()
        if client.connect():
            session = client.get_champ_select_session()
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.Client] = None
        self._connected: bool = False
        self._lockfile_path: Optional[Path] = None
        self._my_recent_picks: dict[str, list[str]] = {}
        self._recent_picks_fetched: bool = False

    # -----------------------------------------------------------------------
    # Connection lifecycle
    # -----------------------------------------------------------------------

    def connect(self) -> bool:
        """Locate the lockfile and establish an authenticated HTTP session."""
        lf = _find_lockfile()
        if lf is None:
            logger.warning("LCU lockfile not found — League client may not be running.")
            self._connected = False
            return False

        try:
            creds = parse_lockfile(lf)
        except Exception as exc:
            logger.error("Failed to parse lockfile: %s", exc)
            self._connected = False
            return False

        self._base_url = f"https://127.0.0.1:{creds['port']}"
        auth_header = _build_auth_header(creds["password"])

        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": auth_header,
                "Accept": "application/json",
            },
            verify=_SSL_VERIFY,
            timeout=5.0,
        )
        self._lockfile_path = lf
        self._connected = True
        logger.info("LCU connected on port %d", creds["port"])
        return True

    def disconnect(self) -> None:
        """Close the HTTP session."""
        if self._client:
            self._client.close()
            self._client = None
        self._connected = False
        self._recent_picks_fetched = False
        logger.info("LCU disconnected.")

    def is_connected(self) -> bool:
        return self._connected and (self._lockfile_path is not None and self._lockfile_path.exists())

    def check_still_alive(self) -> bool:
        """Return False if the lockfile disappeared (client closed)."""
        if self._lockfile_path and not self._lockfile_path.exists():
            self.disconnect()
            return False
        return self._connected

    # -----------------------------------------------------------------------
    # Raw request helper
    # -----------------------------------------------------------------------

    def _get(self, path: str) -> Optional[dict]:
        if not self._client:
            return None
        try:
            resp = self._client.get(path)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.debug("LCU HTTP error %s for %s", exc.response.status_code, path)
            return None
        except httpx.RequestError as exc:
            logger.debug("LCU request error for %s: %s", path, exc)
            self._connected = False
            return None

    # -----------------------------------------------------------------------
    # High-level endpoint wrappers
    # -----------------------------------------------------------------------

    def get_current_summoner(self) -> Optional[dict]:
        """Return the logged-in summoner's info."""
        return self._get("/lol-summoner/v1/current-summoner")

    def get_champ_select_session(self) -> Optional[dict]:
        """
        Return the full champ-select session dict.
        Returns None if not in champ select.
        """
        return self._get("/lol-champ-select/v1/session")

    def get_champion_name(self, champion_id: int) -> str:
        """
        Resolve a champion ID to a name via the local champ-select endpoint.
        Falls back to returning the ID as a string on failure.
        """
        if champion_id == 0:
            return ""
        data = self._get(f"/lol-game-data/assets/v1/champions/{champion_id}.json")
        if data and "name" in data:
            return data["name"]
        return str(champion_id)

    def get_my_team(self) -> list[dict]:
        """Return list of ally picks from the session."""
        session = self.get_champ_select_session()
        if not session:
            return []
        return session.get("myTeam", [])

    def get_their_team(self) -> list[dict]:
        """Return list of enemy picks from the session."""
        session = self.get_champ_select_session()
        if not session:
            return []
        return session.get("theirTeam", [])

    # -----------------------------------------------------------------------
    # Champion data cache (local Data Dragon)
    # -----------------------------------------------------------------------

    def load_champion_map(self) -> dict[int, str]:
        """
        Load champion ID → name mapping from the locally cached Data Dragon JSON.
        Falls back to empty dict if not available yet.
        """
        champ_file = Path(__file__).parent.parent / "assets" / "champion_data.json"
        if not champ_file.exists():
            logger.warning("champion_data.json not found — run setup.py first.")
            return {}
        import json
        with open(champ_file, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # Data Dragon format: data[name][key] = string ID, [id] = numeric ID
        champ_map: dict[int, str] = {}
        for champ_name, champ_info in raw.get("data", {}).items():
            try:
                champ_map[int(champ_info["key"])] = champ_info["id"]
            except (KeyError, ValueError):
                pass
        return champ_map

    def fetch_recent_ranked_picks(self) -> dict[str, list[str]]:
        """
        Fetch recent ranked matches and extract the most played champions per role.
        Uses exponential decay weighting (0.9 ** i) to prioritize recent picks.
        """
        if self._recent_picks_fetched:
            return self._my_recent_picks
            
        res = self._get("/lol-match-history/v1/products/lol/current-summoner/matches")
        self._recent_picks_fetched = True
        
        if not res or "games" not in res:
            return {}
            
        games = res["games"].get("games", [])
        champ_map = self.load_champion_map()
        
        role_weights: dict[str, dict[str, float]] = {
            "TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}
        }
        valid_queues = {420, 440} # Ranked Solo/Duo, Ranked Flex
        
        idx = 0
        for game in games:
            if game.get("queueId") not in valid_queues:
                continue
                
            local_pid = -1
            # Try to get local participant Id
            for pi in game.get("participantIdentities", []):
                local_pid = pi.get("participantId")
                break
                
            if local_pid == -1:
                continue
                
            for p in game.get("participants", []):
                if p.get("participantId") == local_pid:
                    cid = p.get("championId")
                    name = champ_map.get(cid)
                    if not name:
                        continue
                        
                    timeline = p.get("timeline", {})
                    lane = timeline.get("lane", "")
                    role = timeline.get("role", "")
                    
                    parsed_role = ""
                    if lane == "TOP": parsed_role = "TOP"
                    elif lane == "JUNGLE": parsed_role = "JUNGLE"
                    elif lane == "MIDDLE": parsed_role = "MID"
                    elif lane == "BOTTOM":
                        if role == "DUO_SUPPORT": parsed_role = "SUPPORT"
                        else: parsed_role = "ADC"
                        
                    if parsed_role in role_weights:
                        role_weights[parsed_role][name] = role_weights[parsed_role].get(name, 0.0) + (0.9 ** idx)
                    
            idx += 1
            
        for r, w_dict in role_weights.items():
            self._my_recent_picks[r] = sorted(w_dict.keys(), key=lambda k: w_dict[k], reverse=True)[:4]
            
        return self._my_recent_picks

    # -----------------------------------------------------------------------
    # Parsed champ-select state builder
    # -----------------------------------------------------------------------

    def build_champ_select_state(self, champ_map: dict[int, str]) -> ChampSelectState:
        """
        Parse the raw LCU session into a clean ChampSelectState dataclass.
        Returns a state with in_champ_select=False if not in a session.
        """
        session = self.get_champ_select_session()
        if session is None:
            return ChampSelectState(in_champ_select=False)

        def resolve(cid: int) -> str:
            return champ_map.get(cid, str(cid) if cid != 0 else "")

        # My picks / hover
        my_team_raw = session.get("myTeam", [])
        their_team_raw = session.get("theirTeam", [])

        # Local player's slot — the one with localPlayerCellId
        local_cell_id = session.get("localPlayerCellId", -1)
        my_champ_id = 0
        my_position = ""
        my_hover = ""
        ally_ids: list[int] = []
        ally_hovers: list[int] = []
        ally_ban_intents: list[int] = []

        for slot in my_team_raw:
            cid = slot.get("championId", 0)
            intent = slot.get("championPickIntent", 0)
            ban_intent = slot.get("banIntent", 0)

            if slot.get("cellId") == local_cell_id:
                my_champ_id = cid or intent
                my_hover = resolve(intent) if intent else ""
                my_position = slot.get("assignedPosition", "").lower()
            else:
                if cid or intent:
                    ally_ids.append(cid or intent)
                if intent:
                    ally_hovers.append(intent)
                if ban_intent:
                    ally_ban_intents.append(ban_intent)

        enemy_ids: list[int] = []
        for slot in their_team_raw:
            cid = slot.get("championId", 0)
            if cid:
                enemy_ids.append(cid)

        # Map LCU values to readable French labels
        _pos_labels = {
            "top":     "Top",
            "jungle":  "Jungle",
            "middle":  "Mid",
            "bottom":  "ADC",
            "utility": "Support",
        }
        my_position_label = _pos_labels.get(my_position, my_position.capitalize() if my_position else "")

        # Detect if it's MY turn to pick or ban right now
        # LCU actions is a list of phases; each phase is a list of action dicts
        is_my_pick_turn = False
        is_my_ban_turn = False
        my_ban_action_id = 0
        for phase in session.get("actions", []):
            for action in phase:
                if (
                    action.get("isInProgress", False)
                    and action.get("actorCellId") == local_cell_id
                    and not action.get("completed", False)
                ):
                    if action.get("type") == "pick":
                        is_my_pick_turn = True
                    elif action.get("type") == "ban":
                        is_my_ban_turn = True
                        my_ban_action_id = action.get("id", 0)
                    break

        # ── Bans ─────────────────────────────────────────────────────────────
        ban_ids_ally:  list[int] = []
        ban_ids_enemy: list[int] = []

        # ── Draft actions (all picks + bans in order) ─────────────────────
        draft_actions: list[DraftAction] = []

        for phase in session.get("actions", []):
            for action in phase:
                a_type      = action.get("type", "")         # "pick" or "ban"
                a_completed = bool(action.get("completed", False))
                a_cell_id   = action.get("actorCellId", -1)
                a_champ_id  = action.get("championId", 0)
                a_is_ally   = action.get("isAllyAction", True)
                a_is_mine   = (a_cell_id == local_cell_id)

                name = resolve(a_champ_id) if a_champ_id else ""

                if a_type == "ban" and a_completed and name:
                    if a_is_ally:
                        ban_ids_ally.append(a_champ_id)
                    else:
                        ban_ids_enemy.append(a_champ_id)

                draft_actions.append(DraftAction(
                    champion_name=name,
                    is_ally=a_is_ally,
                    is_ban=(a_type == "ban"),
                    is_locked=a_completed,
                    is_my_action=a_is_mine,
                ))

        banned_names = [resolve(i) for i in (ban_ids_ally + ban_ids_enemy) if i]

        timer_phase = session.get("timer", {}).get("phase", "")
        is_finalization_phase = (timer_phase == "FINALIZATION")

        return ChampSelectState(
            in_champ_select=True,
            my_champion_id=my_champ_id,
            my_champion_name=resolve(my_champ_id),
            my_position=my_position_label,
            is_my_pick_turn=is_my_pick_turn,
            is_my_ban_turn=is_my_ban_turn,
            my_ban_action_id=my_ban_action_id,
            is_finalization_phase=is_finalization_phase,
            ally_champion_names=[resolve(i) for i in ally_ids if i],
            enemy_champion_names=[resolve(i) for i in enemy_ids if i],
            banned_champion_names=banned_names,
            my_hover=my_hover,
            ally_hovers=[resolve(i) for i in ally_hovers if i],
            ally_ban_intents=[resolve(i) for i in ally_ban_intents if i],
            my_recent_picks=self.fetch_recent_ranked_picks(),
            draft_actions=draft_actions,
        )
