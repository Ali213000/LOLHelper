#!/usr/bin/env python3
"""
Construit data/champion_item_pools.json : le lot d'objets réellement joués par
champion et par poste, avec taux de jeu et taux de victoire.

Pourquoi un lot plutôt qu'un coeur figé
---------------------------------------
core_items_prescription.json ne retient qu'UN couple d'objets et mesure sa
confiance comme la fréquence de ce couple EXACT. C'est une mesure fragile :

    Kai'Sa ADC        0.678   passe le seuil de 0.35
    Lillia JUNGLE     0.594   passe
    Pantheon TOP      0.333   échoue de justesse
    FiddleSticks      0.210   échoue

Or Pantheon achète Éclipse dans 58 % de ses parties et Couperet noir dans 64 % :
pris séparément ces objets sont massivement joués, c'est leur APPARIEMENT qui
est rare. Le seuil éliminait donc des champions dont les objets sont parfaitement
établis, simplement parce qu'ils construisent dans un ordre variable.

Un taux par objet est stable là où un taux par couple ne l'est pas. Le lot laisse
ensuite au moteur son vrai travail : choisir dans ce lot selon la partie en cours.

Le fichier est indexé par norm_name(nom d'affichage) : Match-V5 écrit
« FiddleSticks » et « Kaisa », la Live Client API « Fiddlesticks » et « Kai'Sa ».
La prescription actuelle conserve la graphie Match-V5, et la recherche échoue.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_POSTES = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MIDDLE": "MID",
           "BOTTOM": "ADC", "UTILITY": "SUPPORT"}

# Un objet doit être vu dans au moins 8 % des parties du champion ET 10 fois.
TAUX_MINIMUM = 0.08
PARTIES_MINIMUM_OBJET = 10
PARTIES_MINIMUM_CHAMPION = 40
# Lissage bayésien du taux de victoire.
K_VICTOIRE = 25


def charger_objets():
    """Objets légendaires terminés, leurs noms, et les bottes."""
    with open(os.path.join(RACINE, "assets", "item_data.json"), encoding="utf-8") as f:
        brut = json.load(f)["data"]
    finis, noms, bottes = set(), {}, set()
    for sid, v in brut.items():
        try:
            iid = int(sid)
        except ValueError:
            continue
        if not v.get("maps", {}).get("11", False):
            continue                                  # hors Faille de l'invocateur
        cout = v.get("gold", {}).get("total", 0)
        noms[iid] = v.get("name", "")
        if "Boots" in (v.get("tags") or []) and cout >= 1000:
            bottes.add(iid)
        elif cout >= 1500 and not v.get("into"):
            finis.add(iid)
    return finis, noms, bottes


def _achats(evenements, pid, finis):
    """Objets légendaires terminés, dans l'ordre d'achat, annulations comprises."""
    ordre = []
    for e in evenements:
        if e.get("participantId") != pid:
            continue
        t, iid = e.get("type"), e.get("itemId")
        if t == "ITEM_PURCHASED" and iid in finis and iid not in ordre:
            ordre.append(iid)
        elif t == "ITEM_UNDO":
            apres = e.get("afterId", 0)
            if apres in ordre:
                ordre.remove(apres)
    return ordre


def _un_match(args):
    chemin_match, chemin_timeline, finis = args
    try:
        with gzip.open(chemin_match, "rt", encoding="utf-8") as f:
            m = json.load(f)
        with gzip.open(chemin_timeline, "rt", encoding="utf-8") as f:
            tl = json.load(f)
    except Exception:
        return []

    info = m.get("info", {})
    if info.get("gameDuration", 0) < 900:              # remakes et parties écourtées
        return []
    evenements = [e for fr in tl.get("info", {}).get("frames", []) for e in fr.get("events", [])]

    sortie = []
    for p in info.get("participants", []):
        poste = _POSTES.get(p.get("teamPosition", ""))
        champ = p.get("championName", "")
        if not poste or not champ:
            continue
        achats = _achats(evenements, p.get("participantId"), finis)
        if not achats:
            continue
        sortie.append((champ, poste, bool(p.get("win")), tuple(achats)))
    return sortie


def parcourir(racine_matchs, finis, limite=0):
    taches = []
    for sous in ("train", "test"):
        base = os.path.join(racine_matchs, sous)
        if not os.path.isdir(base):
            continue
        for shard in sorted(os.listdir(base)):
            dossier = os.path.join(base, shard)
            if not os.path.isdir(dossier):
                continue
            for nom in os.listdir(dossier):
                if not nom.endswith(".json.gz") or nom.endswith("_timeline.json.gz"):
                    continue
                tl = os.path.join(dossier, nom.replace(".json.gz", "_timeline.json.gz"))
                if os.path.exists(tl):
                    taches.append((os.path.join(dossier, nom), tl, finis))
    if limite:
        taches = taches[:limite]
    print(f"{len(taches)} matchs a lire", flush=True)

    with ProcessPoolExecutor() as ex:
        for i, res in enumerate(ex.map(_un_match, taches, chunksize=24), 1):
            if i % 1000 == 0:
                print(f"  {i}/{len(taches)}", flush=True)
            yield from res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="G:/matches")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(RACINE, "data", "champion_item_pools.json"))
    a = ap.parse_args()

    finis, noms_objets, bottes = charger_objets()
    print(f"{len(finis)} objets legendaires, {len(bottes)} bottes", flush=True)

    with open(os.path.join(RACINE, "assets", "champion_data.json"), encoding="utf-8") as f:
        champs = json.load(f)["data"]
    # Match-V5 renvoie l'identifiant Data Dragon ; on veut le nom d'affichage.
    # Index insensible à la casse : Data Dragon écrit « Fiddlesticks » là où
    # Match-V5 envoie « FiddleSticks ». Une correspondance stricte perd le
    # champion et le lot ressort sous une clé que le runtime ne cherche jamais.
    id_vers_nom = {}
    for k, v in champs.items():
        nom = v.get("name", k)
        for variante in (v.get("id", k), k, nom):
            id_vers_nom[variante.lower()] = nom

    parties = collections.Counter()
    victoires = collections.Counter()
    vus = collections.defaultdict(collections.Counter)
    gagnes = collections.defaultdict(collections.Counter)
    rangs = collections.defaultdict(lambda: collections.defaultdict(list))

    for champ, poste, gagne, achats in parcourir(a.raw_dir, finis, a.limit):
        cle = (champ, poste)
        parties[cle] += 1
        victoires[cle] += int(gagne)
        for rang, iid in enumerate(achats, 1):
            vus[cle][iid] += 1
            gagnes[cle][iid] += int(gagne)
            rangs[cle][iid].append(rang)

    from ai.champion_scorer import norm_name

    sortie = {}
    for (champ, poste), n in sorted(parties.items()):
        if n < PARTIES_MINIMUM_CHAMPION:
            continue
        base_victoire = victoires[(champ, poste)] / n
        objets = []
        for iid, vu in vus[(champ, poste)].items():
            taux = vu / n
            if taux < TAUX_MINIMUM or vu < PARTIES_MINIMUM_OBJET:
                continue
            v = gagnes[(champ, poste)][iid]
            # Lissage vers le taux de victoire du champion, pas vers 50 % : on
            # veut savoir si l'OBJET aide, pas si le champion est fort.
            wr = (v + K_VICTOIRE * base_victoire) / (vu + K_VICTOIRE)
            liste = rangs[(champ, poste)][iid]
            objets.append({
                "id": iid,
                "nom": noms_objets.get(iid, str(iid)),
                "taux": round(taux, 3),
                "victoire": round(wr, 3),
                "rang_moyen": round(sum(liste) / len(liste), 2),
                "n": vu,
            })
        if not objets:
            continue
        objets.sort(key=lambda o: -o["taux"])
        nom_affiche = id_vers_nom.get(champ.lower(), champ)
        sortie[f"{norm_name(nom_affiche)}|{poste}"] = {
            "champion": nom_affiche,
            "parties": n,
            "victoire_base": round(base_victoire, 3),
            "objets": objets,
        }

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({
            "reference": "G:/matches (train+test)",
            "taux_minimum": TAUX_MINIMUM,
            "k_victoire": K_VICTOIRE,
            "pools": sortie,
        }, f, ensure_ascii=False, indent=1)

    tailles = [len(v["objets"]) for v in sortie.values()]
    print(f"\n{len(sortie)} couples champion|poste")
    print(f"objets par lot : min {min(tailles)}  mediane {sorted(tailles)[len(tailles)//2]}  max {max(tailles)}")
    for cle in ("Pantheon|TOP", "Pantheon|SUPPORT", "Fiddlesticks|JUNGLE", "Lillia|JUNGLE", "Kaisa|ADC"):
        v = sortie.get(cle)
        if not v:
            print(f"{cle}: absent")
            continue
        top = "  ".join(f"{o['nom']} {o['taux']:.0%}/{o['victoire']:.0%}" for o in v["objets"][:5])
        print(f"{cle} ({v['parties']} parties) : {top}")


if __name__ == "__main__":
    main()
