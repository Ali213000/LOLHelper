#!/usr/bin/env python3
"""
Les composantes heuristiques du scoreur prédisent-elles la victoire ?

team_fit et counter_comp pèsent ensemble 0.55 du score de pick, sur des poids et
des seuils écrits à la main. Contrairement à meta (2.2 points d'écart-type
mesurés) et aux duels de voie (OP.GG), leur pouvoir prédictif n'a jamais été
vérifié — on leur fait confiance parce qu'elles semblent raisonnables.

Le test est direct : pour chaque équipe de chaque match, on calcule ce que le
scoreur aurait attribué à ses cinq champions, et on regarde si cela prédit
l'issue. Comme pour les axes, la comparaison est APPARIÉE — écart entre les deux
camps, chaque match servant de son propre témoin — parce que les deux équipes
draftent et que des compositions comparables s'annulent en absolu.

Si une composante ne prédit rien, son poids n'est pas justifié et le score
gagnerait à la réduire au profit des composantes mesurées.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_POSTES = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID",
           "BOTTOM": "ADC", "UTILITY": "SUPPORT"}


def _un_match(chemin):
    """(poste, champion, equipe, gagne) sans lire la timeline."""
    try:
        with gzip.open(chemin, "rt", encoding="utf-8") as f:
            info = json.load(f).get("info", {})
    except Exception:
        return None
    if info.get("gameDuration", 0) < 900:
        return None
    sortie = []
    for p in info.get("participants", []):
        poste = _POSTES.get(p.get("teamPosition", ""))
        if not poste:
            return None
        sortie.append((poste, p.get("championName", ""), p.get("teamId"),
                       bool(p.get("win"))))
    return sortie if len(sortie) == 10 else None


def fichiers(racine):
    out = []
    for sous in ("train", "test"):
        base = os.path.join(racine, sous)
        if not os.path.isdir(base):
            continue
        for shard in sorted(os.listdir(base)):
            d = os.path.join(base, shard)
            if os.path.isdir(d):
                out += [os.path.join(d, n) for n in os.listdir(d)
                        if n.endswith(".json.gz") and not n.endswith("_timeline.json.gz")]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="G:/matches")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    import ai.champion_scorer as cs
    from ai.champion_scorer import ChampionScorer

    scorer = ChampionScorer(Path(os.path.join(RACINE, "data")))
    id_vers_nom = {}
    with open(os.path.join(RACINE, "assets", "champion_data.json"), encoding="utf-8") as f:
        for k, v in json.load(f)["data"].items():
            nom = v.get("name", k)
            for variante in (v.get("id", k), k, nom):
                id_vers_nom[variante.lower()] = nom

    liste = fichiers(a.raw_dir)
    if a.limit:
        liste = liste[:a.limit]
    print(f"{len(liste)} matchs a lire", flush=True)

    obs = collections.defaultdict(list)      # composante -> (ecart, gagne)
    n = 0
    with ProcessPoolExecutor() as ex:
        for parts in ex.map(_un_match, liste, chunksize=64):
            if not parts:
                continue
            equipes = collections.defaultdict(list)
            for poste, champ, team, gagne in parts:
                cid = cs.norm_name(id_vers_nom.get((champ or "").lower(), champ))
                equipes[team].append((cid, cs.by_role[poste].get(cid) or cs.by_id.get(cid), gagne))
            if len(equipes) != 2 or any(len(v) != 5 or any(x[1] is None for x in v)
                                        for v in equipes.values()):
                continue
            n += 1
            (ta, ma), (tb, mb) = list(equipes.items())
            gagne_a = ma[0][2]

            valeurs = {}
            for etiquette, nous, eux in (("A", ma, mb), ("B", mb, ma)):
                attrs_eux = [x[1] for x in eux]
                menaces = scorer._calculer_menaces(attrs_eux, [x[1] for x in nous])
                fit = counter = 0.0
                for i, (_, moi, _) in enumerate(nous):
                    autres = [x[1] for j, x in enumerate(nous) if j != i]
                    besoins = scorer._calculer_besoins(autres)
                    fit += scorer._score_team_fit(moi, besoins, autres)
                    counter += scorer._score_counter_comp(moi, menaces)
                valeurs[etiquette] = (fit / 5.0, counter / 5.0)

            for idx, nom in enumerate(("team_fit", "counter_comp")):
                d = valeurs["A"][idx] - valeurs["B"][idx]
                obs[nom].append((d, gagne_a))
                obs[nom].append((-d, not gagne_a))

            if n % 1500 == 0:
                print(f"  {n} matchs", flush=True)

    print(f"\n{n} matchs exploites\n")
    print(f"{'composante':16}{'quintile bas':>14}{'quintile haut':>15}{'ecart':>9}{'z':>8}")
    resume = {}
    for nom, liste_obs in obs.items():
        # Trier sur l'ecart SEUL : inclure l'issue dans la cle fabrique l'effet.
        tri = sorted(liste_obs, key=lambda t: t[0])
        q = len(tri) // 5
        bas, haut = tri[:q], tri[-q:]
        wb = sum(g for _, g in bas) / len(bas)
        wh = sum(g for _, g in haut) / len(haut)
        ecart = wh - wb
        z = ecart / math.sqrt(2 * 0.25 / q)
        resume[nom] = {"quintile_bas": round(wb, 4), "quintile_haut": round(wh, 4),
                       "ecart": round(ecart, 4), "z": round(z, 2), "n": len(tri)}
        print(f"{nom:16}{wb:>13.1%}{wh:>15.1%}{ecart:>+9.1%}{z:>8.2f}")

    chemin = os.path.join(RACINE, "data", "draft_measures.json")
    mesures = json.load(open(chemin, encoding="utf-8"))
    mesures["composantes_effet"] = resume
    mesures["composantes_matchs"] = n
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(mesures, f, ensure_ascii=False, indent=1)
    print()
    print(f"ecrit dans {chemin}")

    print("\nRepere : sustain, le plus fort axe de composition etabli, vaut")
    print("+5.0 points (z=3.15). La force par champion vaut 2.2 points d'ecart-type.")


if __name__ == "__main__":
    main()
