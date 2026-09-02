#!/usr/bin/env python3
"""
Récupère duels de voie et synergies depuis le serveur MCP officiel d'OP.GG.

Pourquoi une source externe
---------------------------
Le duel champion contre champion est le seul signal de draft hors de portée de
notre propre collecte. scripts/measure_matchup_coverage.py le chiffre : sur nos
7 550 matchs, 8 611 appariements distincts sont rencontrés, à 4.4 parties
chacun. Pour mesurer un taux de victoire à +/- 5 points sur seulement 12 % des
duels réellement vécus, il faudrait 100 000 matchs ; pour 84 %, un million.

OP.GG agrège des dizaines de millions de parties et publie un serveur MCP
officiel — pas de scraping, pas de contournement, pas de clé d'API :

    https://mcp-api.op.gg/mcp        (github.com/opgginc/opgg-mcp)

Ce qu'on en tire et ce qu'on n'en tire pas
------------------------------------------
L'outil renvoie les 3 meilleurs et 3 pires contres par champion et par poste,
PAS la table complète des 170 adversaires. On récupère donc les extrêmes — ce
sont aussi les seuls duels qui changent une décision de draft. Volumes typiques
observés : 546 à 1 571 parties par duel, soit +/- 2.5 à 4 points, contre 4.4
parties dans nos propres données.

Il fournit en prime les synergies alliées par voie, avec les mêmes volumes
(Lee Sin + Thresh : 5 432 parties, 53 %), qui servent l'équilibre d'équipe.

Courtoisie : une requête à la fois, temporisée, reprise possible sur le fichier
existant. Source à citer en cas d'usage public des données.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

URL = "https://mcp-api.op.gg/mcp"
ENTETES = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}

# Nos fichiers disent TOP/JUNGLE/MID/ADC/SUPPORT ; l'outil attend ces mêmes noms.
_POSTES = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MID": "MID",
           "ADC": "ADC", "SUPPORT": "SUPPORT"}

DELAI = 0.4


class ClientMCP:
    def __init__(self):
        self.s = requests.Session()
        self.sid = None

    def _envoyer(self, methode, params=None, idx=1):
        entetes = dict(ENTETES)
        if self.sid:
            entetes["Mcp-Session-Id"] = self.sid
        corps = {"jsonrpc": "2.0", "id": idx, "method": methode}
        if params is not None:
            corps["params"] = params
        r = self.s.post(URL, headers=entetes, json=corps, timeout=90)
        texte = r.text
        if texte.startswith("event:"):                    # flux SSE
            for ligne in texte.splitlines():
                if ligne.startswith("data:"):
                    texte = ligne[5:].strip()
                    break
        return r, (json.loads(texte) if texte.strip().startswith("{") else texte)

    def ouvrir(self):
        r, _ = self._envoyer("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "lolhelper", "version": "1.0"}})
        self.sid = r.headers.get("Mcp-Session-Id")
        self.s.post(URL, headers={**ENTETES, "Mcp-Session-Id": self.sid},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    timeout=30)
        return self

    def outil(self, nom, arguments, idx=1):
        _, res = self._envoyer("tools/call", {"name": nom, "arguments": arguments}, idx)
        if "error" in res:
            raise RuntimeError(res["error"].get("message", str(res["error"])))
        blocs = res.get("result", {}).get("content", [])
        return blocs[0].get("text", "") if blocs else ""


def _champs(texte: str) -> list:
    """Découpe une liste d'arguments en respectant les guillemets."""
    sortie, courant, dans_guillemets = [], [], False
    for c in texte:
        if c == '"':
            dans_guillemets = not dans_guillemets
        elif c == "," and not dans_guillemets:
            sortie.append("".join(courant).strip())
            courant = []
            continue
        else:
            courant.append(c)
    sortie.append("".join(courant).strip())
    return sortie


def _nombre(x, defaut=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return float(x)
        except (TypeError, ValueError):
            return defaut


def upper_snake(cle: str) -> str:
    """« LeeSin » -> « LEE_SIN », graphie attendue par l'outil."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cle).upper()


def analyser(texte: str) -> dict:
    """Extrait duels et synergies du format compact renvoyé par l'outil."""
    duels = []
    for brut in re.findall(r"StrongCounter\(([^)]*)\)", texte):
        c = _champs(brut)
        if len(c) < 4:
            continue
        parties, victoires = _nombre(c[2]), _nombre(c[3])
        if parties <= 0:
            continue
        duels.append({
            "adversaire_id": _nombre(c[0]),
            "adversaire": c[1],
            "parties": parties,
            "victoires": victoires,
            "victoire": round(victoires / parties, 4),
        })

    synergies = []
    for brut in re.findall(r"Top\(([^)]*)\)", texte):
        c = _champs(brut)
        if len(c) < 11:
            continue
        parties, victoires = _nombre(c[8]), _nombre(c[9])
        if parties <= 0:
            continue
        synergies.append({
            "allie_id": _nombre(c[3]),
            "allie": c[4],
            "poste_allie": c[5],
            "parties": parties,
            "victoires": victoires,
            "victoire": round(victoires / parties, 4),
        })

    # Statistiques du poste : meilleure source que patch_stats.json, dont la clé
    # « meta » est absente et dont le taux de ban est global et sans poste.
    stats = {}
    m = re.search(r'Position\("([A-Z]+)",Stats\(([^)]*)\)', texte)
    if m:
        c = _champs(m.group(2))
        if len(c) >= 5:
            stats = {
                "parties": _nombre(c[0]),
                "victoire": _nombre(c[1], 0.0),
                "taux_pick": _nombre(c[2], 0.0),
                "taux_role": _nombre(c[3], 0.0),
                "taux_ban": _nombre(c[4], 0.0),
            }
    return {"duels": duels, "synergies": synergies, "stats": stats}


def cibles() -> list:
    """(id normalisé, poste) réellement joués, d'après nos fiches champions."""
    from ai.champion_scorer import norm_name

    paires = []
    for chemin in sorted(glob.glob(os.path.join(RACINE, "data", "champions_*.json"))):
        d = json.load(open(chemin, encoding="utf-8"))
        poste = _POSTES.get(d["role"])
        if not poste:
            continue
        for ch in d["champions"]:
            paires.append((norm_name(ch["id"]), poste))
    return paires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(RACINE, "data", "opgg_matchups.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reprendre", action="store_true",
                    help="conserve les entrées déjà présentes dans le fichier")
    a = ap.parse_args()

    paires = cibles()
    if a.limit:
        paires = paires[:a.limit]

    acquis = {}
    if a.reprendre and os.path.exists(a.out):
        acquis = json.load(open(a.out, encoding="utf-8")).get("entrees", {})
        print(f"reprise : {len(acquis)} entrees deja presentes")

    client = ClientMCP().ouvrir()
    echecs = []
    for i, (cid, poste) in enumerate(paires, 1):
        cle = f"{cid}|{poste}"
        if cle in acquis:
            continue
        try:
            texte = client.outil("lol_get_champion_analysis", {
                "champion": upper_snake(cid), "position": poste,
                "game_mode": "ranked"}, idx=100 + i)
            entree = analyser(texte)
            if not entree["duels"]:
                echecs.append((cle, "aucun duel"))
            else:
                acquis[cle] = entree
        except Exception as exc:
            echecs.append((cle, str(exc)[:90]))
        if i % 25 == 0:
            print(f"  {i}/{len(paires)}  ({len(acquis)} retenus, {len(echecs)} echecs)",
                  flush=True)
        time.sleep(DELAI)

    duels = sum(len(v["duels"]) for v in acquis.values())
    syn = sum(len(v["synergies"]) for v in acquis.values())
    parties = [d["parties"] for v in acquis.values() for d in v["duels"]]
    parties.sort()

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({
            "source": "OP.GG MCP officiel (https://mcp-api.op.gg/mcp)",
            "attribution": "Donnees de duels et synergies fournies par OP.GG",
            "recupere_le": time.strftime("%Y-%m-%d"),
            "entrees": acquis,
        }, f, ensure_ascii=False, indent=1)

    print(f"\n{len(acquis)} couples champion|poste, {duels} duels, {syn} synergies")
    if parties:
        print(f"parties par duel : min {parties[0]}  mediane "
              f"{parties[len(parties)//2]}  max {parties[-1]}")
        print(f"duels a plus de 380 parties : "
              f"{sum(1 for p in parties if p >= 380)}/{len(parties)}")
    if echecs:
        print(f"\n{len(echecs)} echecs, premiers :")
        for cle, motif in echecs[:8]:
            print(f"   {cle:26} {motif}")
    print(f"\necrit dans {a.out}")


if __name__ == "__main__":
    main()
