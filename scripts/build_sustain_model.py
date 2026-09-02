#!/usr/bin/env python
"""
scripts/build_sustain_model.py — Assemble le modèle de soin dynamique.

Fusionne les trois mesures (soin par champion, effet des objets, effet de la
domination) en un seul fichier chargé par le moteur.

Le poids d'un ennemi n'est plus une constante :

    soin_estimé = excès_champion x multiplicateur_KDA + Σ apport_objets
    poids       = racine(soin_estimé / 750), plafonné à 1

Filtrage des objets
-------------------
Seuls comptent les objets dont la description montre une mécanique de SOIN
(vol de vie, omnivamp, régénération, « rend des PV »). Sont écartés :

  · les objets seulement CORRÉLÉS — Force de la nature, Jak'Sho, Terminus
    ressortaient à +72/+86 PV/min sans aucune mécanique de soin : ce sont des
    objets de tank achetés par des joueurs qui encaissent longtemps ;
  · les BOUCLIERS (Gage de Sterak) — l'Hémorragie ne les réduit pas ;
  · les RÉSURRECTIONS (Ange gardien, +118) — le retour en vie gonfle
    totalHeal mais échappe totalement à l'anti-soin.
"""
from __future__ import annotations

import argparse
import json
import re

REF_SOIN = 750.0
DELTA_MINIMAL = 40.0        # en deçà, l'apport de l'objet n'est pas exploitable

# Mécaniques qui produisent du soin réductible par l'Hémorragie.
MECANIQUES = ("vol de vie", "omnivamp", "vampirisme", "soigne",
              "rend des pv", "récupérez des pv", "restaure", "régénération",
              "régénère", "soin")

# Écartés malgré une mesure élevée, pour les raisons expliquées en tête.
EXCLUS_EXPLICITES = {
    "Ange gardien",        # résurrection, non réductible
    "Gage de Sterak",      # bouclier, non réductible
    "Bâton séculaire",     # restaure du mana, pas des PV
}

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

# Grille mesurée sur 75 900 participants : ratio de soin selon l'or investi en
# objets (rapporté à la médiane du match) ET le KDA. Les deux signaux portent
# une information indépendante — à or constant le KDA fait encore varier le
# soin de x1.6, et réciproquement x1.34. Combinés, l'amplitude atteint x2.11,
# contre x1.83 pour l'or seul et x2.00 pour le KDA seul.
#
# L'or en objets est déjà calculé par l'app pour l'écart avec les adversaires,
# et reste lisible en jeu : la Live Client API expose les objets ennemis.
DOMINATION = {
    "bornes_or": [0.85, 1.15],      # ratio sur la médiane du match
    "bornes_kda": [1.5, 3.0],
    "grille": [                     # [tranche_or][tranche_kda]
        [0.62, 0.87, 1.05],
        [0.75, 1.00, 1.19],
        [0.83, 1.10, 1.31],
    ],
}


def sans_balises(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesures", default="data/sustain_measured.json")
    ap.add_argument("--contexte", default="data/heal_context.json")
    ap.add_argument("--items", default="assets/item_data.json")
    ap.add_argument("--out", default="data/sustain_model.json")
    args = ap.parse_args()

    mesures = json.loads(open(args.mesures, encoding="utf-8").read())
    contexte = json.loads(open(args.contexte, encoding="utf-8").read())
    items = json.loads(open(args.items, encoding="utf-8").read())["data"]

    # --- Excès de soin par champion, rapporté à la médiane de son poste ---
    champions = {}
    for champ, v in mesures["champions"].items():
        exces = v.get("exces_sur_poste")
        if exces is None or exces < 40:
            continue
        champions[ALIAS.get(champ, champ)] = {
            "exces_par_min": exces,
            "poste": v["poste"],
            "n": v["n_poste"],
        }

    # --- Apport des objets, indexé par IDENTIFIANT ---
    objets, ecartes = {}, []
    for iid, v in contexte["effet_objet"].items():
        delta = v["delta_median_par_min"]
        if delta < DELTA_MINIMAL:
            continue
        nom = v["nom"]
        if nom in EXCLUS_EXPLICITES:
            ecartes.append((nom, delta, "non réductible par l'Hémorragie"))
            continue
        desc = sans_balises(items.get(iid, {}).get("description", ""))
        if not any(m in desc for m in MECANIQUES):
            ecartes.append((nom, delta, "aucune mécanique de soin — corrélation"))
            continue
        objets[iid] = {
            "nom": nom,
            "apport_par_min": delta,
            "champions_mesures": v["champions_mesures"],
        }

    sortie = {
        "_lisez_moi": [
            "Modèle de soin dynamique, mesuré sur les matchs collectés.",
            "poids = racine((excès_champion x mult_KDA + Σ apports_objets) / 750)",
            "Objets indexés par IDENTIFIANT : les noms dérivent à chaque patch.",
        ],
        "reference": REF_SOIN,
        "participants": mesures["matchs_participants"],
        "references_par_poste": mesures["postes"],
        "domination": DOMINATION,
        "champions": dict(sorted(champions.items(),
                                 key=lambda kv: -kv[1]["exces_par_min"])),
        "objets": dict(sorted(objets.items(),
                              key=lambda kv: -kv[1]["apport_par_min"])),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sortie, f, indent=2, ensure_ascii=False)

    print(f"{len(champions)} champions, {len(objets)} objets -> {args.out}")
    print("\nObjets écartés malgré une mesure élevée :")
    for nom, delta, motif in sorted(ecartes, key=lambda e: -e[1])[:10]:
        print(f"   {nom[:30]:32} {delta:>+6.0f}/min   {motif}")


if __name__ == "__main__":
    main()
