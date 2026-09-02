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


def main() -> None:
    mesures = json.load(open(MESURES, encoding="utf-8"))["axes_effet"]
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

    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\necrit dans {CONFIG}")


if __name__ == "__main__":
    main()
