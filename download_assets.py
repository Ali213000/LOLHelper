"""Download champion and item data from Riot Data Dragon.

⚠️ LOCALE — ne pas changer sans mettre à jour stat_analyzer.py :
    * champions → en_US : le LCU et champion_scorer.py travaillent sur les noms
      anglais (l'affichage FR est fait par _CHAMP_FR dans ai/coaching_engine.py).
    * items     → fr_FR : toutes les tables de services/stat_analyzer.py
      (_LETHALITY_ITEMS, _HEALING_ITEMS, _UNIQUE_GROUPS…) sont indexées sur les
      noms d'objets FRANÇAIS. Télécharger les items en en_US casse silencieusement
      le moteur : plus aucune correspondance, aucune erreur levée.
"""
import json
import os
import sys

import requests

CHAMPION_LOCALE = "en_US"
ITEM_LOCALE = "fr_FR"

# Sondes de cohérence : si ces noms disparaissent, le locale est mauvais.
ITEM_PROBES = ["Coiffe de Rabadon", "Couperet noir"]

os.makedirs("assets", exist_ok=True)

ver = requests.get(
    "https://ddragon.leagueoflegends.com/api/versions.json", timeout=10
).json()[0]
print(f"Data Dragon version: {ver}")

champs = requests.get(
    f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/{CHAMPION_LOCALE}/champion.json",
    timeout=30,
).json()
with open("assets/champion_data.json", "w", encoding="utf-8") as f:
    json.dump(champs, f, ensure_ascii=False)
print(f"Champions saved ({CHAMPION_LOCALE}): {len(champs['data'])}")

items = requests.get(
    f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/{ITEM_LOCALE}/item.json",
    timeout=30,
).json()

# Garde-fou : refuse d'écrire un fichier qui casserait stat_analyzer.
item_names = {v.get("name", "") for v in items["data"].values()}
missing = [p for p in ITEM_PROBES if p not in item_names]
if missing:
    sys.exit(
        f"ABANDON : les objets {missing} sont absents du téléchargement "
        f"({ITEM_LOCALE}). assets/item_data.json n'a PAS été modifié — "
        f"l'écraser casserait services/stat_analyzer.py."
    )

with open("assets/item_data.json", "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False)
print(f"Items saved ({ITEM_LOCALE}): {len(items['data'])}")

print("Done.")
