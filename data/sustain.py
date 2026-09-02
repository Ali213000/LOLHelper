"""
data/sustain.py — Modèle de soin dynamique, mesuré sur les matchs collectés.

Le poids d'anti-soin d'un adversaire n'est pas une constante : il dépend de son
champion, de ce qu'il a acheté et de l'état de la partie.

    soin_estimé = excès_champion x facteur_domination + Σ apports_objets
    poids       = racine(soin_estimé / 750), plafonné à 1

Les trois termes viennent de mesures sur 75 900 participants (7 810 matchs) :

  · excès_champion   soin médian du champion moins la médiane de son POSTE.
                     Sans cette correction, tous les jungleurs ressortaient en
                     tête : la sustain de camps place la médiane jungle à
                     429 PV/min contre 151 en mid.

  · apports_objets   effet mesuré à champion constant (avec/sans le même
                     objet), pour ne pas confondre l'objet avec le profil de
                     qui l'achète. Soif-de-sang vaut +94 PV/min.

  · facteur          grille croisée or investi x KDA. Les deux signaux portent
                     une information indépendante : à or constant le KDA fait
                     encore varier le soin de x1.6, et réciproquement x1.34.

Un champion sans soin dans son kit devient une cible d'anti-soin par son seul
build : Zed pèse 0.00 nu et 0.35 avec Soif-de-sang.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

_JSON = Path(__file__).with_name("sustain_model.json")
_modele: dict | None = None


def _charger() -> dict:
    global _modele
    if _modele is None:
        try:
            with open(_JSON, encoding="utf-8") as f:
                _modele = json.load(f)
            logger.debug("Modèle de soin : %d champions, %d objets",
                         len(_modele.get("champions", {})),
                         len(_modele.get("objets", {})))
        except Exception as exc:
            logger.warning("Modèle de soin indisponible (%s) : %s", _JSON, exc)
            _modele = {"champions": {}, "objets": {}, "reference": 750.0}
    return _modele


def disponible() -> bool:
    return bool(_charger().get("champions"))


def _tranche(valeur: float, bornes: list[float]) -> int:
    for i, borne in enumerate(bornes):
        if valeur < borne:
            return i
    return len(bornes)


def facteur_domination(ratio_or: float, kda: float) -> float:
    """Multiplicateur de soin selon l'or investi et le KDA."""
    dom = _charger().get("domination")
    if not dom:
        return 1.0
    i = _tranche(ratio_or, dom["bornes_or"])
    j = _tranche(kda, dom["bornes_kda"])
    try:
        return float(dom["grille"][i][j])
    except (IndexError, KeyError, TypeError):
        return 1.0


def apport_objets(item_ids) -> float:
    """Soin par minute apporté par les objets portés, en PV/min."""
    objets = _charger().get("objets", {})
    return sum(
        objets[str(iid)]["apport_par_min"]
        for iid in (item_ids or [])
        if str(iid) in objets
    )


def poids(champion: str, item_ids=None, kda: float = 1.5,
          ratio_or: float = 1.0) -> float:
    """
    Poids d'anti-soin de cet adversaire, dans [0, 1].

    *ratio_or* est l'or investi en objets rapporté à la médiane de la partie —
    exactement ce que l'app calcule déjà pour l'écart avec les adversaires.
    """
    m = _charger()
    base = (m.get("champions", {}).get(champion) or {}).get("exces_par_min", 0.0)
    total = max(0.0, base) * facteur_domination(ratio_or, kda) + apport_objets(item_ids)
    if total <= 0:
        return 0.0
    return round(min(1.0, math.sqrt(total / m.get("reference", 750.0))), 2)


def detail(champion: str, item_ids=None, kda: float = 1.5,
           ratio_or: float = 1.0) -> dict:
    """Décomposition du poids, pour l'affichage et le diagnostic."""
    m = _charger()
    base = (m.get("champions", {}).get(champion) or {}).get("exces_par_min", 0.0)
    fact = facteur_domination(ratio_or, kda)
    objets = _charger().get("objets", {})
    portes = [
        (objets[str(i)]["nom"], objets[str(i)]["apport_par_min"])
        for i in (item_ids or []) if str(i) in objets
    ]
    return {
        "champion": champion,
        "base_par_min": round(max(0.0, base), 1),
        "facteur_domination": fact,
        "objets": portes,
        "apport_objets": round(sum(a for _, a in portes), 1),
        "poids": poids(champion, item_ids, kda, ratio_or),
    }
