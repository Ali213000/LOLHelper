#!/usr/bin/env python3
"""
Recalibre les importances d'axes de draft_config.json depuis les mesures.

Les importances étaient écrites à la main, avec des commentaires affirmatifs
(« En dessous de 0.6 la comp fond en teamfight ») jamais confrontés aux données.
Mesurées en ÉCART entre les deux camps sur 7 810 matchs — test apparié, chaque
match servant de son propre témoin — elles ne tiennent pas :

    sustain          +4.9 %   importance écrite 0.35
    dive_resistance  +4.7 %   importance écrite 0.50
    poke_siege       -4.4 %   importance écrite 0.40
    waveclear        -4.2 %   importance écrite 0.60
    frontline        +2.7 %   importance écrite 1.00
    engage           +2.6 %   importance écrite 1.00
    hard_cc          +0.4 %   importance écrite 0.80

Les trois axes à importance maximale sont ceux qui ne prédisent presque rien, et
deux axes récompensés vont dans le sens inverse de la victoire.

Quatorze axes sont testés d'un coup : sans correction pour comparaisons
multiples, on attend environ un faux positif par passe. Le seuil de Bonferroni
(z = 2.92) ne laisse passer que sustain et dive_resistance, et de justesse.
poke_siege et waveclear, spectaculaires à première vue, n'y survivent PAS.

Règle appliquée :
  · axe significatif  importance proportionnelle à son effet, 1.0 au plus fort
  · axe non concluant importance uniforme et modeste

Les axes non concluants ne sont pas mis à zéro. À ce volume, un effet réel
allant jusqu'à 3.5 points resterait indétectable : l'absence de preuve n'est pas
une preuve d'absence, et les annuler affirmerait ce que la mesure n'établit pas.
Ils reçoivent donc tous le même poids modeste, faute de pouvoir les départager.

Les cibles (« target ») sont conservées : elles fixent le point de saturation du
besoin, choix structurel que ces mesures ne remettent pas en cause.
"""
from __future__ import annotations

import json
import math
import os
from statistics import NormalDist

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RACINE, "data", "draft_config.json")
MESURES = os.path.join(RACINE, "data", "draft_measures.json")

# Poids uniforme des axes que ce volume ne permet pas de departager.
IMPORTANCE_NON_CONCLUANTE = 0.35


# ---------------------------------------------------------------- poids

# Taille d'effet retenue par composante, en points de victoire.
#   meta   2.2  ecart-type vrai entre champions (methode des moments)
#   lane   3.2  ecart-type des duels OP.GG, corrige de la selection sur extremes
# team_fit 1.7  mesure, NON significatif (z=1.07 sur 5 056 matchs)
#  counter 1.0  mesure a -2.9 (z=-1.82) : aucune preuve positive, on plancher
#
# Les deux dernieres ne sont pas annulees. A ce volume un effet reel jusqu'a
# ~3.2 points reste indetectable : l'absence de preuve n'est pas une preuve
# d'absence. Mais elles ne peuvent plus porter 55 % du score sans l'avoir merite.
EFFET_COMPOSANTE = {"meta": 2.2, "lane": 3.2, "team_fit": 1.7, "counter_comp": 1.0}

# Probabilite que l'adversaire de voie soit connu au moment de choisir. En
# premier pick on ne le connait pas, en dernier si : donner a « lane » le meme
# poids partout revenait a gaspiller ce poids quand la composante est neutre.
DISPONIBILITE_LANE = {"blind_pick": 0.0, "first_pick": 0.1,
                      "middle": 0.5, "last_pick": 1.0}


def calibrer_poids(config, mesures_composantes):
    """Repartit les poids proportionnellement aux tailles d'effet mesurees."""
    effets = dict(EFFET_COMPOSANTE)
    for nom, m in (mesures_composantes or {}).items():
        if nom in effets:
            # On ne retient une mesure que si elle est positive ; sinon plancher.
            effets[nom] = max(1.0, round(m["ecart"] * 100, 1))

    print()
    print(f"{'composante':14}{'effet retenu':>14}")
    for nom, e in effets.items():
        print(f"{nom:14}{e:>13.1f} pt")

    print()
    print(f"{'rang de pick':14}{'meta':>8}{'lane':>8}{'team_fit':>10}{'counter':>9}")
    for slot, dispo in DISPONIBILITE_LANE.items():
        part_lane = effets["lane"] * dispo
        autres = {k: v for k, v in effets.items() if k != "lane"}
        total = part_lane + sum(autres.values())
        poids = {"lane": round(part_lane / total, 3)}
        for k, v in autres.items():
            poids[k] = round(v / total, 3)
        config["weights_by_slot"][slot] = {
            "meta": poids["meta"], "lane": poids["lane"],
            "team_fit": poids["team_fit"], "counter_comp": poids["counter_comp"],
        }
        print(f"{slot:14}{poids['meta']:>8.2f}{poids['lane']:>8.2f}"
              f"{poids['team_fit']:>10.2f}{poids['counter_comp']:>9.2f}")

    config["weights_source"] = (
        "poids proportionnels aux tailles d'effet mesurees "
        "(scripts/measure_component_power.py), ponderes par la disponibilite "
        "de l'adversaire de voie selon le rang de pick"
    )


def main() -> None:
    brut = json.load(open(MESURES, encoding="utf-8"))
    mesures = brut["axes_effet"]
    config = json.load(open(CONFIG, encoding="utf-8"))
    seuils = config["team_thresholds"]

    # Bonferroni sur le nombre d'axes testés simultanément.
    z_seuil = NormalDist().inv_cdf(1 - 0.05 / (2 * len(mesures)))

    def z_de(m):
        par_quintile = m["n"] // 5
        return m["ecart"] / math.sqrt(2 * 0.25 / par_quintile)

    retenus = {k: v for k, v in mesures.items()
               if z_de(v) > z_seuil}          # positifs significatifs seulement
    plafond = max((v["ecart"] for v in retenus.values()), default=0.0)

    print(f"{len(mesures)} axes testes, seuil de Bonferroni z = {z_seuil:.2f}")
    print()
    print(f"{'axe':20} {'effet':>8} {'z':>7} {'avant':>7} {'apres':>7}")
    for axe, cfg in seuils.items():
        m = mesures.get(axe)
        if not m:
            continue
        avant = cfg.get("importance", 1.0)
        z = z_de(m)
        if axe in retenus:
            apres = round(m["ecart"] / plafond, 2)
            verdict = "mesure"
        else:
            apres = IMPORTANCE_NON_CONCLUANTE
            verdict = "non concluant"
        cfg["importance"] = apres
        cfg["effet_mesure"] = round(m["ecart"], 4)
        cfg["z"] = round(z, 2)
        cfg["verdict"] = verdict
        print(f"{axe:20} {m['ecart']:+7.1%} {z:7.2f} {avant:>7.2f} {apres:>7.2f}   {verdict}")

    config["team_thresholds_source"] = (
        "importances calibrées par scripts/calibrate_draft_config.py depuis "
        "data/draft_measures.json (écart entre camps, 7810 matchs, Bonferroni)"
    )

    # La part AP mesurée est plate au-dessus de 40 % (50.2 / 50.0 / 50.9 / 50.4)
    # et ne décroche qu'en dessous : 48.5 % pour les équipes à moins de 40 % AP.
    # L'idéal codé à 0.55 d'AD place la cible pile sur ce bord défavorable.
    # On vise donc le milieu du plateau et on aplatit la pente : la donnée
    # soutient « ne pas être monochrome AD », pas un optimum étroit.
    config.setdefault("damage_mix", {})
    config["damage_mix"]["ideal_ad_ratio"] = 0.48
    config["damage_mix"]["slope"] = 1.2
    config["damage_mix"]["comment"] = (
        "Mesuré : victoire 48.5 % sous 40 % d'AP, plate ensuite (50.0-50.9 %). "
        "Cible au milieu du plateau, pente faible faute d'optimum marqué."
    )

    calibrer_poids(config, brut.get("composantes_effet"))

    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\necrit dans {CONFIG}")


if __name__ == "__main__":
    main()
