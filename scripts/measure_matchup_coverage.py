#!/usr/bin/env python3
"""
Combien de matchs faut-il pour mesurer les duels champion contre champion ?

Question tranchée par la mesure plutôt que par l'estimation. Les appariements ne
sont pas uniformément fréquents : quelques duels reviennent sans cesse, la
plupart sont rares. Ce qui compte n'est donc pas la moyenne mais la COUVERTURE —
la part des duels réellement rencontrés qui atteint un volume exploitable.

Précision visée : un taux de victoire à +/- 3 points demande environ 1 000
parties, à +/- 5 points environ 380.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_POSTES = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID",
           "BOTTOM": "ADC", "UTILITY": "SUPPORT"}


def _un_match(chemin):
    try:
        with gzip.open(chemin, "rt", encoding="utf-8") as f:
            info = json.load(f).get("info", {})
    except Exception:
        return []
    if info.get("gameDuration", 0) < 900:
        return []
    voies = collections.defaultdict(list)
    for p in info.get("participants", []):
        poste = _POSTES.get(p.get("teamPosition", ""))
        if poste:
            voies[poste].append(p.get("championName", ""))
    duels = []
    for poste, champs in voies.items():
        if len(champs) == 2 and all(champs):
            a, b = sorted(champs)          # duel non oriente
            duels.append(f"{a}|{b}|{poste}")
    return duels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="G:/matches")
    ap.add_argument("--cibles", default="100000,250000,1000000")
    a = ap.parse_args()

    fichiers = []
    for sous in ("train", "test"):
        base = os.path.join(a.raw_dir, sous)
        if not os.path.isdir(base):
            continue
        for shard in sorted(os.listdir(base)):
            d = os.path.join(base, shard)
            if os.path.isdir(d):
                fichiers += [os.path.join(d, n) for n in os.listdir(d)
                             if n.endswith(".json.gz") and not n.endswith("_timeline.json.gz")]

    compte = collections.Counter()
    n_matchs = 0
    with ProcessPoolExecutor() as ex:
        for duels in ex.map(_un_match, fichiers, chunksize=64):
            if duels:
                n_matchs += 1
                compte.update(duels)

    total_duels = sum(compte.values())
    print(f"{n_matchs} matchs -> {total_duels} duels de voie, "
          f"{len(compte)} appariements distincts rencontres")
    print(f"moyenne {total_duels/len(compte):.1f} parties par appariement\n")

    for cible in (380, 1000):
        print(f"--- couverture visee : {cible} parties par duel "
              f"(+/- {'5' if cible == 380 else '3'} points)")
        for n_cible in [int(x) for x in a.cibles.split(",")]:
            facteur = n_cible / n_matchs
            # Part des DUELS RENCONTRES (pondérée par leur fréquence) qui
            # atteindrait la cible : c'est ce que le joueur vit réellement,
            # pas la part des appariements théoriques.
            atteints = sum(v for v in compte.values() if v * facteur >= cible)
            distincts = sum(1 for v in compte.values() if v * facteur >= cible)
            print(f"   {n_cible:>9} matchs : {atteints/total_duels:5.1%} des duels vecus, "
                  f"{distincts:>5} appariements sur {len(compte)}")
        print()


if __name__ == "__main__":
    main()
