#!/usr/bin/env python3
import os
import gzip
import json
import logging
import collections
import sys
import math

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stat_analyzer import (
    _HEALING_CHAMPION_WEIGHTS, _HEALING_ITEMS,
    _GW_AD_ITEMS, _GW_AP_ITEMS, _GW_TANK_ITEM
)
from scripts.extract_core_items import _POSITION_MAP
from services.image_cache import ImageCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("validate_triggers")

cache = ImageCache()
_GRIEVOUS_ITEMS = _GW_AD_ITEMS | _GW_AP_ITEMS | _GW_TANK_ITEM
_GRIEVOUS_ITEM_IDS = {cache.get_item_id_by_name(n) for n in _GRIEVOUS_ITEMS if cache.get_item_id_by_name(n) is not None}
_HEALING_ITEM_IDS = {cache.get_item_id_by_name(n) for n in _HEALING_ITEMS if cache.get_item_id_by_name(n) is not None}

ITEM_PRICES = {}
try:
    with open("assets/item_data.json", "r", encoding="utf-8") as f:
        item_data = json.load(f).get("data", {})
        for i_id, i_info in item_data.items():
            ITEM_PRICES[int(i_id)] = i_info.get("gold", {}).get("total", 0)
except Exception as e:
    log.error(f"Failed to load item_data.json: {e}")

def process_match(match_data: dict, timeline_data: dict, thresholds: list[float]):
    frames = timeline_data.get("info", {}).get("frames", [])
    if not frames:
        return []
    
    events = [e for f in frames for e in f.get("events", [])]
    
    participants = match_data.get("info", {}).get("participants", [])
    team_100 = [p["participantId"] for p in participants if p["teamId"] == 100]
    team_200 = [p["participantId"] for p in participants if p["teamId"] == 200]
    
    pid_to_champ = {p["participantId"]: p["championName"] for p in participants}
    pid_to_role = {p["participantId"]: _POSITION_MAP.get(p.get("teamPosition", "")) for p in participants}
    pid_to_win = {p["participantId"]: p.get("win", False) for p in participants}
    
    inventory = {pid: set() for pid in range(1, 11)}
    
    results = []
    
    gw_weight_timeline = {100: [0]*len(frames), 200: [0]*len(frames)}
    primary_healer = {100: [None]*len(frames), 200: [None]*len(frames)}
    
    for frame_idx, frame in enumerate(frames):
        for e in frame.get("events", []):
            if e.get("type") == "ITEM_PURCHASED":
                pid = e.get("participantId")
                if pid: inventory[pid].add(e.get("itemId"))
            elif e.get("type") in ("ITEM_DESTROYED", "ITEM_SOLD", "ITEM_UNDO"):
                pid = e.get("participantId")
                if pid and e.get("itemId") in inventory[pid]:
                    inventory[pid].remove(e.get("itemId"))
                    
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
                
                for item_id in inventory[pid]:
                    if item_id in _HEALING_ITEM_IDS:
                        weight += 0.6
                        
            gw_weight_timeline[team][frame_idx] = weight
            primary_healer[team][frame_idx] = healer

    for pid in range(1, 11):
        my_team = 100 if pid in team_100 else 200
        enemy_team = 200 if my_team == 100 else 100
        champ = pid_to_champ.get(pid)
        role = pid_to_role.get(pid)
        win = pid_to_win.get(pid)
        
        if not role:
            continue
            
        gw_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") in _GRIEVOUS_ITEM_IDS]
        gw_purchase_ts = gw_purchase_events[0]["timestamp"] if gw_purchase_events else None
        
        boots_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") == 1001]
        boots_purchase_ts = boots_purchase_events[0]["timestamp"] if boots_purchase_events else None
        
        my_deaths_by_enemy = collections.defaultdict(list)
        my_takedowns_on_enemy = collections.defaultdict(list)
        for e in events:
            if e.get("type") == "CHAMPION_KILL":
                if e.get("victimId") == pid:
                    my_deaths_by_enemy[e.get("killerId")].append(e.get("timestamp"))
                if e.get("killerId") == pid or pid in e.get("assistingParticipantIds", []):
                    my_takedowns_on_enemy[e.get("victimId")].append(e.get("timestamp"))

        for t in thresholds:
            trigger_frame = None
            healer_id = None
            
            for f_idx in range(len(frames)):
                if gw_weight_timeline[enemy_team][f_idx] >= t:
                    trigger_frame = f_idx
                    healer_id = primary_healer[enemy_team][f_idx]
                    break
                    
            if trigger_frame is None:
                continue
                
            trigger_ts = frames[trigger_frame]["timestamp"]
            window_end = trigger_ts + 480000
            
            # Did they buy at least one item >= 2500g in the window?
            # Or simpler: total spent in window >= 2500
            spent_in_window = 0
            for e in events:
                if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and trigger_ts <= e.get("timestamp") <= window_end:
                    spent_in_window += ITEM_PRICES.get(e.get("itemId"), 0)
                    
            valid_economy = spent_in_window >= 2500
            
            responded_gw = gw_purchase_ts is not None and gw_purchase_ts <= window_end
            responded_boots = boots_purchase_ts is not None and boots_purchase_ts <= window_end
            
            deaths_after = 0
            takedowns_after = 0
            if healer_id:
                deaths_after = sum(1 for ts in my_deaths_by_enemy[healer_id] if trigger_ts < ts <= window_end)
                takedowns_after = sum(1 for ts in my_takedowns_on_enemy[healer_id] if trigger_ts < ts <= window_end)
                
            # Compute Gold Deficit Bucket at trigger frame
            pf = frames[trigger_frame].get("participantFrames", {})
            my_gold = pf.get(str(pid), {}).get("totalGold", 0)
            
            enemy_gold_total = sum(pf.get(str(ep), {}).get("totalGold", 0) for ep in (team_200 if my_team == 100 else team_100))
            my_team_gold_total = sum(pf.get(str(tp), {}).get("totalGold", 0) for tp in (team_100 if my_team == 100 else team_200))
            
            team_gold_diff = my_team_gold_total - enemy_gold_total
            gold_deficit_bucket = round(team_gold_diff / 2000)
                
            results.append({
                "champ": champ,
                "role": role,
                "threshold": t,
                "valid_economy": valid_economy,
                "responded_gw": responded_gw,
                "responded_boots": responded_boots,
                "deaths_after": deaths_after,
                "takedowns_after": takedowns_after,
                "gold_deficit_bucket": gold_deficit_bucket,
                "win": win
            })
            
    return results

def main():
    log.info("Validation des Déclencheurs Binaires (Anti-soin) sur train complet...")
    raw_dir = r"G:\matches\train"
    if not os.path.exists(raw_dir):
        log.error("Dossier introuvable.")
        return
        
    thresholds = [1.0, 1.5, 2.0, 2.5, 3.0]
    all_results = []
    
    matches_processed = 0
    
    for shard in os.listdir(raw_dir):
        shard_path = os.path.join(raw_dir, shard)
        if not os.path.isdir(shard_path): continue
            
        for mf in os.listdir(shard_path):
            if not mf.endswith(".json.gz") or mf.endswith("_timeline.json.gz"): continue
                
            try:
                with gzip.open(os.path.join(shard_path, mf), "rt") as f: match_data = json.load(f)
                with gzip.open(os.path.join(shard_path, mf.replace(".json.gz", "_timeline.json.gz")), "rt") as f: timeline_data = json.load(f)
                
                res = process_match(match_data, timeline_data, thresholds)
                all_results.extend(res)
                matches_processed += 1
                if matches_processed % 5000 == 0:
                    log.info(f"{matches_processed} matchs lus...")
            except Exception:
                pass
                
    log.info(f"Lecture terminée. {matches_processed} parties traitées.")
    
    def print_analysis(condition_name, resp_key):
        for t in thresholds:
            log.info(f"--- ANALYSE AU SEUIL {t} [{condition_name}] ---")
            
            strata = collections.defaultdict(lambda: {"responded": [], "ignored": []})
            for r in all_results:
                if r["threshold"] != t: continue
                if not r["valid_economy"]: continue # BOTH must have spent >= 2500g
                
                key = (r["champ"], r["role"], r["gold_deficit_bucket"])
                if r[resp_key]:
                    strata[key]["responded"].append(r)
                else:
                    strata[key]["ignored"].append(r)
                    
            diff_deaths = []
            diff_takedowns = []
            diff_wins = []
            
            for key, data in strata.items():
                n_pairs = min(len(data["responded"]), len(data["ignored"]))
                if n_pairs == 0: continue
                
                for i in range(n_pairs):
                    d_resp = data["responded"][i]["deaths_after"]
                    d_ign = data["ignored"][i]["deaths_after"]
                    diff_deaths.append(d_resp - d_ign)
                    
                    tk_resp = data["responded"][i]["takedowns_after"]
                    tk_ign = data["ignored"][i]["takedowns_after"]
                    diff_takedowns.append(tk_resp - tk_ign)
                    
                    w_resp = 1.0 if data["responded"][i]["win"] else 0.0
                    w_ign = 1.0 if data["ignored"][i]["win"] else 0.0
                    diff_wins.append(w_resp - w_ign)
                    
            n_pairs = len(diff_deaths)
            if n_pairs == 0:
                log.info("Aucune paire trouvée.")
                continue
                
            # Stats on Deaths
            mean_deaths = sum(diff_deaths) / n_pairs
            var_deaths = sum((d - mean_deaths)**2 for d in diff_deaths) / (n_pairs - 1) if n_pairs > 1 else 0
            se_deaths = math.sqrt(var_deaths / n_pairs) if n_pairs > 0 else 0
            
            # Stats on Takedowns
            mean_takedowns = sum(diff_takedowns) / n_pairs
            var_takedowns = sum((d - mean_takedowns)**2 for d in diff_takedowns) / (n_pairs - 1) if n_pairs > 1 else 0
            se_takedowns = math.sqrt(var_takedowns / n_pairs) if n_pairs > 0 else 0
            
            # Stats on Winrate
            mean_win = sum(diff_wins) / n_pairs
            var_win = sum((w - mean_win)**2 for w in diff_wins) / (n_pairs - 1) if n_pairs > 1 else 0
            se_win = math.sqrt(var_win / n_pairs) if n_pairs > 0 else 0
            
            mde_win = 1.96 * math.sqrt(0.5 / n_pairs) * 2
            
            log.info(f"N paires = {n_pairs}")
            log.info(f"[PROXIMAL] Écart morts par le soigneur : {mean_deaths:+.3f} morts (SE={se_deaths:.3f})")
            log.info(f"[PROXIMAL] Écart takedowns sur soigneur: {mean_takedowns:+.3f} tkdwn (SE={se_takedowns:.3f})")
            log.info(f"[WINRATE]  Écart victoire             : {mean_win*100:+.1f}% (SE={se_win*100:.1f}%) | MDE={mde_win*100:.1f}%")
            
            if se_deaths > 0:
                t_deaths = mean_deaths / se_deaths
                log.info(f"t-stat Morts: {t_deaths:.2f} ({'SIGNIFICATIF' if abs(t_deaths)>1.96 else 'NS'})")
                
            if se_takedowns > 0:
                t_tk = mean_takedowns / se_takedowns
                log.info(f"t-stat Takedowns: {t_tk:.2f} ({'SIGNIFICATIF' if abs(t_tk)>1.96 else 'NS'})")
            
            log.info("-" * 40)
            
    print_analysis("ANTI-SOIN", "responded_gw")
    print_analysis("CONTRÔLE NÉGATIF (BOTTES)", "responded_boots")

if __name__ == "__main__":
    main()
