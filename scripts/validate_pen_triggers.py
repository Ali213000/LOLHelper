#!/usr/bin/env python3
import os
import gzip
import json
import logging
import collections
import sys
import math

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stat_analyzer import _ARMOR_PEN_PCT_ITEMS, _MAGIC_PEN_PCT_ITEMS
from scripts.extract_core_items import _POSITION_MAP
from services.image_cache import ImageCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("validate_pen")

cache = ImageCache()
_ARMOR_PEN_IDS = {cache.get_item_id_by_name(n) for n in _ARMOR_PEN_PCT_ITEMS if cache.get_item_id_by_name(n)}
_MAGIC_PEN_IDS = {cache.get_item_id_by_name(n) for n in _MAGIC_PEN_PCT_ITEMS if cache.get_item_id_by_name(n)}

ITEM_PRICES = {}
try:
    with open("assets/item_data.json", "r", encoding="utf-8") as f:
        item_data = json.load(f).get("data", {})
        for i_id, i_info in item_data.items():
            ITEM_PRICES[int(i_id)] = i_info.get("gold", {}).get("total", 0)
except Exception as e:
    log.error(f"Failed to load item_data.json: {e}")

def get_stats_from_assets():
    path = "assets/champion_data.json"
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("data", {})

CHAMP_STATS_DB = get_stats_from_assets()

def estimate_armor_mr(champ_name: str, level: int):
    stats = CHAMP_STATS_DB.get(champ_name, {}).get("stats", {})
    if not stats:
        return 50, 50
    armor = stats.get("armor", 30) + stats.get("armorperlevel", 0) * (level - 1)
    mr = stats.get("spellblock", 30) + stats.get("spellblockperlevel", 0) * (level - 1)
    return armor, mr

def process_match(match_data: dict, timeline_data: dict, thresholds: list[float], pen_type: str):
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
    
    # Pre-calculate tank count per frame based on current items/level
    # Wait, we need level per frame.
    # Level is in participantFrames
    
    enemy_tanks_timeline = {100: [0]*len(frames), 200: [0]*len(frames)}
    
    for frame_idx, frame in enumerate(frames):
        for e in frame.get("events", []):
            if e.get("type") == "ITEM_PURCHASED":
                pid = e.get("participantId")
                if pid: inventory[pid].add(e.get("itemId"))
            elif e.get("type") in ("ITEM_DESTROYED", "ITEM_SOLD", "ITEM_UNDO"):
                pid = e.get("participantId")
                if pid and e.get("itemId") in inventory[pid]:
                    inventory[pid].remove(e.get("itemId"))
                    
        pf = frame.get("participantFrames", {})
        for team, pids in [(100, team_100), (200, team_200)]:
            tanks = set()
            for pid in pids:
                champ = pid_to_champ.get(pid, "")
                lvl = pf.get(str(pid), {}).get("level", 1)
                if pen_type == "ARMOR":
                    armor, _ = estimate_armor_mr(champ, lvl)
                    if armor > 70:
                        items_armor = sum(40 for iid in inventory[pid] if iid in [3068, 3075, 3082, 3143])
                        if armor + items_armor >= 120:
                            tanks.add(pid)
                elif pen_type == "MAGIC":
                    _, mr = estimate_armor_mr(champ, lvl)
                    if mr > 50:
                        items_mr = sum(40 for iid in inventory[pid] if iid in [3193, 3065, 4401, 3222, 6664])
                        if mr + items_mr >= 80:
                            tanks.add(pid)
            enemy_tanks_timeline[team][frame_idx] = tanks

    for pid in range(1, 11):
        my_team = 100 if pid in team_100 else 200
        my_team_pids = team_100 if my_team == 100 else team_200
        enemy_team = 200 if my_team == 100 else 100
        champ = pid_to_champ.get(pid)
        role = pid_to_role.get(pid)
        win = pid_to_win.get(pid)
        
        if not role:
            continue
            
        if pen_type == "ARMOR":
            pen_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") in _ARMOR_PEN_IDS]
            pen_purchase_ts = pen_purchase_events[0]["timestamp"] if pen_purchase_events else None
            
            _AD_CONTROL_ITEMS = {3031, 3072, 6676, 6672, 3153, 3142, 6692}
            ctrl_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") in _AD_CONTROL_ITEMS]
            ctrl_purchase_ts = ctrl_purchase_events[0]["timestamp"] if ctrl_purchase_events else None
        elif pen_type == "MAGIC":
            pen_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") in _MAGIC_PEN_IDS]
            pen_purchase_ts = pen_purchase_events[0]["timestamp"] if pen_purchase_events else None
            
            _AP_CONTROL_ITEMS = {3089, 4645, 6655, 3157, 3020, 3115}
            ctrl_purchase_events = [e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("itemId") in _AP_CONTROL_ITEMS]
            ctrl_purchase_ts = ctrl_purchase_events[0]["timestamp"] if ctrl_purchase_events else None
        
        for t in thresholds:
            trigger_frame = None
            tank_pids = set()
            for f_idx in range(len(frames)):
                if len(enemy_tanks_timeline[enemy_team][f_idx]) >= t:
                    trigger_frame = f_idx
                    tank_pids = enemy_tanks_timeline[enemy_team][f_idx]
                    break
                    
            if trigger_frame is None:
                continue
                
            trigger_ts = frames[trigger_frame]["timestamp"]
            window_end = trigger_ts + 480000
            
            end_frame = trigger_frame
            for f_idx in range(trigger_frame, len(frames)):
                if frames[f_idx]["timestamp"] <= window_end:
                    end_frame = f_idx
                else:
                    break
                    
            pf_start = frames[trigger_frame].get("participantFrames", {})
            pf_end = frames[end_frame].get("participantFrames", {})
            
            my_gold_start = pf_start.get(str(pid), {}).get("totalGold", 0)
            my_gold_end = pf_end.get(str(pid), {}).get("totalGold", 0)
            
            if pen_type == "ARMOR":
                my_dmg_start = pf_start.get(str(pid), {}).get("damageStats", {}).get("physicalDamageDoneToChampions", 0)
                my_dmg_end = pf_end.get(str(pid), {}).get("damageStats", {}).get("physicalDamageDoneToChampions", 0)
                
                my_team_dmg_start = sum(pf_start.get(str(p), {}).get("damageStats", {}).get("physicalDamageDoneToChampions", 0) for p in my_team_pids)
                my_team_dmg_end = sum(pf_end.get(str(p), {}).get("damageStats", {}).get("physicalDamageDoneToChampions", 0) for p in my_team_pids)
            elif pen_type == "MAGIC":
                my_dmg_start = pf_start.get(str(pid), {}).get("damageStats", {}).get("magicDamageDoneToChampions", 0)
                my_dmg_end = pf_end.get(str(pid), {}).get("damageStats", {}).get("magicDamageDoneToChampions", 0)
                
                my_team_dmg_start = sum(pf_start.get(str(p), {}).get("damageStats", {}).get("magicDamageDoneToChampions", 0) for p in my_team_pids)
                my_team_dmg_end = sum(pf_end.get(str(p), {}).get("damageStats", {}).get("magicDamageDoneToChampions", 0) for p in my_team_pids)
            
            dmg_in_window = my_dmg_end - my_dmg_start
            
            team_dmg_in_window = my_team_dmg_end - my_team_dmg_start
            team_dmg_pct = dmg_in_window / team_dmg_in_window if team_dmg_in_window > 0 else 0
            
            takedowns_on_tanks = 0
            spent_in_window = 0
            for e in events:
                if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and trigger_ts <= e.get("timestamp") <= window_end:
                    spent_in_window += ITEM_PRICES.get(e.get("itemId"), 0)
                elif e.get("type") == "CHAMPION_KILL" and trigger_ts <= e.get("timestamp") <= window_end:
                    if e.get("victimId") in tank_pids:
                        if e.get("killerId") == pid or pid in e.get("assistingParticipantIds", []):
                            takedowns_on_tanks += 1
                            
            valid_economy = spent_in_window >= 2500
            responded_pen = pen_purchase_ts is not None and pen_purchase_ts <= window_end
            responded_ctrl = ctrl_purchase_ts is not None and ctrl_purchase_ts <= window_end
            
            enemy_gold_total = sum(pf_start.get(str(ep), {}).get("totalGold", 0) for ep in (team_200 if my_team == 100 else team_100))
            my_team_gold_total = sum(pf_start.get(str(tp), {}).get("totalGold", 0) for tp in my_team_pids)
            
            team_gold_diff = my_team_gold_total - enemy_gold_total
            gold_deficit_bucket = round(team_gold_diff / 2000)
                
            results.append({
                "champ": champ,
                "role": role,
                "threshold": t,
                "valid_economy": valid_economy,
                "responded_pen": responded_pen,
                "responded_ctrl": responded_ctrl,
                "dmg_in_window": dmg_in_window,
                "team_dmg_pct": team_dmg_pct,
                "takedowns_on_tanks": takedowns_on_tanks,
                "gold_deficit_bucket": gold_deficit_bucket,
                "win": win
            })
            
    return results

def main():
    log.info("Validation des Déclencheurs de Pénétration d'Armure sur train complet...")
    raw_dir = r"G:\matches\train"
    if not os.path.exists(raw_dir):
        return
        
    thresholds = [1, 2, 3] 
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
                
                res = process_match(match_data, timeline_data, thresholds, "MAGIC")
                all_results.extend(res)
                matches_processed += 1
                if matches_processed % 5000 == 0:
                    log.info(f"{matches_processed} matchs lus...")
            except Exception as e:
                import traceback
                traceback.print_exc()
                
    log.info(f"Lecture terminée. {matches_processed} parties traitées.")
    
    def print_analysis(condition_name, resp_key, ignored_key):
        for t in thresholds:
            log.info(f"--- ANALYSE AU SEUIL {t} TANKS [{condition_name}] ---")
            
            strata = collections.defaultdict(lambda: {"responded": [], "ignored": []})
            for r in all_results:
                if r["threshold"] != t: continue
                if not r["valid_economy"]: continue
                if r["role"] not in ["ADC", "TOP", "MID", "JUNGLE"]: continue
                
                key = (r["champ"], r["role"], r["gold_deficit_bucket"])
                
                if condition_name == "CONTROLE NEGATIF":
                    # responded: bought control AD item
                    # ignored: bought neither pen nor control item
                    if r["responded_ctrl"] and not r["responded_pen"]:
                        strata[key]["responded"].append(r)
                    elif not r["responded_ctrl"] and not r["responded_pen"]:
                        strata[key]["ignored"].append(r)
                else:
                    # responded: bought pen item
                    # ignored: bought control AD item (meaning they opted for pure AD instead of Pen)
                    if r["responded_pen"]:
                        strata[key]["responded"].append(r)
                    elif r["responded_ctrl"] and not r["responded_pen"]:
                        strata[key]["ignored"].append(r)
                    
            diff_dmg = []
            diff_pct = []
            diff_tkd = []
            
            for key, data in strata.items():
                n_pairs = min(len(data["responded"]), len(data["ignored"]))
                if n_pairs == 0: continue
                
                for i in range(n_pairs):
                    dmg_resp = data["responded"][i]["dmg_in_window"]
                    dmg_ign = data["ignored"][i]["dmg_in_window"]
                    diff_dmg.append(dmg_resp - dmg_ign)
                    
                    pct_resp = data["responded"][i]["team_dmg_pct"]
                    pct_ign = data["ignored"][i]["team_dmg_pct"]
                    diff_pct.append(pct_resp - pct_ign)
                    
                    tkd_resp = data["responded"][i]["takedowns_on_tanks"]
                    tkd_ign = data["ignored"][i]["takedowns_on_tanks"]
                    diff_tkd.append(tkd_resp - tkd_ign)
                    
            n_pairs = len(diff_dmg)
            if n_pairs == 0:
                log.info("Aucune paire trouvée.")
                continue
                
            mean_dmg = sum(diff_dmg) / n_pairs
            var_dmg = sum((d - mean_dmg)**2 for d in diff_dmg) / (n_pairs - 1) if n_pairs > 1 else 0
            se_dmg = math.sqrt(var_dmg / n_pairs) if n_pairs > 0 else 0
            
            mean_pct = sum(diff_pct) / n_pairs
            var_pct = sum((d - mean_pct)**2 for d in diff_pct) / (n_pairs - 1) if n_pairs > 1 else 0
            se_pct = math.sqrt(var_pct / n_pairs) if n_pairs > 0 else 0
            
            mean_tkd = sum(diff_tkd) / n_pairs
            var_tkd = sum((d - mean_tkd)**2 for d in diff_tkd) / (n_pairs - 1) if n_pairs > 1 else 0
            se_tkd = math.sqrt(var_tkd / n_pairs) if n_pairs > 0 else 0
            
            log.info(f"N paires = {n_pairs}")
            log.info(f"[PROXIMAL] Écart Dégâts         : {mean_dmg:+.0f} (SE={se_dmg:.0f})")
            log.info(f"[PROXIMAL] Écart Part dégâts    : {mean_pct*100:+.2f}% (SE={se_pct*100:.2f}%)")
            log.info(f"[PROXIMAL] Écart Takedowns Tanks: {mean_tkd:+.3f} (SE={se_tkd:.3f})")
            
            if se_dmg > 0:
                t_dmg = mean_dmg / se_dmg
                log.info(f"t-stat Dégâts absolus: {t_dmg:.2f} ({'SIGNIFICATIF' if abs(t_dmg)>1.96 else 'NS'})")
                
            if se_tkd > 0:
                t_tkd = mean_tkd / se_tkd
                log.info(f"t-stat Takedowns Tanks: {t_tkd:.2f} ({'SIGNIFICATIF' if abs(t_tkd)>1.96 else 'NS'})")
                
            log.info("-" * 40)
            
    print_analysis("PENETRATION VS PURE AD", "responded_pen", "responded_ctrl")
    print_analysis("CONTROLE NEGATIF", "responded_ctrl", "")
    
if __name__ == "__main__":
    main()
