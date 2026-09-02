#!/usr/bin/env python3
"""
Construit data/patch_stats.json pour le moteur de ban.

Architecture à fournisseurs : chaque source implémente StatsProvider.
Deux fournisseurs livrés :

  RiotMatchProvider   agrège MATCH-V5. Légitime, gratuit, mais lent.
                      Suffisant pour pickrate/banrate. Insuffisant seul
                      pour les matchups de lane (voir NOTE VOLUME ci-dessous).

  CsvProvider         importe un CSV que tu as obtenu d'une source que tu es
                      autorisé à utiliser (API tierce sous licence, export
                      manuel, dataset public).

NOTE VOLUME — matchups de lane
    5 rôles x ~40 champions jouables = ~1600 paires par rôle, 8000 au total.
    Pour atteindre 200 parties par paire (seuil minimal du scorer), il faut
    ~1.6M matchups de lane, soit ~320 000 parties. À la limite de débit d'une
    clé de développement (100 req / 2 min), cela représente plusieurs semaines
    de collecte continue.

    Conclusion pratique : la Riot API te donne pickrate et banrate en une nuit.
    Les matchups exigent une source tierce sous licence, ou l'acceptation
    d'une couverture partielle (les paires les plus fréquentes uniquement).

Usage:
    python build_patch_stats.py riot   --key RGAPI-xxx --region euw1 --matches 10000
    python build_patch_stats.py csv    --pickrate pr.csv --banrate br.csv --lane lane.csv
    python build_patch_stats.py verify --file data/patch_stats.json
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from typing import Iterable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("patch_stats")

ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]

# MATCH-V5 renvoie teamPosition ; on mappe vers notre nomenclature.
_POSITION_MAP = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUPPORT",
}

# Volume minimal pour qu'une entrée lane soit écrite dans le fichier.
MIN_LANE_GAMES = 200


# ======================================================================
# Interface
# ======================================================================

class StatsProvider(ABC):
    """Chaque source de données implémente cette interface."""

    @abstractmethod
    def build(self) -> dict:
        """Retourne un dict au schéma patch_stats."""


# ======================================================================
# Fournisseur 1 — agrégation Riot MATCH-V5
# ======================================================================

class RiotMatchProvider(StatsProvider):
    """
    Agrège un échantillon de parties classées pour en déduire pickrate,
    banrate et (partiellement) les matchups de lane.

    Limites de débit d'une clé de développement : 20 req/s, 100 req/2min.
    Le facteur contraignant est le second : ~0.83 req/s en régime permanent.
    """

    ROUTING = {
        "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
        "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
        "kr": "asia", "jp1": "asia",
        "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
    }

    RANKED_SOLO_QUEUE = 420

    def __init__(self, api_key: str, region: str, target_matches: int,
                 checkpoint: str = ".patch_stats_checkpoint.json",
                 tiers: Iterable[str] = ("DIAMOND", "EMERALD", "PLATINUM"),
                 persist_raw: bool = True,
                 raw_dir: str = "data/matches"):
        try:
            import requests  # noqa: F401
        except ImportError:
            sys.exit("pip install requests")
        self.key = api_key
        self.region = region
        self.routing = self.ROUTING.get(region)
        if not self.routing:
            sys.exit(f"région inconnue : {region}")
        self.target = target_matches
        self.checkpoint_path = checkpoint
        self.tiers = list(tiers)
        self.persist_raw = persist_raw
        self.raw_dir = raw_dir
        
        self.is_production = False
        self._last_call = 0.0
        self._window_long = collections.deque(maxlen=100)
        self._window_short = collections.deque(maxlen=20)

        self.matches_seen = 0
        self.picks = collections.Counter()       # (champ, role) -> n
        self.bans = collections.Counter()        # champ -> n
        self.lane = collections.defaultdict(lambda: {"games": 0, "wins": 0})

    # ---------------- débit ----------------

    def set_production_limits(self):
        self.is_production = True
        self._window_long = collections.deque(maxlen=29900)
        self._window_short = collections.deque(maxlen=490)

    def _throttle(self):
        """Respecte les limites (dev ou prod)."""
        now = time.time()
        
        # Short window check
        if len(self._window_short) == self._window_short.maxlen:
            elapsed = now - self._window_short[0]
            limit_s = 11 if self.is_production else 1.1
            if elapsed < limit_s:
                time.sleep(limit_s - elapsed)
                now = time.time()
                
        # Long window check
        if len(self._window_long) == self._window_long.maxlen:
            elapsed = now - self._window_long[0]
            limit_l = 601 if self.is_production else 121
            if elapsed < limit_l:
                wait = limit_l - elapsed
                log.info("Fenêtre limite atteinte, pause %.0fs", wait)
                time.sleep(wait)
                now = time.time()
                
        self._last_call = now
        self._window_short.append(now)
        self._window_long.append(now)

    def _get(self, url: str, params: dict | None = None):
        import requests
        for attempt in range(5):
            self._throttle()
            r = requests.get(url, params=params,
                             headers={"X-Riot-Token": self.key}, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                log.warning("429 — attente %ss", wait)
                time.sleep(wait + 1)
                continue
            if r.status_code in (404, 400):
                return None
            log.warning("HTTP %s sur %s (tentative %d)", r.status_code, url, attempt + 1)
            time.sleep(2 ** attempt)
        return None

    # ---------------- collecte ----------------

    def _seed_puuids(self, per_tier: int = 40) -> list[str]:
        """Récupère des puuids depuis les ligues classées, tous tiers demandés."""
        puuids = []
        for tier in self.tiers:
            for division in ("I", "II"):
                data = self._get(
                    f"https://{self.region}.api.riotgames.com"
                    f"/lol/league/v4/entries/RANKED_SOLO_5x5/{tier}/{division}",
                    {"page": 1},
                )
                if not data:
                    continue
                for entry in data[:per_tier]:
                    pid = entry.get("puuid")
                    if pid:
                        puuids.append(pid)
        log.info("%d puuids de départ collectés", len(puuids))
        return puuids

    def _match_ids(self, puuid: str, count: int = 20) -> list[str]:
        ids = self._get(
            f"https://{self.routing}.api.riotgames.com"
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            {"queue": self.RANKED_SOLO_QUEUE, "count": count, "type": "ranked"},
        )
        return ids or []

    def _split(self, match_id: str) -> str:
        h = int(hashlib.md5(match_id.encode()).hexdigest()[:8], 16)
        return "test" if h % 10 == 0 else "train"

    def _ingest(self, match: dict, timeline: dict = None):
        if self.persist_raw:
            mid = match["metadata"]["matchId"]
            split_dir = self._split(mid)
            shard = mid[-2:]
            d = os.path.join(self.raw_dir, split_dir, shard)
            os.makedirs(d, exist_ok=True)
            with gzip.open(os.path.join(d, f"{mid}.json.gz"), "wt") as f:
                json.dump(match, f)
            if timeline:
                # Filtrer la timeline pour gagner de la place
                filtered_frames = []
                for frame in timeline.get("info", {}).get("frames", []):
                    filtered_events = [
                        e for e in frame.get("events", [])
                        if e.get("type") in ("ITEM_PURCHASED", "ITEM_SOLD", "ITEM_UNDO", "ITEM_DESTROYED", "CHAMPION_KILL")
                    ]
                    filtered_frames.append({
                        "timestamp": frame.get("timestamp"),
                        "participantFrames": frame.get("participantFrames"),
                        "events": filtered_events
                    })
                filtered_tl = {"metadata": timeline.get("metadata"), "info": {"frames": filtered_frames}}
                with gzip.open(os.path.join(d, f"{mid}_timeline.json.gz"), "wt") as f:
                    json.dump(filtered_tl, f)

        info = match.get("info", {})
        if info.get("queueId") != self.RANKED_SOLO_QUEUE:
            return
        parts = info.get("participants", [])
        if len(parts) != 10:
            return

        self.matches_seen += 1

        # --- bans ---
        for team in info.get("teams", []):
            for b in team.get("bans", []):
                cid = b.get("championId", -1)
                if cid and cid > 0:
                    self.bans[cid] += 1

        # --- picks + matchups ---
        by_role: dict[str, list] = collections.defaultdict(list)
        for p in parts:
            role = _POSITION_MAP.get(p.get("teamPosition", ""))
            if not role:
                continue          # partie avec rôles non résolus, on ignore
            name = p.get("championName")
            if not name:
                continue
            self.picks[(name, role)] += 1
            by_role[role].append(p)

        for role, players in by_role.items():
            if len(players) != 2:
                continue          # rôle mal résolu ou doublon
            a, b = players
            if a.get("teamId") == b.get("teamId"):
                continue
            na, nb = a["championName"], b["championName"]
            wa = bool(a.get("win"))
            self.lane[(role, na, nb)]["games"] += 1
            self.lane[(role, na, nb)]["wins"] += 1 if wa else 0
            self.lane[(role, nb, na)]["games"] += 1
            self.lane[(role, nb, na)]["wins"] += 0 if wa else 1

    # ---------------- checkpoint ----------------

    def _save_checkpoint(self, seen_ids: set):
        payload = {
            "matches_seen": self.matches_seen,
            "seen_ids": list(seen_ids),
            "picks": {f"{c}|{r}": n for (c, r), n in self.picks.items()},
            "bans": {str(k): v for k, v in self.bans.items()},
            "lane": {f"{r}|{a}|{b}": v for (r, a, b), v in self.lane.items()},
        }
        tmp = self.checkpoint_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, self.checkpoint_path)

    def _load_checkpoint(self) -> set:
        if not os.path.exists(self.checkpoint_path):
            return set()
        with open(self.checkpoint_path) as f:
            d = json.load(f)
        self.matches_seen = d.get("matches_seen", 0)
        for k, n in d.get("picks", {}).items():
            c, r = k.split("|")
            self.picks[(c, r)] = n
        for k, n in d.get("bans", {}).items():
            self.bans[int(k)] = n
        for k, v in d.get("lane", {}).items():
            r, a, b = k.split("|")
            self.lane[(r, a, b)] = v
        log.info("Checkpoint repris : %d parties déjà ingérées", self.matches_seen)
        return set(d.get("seen_ids", []))

    # ---------------- build ----------------

    def build(self) -> dict:
        seen = self._load_checkpoint()
        puuids = self._seed_puuids()
        if not puuids:
            sys.exit("aucun puuid — vérifie la clé API et la région")

        queue = collections.deque(puuids)
        visited_puuids = set(puuids)

        while self.matches_seen < self.target and queue:
            puuid = queue.popleft()
            for mid in self._match_ids(puuid):
                if mid in seen:
                    continue
                seen.add(mid)
                m = self._get(
                    f"https://{self.routing}.api.riotgames.com/lol/match/v5/matches/{mid}"
                )
                if not m:
                    continue
                
                t = None
                if self.persist_raw:
                    t = self._get(
                        f"https://{self.routing}.api.riotgames.com/lol/match/v5/matches/{mid}/timeline"
                    )
                    if not t:
                        continue
                
                self._ingest(m, t)

                # élargissement du graphe de joueurs
                if len(queue) < 500:
                    for p in m.get("info", {}).get("participants", []):
                        pid = p.get("puuid")
                        if pid and pid not in visited_puuids:
                            visited_puuids.add(pid)
                            queue.append(pid)

                if self.matches_seen % 100 == 0:
                    log.info("%d / %d parties", self.matches_seen, self.target)
                    self._save_checkpoint(seen)
                if self.matches_seen >= self.target:
                    break

        self._save_checkpoint(seen)
        return self._assemble()

    def _assemble(self) -> dict:
        n = max(1, self.matches_seen)

        pickrate = {f"{c}|{r}": round(v / n, 5) for (c, r), v in self.picks.items()}

        # banrate par champion : nb de parties où il est banni / nb de parties.
        # On le duplique sur chacun de ses rôles, le scorer prend le max.
        id_to_name = _load_ddragon_id_map()
        banrate: dict[str, float] = {}
        roles_of = collections.defaultdict(set)
        for (c, r) in self.picks:
            roles_of[c].add(r)
        for cid, cnt in self.bans.items():
            name = id_to_name.get(cid)
            if not name:
                continue
            rate = round(cnt / n, 5)
            for r in (roles_of.get(name) or ROLES):
                banrate[f"{name}|{r}"] = rate

        lane: dict = {r: {} for r in ROLES}
        kept = dropped = 0
        for (role, a, b), v in self.lane.items():
            if v["games"] < MIN_LANE_GAMES:
                dropped += 1
                continue
            lane[role].setdefault(a, {})[b] = {
                "wr": round(v["wins"] / v["games"], 4),
                "games": v["games"],
            }
            kept += 1

        log.info("Matchups : %d retenus, %d écartés (< %d parties)",
                 kept, dropped, MIN_LANE_GAMES)
        if kept == 0:
            log.warning(
                "AUCUN matchup ne dépasse le seuil de volume. _counter_me et "
                "_protects_ally retourneront une confiance nulle — le moteur de "
                "ban tournera uniquement sur meta_threat. Augmente --matches ou "
                "utilise une source tierce."
            )

        return {
            "patch": _detect_patch(),
            "fetched_at": time.strftime("%Y-%m-%d"),
            "source": f"riot_match_v5:{self.region}",
            "sample_matches": self.matches_seen,
            "rank_bracket": "+".join(self.tiers),
            "pickrate": pickrate,
            "banrate": banrate,
            "lane": lane,
        }


# ======================================================================
# Fournisseur 2 — import CSV
# ======================================================================

class CsvProvider(StatsProvider):
    """
    Import depuis des CSV que tu as obtenus d'une source autorisée.

    pickrate.csv : champion,role,pickrate
    banrate.csv  : champion,role,banrate
    lane.csv     : role,champion,opponent,winrate,games
    """

    def __init__(self, pickrate=None, banrate=None, lane=None, patch="UNKNOWN"):
        self.p_pr, self.p_br, self.p_lane, self.patch = pickrate, banrate, lane, patch

    def build(self) -> dict:
        import csv
        out = {"patch": self.patch, "fetched_at": time.strftime("%Y-%m-%d"),
               "source": "csv_import", "pickrate": {}, "banrate": {},
               "lane": {r: {} for r in ROLES}}

        if self.p_pr:
            with open(self.p_pr, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    out["pickrate"][f"{row['champion']}|{row['role']}"] = float(row["pickrate"])

        if self.p_br:
            with open(self.p_br, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    out["banrate"][f"{row['champion']}|{row['role']}"] = float(row["banrate"])

        if self.p_lane:
            with open(self.p_lane, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    g = int(row.get("games", 0))
                    if g < MIN_LANE_GAMES:
                        continue
                    role = row["role"]
                    out["lane"].setdefault(role, {}).setdefault(row["champion"], {})[
                        row["opponent"]] = {"wr": float(row["winrate"]), "games": g}

        return out


# ======================================================================
# Utilitaires
# ======================================================================

def _detect_patch() -> str:
    try:
        import requests
        v = requests.get("https://ddragon.leagueoflegends.com/api/versions.json",
                         timeout=10).json()
        return v[0]
    except Exception:
        return "UNKNOWN"


def _load_ddragon_id_map() -> dict[int, str]:
    """championId numérique -> championName (clé DDragon, ex. MonkeyKing)."""
    try:
        import requests
        ver = _detect_patch()
        d = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json",
            timeout=15).json()
        return {int(v["key"]): k for k, v in d["data"].items()}
    except Exception as e:
        log.warning("DDragon indisponible (%s) — banrate sera vide", e)
        return {}


def verify(path: str) -> int:
    """Contrôles de cohérence sur un fichier produit. Retourne le nb d'erreurs."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    errors = warnings = 0

    for key in ("pickrate", "banrate", "lane"):
        if key not in d:
            log.error("clé manquante : %s", key); errors += 1

    for k, v in d.get("pickrate", {}).items():
        if "|" not in k:
            log.error("clé pickrate malformée : %s", k); errors += 1
        if not 0.0 <= v <= 1.0:
            log.error("pickrate hors bornes : %s = %s", k, v); errors += 1

    total_pr = collections.defaultdict(float)
    for k, v in d.get("pickrate", {}).items():
        total_pr[k.split("|")[1]] += v
    for role, s in total_pr.items():
        if not 0.80 <= s <= 1.20:
            log.warning("somme des pickrate en %s = %.2f (attendu ~1.0)", role, s)
            warnings += 1

    n_lane = flat = 0
    for role, champs in d.get("lane", {}).items():
        for a, opps in champs.items():
            for b, e in opps.items():
                n_lane += 1
                if isinstance(e, (int, float)):
                    flat += 1
                elif isinstance(e, dict):
                    if e.get("games", 0) < MIN_LANE_GAMES:
                        log.warning("%s/%s vs %s : %d parties < %d",
                                    role, a, b, e.get("games", 0), MIN_LANE_GAMES)
                        warnings += 1
                    if not 0.0 <= e.get("wr", -1) <= 1.0:
                        log.error("winrate hors bornes : %s/%s vs %s", role, a, b)
                        errors += 1

    if flat:
        log.warning("%d entrées lane au schéma plat (sans 'games') — "
                    "confiance forfaitaire côté scorer", flat)
        warnings += 1

    log.info("Vérification : %d entrées lane, %d erreurs, %d avertissements",
             n_lane, errors, warnings)
    if n_lane == 0:
        log.warning("Aucun matchup : le moteur de ban tournera sur meta_threat seul.")
    return errors


# ======================================================================
# CLI
# ======================================================================

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("riot")
    r.add_argument("--key", required=True)
    r.add_argument("--region", default="euw1")
    r.add_argument("--matches", type=int, default=10000)
    r.add_argument("--out", default="data/patch_stats.json")
    r.add_argument("--raw-dir", default="data/matches")
    r.add_argument("--production", action="store_true", help="Utiliser les limites de clé de production")

    c = sub.add_parser("csv")
    c.add_argument("--pickrate")
    c.add_argument("--banrate")
    c.add_argument("--lane")
    c.add_argument("--patch", default="UNKNOWN")
    c.add_argument("--out", default="data/patch_stats.json")

    v = sub.add_parser("verify")
    v.add_argument("--file", default="data/patch_stats.json")

    a = ap.parse_args()

    if a.cmd == "verify":
        sys.exit(1 if verify(a.file) else 0)

    if a.cmd == "riot":
        provider = RiotMatchProvider(a.key, a.region, a.matches, raw_dir=a.raw_dir)
        if getattr(a, "production", False):
            provider.set_production_limits()
    else:
        provider = CsvProvider(a.pickrate, a.banrate, a.lane, a.patch)
    data = provider.build()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, a.out)          # écriture atomique
    log.info("Écrit → %s", a.out)
    verify(a.out)


if __name__ == "__main__":
    main()
