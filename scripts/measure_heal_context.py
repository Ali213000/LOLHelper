#!/usr/bin/env python
"""
scripts/measure_heal_context.py — De quoi dépend vraiment le soin d'un champion.

Un poids fixe par champion décrit mal la réalité : un Aatrox nourri se soigne
énormément, le même Aatrox en retard beaucoup moins ; une Samira avec
Soif-de-sang régénère à un tout autre rythme que sans. Ce script mesure les
deux effets sur les matchs collectés.

Deux analyses
-------------
1. EFFET D'OBJET, à champion constant. Pour chaque couple (champion, objet),
   on compare le soin médian des parties AVEC et SANS l'objet. Comparer
   globalement porteurs et non-porteurs serait trompeur : Soif-de-sang est
   acheté par des champions qui se soignaient déjà.

2. EFFET DE DOMINATION. Soin médian par tranche de performance, mesurée par
   l'or gagné par minute — plus stable que le KDA et non bornée.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import logging
import os
import statistics

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("contexte")

DUREE_MINIMALE = 600
# Un couple (champion, objet) doit être observé assez souvent des DEUX côtés.
MIN_AVEC = 25
MIN_SANS = 25
# Un objet doit ressortir sur assez de champions pour qu'on lui fasse confiance.
MIN_CHAMPIONS = 4


def parcourir(raw_dir, splits):
    for split in splits:
        base = os.path.join(raw_dir, split)
        if not os.path.isdir(base):
            continue
        for shard in sorted(os.listdir(base)):
            chemin = os.path.join(base, shard)
            if not os.path.isdir(chemin):
                continue
            for nom in os.listdir(chemin):
                if not nom.endswith(".json.gz") or nom.endswith("_timeline.json.gz"):
                    continue
                try:
                    with gzip.open(os.path.join(chemin, nom), "rt") as f:
                        m = json.load(f)
                except Exception:
                    continue
                info = m.get("info", {})
                duree = info.get("gameDuration", 0)
                if duree < DUREE_MINIMALE:
                    continue
                for p in info.get("participants", []):
                    if p.get("gameEndedInEarlySurrender"):
                        continue
                    yield p, duree / 60.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="data/matches")
    ap.add_argument("--splits", default="train,test")
    ap.add_argument("--out", default="data/heal_context.json")
    args = ap.parse_args()
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]

    noms_objets = {}
    try:
        with open("assets/item_data.json", encoding="utf-8") as f:
            for iid, v in json.load(f).get("data", {}).items():
                if iid.isdigit() and int(iid) < 10000:
                    noms_objets.setdefault(int(iid), v.get("name", ""))
    except Exception:
        log.warning("item_data.json illisible, les objets resteront numériques")

    # (champion, objet) -> {avec: [soin/min], sans: [soin/min]}
    par_couple = collections.defaultdict(lambda: {"avec": [], "sans": []})
    # tranche d'or/min -> [soin/min], par champion pour rester comparable
    par_domination = collections.defaultdict(lambda: collections.defaultdict(list))
    objets_vus = collections.Counter()
    lignes = 0

    for p, minutes in parcourir(args.raw_dir, splits):
        champ = p.get("championName")
        if not champ:
            continue
        soin = p.get("totalHeal", 0) / minutes
        objets = {p.get(f"item{i}", 0) for i in range(6)}
        objets.discard(0)
        objets_vus.update(objets)

        for iid in objets:
            par_couple[(champ, iid)]["avec"].append(soin)
        # Les « sans » sont renseignés après coup : on ne connaît la liste des
        # objets pertinents qu'à la fin du parcours. On stocke donc la ligne.
        par_domination[champ][_tranche(p.get("goldEarned", 0) / minutes)].append(soin)

        lignes += 1
        if lignes % 20000 == 0:
            log.info("%d participants…", lignes)

    # Second parcours pour les « sans » : indispensable pour comparer à
    # champion constant sans exploser la mémoire.
    objets_candidats = {iid for iid, n in objets_vus.items() if n >= 300}
    log.info("%d objets assez fréquents, second parcours…", len(objets_candidats))

    for p, minutes in parcourir(args.raw_dir, splits):
        champ = p.get("championName")
        if not champ:
            continue
        soin = p.get("totalHeal", 0) / minutes
        possedes = {p.get(f"item{i}", 0) for i in range(6)}
        for iid in objets_candidats:
            if iid not in possedes and (champ, iid) in par_couple:
                par_couple[(champ, iid)]["sans"].append(soin)

    # --- Agrégation par objet ---
    par_objet = collections.defaultdict(list)
    for (champ, iid), d in par_couple.items():
        if len(d["avec"]) < MIN_AVEC or len(d["sans"]) < MIN_SANS:
            continue
        delta = statistics.median(d["avec"]) - statistics.median(d["sans"])
        par_objet[iid].append((champ, round(delta, 1), len(d["avec"])))

    resultat_objets = {}
    for iid, entrees in par_objet.items():
        if len(entrees) < MIN_CHAMPIONS:
            continue
        deltas = [d for _, d, _ in entrees]
        resultat_objets[str(iid)] = {
            "nom": noms_objets.get(iid, f"Item_{iid}"),
            "delta_median_par_min": round(statistics.median(deltas), 1),
            "champions_mesures": len(entrees),
            "occurrences": sum(n for _, _, n in entrees),
            "exemples": sorted(entrees, key=lambda e: -e[1])[:4],
        }

    # --- Agrégation par tranche de domination ---
    tranches = collections.defaultdict(list)
    for champ, par_tranche in par_domination.items():
        base = par_tranche.get("moyen")
        if not base or len(base) < 30:
            continue
        ref = statistics.median(base)
        if ref <= 0:
            continue
        for tranche, vals in par_tranche.items():
            if len(vals) >= 30:
                tranches[tranche].append(statistics.median(vals) / ref)

    resultat_domination = {
        t: {
            "ratio_median": round(statistics.median(v), 2),
            "champions": len(v),
        }
        for t, v in sorted(tranches.items())
    }

    sortie = {
        "participants": lignes,
        "effet_objet": dict(sorted(
            resultat_objets.items(),
            key=lambda kv: -kv[1]["delta_median_par_min"])),
        "effet_domination": resultat_domination,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sortie, f, indent=2, ensure_ascii=False)
    log.info("%d objets retenus -> %s", len(resultat_objets), args.out)


def _tranche(or_par_min: float) -> str:
    """Tranche de performance économique, proxy de la domination en partie."""
    if or_par_min < 300:
        return "en_retard"
    if or_par_min < 380:
        return "moyen"
    if or_par_min < 460:
        return "en_avance"
    return "nourri"


if __name__ == "__main__":
    main()
