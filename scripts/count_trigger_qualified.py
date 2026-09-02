#!/usr/bin/env python3
import os
import gzip
import json
import logging
import collections
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stat_analyzer import (
    _HEALING_CHAMPION_WEIGHTS, _HEALING_ITEMS,
    _GW_AD_ITEMS, _GW_AP_ITEMS, _GW_TANK_ITEM
)
from scripts.extract_core_items import _POSITION_MAP
from services.image_cache import ImageCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("count_trigger")

cache = ImageCache()
_GRIEVOUS_ITEMS = _GW_AD_ITEMS | _GW_AP_ITEMS | _GW_TANK_ITEM
_GRIEVOUS_ITEM_IDS = {cache.get_item_id_by_name(n) for n in _GRIEVOUS_ITEMS if cache.get_item_id_by_name(n) is not None}
_HEALING_ITEM_IDS = {cache.get_item_id_by_name(n) for n in _HEALING_ITEMS if cache.get_item_id_by_name(n) is not None}

def process_match(match_data: dict, timeline_data: dict, thresholds: list[float]):
    frames = timeline_data.get("info", {}).get("frames", [])
    if not frames:
        return []
    
    events = [e for f in frames for e in f.get("events", [])]
    
    participants = match_data.get("info", {}).get("participants", [])
    team_100 = [p["participantId"] for p in participants if p["teamId"] == 100]
    team_200 = [p["participantId"] for p in participants if p["teamId"] == 200]
    
    pid_to_champ = {p["participantId"]: p["championName"] for p in participants}
    
    # Track items over time for each participant
    inventory = {pid: set() for pid in range(1, 11)}
    
    # Pre-calculate enemy healing weights per frame
    # team_heal_weight[teamId][frame_idx] = weight
    
    results = []
    
    # We will step through events
    # We want to know when gw_weight crosses thresholds.
    # To do this accurately, we track gw_weight per minute (frame)
    gw_weight_timeline = {100: [0]*len(frames), 200: [0]*len(frames)}
    primary_healer = {100: [None]*len(frames), 200: [None]*len(frames)}
    
    for frame_idx, frame in enumerate(frames):
        for pid in range(1, 11):
            pf = frame.get("participantFrames", {}).get(str(pid), {})
            # Read items if possible, but timeline frames don't have items. We rely on events.
            pass
            
        # apply events up to this frame
        for e in frame.get("events", []):
            if e.get("type") == "ITEM_PURCHASED":
                pid = e.get("participantId")
                if pid: inventory[pid].add(e.get("itemId"))
            elif e.get("type") == "ITEM_DESTROYED" or e.get("type") == "ITEM_SOLD" or e.get("type") == "ITEM_UNDO":
                pid = e.get("participantId")
                if pid and e.get("itemId") in inventory[pid]:
                    inventory[pid].remove(e.get("itemId"))
                    
        # Calculate GW weight for each team at this frame
        for team, pids in [(100, team_100), (200, team_200)]:
            weight = 0.0
            healer = None
            max_w = 0.0
            
            for pid in pids:
                champ = pid_to_champ.get(pid, "")
                cw = _HEALING_CHAMPION_WEIGHTS.get(champ, 0.0)
                if cw > 0:
                    weight += cw
                    if cw > max_w:
                        max_w = cw
                        healer = pid
                
                # Check items
                p_items = inventory[pid]
                for item_id in p_items:
                    if item_id in _HEALING_ITEM_IDS:
                        weight += 0.6
                        
            gw_weight_timeline[team][frame_idx] = weight
            primary_healer[team][frame_idx] = healer

    # For each participant, when did the ENEMY team cross the threshold?
    for pid in range(1, 11):
        my_team = 100 if pid in team_100 else 200
        enemy_team = 200 if my_team == 100 else 100
        champ = pid_to_champ.get(pid)
        
        # Did the player buy GW at all?
        gw_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") in _GRIEVOUS_ITEM_IDS]
        gw_purchase_ts = gw_purchase_events[0]["timestamp"] if gw_purchase_events else None
        
        # Kill history
        my_deaths_by_enemy = collections.defaultdict(list)
        for e in events:
            if e.get("type") == "CHAMPION_KILL" and e.get("victimId") == pid:
                my_deaths_by_enemy[e.get("killerId")].append(e.get("timestamp"))

        for t in thresholds:
            trigger_frame = None
            healer_id = None
            
            for f_idx in range(len(frames)):
                if gw_weight_timeline[enemy_team][f_idx] >= t:
                    trigger_frame = f_idx
                    healer_id = primary_healer[enemy_team][f_idx]
                    break
                    
            if trigger_frame is None:
                continue # Threshold not reached
                
            trigger_ts = frames[trigger_frame]["timestamp"]
            
            # Response: bought GW within 8 minutes?
            # Note: User says "my_items=[]" meaning the trigger shouldn't be disabled if they already have it.
            # So if gw_purchase_ts <= trigger_ts + 8*60000, we count it as a response! (Even if they bought it before!)
            # Wait, if they bought it BEFORE, they responded proactively. Let's count it.
            responded = gw_purchase_ts is not None and gw_purchase_ts <= trigger_ts + 480000
            
            # Deaths before and after (from the healer)
            deaths_before = 0
            deaths_after = 0
            if healer_id:
                deaths_before = sum(1 for ts in my_deaths_by_enemy[healer_id] if trigger_ts - 480000 <= ts <= trigger_ts)
                deaths_after = sum(1 for ts in my_deaths_by_enemy[healer_id] if trigger_ts < ts <= trigger_ts + 480000)
                
            results.append({
                "pid": pid,
                "champ": champ,
                "threshold": t,
                "responded": responded,
                "deaths_before": deaths_before,
                "deaths_after": deaths_after,
                "healer_id": healer_id
            })
            
    return results

def main():
    log.info("Comptage des déclencheurs anti-soin (Grievous Wounds)...")
    raw_dir = r"G:\matches\train"
    if not os.path.exists(raw_dir):
        log.error("Dossier introuvable.")
        return
        
    thresholds = [1.0, 1.5, 2.0, 2.5, 3.0]
    
    # counts[threshold][responded] = count
    counts = {t: {True: 0, False: 0} for t in thresholds}
    
    matches_processed = 0
    shards_processed = 0
    
    for shard in os.listdir(raw_dir):
        shard_path = os.path.join(raw_dir, shard)
        if not os.path.isdir(shard_path): continue
        shards_processed += 1
            
        for mf in os.listdir(shard_path):
            if not mf.endswith(".json.gz") or mf.endswith("_timeline.json.gz"): continue
                
            try:
                with gzip.open(os.path.join(shard_path, mf), "rt") as f: match_data = json.load(f)
                with gzip.open(os.path.join(shard_path, mf.replace(".json.gz", "_timeline.json.gz")), "rt") as f: timeline_data = json.load(f)
                
                res = process_match(match_data, timeline_data, thresholds)
                for r in res:
                    counts[r["threshold"]][r["responded"]] += 1
                    
                matches_processed += 1
            except Exception:
                pass
                
        # Just process 10 shards for the quick count to extrapolate
        if shards_processed >= 10:
            break
            
    log.info(f"Terminé. {matches_processed} parties traitées (extrapolation x10 possible pour le corpus complet).")
    for t in thresholds:
        resp = counts[t][True]
        no_resp = counts[t][False]
        total = resp + no_resp
        log.info(f"Seuil {t:.1f} : {total} qualifiés (Réponse: {resp}, Ignoré: {no_resp})")

if __name__ == "__main__":
    main()
