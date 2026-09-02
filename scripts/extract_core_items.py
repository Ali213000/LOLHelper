#!/usr/bin/env python3
import os
import gzip
import json
import logging
import collections
import requests
import argparse
import sys
import math
from typing import Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.champion_affinity import ChampionAffinity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("extract_core")

_POSITION_MAP = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUPPORT",
}

def load_ddragon_data():
    ver = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[0]
    items_data = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/item.json").json()["data"]
    
    completed_items = set()
    item_costs = {}
    for item_id, item in items_data.items():
        cost = item.get("gold", {}).get("total", 0)
        item_costs[int(item_id)] = cost
        
        name = item.get("name")
        if name:
            item_costs[name] = cost

        if cost >= 1500 and not item.get("into"):
            completed_items.add(int(item_id))
            
    champs_data = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json").json()["data"]
    champion_tags = {}
    for cname, cinfo in champs_data.items():
        champion_tags[cname] = cinfo.get("tags", [])
        
    return completed_items, champion_tags

def get_lane_opp_type(champ_name: str, aff: ChampionAffinity) -> str:
    prof = aff.profile(champ_name)
    mix = prof.get("damage_mix")
    if not mix:
        return "UNKNOWN"
    if mix.get("ap", 0) >= 0.60: return "AP"
    if mix.get("ad", 0) >= 0.60: return "AD"
    return "HYBRID"

def replay_inventory_to_find_all(events: list, pid: int, completed_item_ids: set) -> list:
    inv = []
    core_items = []
    for e in events:
        if e.get("participantId") != pid: continue
        t = e.get("type")
        item_id = e.get("itemId")
        if t == "ITEM_PURCHASED":
            inv.append(item_id)
            if item_id in completed_item_ids and item_id not in core_items:
                core_items.append(item_id)
        elif t in ("ITEM_SOLD", "ITEM_DESTROYED"):
            if item_id in inv: inv.remove(item_id)
        elif t == "ITEM_UNDO":
            after_id = e.get("afterId", 0)
            before_id = e.get("beforeId", 0)
            if after_id in inv: inv.remove(after_id)
            if before_id:
                inv.append(before_id)
                if after_id in core_items: core_items.remove(after_id)
    return core_items

def process_match(match_data: dict, timeline_data: dict, completed_items: set, champion_tags: dict, aff: ChampionAffinity) -> list:
    results = []
    info = match_data.get("info", {})
    participants = info.get("participants", [])
    
    frames = timeline_data.get("info", {}).get("frames", [])
    events = [e for f in frames for e in f.get("events", [])]
        
    for p in participants:
        role = _POSITION_MAP.get(p.get("teamPosition", ""))
        champ = p.get("championName", "")
        pid = p.get("participantId")
        team_id = p.get("teamId")
        
        if not role or not champ: continue
            
        all_completed = replay_inventory_to_find_all(events, pid, completed_items)
        if len(all_completed) >= 2:
            core_items = tuple(all_completed[:2])
            situational_items = all_completed[2:]
            
            enemy_team = [ep for ep in participants if ep.get("teamId") != team_id]
            opp = next((ep for ep in enemy_team if _POSITION_MAP.get(ep.get("teamPosition")) == role), None)
            lane_opp_champ = opp.get("championName") if opp else None
            
            lane_opp_type = get_lane_opp_type(lane_opp_champ, aff) if lane_opp_champ else "UNKNOWN"
            enemy_tank_count = sum(1 for ep in enemy_team if "Tank" in champion_tags.get(ep.get("championName", ""), []))
            tank_cat = "tank_hi" if enemy_tank_count >= 2 else "tank_lo"
            
            results.append({
                "champ": champ,
                "role": role,
                "items": core_items,
                "situational": situational_items,
                "lane_opp_type": lane_opp_type,
                "enemy_tank_count": tank_cat
            })
            
    return results

def get_counts(instances):
    counts = collections.defaultdict(float)
    for i in instances:
        counts[i["items"]] += 1.0
    return counts

def max_lift(instances, feature_name, global_counts):
    strata = collections.defaultdict(list)
    for i in instances:
        strata[i[feature_name]].append(i)
        
    best_lift = 0.0
    total_g = sum(global_counts.values())
    
    for val, subset in strata.items():
        if len(subset) < 50: continue
        s_counts = get_counts(subset)
        if not s_counts: continue
        top_item, top_count = collections.Counter(s_counts).most_common(1)[0]
        
        p_s = top_count / len(subset)
        p_g = global_counts[top_item] / total_g
        lift = p_s / max(p_g, 1e-6)
        if lift > best_lift:
            best_lift = lift
    return best_lift

def shrunk_rate(n_item_stratum, n_stratum, p_global, k=150):
    return (n_item_stratum + k * p_global) / (n_stratum + k)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="data/matches", help="Dossier contenant les sous-dossiers de matchs (train/)")
    ap.add_argument("--limit", type=int, default=0, help="Limiter le nombre de fichiers analysés")
    args = ap.parse_args()

    train_dir = os.path.join(args.raw_dir, "train")
    if not os.path.exists(train_dir):
        log.error(f"Le dossier {train_dir} n'existe pas. Veuillez lancer la collecte d'abord.")
        return
        
    completed_items, champion_tags = load_ddragon_data()
    log.info(f"{len(completed_items)} items complets, {len(champion_tags)} champions identifiés.")
    
    aff = ChampionAffinity("data/champion_item_profiles.json", champion_tables_dir="data")
    
    # regroup: grouped_instances[(champ, role)] = [ {items, lane_opp_type, enemy_tank_count, situational}, ... ]
    grouped_instances = collections.defaultdict(list)
    matches_processed = 0
    situational_counts = collections.defaultdict(lambda: collections.Counter())
    role_champ_games = collections.defaultdict(int)
    
    for shard in os.listdir(train_dir):
        shard_path = os.path.join(train_dir, shard)
        if not os.path.isdir(shard_path): continue
            
        files = os.listdir(shard_path)
        match_files = [f for f in files if f.endswith(".json.gz") and not f.endswith("_timeline.json.gz")]
        
        for mf in match_files:
            match_path = os.path.join(shard_path, mf)
            timeline_path = os.path.join(shard_path, mf.replace(".json.gz", "_timeline.json.gz"))
            if not os.path.exists(timeline_path): continue
                
            try:
                with gzip.open(match_path, "rt") as f: match_data = json.load(f)
                with gzip.open(timeline_path, "rt") as f: timeline_data = json.load(f)
                    
                participant_results = process_match(match_data, timeline_data, completed_items, champion_tags, aff)
                for res in participant_results:
                    key = (res["champ"], res["role"])
                    grouped_instances[key].append(res)
                    role_champ_games[key] += 1
                    for sit_item in set(res.get("situational", [])):
                        situational_counts[key][sit_item] += 1
                    
                matches_processed += 1
                if matches_processed % 100 == 0:
                    log.info(f"{matches_processed} matchs traités...")
            except Exception as e:
                log.error(f"Erreur sur {mf}: {e}")
                
    log.info(f"Extraction terminée sur {matches_processed} matchs. Génération de la table...")
    
    prescription_table = {}
    depth_stats = {0: 0, 1: 0, 2: 0}
    
    for (champ, role), instances in grouped_instances.items():
        total_n = len(instances)
        if total_n < 50: continue
            
        global_counts = get_counts(instances)
        global_top_item, global_top_count = collections.Counter(global_counts).most_common(1)[0]
        p_global_top = global_top_count / total_n
        
        # Evaluer les features
        lift_lane = max_lift(instances, "lane_opp_type", global_counts)
        lift_tank = max_lift(instances, "enemy_tank_count", global_counts)
        
        features_ranked = sorted([("lane_opp_type", lift_lane), ("enemy_tank_count", lift_tank)], key=lambda x: -x[1])
        strata_order = [f for f, l in features_ranked]
        
        champ_dict = {
            "strata_order": strata_order,
            "global": {
                "items": list(global_top_item),
                "confidence": round(p_global_top, 3), # pour le global, pas besoin de shrinkage car c'est la baseline
                "samples": total_n
            },
            "strata": {}
        }
        
        # Construction hiérarchique avec arrêt conditionnel
        min_n = 200
        min_lift = 1.8
        max_depth_reached = 0
        strata_kept = 0
        strata_tested = 0
        
        # Depth 1
        f1 = strata_order[0]
        strata_d1 = collections.defaultdict(list)
        for i in instances: strata_d1[i[f1]].append(i)
            
        valid_d1 = {}
        for val1, sub1 in strata_d1.items():
            strata_tested += 1
            if len(sub1) < min_n: continue
            s_counts = get_counts(sub1)
            if not s_counts: continue
            s_top_item, s_top_count = collections.Counter(s_counts).most_common(1)[0]
            
            p_s = s_top_count / len(sub1)
            p_g = global_counts[s_top_item] / total_n
            cond_lift = p_s / max(p_g, 1e-6)
            
            if cond_lift >= min_lift:
                max_depth_reached = max(max_depth_reached, 1)
                strata_kept += 1
                shrunk = shrunk_rate(s_top_count, len(sub1), p_g)
                champ_dict["strata"][str(val1)] = {
                    "items": list(s_top_item),
                    "confidence": round(shrunk, 3),
                    "samples": len(sub1),
                    "lift": round(cond_lift, 2)
                }
                valid_d1[val1] = (sub1, s_top_item, s_top_count)
                
        # Depth 2 (seulement si depth 1 a passé)
        f2 = strata_order[1]
        for val1, (sub1, p1_top_item, p1_top_count) in valid_d1.items():
            strata_d2 = collections.defaultdict(list)
            for i in sub1: strata_d2[i[f2]].append(i)
                
            p_parent = p1_top_count / len(sub1)
            for val2, sub2 in strata_d2.items():
                strata_tested += 1
                if len(sub2) < min_n: continue
                s_counts = get_counts(sub2)
                if not s_counts: continue
                s_top_item, s_top_count = collections.Counter(s_counts).most_common(1)[0]
                
                p_s = s_top_count / len(sub2)
                # Lift par rapport au parent (profondeur 1)
                cond_lift = p_s / max(p_parent, 1e-6)
                
                if cond_lift >= min_lift:
                    max_depth_reached = max(max_depth_reached, 2)
                    strata_kept += 1
                    # Shrinkage par rapport au global de cet item
                    p_g = global_counts[s_top_item] / total_n
                    shrunk = shrunk_rate(s_top_count, len(sub2), p_g)
                    key = f"{val1}|{val2}"
                    champ_dict["strata"][key] = {
                        "items": list(s_top_item),
                        "confidence": round(shrunk, 3),
                        "samples": len(sub2),
                        "lift": round(cond_lift, 2)
                    }

        prescription_table[f"{champ}|{role}"] = champ_dict
        depth_stats[max_depth_reached] += 1
        
        if max_depth_reached > 0:
            log.info(f"{champ}|{role} : profondeur {max_depth_reached} ({strata_kept} strates retenues sur {strata_tested} testées)")
            
    os.makedirs("data", exist_ok=True)
    with open("data/core_items_prescription.json", "w", encoding="utf-8") as f:
        json.dump(prescription_table, f, indent=2, ensure_ascii=False)
        
    log.info(f"Table générée avec {len(prescription_table)} entrées.")
    log.info(f"Profondeur max atteinte : Depth 2: {depth_stats[2]}, Depth 1: {depth_stats[1]}, Depth 0: {depth_stats[0]}")
    
    # Export Situational Frequencies
    situational_frequencies = {}
    for (champ, role), counter in situational_counts.items():
        total_games = role_champ_games[(champ, role)]
        if total_games == 0: continue
            
        freqs = {}
        for item_id, count in counter.items():
            rate = count / total_games
            if rate >= 0.03 and count >= 50:
                freqs[str(item_id)] = {"rate": round(rate, 3), "samples": count}
                
        if freqs:
            situational_frequencies[f"{champ}|{role}"] = freqs
            
    with open("data/situational_frequencies.json", "w", encoding="utf-8") as f:
        json.dump(situational_frequencies, f, indent=2, ensure_ascii=False)
    log.info(f"Garde-fous situationnels générés pour {len(situational_frequencies)} champions/rôles.")

if __name__ == "__main__":
    main()
