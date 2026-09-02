#!/usr/bin/env python3
import os
import gzip
import json
import logging
import collections
import sys

import sys
import collections
from typing import Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.champion_affinity import ChampionAffinity
from scripts.extract_core_items import _POSITION_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("validate_threat")

DEFENSIVE_REACTIVE_IDS = {3026, 3157, 3139, 3102, 3143, 3193, 3075} # Ange Gardien, Zhonya, Cimeterre, Voile, Randuin, Lithoplastron, Cotte épineuse etc.
DEFENSIVE_STANDARD_IDS = {3053, 3110, 4401, 3119, 3065, 3083, 3111} # Sterak, Cœur gelé, Force de la Nature, etc.

def get_item_intent(item_id, champ_name, aff: ChampionAffinity):
    """
    Détermine si l'achat est défensif hors-build, défensif standard, ou offensif.
    """
    prof = aff.profile(champ_name)
    core = prof.get("core_items", [])
    
    # Si c'est un pur objet défensif
    if item_id in DEFENSIVE_REACTIVE_IDS:
        if str(item_id) in core or item_id == 3157 and prof.get("damage_mix", {}).get("ap", 0) > 0.6:
            return "DEFENSIVE_STANDARD"
        return "DEFENSIVE_REACTIVE"
        
    if item_id in DEFENSIVE_STANDARD_IDS:
        if str(item_id) in core or "Fighter" in prof.get("tags", []) or "Tank" in prof.get("tags", []):
            return "DEFENSIVE_STANDARD"
        return "DEFENSIVE_REACTIVE"
        
    return "OFFENSIVE"

def process_match_for_threat(match_data: dict, timeline_data: dict, aff: ChampionAffinity):
    frames = timeline_data.get("info", {}).get("frames", [])
    events = [e for f in frames for e in f.get("events", [])]
    
    # 1. Traquer les morts (qui tue qui, à quelle minute)
    kill_events = [e for e in events if e.get("type") == "CHAMPION_KILL"]
    
    # Grouper les morts par victime
    # kill_history[victim_id] = [(timestamp, killer_id), ...]
    kill_history = collections.defaultdict(list)
    for k in kill_events:
        kill_history[k["victimId"]].append((k["timestamp"], k.get("killerId", 0)))
        
    results = []
    
    for pid in range(1, 11):
        my_deaths = kill_history[pid]
        if not my_deaths: continue
            
        # Chercher s'il y a 3+ morts par le même tueur en < 5 mins (300000 ms)
        # On va tester toutes les fenêtres glissantes
        snowball_killer = None
        snowball_ts = None
        
        for i in range(len(my_deaths) - 2):
            t1, k1 = my_deaths[i]
            t3, k3 = my_deaths[i+2]
            
            if k1 == k3 and my_deaths[i+1][1] == k1 and k1 > 0:
                if t3 - t1 <= 300000: # 5 mins
                    snowball_killer = k1
                    snowball_ts = t3
                    break
                    
        if not snowball_killer:
            continue
            
        # Le joueur s'est fait chain-kill. Quel est son premier objet complet acheté APRES ça ?
        p_info = next(p for p in match_data["info"]["participants"] if p["participantId"] == pid)
        champ = p_info["championName"]
        role = _POSITION_MAP.get(p_info.get("teamPosition", ""))
        
        purchase_event = next((e for e in events if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid and e.get("timestamp") > snowball_ts), None)
        if not purchase_event: continue
            
        item_id = purchase_event["itemId"]
        intent = get_item_intent(item_id, champ, aff)
        
        # Déficit d'or au moment de l'achat
        # On cherche la frame correspondant au timestamp
        t_purchase = purchase_event["timestamp"]
        frame_idx = int(t_purchase // 60000)
        if frame_idx >= len(frames): frame_idx = len(frames) - 1
            
        pf = frames[frame_idx].get("participantFrames", {})
        my_pf = pf.get(str(pid), {})
        killer_pf = pf.get(str(snowball_killer), {})
        
        my_total_gold = my_pf.get("totalGold", 0)
        killer_total_gold = killer_pf.get("totalGold", 0)
        gold_deficit_bucket = round((my_total_gold - killer_total_gold) / 1000)
        
        # Conséquence: nombre de morts face à CE tueur dans les 10 mins suivantes
        deaths_after = sum(1 for t, k in my_deaths if t > t_purchase and t <= t_purchase + 600000 and k == snowball_killer)
        
        results.append({
            "champ": champ,
            "role": role,
            "deficit_bucket": gold_deficit_bucket,
            "intent": intent,
            "deaths_after": deaths_after
        })
        
    return results

def main():
    log.info("Validation du Threat Vector...")
    raw_dir = r"G:\matches\train"
    if not os.path.exists(raw_dir):
        log.error("Dossier introuvable.")
        return
        
    aff = ChampionAffinity("data/champion_item_profiles.json", "data")
    
    all_instances = []
    
    matches_processed = 0
    for shard in os.listdir(raw_dir):
        shard_path = os.path.join(raw_dir, shard)
        if not os.path.isdir(shard_path): continue
            
        for mf in os.listdir(shard_path):
            if not mf.endswith(".json.gz") or mf.endswith("_timeline.json.gz"): continue
                
            try:
                with gzip.open(os.path.join(shard_path, mf), "rt") as f: match_data = json.load(f)
                with gzip.open(os.path.join(shard_path, mf.replace(".json.gz", "_timeline.json.gz")), "rt") as f: timeline_data = json.load(f)
                
                res = process_match_for_threat(match_data, timeline_data, aff)
                all_instances.extend(res)
                matches_processed += 1
                if matches_processed % 500 == 0: log.info(f"{matches_processed} matchs lus...")
            except Exception:
                pass
                
    log.info(f"Terminé. {len(all_instances)} cas de snowball trouvés sur {matches_processed} parties.")
    
    # Appariement (Matching)
    # Grouper par (champ, role, deficit_bucket)
    strata = collections.defaultdict(lambda: {"defensive": [], "offensive": []})
    
    for i in all_instances:
        key = (i["champ"], i["role"], i["deficit_bucket"])
        if i["intent"] == "DEFENSIVE_REACTIVE":
            strata[key]["defensive"].append(i)
        elif i["intent"] == "OFFENSIVE":
            strata[key]["offensive"].append(i)
            
    # Comparaison appariée
    diff_values = []
    
    for key, data in strata.items():
        n_pairs = min(len(data["defensive"]), len(data["offensive"]))
        if n_pairs == 0: continue
            
        for i in range(n_pairs):
            off_deaths = data["offensive"][i]["deaths_after"]
            def_deaths = data["defensive"][i]["deaths_after"]
            diff_values.append(off_deaths - def_deaths)
            
    total_pairs = len(diff_values)
        
    if total_pairs > 1:
        mean_diff = sum(diff_values) / total_pairs
        variance = sum((d - mean_diff)**2 for d in diff_values) / (total_pairs - 1)
        sd = variance ** 0.5
        se = sd / (total_pairs ** 0.5)
        t_stat = mean_diff / se if se > 0 else 0
        
        log.info(f"--- RÉSULTATS APPARIÉS (N={total_pairs} paires) ---")
        log.info(f"Réduction des morts si Achat Défensif : {mean_diff:.2f} morts en moins")
        log.info(f"Écart-type (SD) = {sd:.2f} | Erreur Standard (SE) = {se:.2f}")
        log.info(f"Intervalle de confiance à 95% = [{mean_diff - 1.96*se:.2f}, {mean_diff + 1.96*se:.2f}]")
        log.info(f"Statistique t = {t_stat:.2f}")
        
        if (mean_diff - 1.96*se) > 0:
            log.info("CONCLUSION : L'achat défensif réduit SIGNIFICATIVEMENT les morts. (Validé)")
        else:
            log.info("CONCLUSION : La réduction N'EST PAS statistiquement significative. (Provisoire, nécessite plus de données)")
    else:
        log.info("Pas assez de données pour apparier (>1).")

if __name__ == "__main__":
    main()
