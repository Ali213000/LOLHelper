#!/usr/bin/env python3
"""
Mesure les trois signaux de draft sur les matchs collectés.

Le scoreur de champions combine quatre composantes. Deux étaient constantes :

  · S1 « meta »   patch_stats.json ne contient AUCUNE clé « meta ». La fonction
                  renvoie 0.5 pour tout le monde, et pèse 0.40 en premier pick,
                  0.50 en blind.
  · S2 « lane »   la clé « lane » ne contient que les cinq noms de rôles, pas de
                  duels. La fonction renvoie 0.5, et pèse jusqu'à 0.25.

Comme le fichier existe, STUB_STATS reste faux et les poids ne sont jamais
renormalisés : 45 à 50 % du score final est une constante identique pour tous
les candidats. Seuls team_fit et counter_comp départagent, sur une amplitude
réduite — d'où les mêmes champions proposés partie après partie.

Ce script produit trois mesures :

  meta        force du champion à son poste (parties, victoires)
  axes        effet réel de chaque axe d'équipe sur la victoire — les cibles de
              draft_config sont écrites à la main et n'ont jamais été confrontées
              aux données
  archetypes  duel d'archétype contre archétype dans la même voie

Le duel champion contre champion n'est PAS mesurable ici : 7 810 matchs donnent
environ 39 000 duels de voie répartis sur ~145 000 appariements possibles, soit
moins d'une observation par duel. Le scoreur exige d'ailleurs 200 parties. Les
archétypes regroupent ces observations en 13 catégories, ce qui rend le signal
atteignable.
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_POSTES = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID",
           "BOTTOM": "ADC", "UTILITY": "SUPPORT"}

PARTIES_MINIMUM = 40
DUELS_MINIMUM = 60


def charger_champions():
    """(id normalisé, poste) -> attributs, plus un repli tous postes."""
    from ai.champion_scorer import norm_name

    par_poste, tous = {}, {}
    for f in glob.glob(os.path.join(RACINE, "data", "champions_*.json")):
        d = json.load(open(f, encoding="utf-8"))
        poste = d["role"]
        for c in d["champions"]:
            cid = norm_name(c["id"])
            par_poste[(cid, poste)] = c
            tous.setdefault(cid, c)
    return par_poste, tous


def _un_match(chemin):
    """(poste, champion, gagne) par participant, sans lire la timeline."""
    try:
        with gzip.open(chemin, "rt", encoding="utf-8") as f:
            info = json.load(f).get("info", {})
    except Exception:
        return []
    if info.get("gameDuration", 0) < 900:
        return []
    sortie = []
    for p in info.get("participants", []):
        poste = _POSTES.get(p.get("teamPosition", ""))
        if not poste:
            continue
        sortie.append((poste, p.get("championName", ""), p.get("teamId"),
                       bool(p.get("win"))))
    return [sortie] if len(sortie) == 10 else []


def parcourir(racine_matchs, limite=0):
    fichiers = []
    for sous in ("train", "test"):
        base = os.path.join(racine_matchs, sous)
        if not os.path.isdir(base):
            continue
        for shard in sorted(os.listdir(base)):
            d = os.path.join(base, shard)
            if not os.path.isdir(d):
                continue
            fichiers += [os.path.join(d, n) for n in os.listdir(d)
                         if n.endswith(".json.gz") and not n.endswith("_timeline.json.gz")]
    if limite:
        fichiers = fichiers[:limite]
    print(f"{len(fichiers)} matchs a lire", flush=True)
    with ProcessPoolExecutor() as ex:
        for i, res in enumerate(ex.map(_un_match, fichiers, chunksize=64), 1):
            if i % 2000 == 0:
                print(f"  {i}/{len(fichiers)}", flush=True)
            yield from res


def wilson(succes, total, z=1.96):
    """Demi-largeur de l'intervalle de confiance, pour ne pas lire du bruit."""
    if total == 0:
        return 0.0
    p = succes / total
    return z * math.sqrt(p * (1 - p) / total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="G:/matches")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    from ai.champion_scorer import norm_name

    par_poste, tous = charger_champions()
    with open(os.path.join(RACINE, "assets", "champion_data.json"), encoding="utf-8") as f:
        dd = json.load(f)["data"]
    id_vers_nom = {}
    for k, v in dd.items():
        nom = v.get("name", k)
        for variante in (v.get("id", k), k, nom):
            id_vers_nom[variante.lower()] = nom

    AXES = sorted({k for c in tous.values() for k in c.get("axes", {})})

    meta = collections.defaultdict(lambda: [0, 0])          # champ|poste -> [n, v]
    duels = collections.defaultdict(lambda: [0, 0])         # arch|arch|poste -> [n, v]
    axes_obs = {k: [] for k in AXES}                        # (somme, gagne)
    part_ap = []                                            # (part AP, gagne)
    nb_matchs = 0

    for parts in parcourir(a.raw_dir, a.limit):
        nb_matchs += 1
        equipes = collections.defaultdict(list)
        par_voie = collections.defaultdict(dict)

        for poste, champ, team, gagne in parts:
            nom = id_vers_nom.get((champ or "").lower(), champ)
            cid = norm_name(nom)
            meta[f"{cid}|{poste}"][0] += 1
            meta[f"{cid}|{poste}"][1] += int(gagne)
            attrs = par_poste.get((cid, poste)) or tous.get(cid)
            equipes[team].append((attrs, gagne))
            par_voie[poste][team] = (attrs, gagne)

        # Axes d'équipe, mesurés en ÉCART entre les deux camps.
        #
        # Comparer une somme absolue à la victoire est mal spécifié : les deux
        # équipes draftent, et en file classée leurs compositions se ressemblent
        # en moyenne. Une équipe « bien composée » affronte le plus souvent une
        # équipe tout aussi bien composée, et l'effet s'annule. L'écart entre
        # les deux camps est le bon test, et il est apparié donc bien plus
        # sensible : chaque match sert de son propre témoin.
        valides = [
            (t, m) for t, m in equipes.items()
            if len(m) == 5 and not any(x[0] is None for x in m)
        ]
        if len(valides) == 2:
            (_, ma), (_, mb) = valides
            gagne_a = ma[0][1]
            for k in AXES:
                sa = sum(x[0]["axes"].get(k, 0.0) for x in ma)
                sb = sum(x[0]["axes"].get(k, 0.0) for x in mb)
                axes_obs[k].append((sa - sb, gagne_a))
                axes_obs[k].append((sb - sa, not gagne_a))
            for membres, gagne in ((ma, gagne_a), (mb, not gagne_a)):
                ap = sum(x[0].get("damage_mix", {}).get("ap", 0.0) for x in membres)
                ad = sum(x[0].get("damage_mix", {}).get("ad", 0.0) for x in membres)
                if ap + ad > 0:
                    part_ap.append((ap / (ap + ad), gagne))

        # Duels d'archétype dans la même voie.
        for poste, cotes in par_voie.items():
            if len(cotes) != 2:
                continue
            (a1, g1), (a2, _) = list(cotes.values())
            if a1 is None or a2 is None:
                continue
            x, y = a1.get("archetype"), a2.get("archetype")
            if not x or not y:
                continue
            duels[f"{x}|{y}|{poste}"][0] += 1
            duels[f"{x}|{y}|{poste}"][1] += int(g1)

    print(f"\n{nb_matchs} matchs exploites\n")

    # ---------------------------------------------------------------- meta
    meta_out = {k: {"games": n, "wins": v} for k, (n, v) in meta.items() if n >= PARTIES_MINIMUM}
    print(f"meta : {len(meta_out)} couples champion|poste (>= {PARTIES_MINIMUM} parties)")
    tri = sorted(meta_out.items(), key=lambda kv: -kv[1]["wins"] / kv[1]["games"])
    for k, v in tri[:5]:
        print(f"   {k:28} {v['wins']/v['games']:.1%}  ({v['games']})")
    print("   ...")
    for k, v in tri[-3:]:
        print(f"   {k:28} {v['wins']/v['games']:.1%}  ({v['games']})")

    # ---------------------------------------------------------------- axes
    print("\naxes d'equipe : victoire du quintile bas vs quintile haut")
    resume_axes = {}
    for k in AXES:
        # Trier sur la somme SEULE : inclure l'issue dans la cle place les
        # defaites en bas et les victoires en haut a somme egale, ce qui
        # fabrique l'ecart qu'on pretend mesurer. Le tri stable de Python
        # preserve alors l'ordre de lecture, independant du resultat.
        obs = sorted(axes_obs[k], key=lambda t: t[0])
        if len(obs) < 500:
            continue
        q = len(obs) // 5
        bas, haut = obs[:q], obs[-q:]
        wb = sum(g for _, g in bas) / len(bas)
        wh = sum(g for _, g in haut) / len(haut)
        ic = wilson(sum(g for _, g in haut), len(haut))
        ecart = wh - wb
        signif = abs(ecart) > 2 * ic
        resume_axes[k] = {
            "quintile_bas": round(wb, 4), "quintile_haut": round(wh, 4),
            "ecart": round(ecart, 4), "significatif": bool(signif),
            "ecart_bas": round(bas[-1][0], 2), "ecart_haut": round(haut[0][0], 2),
            "n": len(obs),
        }
        marque = "*" if signif else " "
        print(f" {marque} {k:18} {wb:.1%} -> {wh:.1%}   ecart {ecart:+.1%}  "
              f"(ecart {bas[-1][0]:+.1f} -> {haut[0][0]:+.1f})")

    # ------------------------------------------------------------ part AP
    print("\nequilibre des degats : part AP de l'equipe")
    part_ap.sort(key=lambda t: t[0])
    q = len(part_ap) // 5
    for i in range(5):
        tranche = part_ap[i * q:(i + 1) * q]
        wr = sum(g for _, g in tranche) / len(tranche)
        print(f"   AP {tranche[0][0]:.0%}-{tranche[-1][0]:.0%}   victoire {wr:.1%}  (n={len(tranche)})")

    # ----------------------------------------------------------- duels
    duels_out = {k: {"games": n, "wins": v} for k, (n, v) in duels.items() if n >= DUELS_MINIMUM}
    print(f"\nduels d'archetype : {len(duels_out)} retenus (>= {DUELS_MINIMUM} parties)")
    tri = sorted(duels_out.items(), key=lambda kv: -kv[1]["wins"] / kv[1]["games"])
    for k, v in tri[:6]:
        print(f"   {k:52} {v['wins']/v['games']:.1%}  ({v['games']})")
    print("   ...")
    for k, v in tri[-4:]:
        print(f"   {k:52} {v['wins']/v['games']:.1%}  ({v['games']})")

    sortie = {
        "reference": f"{nb_matchs} matchs, G:/matches (train+test)",
        "parties_minimum": PARTIES_MINIMUM,
        "duels_minimum": DUELS_MINIMUM,
        "meta": meta_out,
        "archetype_duels": duels_out,
        "axes_effet": resume_axes,
    }
    chemin = os.path.join(RACINE, "data", "draft_measures.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(sortie, f, ensure_ascii=False, indent=1)
    print(f"\necrit dans {chemin}")


if __name__ == "__main__":
    main()
