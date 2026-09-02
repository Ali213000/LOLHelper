"""
data/item_conditions.py — chargement de la base des effets conditionnels.

La base (item_conditions.json) est indexée par identifiant d'objet. Ce module la
résout vers les noms courants de Data Dragon, seule forme comprise par le
scoring, et SIGNALE tout identifiant introuvable au lieu de l'ignorer.

C'est le point clé : les listes précédentes étaient écrites à la main en
français et trois d'entre elles avaient dérivé — « Plaques de l'épineux »,
« Chaîne de Chempunk », « Voile de mercure » n'existaient plus. Aucune erreur
n'était levée : le déclencheur correspondant ne partait simplement jamais.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_JSON = Path(__file__).with_name("item_conditions.json")

# Plancher de décote : même privé de tous ses effets conditionnels, un objet
# conserve ses statistiques brutes. On ne descend pas en dessous.
RATIO_MINIMUM = 0.45


def _lire_base() -> list[dict]:
    try:
        with open(_JSON, encoding="utf-8") as f:
            return json.load(f).get("objets", [])
    except Exception as exc:
        logger.error("Base des conditions d'objets illisible (%s) : %s", _JSON, exc)
        return []


def charger(item_id_to_name: dict[int, str]) -> dict[str, list[dict]]:
    """
    Renvoie {nom_objet: [{"trigger": str, "conditional_share": float}, ...]}.

    *item_id_to_name* provient de Data Dragon. Tout identifiant absent est
    journalisé en avertissement : c'est le signal qu'un objet a été retiré du
    jeu ou renuméroté, et que l'entrée doit être mise à jour.
    """
    par_nom: dict[str, list[dict]] = {}
    manquants: list[str] = []

    for entree in _lire_base():
        iid = entree.get("id")
        nom = item_id_to_name.get(iid)
        if not nom:
            manquants.append(f"{iid} ({entree.get('nom_indicatif', '?')})")
            continue
        conditions = [
            {
                "trigger": c["declencheur"],
                "conditional_share": float(c["part"]),
            }
            for c in entree.get("conditions", [])
            if c.get("declencheur") and c.get("part")
        ]
        if conditions:
            par_nom.setdefault(nom, []).extend(conditions)

    if manquants:
        logger.warning(
            "Conditions d'objets : %d identifiant(s) introuvable(s) dans Data "
            "Dragon, entrées ignorées — %s",
            len(manquants), ", ".join(manquants),
        )
    logger.debug("Conditions d'objets chargées pour %d objets.", len(par_nom))
    return par_nom


def ids_par_declencheur(declencheur: str) -> set[int]:
    """Identifiants des objets dont la valeur dépend de *declencheur*."""
    return {
        e["id"] for e in _lire_base()
        if any(c.get("declencheur") == declencheur for c in e.get("conditions", []))
    }


def noms_par_declencheur(declencheur: str, item_id_to_name: dict[int, str]) -> set[str]:
    """Idem, résolu en noms courants (ceux introuvables sont écartés)."""
    return {
        nom for iid in ids_par_declencheur(declencheur)
        if (nom := item_id_to_name.get(iid))
    }


def ratio_effectif(conditions: Iterable[dict], triggers: dict) -> tuple[float, list[str]]:
    """
    Part du prix réellement utile, compte tenu des déclencheurs inactifs.

    Un objet peut porter plusieurs conditions (Rappel mortel : anti-soin ET
    pénétration d'armure) : elles se cumulent multiplicativement, sans jamais
    descendre sous RATIO_MINIMUM.
    """
    ratio = 1.0
    inactifs: list[str] = []
    for cond in conditions:
        part = cond.get("conditional_share", 0.0)
        if part <= 0:
            continue
        if not triggers.get(cond["trigger"], False):
            ratio *= 1.0 - part
            inactifs.append(cond["trigger"])
    return max(RATIO_MINIMUM, ratio), inactifs
