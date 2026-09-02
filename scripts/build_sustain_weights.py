#!/usr/bin/env python
"""
scripts/build_sustain_weights.py — Convertit les mesures brutes en poids [0,1].

Entrée : data/sustain_measured.json (produit par measure_sustain.py)
Sortie : data/sustain_weights.json + comparaison avec les poids actuels.

Normalisation
-------------
SOIN — on part de l'excès sur la médiane du poste, pas du soin brut : la
sustain de jungle (camps, familier) place n'importe quel jungleur à 429/min
contre 151 en mid, ce qui n'a rien à voir avec du soin de combat.
La racine carrée évite qu'un cas extrême (Zac, +1433) écrase tout le milieu
de tableau.

BOUCLIER — échelle linéaire, la distribution étant moins étalée. Le champ ne
couvre QUE les boucliers posés sur des ALLIÉS : les auto-boucliers restent à
la valeur estimée, faute de mesure possible.
"""
from __future__ import annotations

import argparse
import json
import math

# Références de normalisation : valeur au-delà de laquelle le poids sature à 1.
REF_SOIN = 750.0        # excès sur la médiane du poste, en PV/min
REF_BOUCLIER = 250.0    # bouclier sur alliés, en PV/min

# En deçà, le champion n'est pas retenu comme soigneur / porteur de bouclier.
PLANCHER_SOIN = 80.0
PLANCHER_BOUCLIER = 30.0

# Identifiants Riot -> noms d'affichage utilisés par le moteur.
ALIAS = {
    "DrMundo": "Dr. Mundo", "MonkeyKing": "Wukong", "Belveth": "Bel'Veth",
    "Chogath": "Cho'Gath", "Velkoz": "Vel'Koz", "Leblanc": "LeBlanc",
    "Kaisa": "Kai'Sa", "Khazix": "Kha'Zix", "RekSai": "Rek'Sai",
    "KogMaw": "Kog'Maw", "KSante": "K'Sante", "TwistedFate": "Twisted Fate",
    "AurelionSol": "Aurelion Sol", "MasterYi": "Master Yi",
    "MissFortune": "Miss Fortune", "XinZhao": "Xin Zhao",
    "JarvanIV": "Jarvan IV", "LeeSin": "Lee Sin", "TahmKench": "Tahm Kench",
    "Nunu": "Nunu & Willump", "Renata": "Renata Glasc",
}


def afficher(champ: str) -> str:
    return ALIAS.get(champ, champ)


def poids_soin(exces: float | None) -> float:
    if exces is None or exces < PLANCHER_SOIN:
        return 0.0
    return round(min(1.0, math.sqrt(exces / REF_SOIN)), 2)


def poids_bouclier(valeur: float) -> float:
    if valeur < PLANCHER_BOUCLIER:
        return 0.0
    return round(min(1.0, valeur / REF_BOUCLIER), 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesures", default="data/sustain_measured.json")
    ap.add_argument("--out", default="data/sustain_weights.json")
    args = ap.parse_args()

    with open(args.mesures, encoding="utf-8") as f:
        mesures = json.load(f)

    soins, boucliers = {}, {}
    for champ, v in mesures["champions"].items():
        nom = afficher(champ)
        ps = poids_soin(v.get("exces_sur_poste"))
        if ps:
            soins[nom] = {
                "poids": ps,
                "exces_par_min": v["exces_sur_poste"],
                "poste": v["poste"],
                "n": v["n_poste"],
            }
        pb = poids_bouclier(v["shield_allies_par_min"])
        if pb:
            boucliers[nom] = {
                "poids": pb,
                "bouclier_par_min": v["shield_allies_par_min"],
                "n": v["n"],
            }

    sortie = {
        "_lisez_moi": [
            "Poids mesurés sur les matchs collectés, pas estimés.",
            f"Normalisation soin     : racine(excès sur médiane du poste / {REF_SOIN:.0f})",
            f"Normalisation bouclier : bouclier sur alliés / {REF_BOUCLIER:.0f}",
            "Le champ bouclier ne couvre PAS les auto-boucliers : les champions",
            "qui ne protègent qu'eux-mêmes sont absents et restent estimés.",
        ],
        "participants": mesures["matchs_participants"],
        "references_par_poste": mesures["postes"],
        "soin": dict(sorted(soins.items(), key=lambda kv: -kv[1]["poids"])),
        "bouclier": dict(sorted(boucliers.items(), key=lambda kv: -kv[1]["poids"])),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sortie, f, indent=2, ensure_ascii=False)

    print(f"{len(soins)} soigneurs et {len(boucliers)} porteurs de bouclier -> {args.out}")


if __name__ == "__main__":
    main()
