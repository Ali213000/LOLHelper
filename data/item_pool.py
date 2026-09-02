"""
data/item_pool.py — Le lot d'objets intéressants par champion et par poste.

Le moteur note les objets à partir de leurs statistiques brutes. Cela marche
tant que la valeur de l'objet EST dans ses statistiques, et échoue dès qu'elle
vit dans une passive : Éclipse et Glaive d'ombre n'exposent que « 60 AD », donc
face à une Hydre titanesque (40 AD + 600 PV) ils perdent mécaniquement, alors
que Pantheon les achète dans 59 % et 36 % de ses parties.

Aucun réglage d'affinité ne corrige cela — on ne peut pas pondérer une
statistique qui n'existe pas. Le lot renverse la charge de la preuve :

    les chiffres disent quels objets sont bons sur ce champion,
    la partie en cours dit lesquels sont bons maintenant.

Ce qui remplace quoi
--------------------
situational_frequencies.json tenait déjà ce rôle mais ne couvrait que 134
couples champion|poste avec UN objet chacun, indexés en graphie Match-V5
(« FiddleSticks ») que la recherche runtime (« Fiddlesticks ») ne trouve jamais.
Le lot couvre 273 couples, 7 objets en médiane, indexés sur le nom d'affichage.

La recherche tolère les trois graphies rencontrées en production : nom
d'affichage, identifiant Data Dragon, et sortie de norm_name.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_JSON = Path(__file__).with_name("champion_item_pools.json")
_pools: dict | None = None

# Riot nomme le même poste de trois façons selon l'API interrogée.
_ALIAS_POSTE = {
    "UTILITY": "SUPPORT", "SUPPORT": "SUPPORT",
    "MIDDLE": "MID", "MID": "MID",
    "BOTTOM": "ADC", "ADC": "ADC", "BOT": "ADC",
    "TOP": "TOP", "JUNGLE": "JUNGLE",
}

# Un objet absent du lot n'est pas interdit : il est fortement désavantagé.
# Le moteur garde la main quand la partie l'exige (un anti-soin reste jouable
# même si le champion ne l'achète jamais d'habitude).
PENALITE_HORS_LOT = 0.35
# Plancher appliqué à un objet hors lot qui répond à un déclencheur actif.
PLANCHER_DECLENCHEUR = 0.85


def _charger() -> dict:
    global _pools
    if _pools is None:
        try:
            with open(_JSON, encoding="utf-8") as f:
                _pools = json.load(f).get("pools", {})
            logger.debug("Lots d'objets : %d couples champion|poste", len(_pools))
        except Exception as exc:
            logger.warning("Lots d'objets indisponibles (%s) : %s", _JSON, exc)
            _pools = {}
    return _pools


def disponible() -> bool:
    return bool(_charger())


def _cles_champion(champion: str) -> list[str]:
    """Graphies plausibles d'un champion, de la plus probable à la moins."""
    from ai.champion_scorer import norm_name

    brut = (champion or "").strip()
    return [c for c in (norm_name(brut), brut) if c]


def lot(champion: str, poste: str = "") -> dict:
    """
    Lot d'objets de ce champion à ce poste.

    Se rabat sur le poste le plus joué du champion si le poste demandé n'a pas
    assez de parties — un Pantheon support construit comme un Pantheon top, et
    mieux vaut un lot mesuré ailleurs qu'aucun lot du tout.
    """
    pools = _charger()
    if not pools:
        return {}

    p = _ALIAS_POSTE.get((poste or "").upper(), (poste or "").upper())
    for champ in _cles_champion(champion):
        if p:
            trouve = pools.get(f"{champ}|{p}")
            if trouve:
                return trouve
        # Repli insensible à la casse, puis sur le poste le plus fréquenté.
        cible = champ.lower()
        candidats = [
            v for k, v in pools.items()
            if k.rsplit("|", 1)[0].lower() == cible
        ]
        if candidats:
            return max(candidats, key=lambda v: v.get("parties", 0))
    return {}


def merite(champion: str, poste: str, item_id: int | None,
           item_name: str = "") -> float:
    """
    Multiplicateur empirique de cet objet pour ce champion, dans [0.35, 1.25].

    Combine le taux de jeu (est-ce un objet de ce champion ?) et l'écart de
    victoire à sa moyenne (cet objet l'aide-t-il ?). Le taux domine : il repose
    sur bien plus d'observations que le différentiel de victoire.
    """
    donnees = lot(champion, poste)
    if not donnees:
        return 1.0                       # champion non mesuré : aucun avis

    entree = None
    for o in donnees.get("objets", []):
        if (item_id is not None and o["id"] == item_id) or (
            item_name and o["nom"] == item_name
        ):
            entree = o
            break
    if entree is None:
        return PENALITE_HORS_LOT

    # Taux 8 % -> 0.85 ; 50 % -> 1.10 ; 100 % -> 1.20 environ.
    taux = entree["taux"]
    facteur = 0.80 + 0.40 * min(1.0, taux / 0.60)
    # Un objet qui gagne 5 points au-dessus de la moyenne du champion gagne 5 %.
    ecart = entree["victoire"] - donnees.get("victoire_base", 0.5)
    facteur *= 1.0 + max(-0.10, min(0.10, ecart))
    return round(max(PENALITE_HORS_LOT, min(1.25, facteur)), 3)


def detail(champion: str, poste: str = "") -> list[tuple[str, float, float]]:
    """(nom, taux, victoire) du lot, pour l'affichage et le diagnostic."""
    return [
        (o["nom"], o["taux"], o["victoire"])
        for o in lot(champion, poste).get("objets", [])
    ]
