#!/usr/bin/env python3
"""
Mesure la diversité des recommandations de draft, avant et après correction.

Le symptôme rapporté : « ça me propose quasiment à chaque fois les mêmes
champions, pareil pour les bans ». Ce script le quantifie en tirant des drafts
au hasard et en comptant combien de champions distincts sortent par poste.

L'état « avant » est reproduit en neutralisant ce qui était mort en production :
_score_meta renvoyait 0.5 pour tout le monde (patch_stats.json n'a pas de clé
« meta ») et _calculer_penalites renvoyait 0.0 (redondance, équilibre des dégâts
et scaling spécifiés dans draft_config mais jamais codés).
"""
from __future__ import annotations

import collections
import os
import random
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import champion_scorer as cs
from ai.champion_scorer import ChampionScorer, ScorerDraftState

TIRAGES = 300
ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]


def drafts(graine=12345):
    rng = random.Random(graine)
    tous = list(cs.by_id)
    for _ in range(TIRAGES):
        role = rng.choice(ROLES)
        autres = [r for r in ROLES if r != role]
        allies = [{"id": rng.choice(list(cs.by_role[r])), "role": r}
                  for r in rng.sample(autres, rng.randint(1, 4))]
        ennemis = [{"id": rng.choice(list(cs.by_role[r])), "role": r}
                   for r in rng.sample(ROLES, rng.randint(1, 4))]
        pris = {a["id"] for a in allies} | {e["id"] for e in ennemis}
        yield ScorerDraftState(
            my_role=role, pick_slot=rng.randint(1, 5), mode="draft",
            available=[c for c in cs.by_role[role] if c not in pris],
            allies=allies, enemies=ennemis,
            bans=rng.sample(tous, 4), rank="PLATINUM",
            lane_opponent=next((e["id"] for e in ennemis if e["role"] == role), None),
        )


def mesurer(scorer, etiquette):
    par_role = collections.defaultdict(collections.Counter)
    bans = collections.Counter()
    for d in drafts():
        for r in scorer.recommend(d):
            par_role[d.my_role][r.champion_id] += 1
        for b in scorer.recommend_ban(d, top_n=3):
            bans[b.champion_id] += 1

    print(f"\n=== {etiquette} ===")
    total_distincts = 0
    for role in ROLES:
        c = par_role[role]
        if not c:
            continue
        total_distincts += len(c)
        tete = c.most_common(1)[0]
        propositions = sum(c.values())
        print(f"  {role:8} {len(c):3} champions distincts   "
              f"tete : {tete[0]} dans {tete[1]/propositions:.0%} des conseils")
    print(f"  {'BANS':8} {len(bans):3} champions distincts   "
          f"tete : {bans.most_common(1)[0][0]} dans "
          f"{bans.most_common(1)[0][1]/max(1,sum(bans.values())):.0%} des conseils")
    print(f"  total picks : {total_distincts} champions distincts proposes")
    return total_distincts, len(bans)


def main():
    scorer = ChampionScorer(Path("data"))

    # --- Avant : les deux fonctions telles qu'elles tournaient en production.
    meta_reel = ChampionScorer._score_meta
    pen_reel = ChampionScorer._calculer_penalites
    menace_reelle = ChampionScorer._meta_threat

    def menace_ancienne(self, cid, my_role=""):
        ch = cs.get_champion(cid)
        br = sum(self.stats.get("banrate", {}).get(f"{cid}|{r}", 0.0) for r in ch.get("roles", []))
        pr = sum(self.stats.get("pickrate", {}).get(f"{cid}|{r}", 0.0) for r in ch.get("roles", []))
        if not br and not pr:
            return 0.05
        return min(1.0, (br * pr) * 5.0)

    ChampionScorer._score_meta = lambda self, c, d: 0.5
    ChampionScorer._calculer_penalites = lambda self, c, d, a, e: 0.0
    ChampionScorer._meta_threat = menace_ancienne
    avant = mesurer(scorer, "AVANT (meta constante, aucune penalite)")

    ChampionScorer._score_meta = meta_reel
    ChampionScorer._calculer_penalites = pen_reel
    ChampionScorer._meta_threat = menace_reelle
    apres = mesurer(scorer, "APRES (meta mesuree, penalites appliquees)")

    print(f"\npicks : {avant[0]} -> {apres[0]} champions distincts "
          f"({(apres[0]/max(1,avant[0])-1):+.0%})")
    print(f"bans  : {avant[1]} -> {apres[1]} champions distincts "
          f"({(apres[1]/max(1,avant[1])-1):+.0%})")


if __name__ == "__main__":
    main()
