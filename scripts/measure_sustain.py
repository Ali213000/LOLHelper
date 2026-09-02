#!/usr/bin/env python
"""
scripts/measure_sustain.py — Mesure le soin et le bouclier réellement produits
par chaque champion, à partir des matchs collectés.

Remplace des poids réglés à la main par des valeurs observées. Match-V5 expose
par participant :

    totalHeal                        soin total, AUTO-SOIN INCLUS
    totalHealsOnTeammates            soin sur les alliés uniquement
    totalDamageShieldedOnTeammates   bouclier sur les alliés uniquement

Pour l'anti-soin (Hémorragie), c'est `totalHeal` qui compte : la réduction
s'applique aussi bien à l'auto-soin d'un Dr. Mundo qu'aux soins d'une Soraka.

Réserve importante sur les boucliers : le champ ne couvre QUE les boucliers
posés sur des alliés. Les auto-boucliers (Riven, Sett, Kai'Sa, Malphite,
Sion...) sont invisibles ici et devront rester estimés.

    python scripts/measure_sustain.py --raw-dir G:/matches --out data/sustain_measured.json
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import logging
import os
import statistics
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("sustain")

# Parties trop courtes : remakes et redditions précoces faussent les débits.
DUREE_MINIMALE = 600      # secondes
OCCURRENCES_MINIMALES = 30


def parcourir(raw_dir: str, splits: list[str]):
    """Produit les participants de tous les matchs des splits demandés."""
    for split in splits:
        base = os.path.join(raw_dir, split)
        if not os.path.isdir(base):
            log.warning("%s absent, ignoré", base)
            continue
        for shard in sorted(os.listdir(base)):
            chemin_shard = os.path.join(base, shard)
            if not os.path.isdir(chemin_shard):
                continue
            for nom in os.listdir(chemin_shard):
                if not nom.endswith(".json.gz") or nom.endswith("_timeline.json.gz"):
                    continue
                try:
                    with gzip.open(os.path.join(chemin_shard, nom), "rt") as f:
                        match = json.load(f)
                except Exception:
                    continue
                info = match.get("info", {})
                duree = info.get("gameDuration", 0)
                if duree < DUREE_MINIMALE:
                    continue
                minutes = duree / 60.0
                for p in info.get("participants", []):
                    if p.get("gameEndedInEarlySurrender"):
                        continue
                    yield p, minutes


def mesurer(raw_dir: str, splits: list[str]) -> dict:
    par_champion: dict[str, dict[str, list[float]]] = collections.defaultdict(
        lambda: {"heal": [], "heal_allies": [], "shield_allies": []}
    )
    # Le soin brut est fortement dépendant du RÔLE : un jungleur récupère
    # énormément sur ses camps et son familier, ce qui n'a rien à voir avec
    # du soin de combat. On mesure donc aussi par position pour pouvoir
    # comparer chaque champion à la référence de son poste.
    par_poste: dict[str, list[float]] = collections.defaultdict(list)
    par_champ_poste: dict[tuple, list[float]] = collections.defaultdict(list)
    matchs = 0
    lignes = 0

    for p, minutes in parcourir(raw_dir, splits):
        champ = p.get("championName")
        if not champ:
            continue
        poste = p.get("teamPosition") or "INCONNU"
        soin = p.get("totalHeal", 0) / minutes
        par_poste[poste].append(soin)
        par_champ_poste[(champ, poste)].append(soin)
        d = par_champion[champ]
        d["heal"].append(soin)
        d["heal_allies"].append(p.get("totalHealsOnTeammates", 0) / minutes)
        d["shield_allies"].append(p.get("totalDamageShieldedOnTeammates", 0) / minutes)
        lignes += 1
        if lignes % 10000 == 0:
            log.info("%d participants traités…", lignes)

    resultat = {}
    for champ, d in par_champion.items():
        n = len(d["heal"])
        if n < OCCURRENCES_MINIMALES:
            continue
        resultat[champ] = {
            "n": n,
            # Médiane : quelques parties atypiques suffisent à déformer une moyenne.
            "heal_par_min": round(statistics.median(d["heal"]), 1),
            "heal_allies_par_min": round(statistics.median(d["heal_allies"]), 1),
            "shield_allies_par_min": round(statistics.median(d["shield_allies"]), 1),
            # Le 75e centile dit si le champion PEUT beaucoup soigner quand il
            # est joué dans ce sens, là où la médiane décrit le cas courant.
            "heal_p75": round(_centile(d["heal"], 0.75), 1),
            "shield_allies_p75": round(_centile(d["shield_allies"], 0.75), 1),
        }
    postes = {
        poste: {
            "n": len(v),
            "mediane": round(statistics.median(v), 1),
            "p90": round(_centile(v, 0.90), 1),
        }
        for poste, v in sorted(par_poste.items()) if len(v) >= 200
    }

    # Soin d'un champion rapporté à la médiane de son poste principal.
    for champ, info in resultat.items():
        postes_du_champ = [
            (len(v), poste, statistics.median(v))
            for (c, poste), v in par_champ_poste.items()
            if c == champ and len(v) >= 20 and poste in postes
        ]
        if not postes_du_champ:
            info["poste"] = None
            info["exces_sur_poste"] = None
            continue
        n_poste, poste, med = max(postes_du_champ)
        ref = postes[poste]["mediane"]
        info["poste"] = poste
        info["n_poste"] = n_poste
        info["heal_poste"] = round(med, 1)
        info["exces_sur_poste"] = round(med - ref, 1)

    return {
        "matchs_participants": lignes,
        "postes": postes,
        "champions": dict(sorted(resultat.items())),
    }


def _centile(valeurs: list[float], q: float) -> float:
    if not valeurs:
        return 0.0
    tri = sorted(valeurs)
    i = min(len(tri) - 1, int(q * len(tri)))
    return tri[i]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="data/matches")
    ap.add_argument("--splits", default="train,test",
                    help="mesurer une statistique descriptive n'ajuste aucun "
                         "modèle : utiliser test ne compromet pas le holdout")
    ap.add_argument("--out", default="data/sustain_measured.json")
    args = ap.parse_args()

    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    log.info("Lecture de %s (%s)…", args.raw_dir, ", ".join(splits))
    donnees = mesurer(args.raw_dir, splits)

    if not donnees["champions"]:
        log.error("Aucune donnée exploitable.")
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(donnees, f, indent=2, ensure_ascii=False)

    log.info("%d participants, %d champions retenus (>= %d occurrences) -> %s",
             donnees["matchs_participants"], len(donnees["champions"]),
             OCCURRENCES_MINIMALES, args.out)


if __name__ == "__main__":
    main()
