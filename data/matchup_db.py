"""
data/matchup_db.py — Duels de voie et synergies alliées, mesurés par OP.GG.

Le duel champion contre champion est le seul signal de draft hors de portée de
notre collecte. Sur nos 7 550 matchs, 8 611 appariements distincts sont
rencontrés à 4.4 parties chacun ; il faudrait un million de matchs pour couvrir
84 % des duels réellement vécus à +/- 5 points.

S2 « lane » renvoyait donc 0.5 pour tout le monde — la clé « lane » de
patch_stats.json ne contient que les cinq noms de rôles, pas de duels — alors
qu'elle pèse jusqu'à 0.25 du score de pick.

Les données proviennent du serveur MCP officiel d'OP.GG et couvrent les 3
meilleurs et 3 pires contres par champion et par poste : les extrêmes, c'est-à-
dire les seuls duels qui changent une décision. Un appariement absent n'est pas
un appariement neutre, c'est un appariement inconnu : la fonction rend None et
l'appelant retombe sur sa valeur neutre, sans inventer d'avantage.

Attribution : données de duels et synergies fournies par OP.GG.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_JSON = Path(__file__).with_name("opgg_matchups.json")
_donnees: dict | None = None

# Riot nomme le même poste de trois façons selon l'API interrogée.
_ALIAS_POSTE = {
    "UTILITY": "SUPPORT", "SUPPORT": "SUPPORT",
    "MIDDLE": "MID", "MID": "MID",
    "BOTTOM": "ADC", "ADC": "ADC", "BOT": "ADC",
    "TOP": "TOP", "JUNGLE": "JUNGLE",
}


def _charger() -> dict:
    global _donnees
    if _donnees is None:
        try:
            with open(_JSON, encoding="utf-8") as f:
                _donnees = json.load(f).get("entrees", {})
            logger.debug("Duels OP.GG : %d couples champion|poste", len(_donnees))
        except Exception as exc:
            logger.warning("Duels OP.GG indisponibles (%s) : %s", _JSON, exc)
            _donnees = {}
    return _donnees


def disponible() -> bool:
    return bool(_charger())


def _entree(champion: str, poste: str) -> dict | None:
    from ai.champion_scorer import norm_name

    d = _charger()
    if not d:
        return None
    p = _ALIAS_POSTE.get((poste or "").upper(), (poste or "").upper())
    return d.get(f"{norm_name(champion)}|{p}")


def duel(mon_champion: str, adversaire: str, poste: str) -> dict | None:
    """
    Duel de voie mesuré, ou None si cet appariement n'est pas couvert.

    Cherche d'abord dans MES contres, puis dans ceux de l'adversaire en
    inversant le résultat : OP.GG ne liste que six duels par champion, et un
    appariement peut n'apparaître que d'un seul côté.
    """
    from ai.champion_scorer import norm_name

    cible = norm_name(adversaire)
    e = _entree(mon_champion, poste)
    if e:
        for d in e.get("duels", []):
            if norm_name(d["adversaire"]) == cible:
                return dict(d, sens="direct")

    inverse = _entree(adversaire, poste)
    if inverse:
        moi = norm_name(mon_champion)
        for d in inverse.get("duels", []):
            if norm_name(d["adversaire"]) == moi:
                return {
                    "adversaire": adversaire,
                    "parties": d["parties"],
                    "victoires": d["parties"] - d["victoires"],
                    "victoire": round(1.0 - d["victoire"], 4),
                    "sens": "inverse",
                }
    return None


def synergie(mon_champion: str, poste: str, allie: str) -> dict | None:
    """Synergie mesurée avec cet allié, ou None si non couverte."""
    from ai.champion_scorer import norm_name

    for champ, p, autre in ((mon_champion, poste, allie), (allie, "", mon_champion)):
        e = _entree(champ, p) if p else None
        if e is None and not p:
            # L'allié peut être listé sous n'importe lequel de ses postes.
            d = _charger()
            cible = norm_name(champ)
            for cle, v in d.items():
                if cle.rsplit("|", 1)[0] == cible:
                    e = v
                    break
        if not e:
            continue
        cible = norm_name(autre)
        for s in e.get("synergies", []):
            if norm_name(s["allie"]) == cible:
                return s
    return None


def stats_poste(champion: str, poste: str) -> dict | None:
    """Parties, victoire, taux de pick et de ban du champion à ce poste."""
    e = _entree(champion, poste)
    return (e or {}).get("stats") or None
