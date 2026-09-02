import argparse
import os
import sys
sys.path.insert(0, os.getcwd())
import json
import logging
import gzip
import collections
import math

from services.stat_analyzer import StatAnalyzer
from data.champion_affinity import ChampionAffinity
from services.image_cache import ImageCache
from core.state_manager import InGameState, PlayerInGameData, LiveStats

log = logging.getLogger("test_engine")
log.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
log.addHandler(ch)

# Mapping DDragon roles to StatAnalyzer expected roles
ROLE_MAP = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUPPORT"
}

def load_champion_stats():
    path = "assets/champion_data.json"
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("data", {})

def reconstruct_stats(champ_name: str, level: int, champ_data_db: dict) -> LiveStats:
    stats_data = champ_data_db.get(champ_name, {}).get("stats", {})
    if not stats_data:
        return LiveStats(100, 100, 2000, 2000, 0, 0, 1.0)
        
    hp_base = stats_data.get("hp", 2000)
    hp_per_lvl = stats_data.get("hpperlevel", 0)
    armor_base = stats_data.get("armor", 100)
    armor_per_lvl = stats_data.get("armorperlevel", 0)
    mr_base = stats_data.get("spellblock", 100)
    mr_per_lvl = stats_data.get("spellblockperlevel", 0)
    
    # Simple linear scaling (game actually uses nonlinear stat growth, but this is close enough for the heuristic)
    level_growth = max(0, level - 1)
    
    return LiveStats(
        armor=armor_base + armor_per_lvl * level_growth,
        magic_resist=mr_base + mr_per_lvl * level_growth,
        max_health=hp_base + hp_per_lvl * level_growth,
        current_health=hp_base + hp_per_lvl * level_growth,
        attack_damage=stats_data.get("attackdamage", 0) + stats_data.get("attackdamageperlevel", 0) * level_growth,
        ability_power=0,
        attack_speed=stats_data.get("attackspeed", 0.625) + stats_data.get("attackspeedperlevel", 0) * level_growth
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=str, default="G:\\matches", help="Dossier des matchs raw")
    parser.add_argument("--limit", type=int, default=0, help="Nombre max d'évaluations (0 = sans limite)")
    args = parser.parse_args()

    # Load components
    analyzer = StatAnalyzer()
    cache = ImageCache()
    aff = ChampionAffinity("data/champion_item_profiles.json", "data")
    analyzer.affinity = aff
    
    # Load Core Prescriptions
    core_prescriptions = {}
    if os.path.exists("data/core_items_prescription.json"):
        with open("data/core_items_prescription.json", "r", encoding="utf-8") as f:
            core_prescriptions = json.load(f)
            
    # Load DDragon Tags
    try:
        import requests
        ver = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()[0]
        champs_data = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json").json()["data"]
        champion_tags = {cname: cinfo.get("tags", []) for cname, cinfo in champs_data.items()}
    except Exception:
        champion_tags = {}

    champ_data_db = load_champion_stats()
    candidate_items = sorted(list(cache.valid_items))
    
    # Allow raw_dir to point directly to G:\matches\test or appending test
    test_dir = os.path.join(args.raw_dir, "test")
    if not os.path.exists(test_dir):
        if os.path.exists(args.raw_dir):
            test_dir = args.raw_dir
        else:
            log.error(f"Le dossier {test_dir} n'existe pas.")
            return

    # Metrics
    total_evals = 0
    top1_hits = 0
    top3_hits = 0
    
    slot_stats = collections.defaultdict(lambda: {
        "evals": 0, "hits": 0,
        "a_accord_win": 0, "a_accord_tot": 0,
        "a_desacc_win": 0, "a_desacc_tot": 0,
        "b_accord_win": 0, "b_accord_tot": 0,
        "b_desacc_win": 0, "b_desacc_tot": 0,
        "a_evals": 0, "b_evals": 0
    })
    tracked_shares = []
    tracked_diffs = []

    for root, dirs, files in os.walk(test_dir):
        for file in files:
            if not file.endswith(".json.gz") or file.endswith("_timeline.json.gz") or file.endswith("timeline.json.gz"):
                continue
                
            base_name = file.replace(".json.gz", "")
            timeline_file = os.path.join(root, f"{base_name}_timeline.json.gz")
            if not os.path.exists(timeline_file):
                continue
                
            try:
                with gzip.open(os.path.join(root, file), 'rt', encoding='utf-8') as f:
                    match_data = json.load(f)
                with gzip.open(timeline_file, 'rt', encoding='utf-8') as f:
                    timeline_data = json.load(f)
            except Exception as e:
                continue
                
            info = match_data.get("info", {})
            participants = info.get("participants", [])
            
            # Map puuid to participant data
            p_map = {p["puuid"]: p for p in participants}
            p_id_map = {p["participantId"]: p for p in participants}
            
            frames = timeline_data.get("info", {}).get("frames", [])
            if not frames:
                continue

            for p in participants:
                puuid = p["puuid"]
                pid = p["participantId"]
                team_id = p["teamId"]
                role_raw = p.get("teamPosition", "UNKNOWN")
                role = ROLE_MAP.get(role_raw, "SUPPORT")
                
                win = p.get("win", False)
                
                # Exclude if no core prescription strata exists
                key = f"{p['championName']}|{role}"
                if key not in core_prescriptions:
                    possible = [k for k in core_prescriptions.keys() if k.startswith(p['championName'] + "|")]
                    if possible:
                        key = max(possible, key=lambda k: core_prescriptions[k].get("global", {}).get("samples", 0))
                    else:
                        continue
                        
                enemy_team_data = [ep for ep in participants if ep["teamId"] != team_id]
                ally_team_data = [ap for ap in participants if ap["teamId"] == team_id]
                
                # Find lane opponent
                lane_opp = next((ep for ep in enemy_team_data if ep.get("teamPosition") == role_raw), None)
                if not lane_opp:
                    # Fallback to closest by level
                    lane_opp = min(enemy_team_data, key=lambda ep: abs(ep["champLevel"] - p["champLevel"])) if enemy_team_data else None
                    
                # Collect item purchases for player
                purchases = []
                for frame in frames:
                    for e in frame.get("events", []):
                        if e.get("type") == "ITEM_PURCHASED" and e.get("participantId") == pid:
                            iid = e.get("itemId")
                            name = cache.get_item_name_by_id(iid)
                            if name in candidate_items:
                                purchases.append({
                                    "timestamp": e.get("timestamp", 0),
                                    "itemId": iid,
                                    "itemName": name,
                                    "frame": frame
                                })
                                
                completed_owned = []
                
                for idx, pur in enumerate(purchases):
                    ts = pur["timestamp"]
                    bought_item_name = pur["itemName"]
                    
                    frame = pur["frame"]
                    pf = frame.get("participantFrames", {}).get(str(pid), {})
                    if not pf:
                        continue
                        
                    level = pf.get("level", p.get("champLevel", 1))
                    gold = pf.get("totalGold", 0)
                    
                    # Reconstruct LiveStats
                    ls = reconstruct_stats(p["championName"], level, champ_data_db)
                    
                    # Compute dynamic lane diffs
                    lane_diff_gold = 0
                    if lane_opp:
                        op_id = lane_opp["participantId"]
                        op_pf = frame.get("participantFrames", {}).get(str(op_id), {})
                        op_gold = op_pf.get("totalGold", 0)
                        lane_diff_gold = gold - op_gold
                        
                    team_gold = sum(frame.get("participantFrames", {}).get(str(ap["participantId"]), {}).get("totalGold", 0) for ap in ally_team_data)
                    gold_share = gold / max(team_gold, 1)
                    
                    tracked_shares.append(gold_share)
                    tracked_diffs.append(lane_diff_gold)
                    
                    gs = InGameState()
                    gs.game_time_seconds = ts / 1000.0
                    gs.live_stats = ls
                    
                    lp = PlayerInGameData()
                    lp.champion_name = p["championName"]
                    lp.level = level
                    lp.gold = gold
                    lp.kills = p.get("kills", 0)
                    lp.deaths = p.get("deaths", 0)
                    lp.assists = p.get("assists", 0)
                    lp.items = [cache.get_item_id_by_name(n) for n in completed_owned if cache.get_item_id_by_name(n)]
                    lp.stats = ls
                    lp.team = team_id
                    gs.local_player = lp
                    
                    lo = None
                    if lane_opp:
                        lo = PlayerInGameData()
                        lo.champion_name = lane_opp["championName"]
                        op_pf = frame.get("participantFrames", {}).get(str(lane_opp["participantId"]), {})
                        lo.level = op_pf.get("level", 1)
                        lo.kills = lane_opp.get("kills", 0)
                        lo.deaths = lane_opp.get("deaths", 0)
                        lo.assists = lane_opp.get("assists", 0)
                        lo.team = lane_opp["teamId"]
                        gs.all_players.append(lo)
                        
                    for ep in enemy_team_data:
                        ep_pd = PlayerInGameData()
                        ep_pd.champion_name = ep["championName"]
                        ep_pf = frame.get("participantFrames", {}).get(str(ep["participantId"]), {})
                        ep_pd.level = ep_pf.get("level", 1)
                        ep_pd.kills = ep.get("kills", 0)
                        ep_pd.deaths = ep.get("deaths", 0)
                        ep_pd.assists = ep.get("assists", 0)
                        ep_pd.team = ep["teamId"]
                        gs.all_players.append(ep_pd)
                        
                    # PREDICTION
                    algo_recommended_item_id = None
                    core_items = []
                    
                    if len(completed_owned) < 2:
                        prescription_data = core_prescriptions.get(key)
                        if prescription_data:
                            lane_opp_name = lo.champion_name if lo else None
                            lane_opp_type = "UNKNOWN"
                            if lane_opp_name:
                                prof = aff.profile(lane_opp_name)
                                mix = prof.get("damage_mix") or {}
                                if mix.get("ap", 0) >= 0.60: lane_opp_type = "AP"
                                elif mix.get("ad", 0) >= 0.60: lane_opp_type = "AD"
                                else: lane_opp_type = "HYBRID"
                                
                            enemy_tank_count = sum(1 for e in enemy_team_data if "Tank" in champion_tags.get(e.get("championName", ""), []))
                            tank_cat = "tank_hi" if enemy_tank_count >= 2 else "tank_lo"
                            
                            ctx = {"lane_opp_type": lane_opp_type, "enemy_tank_count": tank_cat}
                            order = prescription_data.get("strata_order", [])
                            
                            best_stratum = prescription_data.get("global", {})
                            if order:
                                full_key_parts = [ctx.get(f, "") for f in order]
                                for depth in range(len(full_key_parts), 0, -1):
                                    k = "|".join(full_key_parts[:depth])
                                    if k in prescription_data.get("strata", {}):
                                        best_stratum = prescription_data["strata"][k]
                                        break
                                        
                            if best_stratum:
                                conf = best_stratum.get("confidence", 0)
                                slot = len(completed_owned)
                                
                                use_prescription = False
                                if slot == 0:
                                    use_prescription = True
                                elif slot == 1:
                                    use_prescription = conf > 0.25
                                else:
                                    use_prescription = conf > 0.35
                                    
                                if use_prescription:
                                    core_items = best_stratum.get("items", [])
                                    for ci in core_items:
                                        ci_name = cache.get_item_name_by_id(ci)
                                        if ci_name not in completed_owned:
                                            algo_recommended_item_id = ci
                                            break
                                else:
                                    core_items = []
                                        
                    # LOGIQUE SITUATIONNELLE (REGIME B)
                    ranked_names = []
                    if not algo_recommended_item_id:
                        try:
                            old_gold = gs.local_player.gold
                            gs.local_player.gold = 99999
                            report = analyzer.analyze(gs, lo, candidate_items, my_position=role)
                            gs.local_player.gold = old_gold
                            
                            ranked_names = [it[0] for it in report.ranked_items]
                            if ranked_names:
                                algo_recommended_item_id = cache.get_item_id_by_name(ranked_names[0])
                        except Exception as e:
                            log.error(f"Erreur d'analyse: {e}")
                            completed_owned.append(bought_item_name)
                            continue
                            
                    if not algo_recommended_item_id:
                        completed_owned.append(bought_item_name)
                        continue
                        
                    total_evals += 1
                    is_hit = False
                    
                    algo_item_name = cache.get_item_name_by_id(algo_recommended_item_id) if algo_recommended_item_id else (ranked_names[0] if ranked_names else "")
                    
                    if algo_item_name == bought_item_name:
                        top1_hits += 1
                        is_hit = True
                    
                    if core_items:
                        core_names = [cache.get_item_name_by_id(ci) for ci in core_items]
                        if bought_item_name in core_names:
                            top3_hits += 1
                    else:
                        if ranked_names and bought_item_name in ranked_names[:3]:
                            top3_hits += 1
                    
                    slot_index = len(completed_owned)
                    slot_stats[slot_index]["evals"] += 1
                    
                    if is_hit:
                        slot_stats[slot_index]["hits"] += 1
                    if core_items:
                        slot_stats[slot_index]["a_evals"] += 1
                        if is_hit:
                            slot_stats[slot_index]["a_accord_tot"] += 1
                            if win: slot_stats[slot_index]["a_accord_win"] += 1
                        else:
                            slot_stats[slot_index]["a_desacc_tot"] += 1
                            if win: slot_stats[slot_index]["a_desacc_win"] += 1
                    else:
                        slot_stats[slot_index]["b_evals"] += 1
                        if is_hit:
                            slot_stats[slot_index]["b_accord_tot"] += 1
                            if win: slot_stats[slot_index]["b_accord_win"] += 1
                        else:
                            slot_stats[slot_index]["b_desacc_tot"] += 1
                            if win: slot_stats[slot_index]["b_desacc_win"] += 1
                            
                    completed_owned.append(bought_item_name)
                        
            if args.limit > 0 and total_evals >= args.limit:
                break
        if args.limit > 0 and total_evals >= args.limit:
            break
            
    if total_evals > 0:
        log.info(f"BENCHMARK RESULTS (N={total_evals}):")
        log.info(f"Top-1 Accord (Mimétisme) : {top1_hits / total_evals * 100:.1f}%")
        log.info(f"Top-3 Accord (Mimétisme) : {top3_hits / total_evals * 100:.1f}%")
        
        def print_winrate(name, w_acc, t_acc, w_des, t_des):
            if t_acc > 0 and t_des > 0:
                wr_acc = w_acc / t_acc
                wr_des = w_des / t_des
                diff = wr_acc - wr_des
                
                p1 = wr_acc
                p2 = wr_des
                n1 = t_acc
                n2 = t_des
                se = math.sqrt(p1*(1-p1)/n1 + p2*(1-p2)/n2)
                
                log.info(f"--- {name} ---")
                log.info(f"Winrate quand Accord    : {wr_acc*100:.1f}% (N={t_acc})")
                log.info(f"Winrate quand Désaccord : {wr_des*100:.1f}% (N={t_des})")
                log.info(f"Ecart de Winrate        : {diff*100:+.1f}%")
                log.info(f"Standard Error (SE)     : {se*100:.2f}%")
                
                t_stat = diff / se if se > 0 else 0
                if abs(t_stat) >= 1.96:
                    log.info(f"L'écart EST statistiquement significatif (t={t_stat:.2f}).")
                else:
                    log.info("L'écart N'EST PAS statistiquement significatif (Bruit possible).")

        log.info("--- DÉTAIL PAR SLOT ---")
        for s in sorted(slot_stats.keys()):
            stats = slot_stats[s]
            if stats["evals"] > 0:
                rate = stats["hits"] / stats["evals"] * 100
                slot_name = s + 1
                log.info(f"Slot {slot_name} : {rate:.1f}% d'accord (N={stats['evals']}, Régime A: {stats['a_evals']}, Régime B: {stats['b_evals']})")
                
                if stats["a_evals"] > 0:
                    print_winrate(f"  -> Régime A (Slot {slot_name})", stats["a_accord_win"], stats["a_accord_tot"], stats["a_desacc_win"], stats["a_desacc_tot"])
                if stats["b_evals"] > 0:
                    print_winrate(f"  -> Régime B (Slot {slot_name})", stats["b_accord_win"], stats["b_accord_tot"], stats["b_desacc_win"], stats["b_desacc_tot"])
                
        if tracked_shares:
            log.info("gold_share: min=%.2f max=%.2f", min(tracked_shares), max(tracked_shares))
        if tracked_diffs:
            log.info("lane_diff: min=%d max=%d", min(tracked_diffs), max(tracked_diffs))

if __name__ == "__main__":
    main()
