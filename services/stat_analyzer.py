"""
services/stat_analyzer.py — Universal item recommendation engine.

3-axis evaluation system (applies identically to ALL champion classes):
  Axis 1: Power Spike  — core items complete?
  Axis 2: Win Condition Metric — class-specific effectiveness (kill threshold,
           DPS ratio, balance ratio, frontline score)
  Axis 3: Situational Triggers — Grievous Wounds, armor pen, tenacity,
           defensive adaptation, anti-fed-enemy
"""
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from data.champion_affinity import ChampionAffinity

from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # imports réservés au typage
    from core.state_manager import LiveStats

logger = logging.getLogger(__name__)


# ============================================================
# Constants — stat gold values (source: LoL Wiki V14.21)
# OR par point de stat, dérivé des composants les moins chers.
# Ce sont les valeurs utilisées pour calculer l'efficacité en or.
# À réviser chaque grosse préseason si Riot re-price un composant.
# ============================================================

# Valeurs for DDragon stat key → gold per stat unit
_GOLD_PER_STAT: dict[str, float] = {
    "FlatPhysicalDamageMod":  35.00,   # AD        — Épée Longue
    "FlatMagicDamageMod":     20.00,   # AP        — Tome d'Amplification (corrigé)
    "FlatArmorMod":           20.00,   # Armure    — Armure de Tissu
    "FlatSpellBlockMod":      20.00,   # MR        — Cape Anti-magie (corrigé)
    "FlatHPPoolMod":           2.67,   # PV        — Cristal de Rubis  (valeur / point)
    "FlatCritChanceMod":    4000.00,   # Crit%     — Cape d'Agilité (par fraction 0-1 → ×100)
    "PercentAttackSpeedMod":2500.00,   # AS%       — Dague (par fraction 0-1 → ×100)
    "PercentLifeStealMod":  5360.00,   # LifeSteal — Sceptre Vampirique (53.6 / 1%)
    "FlatHPRegenMod":         17.00,
    "FlatMPPoolMod":           1.00,   # Mana      — Cristal de Saphir (1g/point)
    "FlatMovementSpeedMod":   12.00,   # MS plate  — Bottes (12g/point)
}

# Valeurs wiki par point de stat — utilisées pour les calculs marginaux
# (format lisible, pas le format DDragon)
_WIKI_GOLD: dict[str, float] = {
    "hp":               2.67,   # par 1 PV
    "ap":              20.00,   # par 1 AP
    "armor":           20.00,   # par 1 armure
    "mr":              20.00,   # par 1 RM
    "as_pct":          25.00,   # par 1% AS bonus
    "lethality":       30.00,   # par 1 létalité
    "ad":              35.00,   # par 1 AD
    "crit_pct":        40.00,   # par 1% crit
    "armor_pen_pct":   41.70,   # par 1% pén. armure
    "magic_pen_flat":  46.70,   # par 1 pén. magique plate
    "ah":              50.00,   # par 1 AH
    "lifesteal_pct":   53.60,   # par 1% vol de vie
    "mana":             1.00,   # par 1 mana
    "flat_ms":         12.00,   # par 1 MS plate
}

# Base AS approximative (pour calculer le bonus AS actuel)
_BASE_AS_BY_CLASS: dict[str, float] = {
    "Assassin":  0.668,
    "Mage":      0.625,
    "Marksman":  0.658,
    "Fighter":   0.644,
    "Tank":      0.612,
    "Support":   0.604,
}



# ============================================================
# Constants — champion behaviour
# ============================================================

# Champions with strong healing — weighted by heal impact (1.0 = critical, 0.3 = marginal).
# Trigger requires total weight >= 1.5 OR a single champion with weight >= 0.9.
# Removed marginal healers: Garen, Nasus, Jax, Taric, Milio, Kayn, Ekko, Hecarim.
# Poids de repli, MESURÉS sur 75 900 participants : soin médian du champion
# moins la médiane de son POSTE, ramené dans [0,1] par racine(excès / 750).
# Sans la correction par poste, tous les jungleurs ressortaient en tête — la
# sustain de camps place la médiane jungle à 429 PV/min contre 151 en mid.
#
# Ce sont les valeurs à l'état MOYEN et sans objet de soin. Le moteur utilise
# normalement le modèle dynamique (data/sustain.py), qui ajoute l'effet du
# build et de la domination ; cette table ne sert que de repli.
#
# Les estimations précédentes étaient nettement à côté : Aatrox valait 1.00
# pour 0.58 mesuré, Warwick 0.90 pour 0.56, Zac 0.50 pour 1.00, et Darius
# 0.60 alors qu'il ne dépasse pas sa médiane de poste.
_HEALING_CHAMPION_WEIGHTS: dict[str, float] = {
    "Zac": 1.00,                "Vladimir": 1.00,           "Soraka": 1.00,
    "Briar": 0.85,              "Sona": 0.77,               "Nami": 0.69,
    "Trundle": 0.69,            "Tryndamere": 0.67,         "Fiora": 0.67,
    "Taric": 0.66,              "Nasus": 0.64,              "Rek'Sai": 0.64,
    "Olaf": 0.60,               "Xin Zhao": 0.60,           "Aatrox": 0.58,
    "Dr. Mundo": 0.58,          "Nidalee": 0.58,            "Lillia": 0.57,
    "Senna": 0.57,              "Locke": 0.57,              "Warwick": 0.56,
    "Hecarim": 0.56,            "Nunu & Willump": 0.55,     "Tahm Kench": 0.53,
    "Milio": 0.52,              "Zaahen": 0.51,             "Alistar": 0.51,
    "Irelia": 0.46,             "Gragas": 0.46,             "Janna": 0.44,
    "Illaoi": 0.44,             "Swain": 0.44,              "Sylas": 0.41,
    "Samira": 0.40,             "Cassiopeia": 0.38,         "Yuumi": 0.38,
    "Seraphine": 0.37,          "Nilah": 0.37,              "Rakan": 0.35,
    "Renekton": 0.35,           "Yasuo": 0.35,              "Yorick": 0.34,
    "Kayn": 0.33,               "FiddleSticks": 0.33,       "Zilean": 0.33,
    "Cho'Gath": 0.32,           "Kassadin": 0.32,           "Sion": 0.32,
    "Bel'Veth": 0.31,           "Mordekaiser": 0.31,        "Kindred": 0.31,
    "Ekko": 0.31,
}
# Pre-computed set of champion names for fast membership tests
_HEALING_CHAMPIONS: set[str] = set(_HEALING_CHAMPION_WEIGHTS.keys())

# Items with strong lifesteal/omnivamp/healing that trigger anti-heal
_HEALING_ITEMS: set[str] = {
    "Soif-de-sang",               # Bloodthirster
    "Lame du roi déchu",          # Blade of the Ruined King
    "Hydre vorace",               # Ravenous Hydra
    "Voie immortelle",            # Riftmaker
    "Arc-bouclier immortel",      # Immortal Shieldbow
    "Ciel éventré",              # Sundered Sky
    "Régénérateur de pierre de lune", # Moonstone Renewer
    "Rédemption",                # Redemption
}

# Champions with notable hard CC (trigger tenacity if 3+ in enemy team).
# Only kept champions with *hard* CC (stuns/suppressions/knockups) — not slows.
from data.hard_cc import HARD_CC_CHAMPIONS as _CC_CHAMPIONS

# Enemy damage type by class
_CLASS_DAMAGE_SPLIT: dict[str, tuple[float, float]] = {
    "Marksman": (1.00, 0.00),
    "Assassin": (0.80, 0.20),
    "Fighter":  (0.75, 0.25),
    "Mage":     (0.05, 0.95),
    "Support":  (0.20, 0.80),
    "Tank":     (0.60, 0.40),
}

# Burst estimate from enemy: (base, per_level) — damage dealt in ~3 seconds
_CLASS_BURST_FORMULA: dict[str, tuple[float, float]] = {
    "Assassin": (280.0, 78.0),
    "Mage":     (220.0, 68.0),
    "Marksman": (150.0, 48.0),
    "Fighter":  (180.0, 55.0),
    "Tank":     (100.0, 30.0),
    "Support":  ( 90.0, 35.0),
}

# ============================================================
# Constants — item classifications
# ============================================================

# Pénétration d'armure plate et % — items létalité (létalité, %pén armure)
# Format: nom_fr -> (létalité_plate, %pen_armure)
_LETHALITY_ITEMS: dict[str, tuple[float, float]] = {
    "Lame spectre de Youmuu":  (18, 0.00),
    "Lame funeste":            (18, 0.00),   # Duskblade of Draktharr
    "Griffes du rôdeur":       (10, 0.00),   # Edge of Night
    "Écho-lames":              (15, 0.00),   # Voltaic Cyclosword
    "Crochet de serpent":      (12, 0.00),   # Serpent's Fang
    "Hydre profane":           (15, 0.00),   # Profane Hydra
    "Couperet noir":            (21, 0.00),   # Black Cleaver
    "Dague dentelée":          (10, 0.00),   # Serrated Dirk (component)
    "Lame d'avarice":          (15, 0.00),   # The Collector
    "Rancune de Serylda":      ( 0, 0.30),   # Serylda's Grudge
    "Salutations de Dominik":  ( 0, 0.35),   # Lord Dominik's Regards
    "Rappel mortel":           ( 0, 0.30),   # Mortal Reminder
}

_MAGIC_PEN_ITEMS: dict[str, tuple[float, float]] = {
    "Bâton du vide":           ( 0, 0.40),   # Void Staff
    "Fleur de crypte":         ( 0, 0.40),   # Cryptbloom
    "Flamme-ombre":            (10, 0.00),   # Shadowflame
    "Flamme sauvage":          (10, 0.00),   # Stormsurge
    "Lame des mirages":        (10, 0.00),   # Malignance
    "Écho de Luden":           (10, 0.00),   # Luden's Companion
    "Sandales de Mercure":     (18, 0.00),   # Sorcerer's Shoes
    "Pique dimensionnelle de Zaz'Zak": (10, 0.00),  # Horizon Focus
}

# Grievous Wounds items par type de dégâts
# Repli si assets/item_data.json est indisponible. La liste réelle est
# reconstruite depuis les descriptions DDragon dans _ensure_loaded() : les noms
# écrits à la main dérivent à chaque patch. "Plaques de l'épineux" et
# "Chaîne de Chempunk" n'existaient plus, ce qui désactivait silencieusement
# tout le déclencheur anti-soin — pour les tanks, il ne pouvait jamais partir.
_GW_AD_ITEMS  = {"Rappel mortel", "Épée dentelée chimico-punk", "Marque du bourreau"}
_GW_AP_ITEMS  = {"Morellonomicon", "Orbe de l'oubli"}
_GW_TANK_ITEM = {"Cotte épineuse", "Armure roncière"}   # Thornmail, Bramble Vest

# Mot-clé Hémorragie dans la description FR = Grievous Wounds
_GW_KEYWORD = "hémorragie"

# Objets d'adaptation défensive par classe (damage-dealer qui achète 1 item défensif quand fed)
_ADAPT_ASSASSIN = {
    "Ange gardien",         # meilleur all-rounder (armure + revive), protège la bounty
    "Danse de la mort",     # anti-burst physique + heal on kills (resets)
    "Gueule de Malmortius", # anti-burst AP spécifiquement
    "Griffes du rôdeur",    # spell shield pour CC point-and-click
    "Crochet de serpent",   # vs compos avec beaucoup de boucliers
}
_ADAPT_MAGE = {
    "Sablier de Zhonya",    # stasis vs AD assassins/fighters qui te plongent
    "Voile de la Banshee",  # vs burst AP point-and-click (Veigar, Annie)
}
_ADAPT_MARKSMAN = {
    "Ange gardien",         # item défensif classique ADC
    "Arc-bouclier immortel",# mythique avec bouclier intégré
    "Gage de Sterak",       # vs compos à fort burst (moins courant)
}
_ADAPT_FIGHTER = {
    "Gage de Sterak",       # fighters ont high base AD → plus efficace ici
    "Danse de la mort",     # bruiser anti-burst
    "Gueule de Malmortius", # vs menaces AP
    "Ange gardien",         # sécurité générique
}
_ADAPT_TANK = set() # Tanks n'ont pas d'items défensifs "hybrides" puisqu'ils buildent déjà full défense.

# Items de ténacité
_TENACITY_ITEMS = {"Ceinture de mercure", "Force du vent", "Force de la nature", "Gage de Sterak"}

# Items % pen armure (anti-tank pour dealers physiques)
_ARMOR_PEN_PCT_ITEMS = {"Salutations de Dominik", "Rancune de Serylda", "Rappel mortel"}

# Items % pen magie (anti-tank pour dealers AP)
_MAGIC_PEN_PCT_ITEMS = {"Bâton du vide", "Fleur de crypte"}

# ============================================================
# 1. PASSIFS MULTIPLICATIFS
# Ces items ont des passifs qui scalent avec les stats actuelles
# — les stats brutes DDragon sous-estiment leur vraie valeur.
# ============================================================

# Items with multiplicative AP bonus (value SCALES with current AP)
_MULTIPLICATIVE_AP_ITEMS: dict[str, float] = {
    "Coiffe de Rabadon":     0.35,   # +35% AP total
}

# Items with Spellblade (bonus damage after spell, scales with base AD or AP)
_SPELLBLADE_ITEMS: dict[str, tuple[float, float]] = {
    "Force de la trinité":   (2.00, 0.00),   # 200% base AD bonus magic damage
    "Liche":                 (0.75, 1.50),   # 75% AD + 150% AP bonus magic damage
    "Épée de l'essence":     (1.00, 0.00),   # 100% base AD
    "Muramana":              (0.00, 0.00),   # special (mana-to-AD)
}

# Items with powerful on-hit/attack passives (Kraken, Statikk, BotRK)
_ON_HIT_PASSIVE_ITEMS: set[str] = {
    "Tueur de krakens",
    "Poignard de Statikk",
    "Lame du roi déchu",
    "Lame enragée de Guinsoo",
    "Terminus",
    "Au bout du rouleau",
    "Ouragan de Runaan",
}

# Items with huge bruiser passives (Shields, Cleave, auto-crits)
_BRUISER_PASSIVE_ITEMS: set[str] = {
    "Couperet noir", "Force de la trinité", "Hydre titanesque", "Gage de Sterak",
    "Estropieur", "Lance de Shojin", "Ciel éventré", "Danse de la mort"
}

_TANK_PASSIVE_ITEMS: set[str] = {
    "Cotte épineuse", "Plaque du mort", "Force de la nature", "Visage spirituel",
    "Rookern kaénique", "Égide solaire", "Rayonnement du vide", "Désespoir infini",
    "Cœur gelé", "Gantelet givrant", "Présage de Randuin", "Armure de Warmog",
    "Jak'Sho, le Protéiforme"
}

# Items with massive mage passives (Burn, execute, proc)
_MAGE_PASSIVE_ITEMS: set[str] = {
    "Tourment de Liandry",
    "Affliction de Liandry",
    "Flamme-ombre",
    "Compagnon de Luden",
    "Écho de Luden",
}

# Items with armor shred stack
_ARMOR_SHRED_ITEMS: dict[str, tuple[float, int]] = {
    "Couperet noir":    (0.06, 6),   # 6% par stack, 6 stacks max = 36% total
}

# ============================================================
# 2. BUILD PATH — components for recall advice
# Maps final item name → list of preferred components in priority order.
# First component in list = highest spike / buy first.
# ============================================================

_ITEM_BUILD_PATH_PRIORITY: dict[str, list[str]] = {
    # Assassin létalité
    "Lame funeste":            ["Pointe dentelée", "Épée longue"],
    "Lame spectre de Youmuu":  ["Pointe dentelée", "Épée longue"],
    "Griffes du rôdeur":       ["Pointe dentelée", "Épée longue"],
    "Écho-lames":              ["Pointe dentelée", "Épée longue"],
    "Hydre profane":           ["Tiamat", "Pointe dentelée"],
    "Crochet de serpent":      ["Épée longue", "Piochon"],
    "Lame d'avarice":          ["Pointe dentelée", "Piochon"],

    # AP mage
    "Écho de Luden":           ["Chapitre perdu", "Tome d'amplification"],
    "Flamme-ombre":            ["Amplificateur Hextech", "Tome d'amplification"],
    "Flamme sauvage":          ["Amplificateur Hextech", "Tome d'amplification"],
    "Chapeau de Rabadon":      ["Baguette géante", "Baguette de foudre"],
    "Bâton du vide":           ["Baguette de foudre", "Tome d'amplification"],
    "Fleur de crypte":         ["Baguette de foudre", "Tome d'amplification"],
    "Sablier de Zhonya":       ["Brassard de l'écuyer", "Baguette de foudre"],
    "Voile de la Banshee":     ["Égide spectrale", "Baguette de foudre"],
    "Morellonomicon":          ["Orbe de l'oubli", "Tome d'amplification"],
    "Pique dimensionnelle de Zaz'Zak": ["Baguette de foudre", "Tome d'amplification"],
    "Lame des mirages":        ["Orbe de l'oubli", "Tome d'amplification"],

    # ADC
    "Tueur de krakens":        ["Piochon", "Dague", "Épée longue"],
    "Poignard de Statikk":     ["Piochon", "Dague", "Épée longue"],
    "Lame du roi déchu":       ["Sceptre vampirique", "Piochon", "Dague"],
    "Terminus":                ["Arc courbe", "Dague"],
    "Lame enragée de Guinsoo": ["Livre d'amplification", "Dague"],
    "Pourfendeur divin":       ["Piochon", "Dague"],
    "Lame d'infini":           ["Grande épée", "Cape d'agilité"],
    "Salutations de Dominik":  ["Dernier souffle", "Piochon"],
    "Rappel mortel":           ["Dernier souffle", "Piochon"],
    "Danseur fantôme":         ["Dague", "Cape d'agilité"],
    "Ouragan de Runaan":       ["Dague", "Dague"],
    "Hydre vorace":            ["Tiamat", "Grande épée"],

    # Fighter / Bruiser
    "Force de la trinité":     ["Pierre de sort", "Fouchard"],
    "Couperet noir":            ["Fouchard", "Piochon"],
    "Danse de la mort":        ["Œil de vigilance", "Piochon"],
    "Gage de Sterak":          ["Sceptre vampirique", "Ceinture du géant"],
    "Gueule de Malmortius":    ["Buveur de sorts Hextech", "Épée longue"],
    "Ange gardien":            ["Armure de tissu", "Grande épée"],
    "Rancune de Serylda":      ["Marteau de guerre de Caulfield", "Dernier souffle"],

    # Tank
    "Égide de feu solaire":    ["Cendres de Bami", "Armure de tissu"],
    "Armure de Warmog":        ["Ceinture du géant", "Cristal de rubis"],
    "Plaques de l'épineux":    ["Gilet d'épines", "Cristal de rubis"],
    "Présage de Randuin":      ["Courrier du gardien", "Cristal de rubis"],
    "Force de la nature":      ["Mantelet anti-magie", "Ceinture du géant"],
    "Masque abyssal":          ["Égide spectrale", "Cristal de rubis"],
}

# ============================================================
# 3. COUNTER-ITEMS SITUATIONNELS
# ============================================================

# Champions with strong personal shields (trigger anti-shield recommendation)
# Poids des boucliers adverses (même logique que les soins) : un bouclier
# passif marginal ne doit pas peser autant qu'une Lulu dédiée au sujet.
# Auparavant un simple comptage à seuil 2 : une seule enchanteresse, si
# spécialisée soit-elle, ne déclenchait jamais le Crochet de serpent.
_SHIELD_CHAMPION_WEIGHTS: dict[str, float] = {
    # Bouclier = raison d'être du champion
    "Lulu":         1.0,   "Karma":        0.9,   "Janna":        0.9,
    "Yuumi":        0.85,  "Seraphine":    0.8,   "Orianna":      0.75,
    "Shen":         0.75,  "Renata Glasc": 0.7,   "Rakan":        0.7,
    "Taric":        0.6,   "Milio":        0.55,  "Sona":         0.55,
    "Ivern":        0.7,
    # Bouclier significatif mais secondaire
    "Lux":          0.55,  "Morgana":      0.55,  "Braum":        0.6,
    "Tahm Kench":   0.6,   "Bard":         0.5,   "Poppy":        0.5,
    "Sivir":        0.5,   "Ekko":         0.55,  "Camille":      0.5,
    "Aatrox":       0.45,  "Riven":        0.55,  "Sett":         0.5,
    "Sion":         0.5,   "Diana":        0.5,   "Annie":        0.4,
    "Blitzcrank":   0.4,   "Malphite":     0.35,  "Kai'Sa":       0.4,
    "Volibear":     0.4,
}
_SHIELD_CHAMPIONS: set[str] = set(_SHIELD_CHAMPION_WEIGHTS.keys())

# Champions whose key CC can be removed by QSS
_QSS_CC_CHAMPIONS: set[str] = {
    "Lissandra",     # R suppress
    "Mordekaiser",   # R realm
    "Warwick",       # R suppress
    "Malzahar",      # R suppress
    "Urgot",         # R suppress
    "Skarner",       # R suppress
    "Blitzcrank",    # Q+R grab
    "Veigar",        # E cage
    "Ashe",          # R stun
    "Ahri",          # R charm chained
    "Leona",         # E+R hard CC chain
    "Nautilus",      # Q+R hook chain
    "Thresh",        # Q hook
}

# Anti-shield items par type de dégâts


# Les valeurs conditionnelles vivent désormais dans data/item_conditions.json,
# indexées par identifiant d'objet et chargées dans StatAnalyzer._conditions.

# ============================================================
# 4.5. DYNAMIQUE DES DÉCLENCHEURS BINAIRES (COHEN'S D)
# ============================================================

_TRIGGER_EFFECT = {
    "need_armor_pen":  0.32,     # les trois mesurés sont statistiquement
    "need_magic_pen":  0.32,     # indistinguables → même poids
    "need_grievous":   0.32,
    "need_qss":        None,
    "need_tenacity":   None,
    "need_antishield": None,
    "need_anticrit": None,
    "need_antiauto": None,
    "can_adapt_defense": None,
    "need_armor": None,
    "need_mr": None,
}

_TRIGGER_REF = 0.32
_TRIGGER_MAX_BONUS = 0.35

_LEGACY_BASE_SCORE = {
    "need_grievous": 1.0,
    "need_armor_pen": 0.95,
    "need_magic_pen": 0.95,
    "need_antishield": 0.90,
    "need_qss": 0.90,
    "need_tenacity": 0.85,
    "can_adapt_defense": 0.80,
    "need_armor": 0.85,
    "need_mr": 0.85,
}

import os

def get_trigger_bonus(trigger_name: str) -> float:
    d = _TRIGGER_EFFECT.get(trigger_name)
    if os.getenv("LEGACY_WEIGHTS") == "1" or d is None:
        return _LEGACY_BASE_SCORE.get(trigger_name, 0.0) * _TRIGGER_MAX_BONUS
    return _TRIGGER_MAX_BONUS * (d / _TRIGGER_REF)


# ============================================================
# 5. RUNE → ITEM SYNERGIES
# Maps keystone/path to preferred stat categories and specific items.
# ============================================================

# Keystone → primary stat push
_KEYSTONE_STAT_PRIORITY: dict[str, dict] = {
    # --- Domination ---
    "Électrocution":    {"class_match": ["Assassin", "Mage"], "push": "burst",
                         "boost_items": {"Lame funeste", "Flamme-ombre", "Écho de Luden"},
                         "avoid": "sustain"},
    "Moisson noire":    {"class_match": ["Assassin", "Mage"], "push": "burst",
                         "boost_items": {"Lame d'avarice", "Flamme-ombre"},
                         "note": "exécution synergy"},
    "Prédateur":        {"class_match": ["Assassin", "Fighter"], "push": "lethality",
                         "boost_items": {"Lame spectre de Youmuu", "Écho-lames"},
                         "note": "mobilité — boots + gap close items"},

    # --- Précision ---
    "Conquérant":       {"class_match": ["Fighter", "Marksman"], "push": "sustained",
                         "boost_items": {"Force de la trinité", "Couperet noir", "Hydre vorace"},
                         "note": "stack conqueror → healing items max valeur"},
    "Tempo léthal":     {"class_match": ["Marksman", "Fighter"], "push": "as_crit",
                         "boost_items": {"Danseur fantôme", "Ouragan de Runaan", "Pourfendeur divin"},
                         "note": "AS items maximisent les stacks"},
    "Attaque soutenue": {"class_match": ["Marksman", "Fighter"], "push": "on_hit",
                         "boost_items": {"Pourfendeur divin", "Lame du roi déchu"},
                         "note": "on-hit + AS synergy"},
    "Pas vif":          {"class_match": ["Marksman"], "push": "sustain_adc",
                         "boost_items": {"Lame du roi déchu", "Pourfendeur divin"}, "avoid": ""},
    "Poigne de l'immortel": {"class_match": ["Tank", "Fighter"], "push": "hp",
                              "boost_items": {"Égide de feu solaire"},
                              "note": "HP items maximisent les procs Grasp"},

    # --- Sorcellerie ---
    "Comète arcanique": {"class_match": ["Mage", "Support"], "push": "ap_haste",
                         "boost_items": {"Écho de Luden", "Flamme-ombre"}, "avoid": ""},
    "Ruée de phase":    {"class_match": ["Mage", "Assassin"], "push": "ap_mobility",
                         "boost_items": {"Voie immortelle", "Écho de Luden"}, "avoid": ""},

    # --- Inspiration ---
    "Premier coup":     {"class_match": [], "push": "gold_efficiency",
                         "boost_items": {},
                         "note": "maximiser l'or gagné → items chers en priorité"},
    "Augment glacial":  {"class_match": ["Support", "Tank"], "push": "utility",
                         "boost_items": {}, "note": "slow-oriented items"},

    # --- Volonté (Resolve) ---
    "Contre-choc":      {"class_match": ["Tank", "Support"], "push": "armor_hp", "boost_items": {}},
    "Secousse":         {"class_match": ["Tank", "Support", "Fighter"], "push": "armor_hp",
                         "boost_items": {"Égide de feu solaire", "Armure de Warmog"}},
}

# Rune path → secondary stat push (if no keystone match)
_PATH_STAT_PUSH: dict[str, str] = {
    "Domination":   "burst",
    "Precision":    "sustained",
    "Sorcery":      "ap",
    "Resolve":      "tank",
    "Inspiration":  "utility",
}



@dataclass
class StatReport:
    """Complete analysis result passed to the LLM prompt."""

    # Player
    player_class: str    = "Fighter"
    player_champion: str = ""

    # Raw stats
    armor: float         = 0.0
    magic_resist: float  = 0.0
    max_hp: float        = 0.0
    current_hp: float    = 0.0
    attack_damage: float = 0.0
    ability_power: float = 0.0
    ability_haste: float = 0.0
    crit_chance: float   = 0.0
    attack_speed: float  = 0.0

    # After pen
    effective_armor: float = 0.0
    effective_mr: float    = 0.0

    # EHP
    ehp_vs_ad: float = 0.0
    ehp_vs_ap: float = 0.0

    # Enemy team
    enemy_ad_pct: float   = 0.5
    enemy_ap_pct: float   = 0.5
    enemy_tank_count: int = 0
    enemy_cc_count: int   = 0

    # Lane opponent
    lane_opp_name: str  = "inconnu"
    lane_opp_class: str = "Fighter"
    lane_opp_level: int = 1

    # Gold comparison (items only — most accurate measure of lead/behind)
    my_item_gold: int        = 0
    opp_item_gold: int       = 0
    lane_gold_diff: int      = 0    # positive = I'm ahead of my lane opp
    my_team_gold: int        = 0
    enemy_team_gold: int     = 0
    team_gold_diff: int      = 0    # positive = my team is ahead overall

    # Win condition (Axis 2)
    win_ratio: float           = 0.5
    win_ratio_type: str        = "burst_ratio"
    win_ratio_label: str       = ""
    win_ratio_explanation: str = ""

    # Situational triggers (Axis 3)
    need_grievous: bool     = False
    gw_source: str          = ""
    need_armor_pen: bool    = False
    need_magic_pen: bool    = False
    need_tenacity: bool     = False
    can_adapt_defense: bool = False
    enemy_fed_name: str     = ""

    # Results
    stat_priority: str       = "DAMAGE"
    priority_explanation: str = ""
    top_gains_summary: str   = ""   # top marginal gains per 1000g, for LLM display
    component_advice: str    = ""   # advice on what component to buy right now
    ranked_items: list[tuple[str, float, float, str]] = field(default_factory=list)

    def format_for_prompt(self) -> str:
        ar = 100 * self.effective_armor / (100 + self.effective_armor) if self.effective_armor else 0
        mr = 100 * self.effective_mr    / (100 + self.effective_mr)    if self.effective_mr    else 0

        ratio_label = {
            "burst_ratio":    f"Burst ratio (one-shot squish) : {self.win_ratio:.2f}",
            "ap_burst_ratio": f"AP burst ratio (one-shot squish) : {self.win_ratio:.2f}",
            "dps_ratio":      f"DPS ratio (tank en 4s) : {self.win_ratio:.2f}",
            "balance_ratio":  f"Balance offensif/défensif : {self.win_ratio:.2f}",
            "frontline_score":f"Score frontline EHP : {self.win_ratio:.0f}",
        }.get(self.win_ratio_type, f"Win ratio : {self.win_ratio:.2f}")

        # Gold status labels
        if self.lane_gold_diff > 1500:
            lane_status = f"+{self.lane_gold_diff}g (AVANCE LANE)"
        elif self.lane_gold_diff > 300:
            lane_status = f"+{self.lane_gold_diff}g (légèrement devant)"
        elif self.lane_gold_diff < -1500:
            lane_status = f"{self.lane_gold_diff}g (RETARD LANE)"
        elif self.lane_gold_diff < -300:
            lane_status = f"{self.lane_gold_diff}g (légèrement derrière)"
        else:
            lane_status = f"{self.lane_gold_diff:+}g (équilibré)"

        if self.team_gold_diff > 3000:
            team_status = f"+{self.team_gold_diff}g (AVANCE ÉQUIPE)"
        elif self.team_gold_diff < -3000:
            team_status = f"{self.team_gold_diff}g (RETARD ÉQUIPE)"
        else:
            team_status = f"{self.team_gold_diff:+}g (serré)"

        lines = [
            "=== ANALYSE (PRÉ-CALCULÉE) ===",
            f"Champion : {self.player_champion} [{self.player_class}]",
            f"Stats : PV {self.current_hp:.0f}/{self.max_hp:.0f} | AD {self.attack_damage:.0f} | AP {self.ability_power:.0f} | AS {self.attack_speed:.2f}",
            f"Armure eff. : {self.effective_armor:.0f} ({ar:.1f}% réduction) | MR eff. : {self.effective_mr:.0f} ({mr:.1f}% réduction)",
            f"Équipe ennemie : {self.enemy_ad_pct*100:.0f}% phys / {self.enemy_ap_pct*100:.0f}% mag | Tanks : {self.enemy_tank_count} | CC : {self.enemy_cc_count}",
            "",
            f"OR (valeur items) :",
            f"  Lane : moi {self.my_item_gold}g vs {self.lane_opp_name} {self.opp_item_gold}g → {lane_status}",
            f"  Équipe : mon équipe {self.my_team_gold}g vs ennemis {self.enemy_team_gold}g → {team_status}",
            "",
            f"WIN CONDITION — {ratio_label}",
            f"→ {self.win_ratio_explanation}",
        ]

        triggers = []
        if self.need_grievous:
            triggers.append(f"Anti-soin requis ({self.gw_source})")
        if self.need_armor_pen:
            triggers.append(f"Pénétration armor requise ({self.enemy_tank_count} tanks/bruisers)")
        if self.need_magic_pen:
            triggers.append("Pénétration magique requise (ennemis ont haute MR)")
        if self.need_tenacity:
            triggers.append(f"Ténacité requise ({self.enemy_cc_count} sources CC)")
        if self.can_adapt_defense:
            triggers.append("AVANCE OR — 1 item défensif autorisé pour protéger la lead")
        if self.enemy_fed_name:
            triggers.append(f"{self.enemy_fed_name} est fed — item counter prioritaire")

        if triggers:
            lines += ["", "DÉCLENCHEURS ACTIFS :"] + [f"  • {t}" for t in triggers]

        lines += [
            "",
            f"PRIORITÉ : {self.stat_priority}",
            f"Raison : {self.priority_explanation}",
            f"Gain marginal /1000g : {self.top_gains_summary}",
        ]
        if self.component_advice:
            lines.append(f"Build path conseil : {self.component_advice}")

        if self.ranked_items:
            lines += ["", "Items classés par rentabilité :"]
            for i, (name, score, ge, reason) in enumerate(self.ranked_items[:5], 1):
                lines.append(f"  {i}. {name} (score={score:.2f}, eff-or={ge:.0f}%) — {reason}")

        lines.append("===========================")
        return "\n".join(lines)


# ============================================================
# StatAnalyzer
# ============================================================

class StatAnalyzer:
    """Singleton universal item analysis engine."""

    _instance: Optional["StatAnalyzer"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "StatAnalyzer":
        # Verrouillé : plusieurs threads worker peuvent instancier en parallèle.
        with cls._instance_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._loaded = False
                obj.affinity = None
                cls._instance = obj
            return cls._instance

    # ----------------------------------------------------------
    # Data loading
    # ----------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._champ_tags: dict[str, list[str]] = {}
        self._item_db:    dict[str, dict]      = {}
        # All item names known by DDragon (French localization)
        self._ddragon_items: set[str]          = set()
        self._item_id_to_name: dict[int, str]  = {}
        self._UNIQUE_GROUPS: dict[str, set[str]] = {}
        
        self.affinity = ChampionAffinity("data/champion_item_profiles.json", champion_tables_dir="data")

        self._champ_stats: dict[str, dict] = {}
        try:
            with open("assets/champion_data.json", encoding="utf-8") as f:
                for champ_id, v in json.load(f).get("data", {}).items():
                    n = v.get("name", "")
                    tags = v.get("tags", [])
                    st = v.get("stats", {}) or {}
                    if n:
                        self._champ_tags[n] = tags
                        self._champ_stats[n] = st
                    if champ_id:
                        self._champ_tags[champ_id] = tags
                        self._champ_stats[champ_id] = st
        except Exception as e:
            logger.warning("StatAnalyzer: champion_data load error: %s", e)

        try:
            with open("assets/item_data.json", encoding="utf-8") as f:
                raw_items = json.load(f).get("data", {})

            # 216 noms sont partagés par plusieurs IDs (variantes Arena 223xxx,
            # Swarm 773xxx…). Indexer par nom sans arbitrer laissait la dernière
            # variante lue écraser la version Faille de l'invocateur — pour 94
            # objets, dont Thornmail, dont les stats, tags et description
            # devenaient ceux d'un autre mode de jeu.
            def _rang(entry):
                item_id_str, v = entry
                try:
                    numeric = int(item_id_str)
                except ValueError:
                    numeric = 10 ** 9
                sr = bool(v.get("maps", {}).get("11", False))
                return (0 if sr else 1, numeric)   # Faille d'abord, puis ID de base

            for item_id_str, v in sorted(raw_items.items(), key=_rang, reverse=True):
                n = v.get("name", "").strip()
                if n:
                    self._item_db[n] = v          # le meilleur candidat écrit en dernier
                    self._ddragon_items.add(n)
                    try:
                        self._item_id_to_name[int(item_id_str)] = n
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning("StatAnalyzer: item_data load error: %s", e)

        # ---- Effets conditionnels (base indexée par identifiant) ----
        from data import item_conditions as _cond
        self._conditions = _cond.charger(self._item_id_to_name)
        self._cond_module = _cond
        # Ensembles « ai-je déjà cet effet ? », résolus depuis la même base.
        self._qss_items = _cond.noms_par_declencheur("need_qss", self._item_id_to_name)
        self._antishield_items = _cond.noms_par_declencheur(
            "need_antishield", self._item_id_to_name)

        # ---- Anti-soin : listes déduites des descriptions DDragon ----
        self._gw_ad, self._gw_ap, self._gw_tank = set(), set(), set()
        for name, v in self._item_db.items():
            desc = (v.get("description", "") or "").lower()
            if _GW_KEYWORD not in desc:
                continue
            tags = v.get("tags", [])
            if "Armor" in tags or "Health" in tags:
                self._gw_tank.add(name)
            if "SpellDamage" in tags:
                self._gw_ap.add(name)
            if "Damage" in tags or "CriticalStrike" in tags:
                self._gw_ad.add(name)
        if not (self._gw_ad or self._gw_ap or self._gw_tank):
            self._gw_ad, self._gw_ap, self._gw_tank = (
                set(_GW_AD_ITEMS), set(_GW_AP_ITEMS), set(_GW_TANK_ITEM))
        logger.debug("Anti-soin — AD:%s AP:%s Tank:%s",
                     sorted(self._gw_ad), sorted(self._gw_ap), sorted(self._gw_tank))

        try:
            with open("data/situational_frequencies.json", encoding="utf-8") as f:
                self._situational_freqs = json.load(f)
        except Exception as e:
            logger.warning("StatAnalyzer: situational_frequencies load error: %s", e)
            self._situational_freqs = {}

        self._loaded = True
        
        # Build _UNIQUE_GROUPS for mechanical redundancy filtering
        for iid, data in self._item_db.items():
            desc = data.get("description", "")
            for tag in ("Hydre", "Brillance", "Éclipse", "Immolation", "Contact glacé"):
                if tag in desc:
                    self._UNIQUE_GROUPS.setdefault(tag, set()).add(data.get("name", iid))
                    
        logger.info("StatAnalyzer: %d champions, %d items, %d freq profiles, %d unique groups", 
                    len(self._champ_tags), len(self._item_db), len(self._situational_freqs), len(self._UNIQUE_GROUPS))

        # P0 — Validate constant pools against DDragon (silent failures become warnings)
        self._validate_item_pool()

    def _validate_item_pool(self) -> None:
        """Warn about any hardcoded item names that don't exist in the loaded DDragon data."""
        pools = [
            ("_LETHALITY_ITEMS",     set(_LETHALITY_ITEMS.keys())),
            ("_MAGIC_PEN_ITEMS",     set(_MAGIC_PEN_ITEMS.keys())),
            ("_ARMOR_PEN_PCT_ITEMS", _ARMOR_PEN_PCT_ITEMS),
            ("_MAGIC_PEN_PCT_ITEMS", _MAGIC_PEN_PCT_ITEMS),
            ("_HEALING_ITEMS",       _HEALING_ITEMS),
            ("_TENACITY_ITEMS",      _TENACITY_ITEMS),
        ]
        for pool_name, items in pools:
            unknown = items - self._ddragon_items
            if unknown:
                logger.warning(
                    "StatAnalyzer._validate_item_pool: %s contient des items inconnus de DDragon: %s",
                    pool_name, sorted(unknown)
                )

    # ----------------------------------------------------------
    # Champion classification
    # ----------------------------------------------------------

    def _get_class(self, name: str) -> str:
        self._ensure_loaded()
        
        # Override specific champions that Riot tags as Assassin/Fighter/Marksman
        # but who deal primarily Magic Damage (AP) and build AP items.
        _AP_OVERRIDES = {
            "Akali", "Ekko", "Evelynn", "Fizz", "Kassadin", "Katarina", 
            "LeBlanc", "Diana", "Sylas", "Gwen", "Teemo", "Mordekaiser", 
            "Rumble", "Singed", "Gragas", "Elise", "Nidalee", "Kennen"
        }
        if name in _AP_OVERRIDES:
            return "Mage"
            
        tags = self._champ_tags.get(name, [])
        if tags:
            return tags[0]
        return "Fighter"

    def _get_support_subtype(self, name: str) -> str:
        """Returns the effective class to use for Support champions."""
        tags = self._champ_tags.get(name, [])
        if "Mage" in tags:
            return "Mage"
        if "Tank" in tags or "Fighter" in tags:
            return "Tank"
        return "Support"

    def _resolve_class(self, name: str) -> str:
        """Returns the effective gameplay class for item scoring."""
        cls = self._get_class(name)
        return self._get_support_subtype(name) if cls == "Support" else cls

    # ----------------------------------------------------------
    # Enemy team analysis
    # ----------------------------------------------------------

    def _enemy_damage_split(self, enemies) -> tuple[float, float]:
        ad = ap = 0.0
        for p in enemies:
            prof = self.affinity.profile(p.champion_name)
            mix = prof.get("damage_mix")
            if mix:
                ad += mix.get("ad", 0.5)
                ap += mix.get("ap", 0.5)
            else:
                a, b = _CLASS_DAMAGE_SPLIT.get(self._get_class(p.champion_name), (0.65, 0.35))
                ad += a; ap += b
        n = len(enemies) or 1
        return ad / n, ap / n

    def _count_tanks(self, enemies) -> int:
        return sum(1 for p in enemies if self._get_class(p.champion_name) in ("Tank", "Fighter"))

    def _count_cc(self, enemies) -> int:
        return sum(1 for p in enemies if p.champion_name in _CC_CHAMPIONS)

    # ----------------------------------------------------------
    # Resistances & EHP
    # ----------------------------------------------------------

    def _effective_armor(self, armor: float, items: list[str]) -> float:
        flat = pct = 0.0
        for it in items:
            f, p = _LETHALITY_ITEMS.get(it, (0, 0))
            flat = max(flat, f); pct = max(pct, p)
        return max(0.0, armor * (1 - pct) - flat)

    def _effective_mr(self, mr: float, items: list[str]) -> float:
        flat = pct = 0.0
        for it in items:
            f, p = _MAGIC_PEN_ITEMS.get(it, (0, 0))
            flat = max(flat, f); pct = max(pct, p)
        return max(0.0, mr * (1 - pct) - flat)

    @staticmethod
    def _ehp(hp: float, res: float) -> float:
        return hp * (1 + res / 100)

    # ----------------------------------------------------------
    # Enemy stats estimation (from level + items, since we can't read them directly)
    # ----------------------------------------------------------

    @staticmethod
    def _croissance(base: float, par_niveau: float, level: int) -> float:
        """Croissance de statistique de League : non linéaire avec le niveau."""
        n = max(0, level - 1)
        return base + par_niveau * n * (0.7025 + 0.0175 * n)

    def _estimate_enemy_stats(self, champion_name: str, level: int, items) -> tuple[float, float, float]:
        """
        Estime PV, armure et RM d'un adversaire.

        La Live Client API n'expose championStats QUE pour le joueur local : les
        résistances adverses ne peuvent qu'être reconstituées, à partir des
        statistiques de base Data Dragon (exactes, 173 champions) et des objets
        portés — que l'API donne, elle, sous forme d'IDENTIFIANTS.

        C'est le second point : la version précédente cherchait ces objets dans
        un dictionnaire indexé par nom. Les identifiants ne correspondaient à
        rien, et l'armure d'équipement adverse n'était donc jamais comptée —
        un Malphite plein d'armure ressortait à 88.
        """
        st = self._champ_stats.get(champion_name) or {}
        if st:
            hp = self._croissance(st.get("hp", 570), st.get("hpperlevel", 90), level)
            armor = self._croissance(st.get("armor", 28), st.get("armorperlevel", 4.2), level)
            mr = self._croissance(st.get("spellblock", 30), st.get("spellblockperlevel", 1.3), level)
        else:
            # Repli sur des moyennes de classe si le champion est inconnu.
            cls = self._get_class(champion_name)
            base = {
                "Tank": (650, 32, 32), "Fighter": (600, 30, 28),
                "Assassin": (510, 22, 30), "Mage": (510, 22, 30),
                "Marksman": (490, 22, 30),
            }.get(cls, (530, 24, 30))
            hp = base[0] + level * 90
            armor = base[1] + level * 3.5
            mr = base[2] + level * 1.25

        for item in items or []:
            nom = self._item_id_to_name.get(item) if isinstance(item, int) else item
            st_item = (self._item_db.get(nom) or {}).get("stats", {})
            hp += st_item.get("FlatHPPoolMod", 0)
            armor += st_item.get("FlatArmorMod", 0)
            mr += st_item.get("FlatSpellBlockMod", 0)

        return hp, armor, mr

    # ----------------------------------------------------------
    # Gold efficiency
    # ----------------------------------------------------------

    def _gold_efficiency(self, item_name: str) -> float:
        self._ensure_loaded()
        item = self._item_db.get(item_name, {})
        cost = item.get("gold", {}).get("total", 0)
        if cost <= 0:
            return 0.0
        stat_value = sum(
            item.get("stats", {}).get(stat, 0.0) * gv
            for stat, gv in _GOLD_PER_STAT.items()
        )
        return stat_value / cost

    def _primary_stat(self, item_name: str) -> str:
        item  = self._item_db.get(item_name, {})
        stats = item.get("stats", {})
        vals  = {
            "ad":    stats.get("FlatPhysicalDamageMod", 0) * 35,
            "ap":    stats.get("FlatMagicDamageMod",    0) * 20,
            "armor": stats.get("FlatArmorMod",          0) * 20,
            "mr":    stats.get("FlatSpellBlockMod",     0) * 20,
            "hp":    stats.get("FlatHPPoolMod",         0) * 2.67,
        }
        best = max(vals, key=vals.get)   # type: ignore
        return best if vals[best] > 0 else "mixed"

    def _item_stat_breakdown(self, item_name: str) -> dict[str, float]:
        """Raw stat amounts from DDragon. AS/crit expressed as fractions (0-1)."""
        item = {}
        for it in self._item_db.values():
            if it.get("name") == item_name:
                item = it
                break
        stats = item.get("stats", {})
        return {
            "ad":           stats.get("FlatPhysicalDamageMod",  0),
            "ap":           stats.get("FlatMagicDamageMod",     0),
            "armor":        stats.get("FlatArmorMod",           0),
            "mr":           stats.get("FlatSpellBlockMod",      0),
            "hp":           stats.get("FlatHPPoolMod",          0),
            "as_frac":      stats.get("PercentAttackSpeedMod",  0),  # 0-1
            "crit_frac":    stats.get("FlatCritChanceMod",      0),  # 0-1
            "lifesteal":    stats.get("PercentLifeStealMod",    0),  # 0-1
            "mana":         stats.get("FlatMPPoolMod",          0),
            "flat_ms":      stats.get("FlatMovementSpeedMod",   0),
            # Lethality and pen are not in DDragon stats — handled separately via constants
            "lethality":    sum(_LETHALITY_ITEMS.get(item_name, (0, 0))[:1]),
            "pct_armor_pen": _LETHALITY_ITEMS.get(item_name, (0, 0))[1] if len(_LETHALITY_ITEMS.get(item_name, (0, 0))) > 1 else 0,
            "flat_magic_pen": _MAGIC_PEN_ITEMS.get(item_name, (0, 0))[0] if _MAGIC_PEN_ITEMS.get(item_name) else 0,
            "pct_magic_pen":  _MAGIC_PEN_ITEMS.get(item_name, (0, 0))[1] if _MAGIC_PEN_ITEMS.get(item_name) else 0,
        }

    def _item_has_lethality(self, item_name: str) -> bool:
        return item_name in _LETHALITY_ITEMS

    def _item_has_pct_armor_pen(self, item_name: str) -> bool:
        return item_name in _ARMOR_PEN_PCT_ITEMS

    def _item_has_pct_magic_pen(self, item_name: str) -> bool:
        return item_name in _MAGIC_PEN_PCT_ITEMS

    def _to_affinity_keys(self, sb: dict[str, float], item_name: str) -> dict[str, float]:
        """Convert _item_stat_breakdown output to ChampionAffinity schema."""
        return {
            "ad": sb.get("ad", 0),
            "ap": sb.get("ap", 0),
            "armor": sb.get("armor", 0),
            "mr": sb.get("mr", 0),
            "health": sb.get("hp", 0),
            "attack_speed": sb.get("as_frac", 0),
            "crit": sb.get("crit_frac", 0),
            "lifesteal": sb.get("lifesteal", 0),
            "mana": sb.get("mana", 0),
            "movespeed": sb.get("flat_ms", 0),
            "lethality": sb.get("lethality", 0),
            "armor_pen_pct": sb.get("pct_armor_pen", 0),
            "magic_pen_flat": sb.get("flat_magic_pen", 0),
            "magic_pen_pct": sb.get("pct_magic_pen", 0),
            "on_hit": 1.0 if item_name in _ON_HIT_PASSIVE_ITEMS else 0.0,
        }

    # ----------------------------------------------------------
    # Marginal Gain Engine
    # Source: LoL Wiki itemisation theory — ΔOutput / ΔCost
    # ----------------------------------------------------------

    def _compute_dps(self, ad: float, as_: float, crit: float, armor_eff: float) -> float:
        """
        DPS = AD × AS × [1 + crit% × 0.75] × 100/(100 + armor_eff)
        crit_multiplier − 1 = 0.75 (175% crit damage base)
        """
        crit_factor = 1 + crit * 0.75
        dmg_factor  = 100 / (100 + max(0, armor_eff))
        return ad * as_ * crit_factor * dmg_factor

    def _pen_threshold_armor(self, lethality: float, pct_pen: float) -> float:
        """
        Lethality vs %pen break-even armor:
            A = L / p
        Below A → létalité gagne (cibles fragiles)
        Above A → %pen gagne (tanks)
        """
        return lethality / max(pct_pen, 0.001)

    def _compute_marginal_gains_per_1000g(
        self,
        ls: "LiveStats",
        player_class: str,
        opp_armor: float,     # raw armor of priority target (before our pen)
        opp_mr: float,        # raw MR of priority target
        my_items: list[str],
        prof: dict,
        my_gold_share: float = 0.2,
        team_frontline: float = 1.0,
        death_cost: float = 0.5,
    ) -> dict[str, float]:
        """
        For each relevant stat, computes the % output gain per 1000g invested.

        The gain metric follows the wiki framework:
        - For product factors (AD × AS × crit × pen): gain% = ΔFactor / Factor
        - For EHP (linear): gain% = ΔEHP / current_EHP
        - For binary thresholds (Zhonya, GW): value is qualitative, not included here

        Gains > 0.30 (30%) per 1000g are exceptional.
        Rule: always buy the stat with the highest gain%, unless a binary threshold is needed.
        """
        gains: dict[str, float] = {}

        # Set a reasonable floor to avoid infinite/huge gains when stats are near 0.
        # e.g., an ADC with 0 bonus AD still has ~50 base AD. A mage with 0 AP still has spell base damages equivalent to having ~100 AP.
        ad  = max(ls.attack_damage, 50.0)
        ap  = max(ls.ability_power, 100.0)
        as_ = max(ls.attack_speed, 0.6)
        crit = max(ls.crit_chance, 0.0)
        ah   = ls.ability_haste
        hp   = max(ls.max_health, 1.0)
        armor_self = ls.armor
        mr_self    = ls.magic_resist

        # My pen items
        my_flat_leth = sum(_LETHALITY_ITEMS.get(it, (0, 0))[0] for it in my_items)
        my_pct_pen   = max((_LETHALITY_ITEMS.get(it, (0, 0))[1] for it in my_items), default=0)
        my_flat_mpen = sum(_MAGIC_PEN_ITEMS.get(it, (0, 0))[0] for it in my_items)
        my_pct_mpen  = max((_MAGIC_PEN_ITEMS.get(it, (0, 0))[1] for it in my_items), default=0)

        # Effective armor after my pen (for target)
        opp_armor_eff = max(0, opp_armor * (1 - my_pct_pen) - my_flat_leth)
        opp_mr_eff    = max(0, opp_mr    * (1 - my_pct_mpen) - my_flat_mpen)

        # ---- Physical damage factors ----
        # AD gain: 1000g / 35g = 28.57 AD → Δ% = ΔAD/AD
        delta_ad = 1000 / _WIKI_GOLD["ad"]
        gains["AD"] = delta_ad / ad

        # AS gain (Marksman mainly, also useful on melee fighters)
        base_as = _BASE_AS_BY_CLASS.get(player_class, 0.625)
        bonus_as = max(as_ - base_as, 0.01)
        delta_as = (1000 / _WIKI_GOLD["as_pct"]) / 100   # fraction
        gains["AS"] = delta_as / (1 + bonus_as)

        # Crit gain: 1000/40 = 25% crit
        delta_crit = (1000 / _WIKI_GOLD["crit_pct"]) / 100
        gains["Crit"] = (delta_crit * 0.75) / (1 + crit * 0.75)

        # Lethality: gain = reduction in effective armor → Δ multiplier
        delta_leth = 1000 / _WIKI_GOLD["lethality"]
        new_eff_leth = max(0, opp_armor_eff - delta_leth)
        gains["Lethality"] = (
            (100 / (100 + new_eff_leth)) / max(100 / (100 + opp_armor_eff), 0.001) - 1
            if opp_armor_eff > 0 else 0
        )

        # % Armor pen: gain = Δ multiplier
        delta_pct = (1000 / _WIKI_GOLD["armor_pen_pct"]) / 100
        new_pct_pen = min(0.95, my_pct_pen + delta_pct)
        new_eff = max(0, opp_armor * (1 - new_pct_pen) - my_flat_leth)
        gains["PctArmorPen"] = (
            (100 / (100 + new_eff)) / max(100 / (100 + opp_armor_eff), 0.001) - 1
            if opp_armor_eff > 0 else 0
        )

        # Pen recommendation: threshold A = L / p
        thresh = self._pen_threshold_armor(my_flat_leth, my_pct_pen)
        gains["_pen_recommendation"] = (
            "lethality" if opp_armor < thresh else "pct_armor_pen"
        )

        # ---- AP damage factors ----
        # AP gain: 1000/20 = 50 AP → Δ% = ΔAP/AP
        delta_ap = 1000 / _WIKI_GOLD["ap"]
        gains["AP"] = delta_ap / ap

        # AH: 1000/50 = 20 AH → Δ% = ΔAH/(100+AH)
        delta_ah = 1000 / _WIKI_GOLD["ah"]
        gains["AH"] = delta_ah / (100 + ah)

        # Flat magic pen: 1000/46.7 = 21.4 flat → gain = Δ multiplier
        delta_mpen = 1000 / _WIKI_GOLD["magic_pen_flat"]
        new_eff_mr = max(0, opp_mr_eff - delta_mpen)
        gains["MagicPenFlat"] = (
            (100 / (100 + new_eff_mr)) / max(100 / (100 + opp_mr_eff), 0.001) - 1
            if opp_mr_eff > 0 else 0
        )

        # % magic pen: 1000/41.7 = 23.97% → gain = Δ multiplier
        delta_pct_m = (1000 / _WIKI_GOLD["armor_pen_pct"]) / 100  # same gold as armor pen
        new_pct_m   = min(0.95, my_pct_mpen + delta_pct_m)
        new_eff_mr2 = max(0, opp_mr * (1 - new_pct_m) - my_flat_mpen)
        gains["PctMagicPen"] = (
            (100 / (100 + new_eff_mr2)) / max(100 / (100 + opp_mr_eff), 0.001) - 1
            if opp_mr_eff > 0 else 0
        )

        # ---- Missing Stats Estimation ----
        gains["Lifesteal"] = (1000 / _WIKI_GOLD.get("lifesteal_pct", 53.6)) / 100
        gains["Omnivamp"] = (1000 / 60.0) / 100
        gains["HealShieldPower"] = (1000 / 55.0) / 100
        # Mana and MS gains are small fractions. For MS, 12000g = 1000 flat MS. Let's say +1000g is +83 MS, 
        # base MS is 340, so +83 / 340 = 24% gain. 
        # Mana: +1000g = 1000 mana, base mana = 1000 -> 100% gain = 1.0. 
        gains["Mana"] = (1000 / _WIKI_GOLD["mana"]) / max(ls.stats.get("mp", 500), 100) if getattr(ls, "stats", None) else (1000 / _WIKI_GOLD["mana"]) / 1000.0
        gains["Movespeed"] = (1000 / _WIKI_GOLD["flat_ms"]) / max(ls.stats.get("movespeed", 340), 100) if getattr(ls, "stats", None) else (1000 / _WIKI_GOLD["flat_ms"]) / 340.0
        gains["OnHit"] = 30.0 / ad if ad > 0 else 0.1

        # ---- EHP factors (all classes, weighted by incoming damage type) ----
        # EHP = HP × (1 + R/100) → no diminishing returns on resistances themselves,
        # but relative value of resistance decreases when you have little HP.
        # Optimal ratio: HP ≈ 7.5 × (100 + R)

        ehp_ad = max(self._ehp(hp, armor_self), 1)
        ehp_ap = max(self._ehp(hp, mr_self),    1)

        # HP per 1000g: 1000/2.67 = 374.5 HP
        delta_hp = 1000 / _WIKI_GOLD["hp"]
        gains["HP_for_AD_EHP"] = delta_hp * (1 + armor_self / 100) / ehp_ad
        gains["HP_for_AP_EHP"] = delta_hp * (1 + mr_self    / 100) / ehp_ap

        # Armor per 1000g: 1000/20 = 50 armor
        delta_res = 1000 / _WIKI_GOLD["armor"]
        gains["Armor_EHP"] = hp * delta_res / 100 / ehp_ad
        gains["MR_EHP"]    = hp * delta_res / 100 / ehp_ap

        # ---- Offense vs Defense arbitrage ----
        # Total contribution ∝ DPS × EHP  (for sustained fighters/ADC)
        # For assassins/burst: contribution = peak burst (not DPS × EHP)
        if player_class == "Marksman":
            dps = self._compute_dps(ad, as_, crit, opp_armor_eff)
            # Best offense gain%
            best_offense = max(gains.get("AD", 0), gains.get("AS", 0),
                               gains.get("Crit", 0), gains.get("PctArmorPen", 0))
            # Best defense gain% vs AD (primary threat for ADC)
            best_defense = max(gains.get("HP_for_AD_EHP", 0), gains.get("Armor_EHP", 0))
            gains["_arb_offense"]  = best_offense
            gains["_arb_defense"]  = best_defense
            gains["_arb_verdict"]  = "OFFENSE" if best_offense >= best_defense else "DEFENSE"

        elif player_class == "Fighter":
            best_offense = max(gains.get("AD", 0), gains.get("PctArmorPen", 0), 0)
            best_defense = max(gains.get("HP_for_AD_EHP", 0), gains.get("Armor_EHP", 0),
                               gains.get("MR_EHP", 0))
            gains["_arb_offense"] = best_offense
            gains["_arb_defense"] = best_defense
            gains["_arb_verdict"] = "OFFENSE" if best_offense >= best_defense else "DEFENSE"
            
        # ---- Context Modifiers (Regime B) ----
        if my_gold_share > 0.30:
            # Main carry: boost damage
            for stat in ("AD", "AP", "AS", "Crit", "PctArmorPen", "Lethality", "PctMagicPen", "MagicPenFlat"):
                if stat in gains: gains[stat] *= 1.15
        elif my_gold_share < 0.15:
            # Behind: boost utility and defense
            for stat in ("Armor_EHP", "MR_EHP", "HP_for_AD_EHP", "HP_for_AP_EHP", "AH", "Movespeed"):
                if stat in gains: gains[stat] *= 1.15

        # Team frontline deficit
        my_archetype = prof.get("archetype", "")
        if team_frontline < 0.8 and my_archetype in ("bruiser_splitpush", "battlemage", "tank_vanguard", "tank_warden", "juggernaut"):
            for stat in ("HP_for_AD_EHP", "HP_for_AP_EHP"):
                if stat in gains: gains[stat] *= 1.25
            for stat in ("Armor_EHP", "MR_EHP"):
                if stat in gains: gains[stat] *= 1.10

        # Death cost (value of EHP increases as respawn timers get longer)
        ehp_weight = (0.7 + 0.6 * death_cost)
        for stat in ("HP_for_AD_EHP", "HP_for_AP_EHP", "Armor_EHP", "MR_EHP"):
            if stat in gains: gains[stat] *= ehp_weight

        # Apply Affinity Profile
        _STAT_TO_AFFINITY = {
            "AD": "ad", "AP": "ap", "AS": "attack_speed", "Crit": "crit",
            "Lethality": "lethality", "PctArmorPen": "armor_pen_pct",
            "MagicPenFlat": "magic_pen_flat", "PctMagicPen": "magic_pen_pct",
            "Armor_EHP": "armor", "MR_EHP": "mr",
            "HP_for_AD_EHP": "health", "HP_for_AP_EHP": "health",
            "AH": "ability_haste", "Lifesteal": "lifesteal",
            "Omnivamp": "omnivamp", "HealShieldPower": "heal_shield_power",
            "Mana": "mana", "Movespeed": "movespeed", "OnHit": "on_hit",
        }
        
        unmapped = {k for k in gains if not k.startswith("_")} - set(_STAT_TO_AFFINITY)
        if unmapped:
            logger.warning("stats sans affinité (multiplicateur 1.0) : %s", sorted(unmapped))

        # Log raw gains for calibration check
        logger.info("gains bruts : %s", {k: round(v, 2) for k, v in sorted(gains.items()) if not k.startswith("_")})
            
        for k, v in list(gains.items()):
            if k in _STAT_TO_AFFINITY:
                aff_key = _STAT_TO_AFFINITY[k]
                gains[k] = v * self.affinity.stat_multiplier(prof, aff_key)
                
        logger.info("axes de gains calculés : %s", sorted([k for k in gains.keys() if not k.startswith("_")]))

        return gains

    def _format_top_gains(self, gains: dict[str, float], n: int = 3) -> str:
        """Format top N marginal gains for the LLM prompt."""
        sorted_g = sorted(
            ((k, v) for k, v in gains.items()
             if not k.startswith("_") and isinstance(v, (int, float)) and v > 0),
            key=lambda x: x[1], reverse=True
        )[:n]
        parts = [f"{k} +{v*100:.1f}%/1000g" for k, v in sorted_g]
        arb = gains.get("_arb_verdict", "")
        pen = gains.get("_pen_recommendation", "")
        extras = []
        if arb:
            extras.append(f"Arbitrage: {arb}")
        if pen:
            extras.append(f"Pen. recommandée: {pen.replace('_', ' ')}")
        return " | ".join(parts + extras) if parts else "—"



    # ----------------------------------------------------------
    # 1. Passifs multiplicatifs
    # ----------------------------------------------------------

    def _effective_ap_with_rabadon(self, current_ap: float, my_items: list[str]) -> float:
        """
        If Rabadon's Deathcap is in the build, total AP = current_ap × 1.35.
        This corrects the DDragon underestimation of Rabadon's true value.
        """
        for item in my_items:
            mult = _MULTIPLICATIVE_AP_ITEMS.get(item, 0)
            if mult > 0:
                return current_ap * (1 + mult)
        return current_ap

    def _rabadon_value_score(self, current_ap: float) -> float:
        """
        Relative value of Rabadon's Deathcap scales with already-owned AP.
        - Below 150 AP: poor purchase (low base to multiply)
        - 150-250 AP: good purchase
        - 250+ AP: excellent, highest priority
        Returns 0-1 score.
        """
        if current_ap < 100:
            return 0.20
        elif current_ap < 150:
            return 0.45
        elif current_ap < 200:
            return 0.70
        elif current_ap < 250:
            return 0.85
        else:
            return 1.00

    def _spellblade_score(self, item_name: str, base_ad: float, ap: float) -> float:
        """
        Score bonus damage from Spellblade passive (Trinity Force, Lich Bane, etc.).
        Returns the bonus damage per proc as a % of the item's flat AD/AP contribution.
        Higher = this item's passive adds significantly more than stats alone.
        """
        mult = _SPELLBLADE_ITEMS.get(item_name)
        if not mult:
            return 0.0
        ad_mult, ap_mult = mult
        # Base AD ≈ total AD / 2.5 (rough estimate, scaled by level)
        est_base_ad = base_ad * 0.4
        bonus_dmg = est_base_ad * ad_mult + ap * ap_mult
        stat_dmg = max(base_ad, ap, 1)
        return min(1.0, bonus_dmg / (stat_dmg * 3))

    def _black_cleaver_score(self, my_items: list[str], opp_armor: float, player_class: str) -> float:
        """
        Black Cleaver's true value depends on:
        - Already stacking physical items (so you actually auto enough to shred)
        - Enemy armor (>100 armor: high value; <60: lower value)
        - Class: Fighters/ADC who auto-attack a lot benefit most
        Returns 0-1 score.
        """
        if player_class not in ("Fighter", "Marksman"):
            return 0.3
        auto_items = sum(
            1 for it in my_items
            if self._primary_stat(it) == "ad" and it not in ("Coiffe de Rabadon",)
        )
        if opp_armor >= 120:
            return min(1.0, 0.6 + auto_items * 0.15)
        elif opp_armor >= 80:
            return min(1.0, 0.45 + auto_items * 0.1)
        else:
            return max(0.2, 0.35 - (80 - opp_armor) * 0.003)

    # ----------------------------------------------------------
    # 2. Build path — component recommendation at recall
    # ----------------------------------------------------------

    def _get_component_advice(
        self, item_name: str, current_gold: float
    ) -> str:
        """
        Given the next item to build and current gold, return the best component to buy.
        Priority: first component in _ITEM_BUILD_PATH_PRIORITY that we can afford.
        If we can afford the full item, say so.
        """
        total_cost = self._item_db.get(item_name, {}).get("gold", {}).get("total", 0)
        if current_gold >= total_cost > 0:
            return f"✅ Complète {item_name} maintenant ({total_cost}g)"

        components = _ITEM_BUILD_PATH_PRIORITY.get(item_name, [])
        if not components:
            return ""

        # Find the first component we can afford
        for comp in components:
            comp_cost = self._item_db.get(comp, {}).get("gold", {}).get("total", 0)
            if comp_cost == 0:
                # Component not in DB, estimate from common component prices
                comp_cost = {"Pointe dentelée": 1100, "Épée longue": 350, "Grande épée": 1300,
                             "Piochon": 875, "Tome d'amplification": 435, "Baguette de foudre": 850,
                             "Baguette géante": 1250, "Dague": 300, "Dernier souffle": 1300,
                             "Chapitre perdu": 1300, "Cape d'agilité": 600, "Tiamat": 1500,
                             "Phage": 1100, "Brillance": 1000, "Gemme exaltante": 800, "Buveur de sorts Hextech": 1300,
                             "Armure de tissu": 300, "Mantelet anti-magie": 450, "Cristal de rubis": 400,
                             "Ceinture du géant": 1000, "Alternateur Hextech": 1050,
                             "Brassard de l'écuyer": 1600, "Orbe de l'oubli": 800,
                             "Courrier du gardien": 1000, "Gilet d'épines": 1000, "Manteau de spectre": 1200,
                             "Cendres de Bami": 1000, "Sceptre vampirique": 900,
                             "Marteau de guerre de Caulfield": 1100, "Arc courbe": 700}.get(comp, 500)
            if current_gold >= comp_cost:
                return f"→ Achète {comp} ({comp_cost}g) pour construire {item_name}"

        # Can't afford any component
        cheapest = components[-1] if components else ""
        return f"→ Farm encore, vise {cheapest} pour {item_name}"

    # ----------------------------------------------------------
    # 3. Counter-items situationnels
    # ----------------------------------------------------------

    def _count_shields(self, enemies) -> int:
        """Nombre d'ennemis porteurs d'un bouclier notable (compat historique)."""
        return sum(1 for p in enemies if p.champion_name in _SHIELD_CHAMPIONS)

    @staticmethod
    def _shield_weight(enemies) -> tuple[float, float, list[str]]:
        """Poids cumulé des boucliers adverses, poids maximal, et sources."""
        noms = sorted({p.champion_name for p in enemies} & _SHIELD_CHAMPIONS)
        poids = [_SHIELD_CHAMPION_WEIGHTS.get(n, 0.0) for n in noms]
        # Arrondi à 2 décimales, comme les poids eux-mêmes : sans lui, une paire
        # pile au seuil peut basculer sur un arrondi binaire.
        return round(sum(poids), 2), max(poids, default=0.0), noms

    def _count_qss_cc(self, enemies) -> int:
        """Count enemies with QSS-removable hard CC."""
        return sum(1 for p in enemies if p.champion_name in _QSS_CC_CHAMPIONS)

    def _get_antishield_item(self, player_class: str) -> str:
        if player_class in ("Assassin", "Fighter", "Marksman"):
            return "Serpent's Fang"
        elif player_class == "Mage":
            return "Shadowflame"   # also deals execute damage
        return "Serpent's Fang"

    # ----------------------------------------------------------
    # 5. Rune synergy
    # ----------------------------------------------------------

    def _get_rune_item_bonus(
        self, item_name: str, keystone: str, primary_path: str
    ) -> float:
        """
        Returns a bonus score (0.0–0.30) if item synergizes with the player's keystone/runes.
        This ADDS to the existing score — it's a boost, not a replacement.
        """
        rune_data = _KEYSTONE_STAT_PRIORITY.get(keystone) or {}
        boost_items = rune_data.get("boost_items", set())
        if item_name in boost_items:
            return 0.25   # strong keystone synergy

        # Secondary path bonus (lighter)
        path_push = _PATH_STAT_PUSH.get(primary_path, "")
        prim = self._primary_stat(item_name)
        if path_push == "burst" and prim in ("ad",) and self._item_has_lethality(item_name):
            return 0.10
        if path_push == "ap" and prim == "ap":
            return 0.10
        if path_push == "sustained" and item_name in ("Force de la trinité", "Hydre vorace", "Couperet noir"):
            return 0.10

        return 0.0

    def _rune_summary(self, keystone: str, primary_path: str, secondary_path: str) -> str:
        """Short human-readable rune context for the LLM prompt."""
        rune_data = _KEYSTONE_STAT_PRIORITY.get(keystone) or {}
        note = rune_data.get("note", "")
        push = rune_data.get("push", _PATH_STAT_PUSH.get(primary_path, ""))
        if keystone:
            s = f"{keystone} ({primary_path}/{secondary_path}) → {push}"
            if note:
                s += f" — {note}"
            return s
        return f"{primary_path}/{secondary_path} → {push}"

    # ----------------------------------------------------------
    # Axis 2 — Win Condition Metric per class
    # ----------------------------------------------------------

    def _axis2_assassin(self, ls, my_items: list[str], enemies, level: int) -> tuple[float, str, str]:
        """Kill threshold: burst_ratio = my_burst / squish_target_EHP."""
        flat_leth = sum(_LETHALITY_ITEMS.get(it, (0, 0))[0] for it in my_items)
        pct_pen   = max((_LETHALITY_ITEMS.get(it, (0, 0))[1] for it in my_items), default=0)
        level_mult = 0.70 + level * 0.020
        raw_burst  = ls.attack_damage * level_mult * 3.5

        squish_enemies = [p for p in enemies if self._get_class(p.champion_name) in ("Mage", "Marksman", "Assassin", "Support")]
        if not squish_enemies:
            squish_enemies = enemies

        if squish_enemies:
            target = max(squish_enemies, key=lambda p: p.kills)  # most dangerous squish
            t_hp, t_armor, _ = self._estimate_enemy_stats(target.champion_name, target.level, target.items)
            eff_t_armor = max(0, t_armor * (1 - pct_pen) - flat_leth)
            target_ehp  = self._ehp(t_hp, eff_t_armor)
        else:
            target_ehp = 2000

        ratio = raw_burst / max(target_ehp, 1)
        if ratio < 0.80:
            exp = f"Burst {raw_burst:.0f} insuffisant pour one-shot (cible EHP {target_ehp:.0f}) → DÉGÂTS PRIORITAIRES."
        elif ratio >= 1.10:
            exp = f"One-shot possible (burst {raw_burst:.0f} > EHP {target_ehp:.0f}) → kill threshold atteint, adaptation défensive autorisée."
        else:
            exp = f"Burst {raw_burst:.0f} proche du seuil (cible EHP {target_ehp:.0f}) → continuer offensif."
        return ratio, "burst_ratio", exp

    def _axis2_mage(self, ls, my_items: list[str], enemies, level: int) -> tuple[float, str, str]:
        """AP burst ratio: my_ap_burst / squish_target_EHP(magic)."""
        flat_mpen = sum(_MAGIC_PEN_ITEMS.get(it, (0, 0))[0] for it in my_items)
        pct_mpen  = max((_MAGIC_PEN_ITEMS.get(it, (0, 0))[1] for it in my_items), default=0)
        # Level multiplier: at level 18 ~1.0, at level 1 ~0.50
        level_mult = 0.55 + level * 0.025
        # Use 3.5× AP as the average burst multiplier for most mages.
        # Rabadon passive already inflated into ls.ability_power by the game engine.
        raw_burst  = ls.ability_power * level_mult * 3.5

        squish_enemies = [p for p in enemies if self._get_class(p.champion_name) in ("Mage", "Marksman", "Assassin", "Support")]
        if not squish_enemies:
            squish_enemies = enemies

        if squish_enemies:
            target = max(squish_enemies, key=lambda p: p.kills)
            t_hp, _, t_mr = self._estimate_enemy_stats(target.champion_name, target.level, target.items)
            eff_t_mr   = max(0, t_mr * (1 - pct_mpen) - flat_mpen)
            target_ehp = self._ehp(t_hp, eff_t_mr)
        else:
            target_ehp = 2200

        ratio = raw_burst / max(target_ehp, 1)
        if ratio < 0.80:
            exp = f"AP burst {raw_burst:.0f} insuffisant (cible EHP-magic {target_ehp:.0f}) → PRIORITÉ AP pur."
        elif ratio >= 1.10:
            exp = f"Burst AP {raw_burst:.0f} > EHP {target_ehp:.0f} → threshold AP atteint, Zhonya/Banshee autorisés."
        else:
            exp = f"AP burst {raw_burst:.0f} proche du seuil {target_ehp:.0f} → continuer AP."
        return ratio, "ap_burst_ratio", exp

    def _axis2_marksman(self, ls, my_items: list[str], enemies, level: int) -> tuple[float, str, str]:
        """DPS ratio: (AD × AS × crit_factor × 4s) / tank_EHP."""
        flat_pen = sum(_LETHALITY_ITEMS.get(it, (0, 0))[0] for it in my_items)
        pct_pen  = max((_LETHALITY_ITEMS.get(it, (0, 0))[1] for it in my_items), default=0)
        crit_mult = 1 + ls.crit_chance * 0.75
        dps_4s    = ls.attack_damage * ls.attack_speed * crit_mult * 4.0

        # Target: tankiest enemy
        tank_enemies = [p for p in enemies if self._get_class(p.champion_name) in ("Tank", "Fighter")]
        if not tank_enemies:
            tank_enemies = enemies

        if tank_enemies:
            target = max(tank_enemies, key=lambda p: p.level)
            t_hp, t_armor, _ = self._estimate_enemy_stats(target.champion_name, target.level, target.items)
            eff_armor  = max(0, t_armor * (1 - pct_pen) - flat_pen)
            target_ehp = self._ehp(t_hp, eff_armor)
        else:
            target_ehp = 4000

        ratio = dps_4s / max(target_ehp, 1)
        if ratio < 0.60:
            exp = f"DPS {dps_4s:.0f} en 4s insuffisant pour détruire le tank (EHP {target_ehp:.0f}) → AD/crit/AS prioritaires."
        elif ratio < 1.0:
            exp = f"DPS {dps_4s:.0f} correct mais tanks solides — % armor pen recommandé."
        else:
            exp = f"DPS {dps_4s:.0f} suffisant vs tanks — lifesteal ou item survie possible."
        return ratio, "dps_ratio", exp

    def _axis2_fighter(self, ls, enemies, level: int, eff_armor: float, eff_mr: float) -> tuple[float, str, str]:
        """Balance ratio: offense_score / defense_score."""
        offense = ls.attack_damage * 2.5 + ls.ability_power * 1.2
        defense = (self._ehp(ls.max_health, eff_armor) + self._ehp(ls.max_health, eff_mr)) / 2

        # Normalise: expected offense at this level
        expected_off = 80 + level * 18
        expected_def = 2000 + level * 150
        balance = (offense / max(expected_off, 1)) / max(defense / max(expected_def, 1), 0.01)

        if balance < 0.80:
            exp = "Trop défensif par rapport aux dégâts — item bruiser offensif recommandé."
        elif balance > 1.20:
            exp = "Trop offensif, survivabilité insuffisante — item bruiser défensif recommandé."
        else:
            exp = "Balance offense/défense bonne — continuer le build bruiser selon la menace."
        return balance, "balance_ratio", exp

    def _axis2_tank(self, ls, eff_armor: float, eff_mr: float, ad_pct: float, ap_pct: float) -> tuple[float, str, str]:
        """Frontline score: weighted EHP."""
        score = self._ehp(ls.max_health, eff_armor) * ad_pct + self._ehp(ls.max_health, eff_mr) * ap_pct
        exp   = f"EHP physique {self._ehp(ls.max_health, eff_armor):.0f} × {ad_pct*100:.0f}% + EHP magique {self._ehp(ls.max_health, eff_mr):.0f} × {ap_pct*100:.0f}%."
        return score, "frontline_score", exp

    # ----------------------------------------------------------
    # Gold comparison (item gold value = most accurate lead metric)
    # ----------------------------------------------------------

    def _compute_item_gold(self, items: list[str]) -> int:
        """Sum the gold cost of all items a player owns. Reflects farm + kills + objectives."""
        total = 0
        for item_name in items:
            cost = self._item_db.get(item_name, {}).get("gold", {}).get("total", 0)
            total += cost
        return total

    def _team_item_gold(self, players) -> int:
        return sum(self._compute_item_gold(p.items) for p in players)

    def _gold_lead_label(self, diff: int) -> str:
        if diff > 2000:
            return "AVANCE FORTE"
        elif diff > 500:
            return "légère avance"
        elif diff < -2000:
            return "RETARD FORT"
        elif diff < -500:
            return "léger retard"
        return "équilibré"

    def _poids_soin_dynamiques(self, game_state, enemies) -> dict[str, float] | None:
        """
        Poids d'anti-soin mesurés, tenant compte du build et de l'état de partie.

        Renvoie None si le modèle n'est pas disponible : le moteur retombe alors
        sur la table de poids fixes.
        """
        try:
            from data import sustain
            if not sustain.disponible():
                return None
            from services.image_cache import ImageCache
            cache = ImageCache()

            # Or investi rapporté à la médiane de la partie — l'app calcule déjà
            # cet écart pour l'affichage « avantage or (objets) ».
            tous = list(getattr(game_state, "all_players", None) or enemies)
            ors = [
                sum(cache.get_item_gold_value(i) for i in (p.items or []))
                for p in tous
            ]
            ors_positifs = [o for o in ors if o > 0]
            mediane = (sorted(ors_positifs)[len(ors_positifs) // 2]
                       if ors_positifs else 0)

            resultat: dict[str, float] = {}
            for p in enemies:
                invest = sum(cache.get_item_gold_value(i) for i in (p.items or []))
                ratio = (invest / mediane) if mediane else 1.0
                kda = (p.kills + p.assists) / max(1, p.deaths)
                resultat[p.champion_name] = sustain.poids(
                    p.champion_name, p.items, kda=kda, ratio_or=ratio)
            return resultat
        except Exception:
            logger.debug("Modèle de soin dynamique indisponible.", exc_info=True)
            return None

    def _check_triggers(
        self,
        enemies,
        player_class: str,
        win_ratio: float,
        win_ratio_type: str,
        my_items: list[str],
        lane_gold_diff: float = 0.0,
        team_gold_diff: float = 0.0,
        ad_pct: float = 0.5,
        ap_pct: float = 0.5,
        player_deaths: int = 0,
        lane_opponent_name: str = "",
        my_champion_name: str = "",
        poids_soin: dict[str, float] | None = None,
    ) -> dict:
        enemy_names = {p.champion_name for p in enemies}
        tank_count  = self._count_tanks(enemies)
        cc_count    = self._count_cc(enemies)

        enemy_items = {item for p in enemies for item in p.items}

        # Seuils calibrés sur la distribution mesurée (15 180 équipes) :
        # le cumul médian d'une équipe vaut 1.19 et le 90e centile 2.01.
        # 2.00 vise donc le dixième d'équipes les plus soignantes ; l'ancien
        # 1.50 aurait déclenché dans 31.7 % des parties, soit un conseil
        # permanent donc sans valeur. Avec le spécialiste à 0.80 — le bruiser
        # nourri qui a pris un objet de soin — on couvre 24.3 % des parties.
        # Poids mesurés si le modèle dynamique les a fournis (ils tiennent compte
        # du build et de l'état de la partie), sinon repli sur la table estimée.
        if poids_soin:
            heal_champs = sorted(c for c, w in poids_soin.items() if w > 0)
            _poids = lambda c: poids_soin.get(c, 0.0)
        else:
            heal_champs = sorted(enemy_names & _HEALING_CHAMPIONS)
            _poids = lambda c: _HEALING_CHAMPION_WEIGHTS.get(c, 0.0)
        gw_weight   = sum(_poids(c) for c in heal_champs)
        heal_items  = sorted(enemy_items & _HEALING_ITEMS)        # deterministic
        # Each heal item contributes 0.6 weight
        gw_weight  += len(heal_items) * 0.6
        gw_sources  = heal_champs + heal_items

        # Le vis-à-vis de lane compte davantage : on l'affronte en boucle, son
        # soin décide de l'échange. Un soigneur support croisé en teamfight n'a
        # pas le même poids qu'un Fiddlesticks qui régénère à chaque trade.
        _LANE_OPP_BONUS = 0.5
        lane_heal = _poids(lane_opponent_name)
        if lane_heal:
            gw_weight += lane_heal * _LANE_OPP_BONUS

        max_single  = max((_poids(c) for c in heal_champs), default=0.0)
        SEUIL_CUMUL_SOIN, SEUIL_SOIGNEUR_SEUL = 2.00, 0.80
        need_gw     = gw_weight >= SEUIL_CUMUL_SOIN or max_single >= SEUIL_SOIGNEUR_SEUL
        gw_source_str = ", ".join(gw_sources) if gw_sources else ""
        # ---- Pénétration : on lit les RÉSISTANCES, pas les étiquettes ----
        # L'ancienne règle armure comptait les champions taggés Tank/Fighter et
        # ignorait leur armure réelle : un unique adversaire à 250 d'armure ne
        # déclenchait rien, alors que le pendant magique lisait déjà la
        # résistance estimée. Les deux voies sont désormais symétriques.
        resist = [
            self._estimate_enemy_stats(p.champion_name, p.level, p.items)
            for p in enemies
        ]
        armures = [r[1] for r in resist]
        rms = [r[2] for r in resist]
        armure_max = max(armures, default=0.0)
        rm_max = max(rms, default=0.0)

        # Profil de dégâts du joueur : un assassin AP ou un bruiser AP doit
        # pouvoir se voir conseiller de la pénétration magique, ce que le test
        # `player_class == "Mage"` interdisait.
        mon_ap, mon_ad = 0.0, 0.0
        if my_champion_name and self.affinity:
            mix = (self.affinity.profile(my_champion_name) or {}).get("damage_mix") or {}
            mon_ap, mon_ad = mix.get("ap", 0.0), mix.get("ad", 0.0)
        if not (mon_ap or mon_ad):
            mon_ad, mon_ap = _CLASS_DAMAGE_SPLIT.get(player_class, (0.5, 0.5))

        # Seuils distincts : armure et RM n'ont pas la même échelle. Au niveau 16
        # un champion nu tourne autour de 100 d'armure mais seulement 55 de RM ;
        # appliquer le même seuil aux deux rendait la pénétration magique
        # pratiquement indéclenchable.
        SEUIL_ARMURE, SEUIL_ARMURE_MAX = 150.0, 250.0
        SEUIL_RM, SEUIL_RM_MAX = 80.0, 120.0

        armure_haute = sum(1 for a in armures if a >= SEUIL_ARMURE)
        rm_haute = sum(1 for m in rms if m >= SEUIL_RM)

        need_pen = mon_ad >= 0.45 and (armure_haute >= 2 or armure_max >= SEUIL_ARMURE_MAX)
        need_mpen = mon_ap >= 0.45 and (rm_haute >= 2 or rm_max >= SEUIL_RM_MAX)

        need_ten = cc_count >= 3

        # Menace de coups critiques. Sans cette mesure, les objets anti-critique
        # étaient notés sur leurs seules stats brutes : Présage de Randuin
        # ressortait à 75 armure + 350 PV pour 2700 or, sans que le moteur voie
        # qu'une part du prix paie une passive inutile face à une équipe sans crit.
        # Auto-attaquants adverses : conditionne la valeur des effets qui ne
        # mordent que sur les attaques de base (aura du Cœur gelé, Coques en
        # acier). Une équipe full sorts les rend inertes.
        need_antiauto = False
        for p in enemies:
            prof = self.affinity.profile(p.champion_name) if self.affinity else {}
            flags = prof.get("flags") or {}
            if flags.get("auto_based") or flags.get("on_hit") or flags.get("crit_viable"):
                need_antiauto = True
                break

        need_anticrit = False
        for p in enemies:
            prof = self.affinity.profile(p.champion_name) if self.affinity else {}
            if (prof.get("flags") or {}).get("crit_viable", False):
                need_anticrit = True
                break
            for iid in getattr(p, "items", []):
                nm = self._item_id_to_name.get(iid)
                if nm and self._item_stat_breakdown(nm).get("crit_frac", 0) > 0:
                    need_anticrit = True
                    break
            if need_anticrit:
                break

        # ---- Gold-based ahead/behind detection ----
        # Item gold = the most honest measure of economic state.
        # 2 shutdown kills (2000g each) appear as +4000g → correctly shows you're fed.
        # 5 regular kills (300g each) = +1500g → correctly shows you're moderately ahead.
        # A CS lead also shows up here, making it more accurate than KDA.
        if player_class in ("Assassin", "Mage", "Marksman"):
            threat_is_lethal = any(p.is_fed for p in enemies)
            if lane_gold_diff > 1500:
                adapt_threshold = 1.0                        # avance : porte ouverte
                gold_state = "AVANCE (protéger la prime)"
            elif player_deaths >= 2 and threat_is_lethal:
                adapt_threshold = 0.5                        # danger réel : porte entrouverte
                gold_state = "DANGER LÉTAL (survivre)"
            else:
                adapt_threshold = 999.0                      # ni l'un ni l'autre : fermée
                gold_state = "FERMÉE"

            can_adapt = win_ratio >= adapt_threshold
        else:
            can_adapt       = False
            adapt_threshold = 999.0
            gold_state      = ""

        # Anti-shield and QSS checks
        shield_count = self._count_shields(enemies)
        shield_weight, shield_max, shield_sources = self._shield_weight(enemies)
        # Seuil propre aux boucliers : 1.3, contre 1.5 pour l'anti-soin. Les deux
        # échelles ne sont pas comparables — plusieurs champions atteignent 1.0
        # en soin (Aatrox, Vladimir, Soraka, Yuumi) alors que seule Lulu y arrive
        # en bouclier, donc les cumuls y sont mécaniquement plus bas.
        # 1.35 sépare deux porteurs qui protègent vraiment (Tahm Kench + Shen)
        # de paires où le bouclier reste accessoire (Lux + Orianna = 1.30,
        # Braum + Riven = 1.15, Malphite + Diana = 0.85).
        _SEUIL_BOUCLIER = 1.35
        need_antishield = shield_weight >= _SEUIL_BOUCLIER or shield_max >= 0.9
        qss_cc_sources = [p.champion_name for p in enemies if p.champion_name in _QSS_CC_CHAMPIONS]
        need_qss = len(qss_cc_sources) > 0

        fed_name = ""
        for p in enemies:
            if p.is_fed:
                fed_name = p.champion_name
                break

        # --- Disable triggers if already built ---
        # If the player already has the required component or full item, we drop the trigger
        # so we don't force them to finish the full item immediately.
        my_items_set = set(my_items)
        
        # Anti-soin déjà en inventaire → on relâche le déclencheur.
        # Cette liste était écrite à la main et contenait des noms disparus
        # ("Gilet d'épines", "Plaques de l'épineux", "Chaîne de Chempunk") :
        # acheter Thornmail ne désactivait donc pas le déclencheur.
        gw_all_items = self._gw_ad | self._gw_ap | self._gw_tank
        if my_items_set & gw_all_items:
            need_gw = False

        # QSS items
        if my_items_set & self._qss_items:
            need_qss = False
            
        # Anti-shield items
        if my_items_set & self._antishield_items:
            need_antishield = False

        # Armor / Magic Pen items
        if my_items_set & _ARMOR_PEN_PCT_ITEMS:
            need_pen = False
        if my_items_set & _MAGIC_PEN_PCT_ITEMS:
            need_mpen = False

        return {
            "need_grievous":     need_gw,
            "need_anticrit":     need_anticrit,
            "enemy_max_armor":   round(armure_max, 1),
            "enemy_max_mr":      round(rm_max, 1),
            "need_antiauto":     need_antiauto,
            "gw_source":         gw_source_str,
            "need_armor_pen":    need_pen,
            "need_magic_pen":    need_mpen,
            "need_tenacity":     need_ten,
            "can_adapt_defense": can_adapt,
            "need_armor":        (gold_state == "RETARD LANE" or can_adapt) and ad_pct >= 0.70,
            "need_mr":           (gold_state == "RETARD LANE" or can_adapt) and ap_pct >= 0.70,
            "enemy_fed_name":    fed_name,
            "tank_count":        tank_count,
            "cc_count":          cc_count,
            "gold_state":        gold_state,
            "adapt_threshold":   adapt_threshold,
            "need_antishield":   need_antishield,
            "shield_count":      shield_count,
            "shield_weight":     round(shield_weight, 2),
            "shield_source":     ", ".join(shield_sources),
            "need_qss":          need_qss,
            "qss_cc_source":     ", ".join(qss_cc_sources) if qss_cc_sources else "",
        }

    # ----------------------------------------------------------
    # Axis 2+3 combined — stat priority determination
    # ----------------------------------------------------------

    def _determine_priority(
        self,
        player_class: str,
        win_ratio: float,
        triggers: dict,
        ad_pct: float,
        ap_pct: float,
    ) -> tuple[str, str]:
        """Returns (stat_priority, explanation)."""
        gw  = triggers["need_grievous"]
        pen = triggers["need_armor_pen"]
        mpen= triggers["need_magic_pen"]
        ten = triggers["need_tenacity"]
        can = triggers["can_adapt_defense"]

        # ---- Damage classes ----
        if player_class == "Assassin":
            if win_ratio < 0.80:
                return "AD_LETHALITY", "Seuil de kill non atteint — dégâts AD/létalité en priorité absolue."
            if gw:
                return "GRIEVOUS_WOUNDS", "Anti-soin en priorité (ennemi avec healing fort)."
            if pen:
                return "ARMOR_PEN_PCT", f"Pénétration armor % requise ({triggers['tank_count']} tanks/bruisers)."
            if can:
                return "DEFENSIVE_ADAPT", "Seuil de kill atteint — 1 item défensif pour survivre après le combo."
            return "AD_LETHALITY", "Continuer la montée en puissance offensive."

        elif player_class == "Mage":
            if win_ratio < 0.80:
                return "AP", "AP burst insuffisant — items AP en priorité absolue."
            if gw:
                return "GRIEVOUS_AP", "Anti-soin AP requis (Shadowflame, Morellonomicon)."
            if mpen:
                return "MAGIC_PEN_PCT", "Void Staff / Cryptbloom — ennemis ont haute MR."
            if can:
                return "DEFENSIVE_AP", "AP threshold atteint — Zhonya's ou Banshee autorisés."
            return "AP", "Continuer la montée AP."

        elif player_class == "Marksman":
            if win_ratio < 0.60:
                return "AD_CRIT_AS", "DPS insuffisant — AD, crit et vitesse d'attaque prioritaires."
            if gw:
                return "GRIEVOUS_AD", "Mortal Reminder prioritaire (anti-soin ennemi)."
            if pen:
                return "ARMOR_PEN_PCT", f"Pénétration armor % ({triggers['tank_count']} tanks en face)."
            if win_ratio >= 1.0:
                return "DEFENSIVE_ADC", "DPS suffisant — Lifesteal ou Guardian Angel possible."
            return "AD_CRIT_AS", "Continuer la montée DPS."

        # ---- Fighter ----
        elif player_class == "Fighter":
            if gw:
                return "GRIEVOUS_WOUNDS", "Anti-soin en priorité (Chempunk, Mortal Reminder)."
            if win_ratio < 0.80:
                return "AD_BRUISER", "Trop défensif — item bruiser offensif (Trinity Force, BC)."
            if win_ratio > 1.20:
                return "BRUISER_DEFENSE", "Trop offensif — item défensif bruiser (Maw, Sterak's)."
            # Balance OK — follow threat
            if ad_pct >= ap_pct:
                return "BRUISER_ARMOR", "Balance bonne — bruiser avec armor (Sunfire, Heartsteel)."
            return "BRUISER_MR", "Balance bonne — bruiser avec MR (Sterak's, Maw)."

        # ---- Tank ----
        elif player_class == "Tank":
            if gw:
                return "THORNMAIL", "Thornmail prioritaire (Grievous Wounds tank)."
            if ad_pct >= 0.55:
                return "ARMOR", f"Tank vs {ad_pct*100:.0f}% physiques — armure prioritaire."
            return "MR", f"Tank vs {ap_pct*100:.0f}% magiques — MR prioritaire."

        # ---- Tank Support ----
        elif player_class == "Tank_Support":
            if gw:
                return "GRIEVOUS_SUPPORT", "Anti-soin tank prioritaire (Cotte épineuse)."
            if ad_pct >= 0.55:
                return "ARMOR_SUPPORT", "Items défensifs armor pour support tank (Vœu du chevalier)."
            return "MR_SUPPORT", "Items MR pour support tank (Masque abyssal, Locket)."

        else:
            if gw:
                return "GRIEVOUS_SUPPORT", "Anti-soin prioritaire (Putrificateur)."
            if ad_pct >= 0.6:
                return "ARMOR_SUPPORT", "Composition AD — Zhonya ou Vœu du chevalier."
            return "UTILITY_SUPPORT", "Items utility/AP pour support enchanter."

    # ----------------------------------------------------------
    # Universal item scoring
    # ----------------------------------------------------------

    def _score_item(
        self,
        item_name: str,
        player_class: str,
        win_ratio: float,
        stat_priority: str,
        triggers: dict,
        already_have: list[str],
        marginal_gains: dict | None = None,
        affinity_factor: float = 1.0,
        affinity_note: str = "",
        is_component: bool = False,
        current_gold: float = 0.0,
        situational_penalty: float = 1.0
    ) -> tuple[float, float, str]:
        """
        Returns (composite_score, gold_efficiency_pct, reason).

        Scoring formula (in order of weight):
          1. Binary threshold triggers (GW, pen, tenacity, adapt) — highest priority
          2. Marginal gain match — wiki math: which stat improves my output the most?
          3. Gold efficiency baseline
          4. Build synergy
        """
        gold_eff = self._gold_efficiency(item_name)
        prim     = self._primary_stat(item_name)
        reason   = ""

        # ---- Step 1: Binary threshold triggers (override all math) ----
        # Source: "Les seuils binaires battent les maths" — anti-heal, QSS, GA, Banshee
        trigger_score = 0.0

        gw_items_for_class = (
            self._gw_ad   if player_class in ("Assassin", "Fighter", "Marksman")
            else self._gw_ap   if player_class == "Mage"
            else self._gw_tank
        )
        # Thornmail gives GW but only makes sense on tanks/fighters — exclude pure damage classes
        allowed_gw = gw_items_for_class
        if triggers["need_grievous"] and item_name in allowed_gw:
            trigger_score = get_trigger_bonus("need_grievous")
            reason = "Seuil binaire — Grievous Wounds (anti-soin)"

        elif triggers["need_armor_pen"] and self._item_has_pct_armor_pen(item_name):
            trigger_score = get_trigger_bonus("need_armor_pen")
            reason = f"Seuil binaire — % pen armor ({triggers['tank_count']} tanks)"

        elif triggers["need_magic_pen"] and self._item_has_pct_magic_pen(item_name):
            trigger_score = get_trigger_bonus("need_magic_pen")
            reason = "Seuil binaire — % pen magie (ennemis MR élevée)"

        elif triggers["need_tenacity"] and item_name in _TENACITY_ITEMS:
            trigger_score = get_trigger_bonus("need_tenacity")
            reason = "Seuil binaire — ténacité anti-CC"

        elif triggers.get("need_antishield") and item_name in self._antishield_items:
            trigger_score = get_trigger_bonus("need_antishield")
            sc = triggers.get("shield_count", 0)
            reason = f"Seuil binaire — anti-bouclier ({sc} champions avec shield)"

        elif triggers.get("need_qss") and item_name in self._qss_items:
            trigger_score = get_trigger_bonus("need_qss")
            reason = f"Seuil binaire — QSS anti-CC ({triggers.get('qss_cc_source', '')})"

        elif triggers["can_adapt_defense"]:
            adapt_set = (
                _ADAPT_ASSASSIN  if player_class == "Assassin"
                else _ADAPT_MAGE      if player_class == "Mage"
                else _ADAPT_MARKSMAN  if player_class == "Marksman"
                else _ADAPT_FIGHTER   if player_class == "Fighter"
                else _ADAPT_TANK      if player_class == "Tank"
                else set()
            )
            if item_name in adapt_set:
                gold_label = triggers.get("gold_state", "avance détectée")
                trigger_score = get_trigger_bonus("can_adapt_defense")
                reason = f"Survie protective — {gold_label}"

        # If it's an injected defensive component, give it a trigger score so it can compete
        if is_component and trigger_score == 0.0:
            # Check if it matches an active trigger
            _COMP_TRIGGERS = {
                1029: "need_armor", 1031: "need_armor", 3076: "need_armor",
                1033: "need_mr", 1057: "need_mr",
                3123: "need_grievous", 3916: "need_grievous",
                3140: "need_qss",
                1160: "can_adapt_defense", 1400: "can_adapt_defense"
            }
            for cid, tkey in _COMP_TRIGGERS.items():
                if self._item_id_to_name.get(cid) == item_name:
                    if triggers.get(tkey):
                        trigger_score = get_trigger_bonus(tkey)
                        reason = f"Composant de survie d'urgence ({tkey})"
                    break

        # ---- Step 2: Marginal gain match (wiki math) ----
        # Map item stats → their marginal gain from the precomputed dict.
        # Score = the sum of gains this item provides, normalized.
        marginal_score = 0.0

        if marginal_gains:
            import copy
            marginal_gains = copy.deepcopy(marginal_gains)
            # Enforce stat_priority strictly: penalize defensive gains if priority is purely offensive
            # Do not penalize if it's an injected component (which is there precisely for defense)
            if not is_component:
                if "AD_CRIT" in stat_priority or "AD_LETHALITY" in stat_priority:
                    for k in ["Armor_EHP", "MR_EHP", "HP_for_AD_EHP", "HP_for_AP_EHP", "AP", "MagicPenFlat", "PctMagicPen"]:
                        if k in marginal_gains:
                            marginal_gains[k] *= 0.05
                elif "AP" in stat_priority and "DEFENSIVE" not in stat_priority:
                    for k in ["Armor_EHP", "MR_EHP", "HP_for_AD_EHP", "HP_for_AP_EHP", "AD", "Crit", "AS", "Lethality", "PctArmorPen"]:
                        if k in marginal_gains:
                            marginal_gains[k] *= 0.05
                elif player_class in ("Tank", "Tank_Support"):
                    for k in ["AD", "AP", "Crit", "AS", "Lethality", "PctArmorPen", "MagicPenFlat", "PctMagicPen"]:
                        if k in marginal_gains:
                            marginal_gains[k] *= 0.05
                            
                    # Penalize the wrong resistance
                    if "MR" in stat_priority and "Armor_EHP" in marginal_gains:
                        marginal_gains["Armor_EHP"] *= 0.05
                    elif "ARMOR" in stat_priority and "MR_EHP" in marginal_gains:
                        marginal_gains["MR_EHP"] *= 0.05

            sb = self._item_stat_breakdown(item_name)
            item_gain = 0.0
            gain_parts = []

            # AD contribution
            if sb["ad"] > 0 and "AD" in marginal_gains:
                # Proportional: item gives sb["ad"] AD, 1000g gives (1000/35) AD
                frac = sb["ad"] / (1000 / _WIKI_GOLD["ad"])
                item_gain += marginal_gains["AD"] * frac
                gain_parts.append(f"AD+{marginal_gains['AD']*frac*100:.0f}%")

            # AP contribution
            if sb["ap"] > 0 and "AP" in marginal_gains:
                frac = sb["ap"] / (1000 / _WIKI_GOLD["ap"])
                item_gain += marginal_gains["AP"] * frac
                gain_parts.append(f"AP+{marginal_gains['AP']*frac*100:.0f}%")

            # Armor contribution
            if sb["armor"] > 0 and "Armor_EHP" in marginal_gains:
                frac = sb["armor"] / (1000 / _WIKI_GOLD["armor"])
                item_gain += marginal_gains["Armor_EHP"] * frac
                gain_parts.append(f"Armor+{marginal_gains['Armor_EHP']*frac*100:.0f}%EHP")

            # MR contribution
            if sb["mr"] > 0 and "MR_EHP" in marginal_gains:
                frac = sb["mr"] / (1000 / _WIKI_GOLD["mr"])
                item_gain += marginal_gains["MR_EHP"] * frac
                gain_parts.append(f"MR+{marginal_gains['MR_EHP']*frac*100:.0f}%EHP")

            # HP contribution
            if sb["hp"] > 0:
                frac = sb["hp"] / (1000 / _WIKI_GOLD["hp"])
                ad_w = marginal_gains.get("_ad_pct", 0.5)
                ap_w = marginal_gains.get("_ap_pct", 0.5)
                hp_gain = (
                    marginal_gains.get("HP_for_AD_EHP", 0) * ad_w +
                    marginal_gains.get("HP_for_AP_EHP", 0) * ap_w
                )
                item_gain += hp_gain * frac
                gain_parts.append(f"HP+{hp_gain*frac*100:.0f}%EHP")

            # AS contribution
            if sb["as_frac"] > 0 and "AS" in marginal_gains:
                frac = sb["as_frac"] / ((1000 / _WIKI_GOLD["as_pct"]) / 100)
                item_gain += marginal_gains["AS"] * frac
                gain_parts.append(f"AS+{marginal_gains['AS']*frac*100:.0f}%")

            # Crit contribution
            if sb["crit_frac"] > 0 and "Crit" in marginal_gains:
                frac = sb["crit_frac"] / ((1000 / _WIKI_GOLD["crit_pct"]) / 100)
                item_gain += marginal_gains["Crit"] * frac
                gain_parts.append(f"Crit+{marginal_gains['Crit']*frac*100:.0f}%")

            # Lethality contribution
            if sb["lethality"] > 0 and "Lethality" in marginal_gains:
                frac = sb["lethality"] / (1000 / _WIKI_GOLD["lethality"])
                item_gain += marginal_gains["Lethality"] * frac
                gain_parts.append(f"Leth+{marginal_gains['Lethality']*frac*100:.0f}%")

            # % Armor pen contribution
            if sb["pct_armor_pen"] > 0 and "PctArmorPen" in marginal_gains:
                frac = sb["pct_armor_pen"] / ((1000 / _WIKI_GOLD["armor_pen_pct"]) / 100)
                item_gain += marginal_gains["PctArmorPen"] * frac
                gain_parts.append(f"%Pen+{marginal_gains['PctArmorPen']*frac*100:.0f}%")

            # Flat magic pen contribution
            if sb["flat_magic_pen"] > 0 and "MagicPenFlat" in marginal_gains:
                frac = sb["flat_magic_pen"] / (1000 / _WIKI_GOLD["magic_pen_flat"])
                item_gain += marginal_gains["MagicPenFlat"] * frac
                gain_parts.append(f"MPen+{marginal_gains['MagicPenFlat']*frac*100:.0f}%")

            # % magic pen contribution
            if sb["pct_magic_pen"] > 0 and "PctMagicPen" in marginal_gains:
                frac = sb["pct_magic_pen"] / ((1000 / _WIKI_GOLD["armor_pen_pct"]) / 100)
                item_gain += marginal_gains["PctMagicPen"] * frac
                gain_parts.append(f"MPen%+{marginal_gains['PctMagicPen']*frac*100:.0f}%")

            # Normalize: 0.2 gain = 20% output increase = excellent score
            marginal_score = min(1.0, item_gain / 0.25)

            if gain_parts and not reason:
                reason = " | ".join(gain_parts[:2])

        # Fallback to stat-priority heuristic if no marginal gains available
        if not marginal_gains or marginal_score == 0:
            if "AD_LETHALITY" in stat_priority or "AD_CRIT" in stat_priority:
                marginal_score = 1.0 if (prim == "ad" or self._item_has_lethality(item_name)) else 0.05
            elif "AP" in stat_priority:
                marginal_score = 1.0 if prim == "ap" else 0.05
            elif "ARMOR" in stat_priority:
                marginal_score = 1.0 if prim == "armor" else (0.75 if prim == "hp" else 0.1)
            elif "MR" in stat_priority:
                marginal_score = 1.0 if prim == "mr" else (0.70 if prim == "hp" else 0.1)
            else:
                marginal_score = 0.4

        # ---- Step 3: Gold efficiency ----
        # Saturation à 140% d'efficacité au lieu de 100% pour distinguer les objets très rentables
        gold_score = min(1.0, gold_eff / 1.4)

        # ---- Step 3b: Valeur Conditionnelle (Conditional Share) ----
        conds = self._conditions.get(item_name)
        if conds:
            ratio_utile, inactifs = self._cond_module.ratio_effectif(conds, triggers)
            if inactifs:
                # L'or investi dans l'effet inutilisé est perdu.
                # On ne pénalise pas si on a déjà engagé l'or (composant possédé) ou si l'objet est déjà possédé (rescoring)
                components = set(_ITEM_BUILD_PATH_PRIORITY.get(item_name, []))
                gold_committed = bool(components and (components & set(already_have)))
                is_owned = item_name in already_have
                
                if not gold_committed and not is_owned:
                    gold_score *= ratio_utile
                    marginal_score *= ratio_utile

                    perdu = int(round((1.0 - ratio_utile) * 100))
                    reason_suffix = f" — {perdu}% du prix inutilisé"
                    if not reason:
                        reason = f"eff-or {gold_eff*100:.0f}%"
                    reason += reason_suffix

        # ---- Step 4: Build synergy via build path (P1 fix) ----
        # Uses _ITEM_BUILD_PATH_PRIORITY to check if we own a component of this item.
        components = set(_ITEM_BUILD_PATH_PRIORITY.get(item_name, []))
        synergy_n = 1.0 if (components and components & set(already_have)) else 0.0

        # ---- Step 5: Multiplicative passive correction ----
        # Items whose DDragon stats understate their true power
        passive_bonus = 0.0
        if item_name in _MULTIPLICATIVE_AP_ITEMS and marginal_gains:
            # Rabadon's: raw score doesn't capture the ×1.35 multiplier on ALL existing AP
            ap_now = marginal_gains.get("_current_ap", 0)
            passive_bonus = max(passive_bonus, self._rabadon_value_score(ap_now) * 0.4)
        if item_name in _SPELLBLADE_ITEMS and marginal_gains:
            ad_now = marginal_gains.get("_current_ad", 0)
            ap_now = marginal_gains.get("_current_ap", 0)
            sb_score = self._spellblade_score(item_name, ad_now, ap_now)
            passive_bonus = max(passive_bonus, sb_score * 0.20)
        if item_name == "Couperet noir" and marginal_gains:
            ad_now  = marginal_gains.get("_current_ad", 0)
            opp_arm = marginal_gains.get("_opp_armor", 60)
            bc_score = self._black_cleaver_score(already_have, opp_arm, player_class)
            passive_bonus = max(passive_bonus, bc_score * 0.4)
        if item_name in _ON_HIT_PASSIVE_ITEMS and marginal_gains:
            if "AD_CRIT" in stat_priority or "AD_BRUISER" in stat_priority:
                passive_bonus = max(passive_bonus, 0.5)  # Massive passive DPS gain
        if item_name in _BRUISER_PASSIVE_ITEMS and marginal_gains:
            if player_class == "Fighter":
                passive_bonus = max(passive_bonus, 0.4)
                
        if item_name in _TANK_PASSIVE_ITEMS and marginal_gains:
            if player_class in ("Tank", "Tank_Support"):
                passive_bonus = max(passive_bonus, 0.45)
            elif player_class == "Fighter":
                passive_bonus = max(passive_bonus, 0.25)
        if item_name in _MAGE_PASSIVE_ITEMS and marginal_gains:
            if player_class == "Mage" or player_class == "Assassin":
                passive_bonus = max(passive_bonus, 0.35)

        # Support items get a massive boost if the player is actually playing Support
        # This overrides their low raw gold efficiency and ensures they don't get bruiser/mage items
        _TANK_SUPPORT_ITEMS = {"Médaillon de l'Iron Solari", "Vœu du chevalier", "Convergence de Zeke", "Plaque du mort", "Masque abyssal", "Armure de Warmog", "Opposition céleste"}
        _ENCHANTER_SUPPORT_ITEMS = {"Régénérateur de pierre de lune", "Écho d'Helia", "Encensoir ardent", "Bâton des flots", "Rédemption", "Cœur gelé", "Mandat impérial"}
        if player_class == "Tank_Support" and item_name in _TANK_SUPPORT_ITEMS:
            passive_bonus = max(passive_bonus, 0.70)
        elif player_class == "Support" and item_name in _ENCHANTER_SUPPORT_ITEMS:
            passive_bonus = max(passive_bonus, 0.70)

        # Hard exclusions for supports (prevent expensive bruiser/carry items from leaking due to raw HP/Stats)
        if player_class in ("Support", "Tank_Support"):
            if item_name in _BRUISER_PASSIVE_ITEMS or item_name in _MAGE_PASSIVE_ITEMS:
                return 0.0, 0.0, "Objet trop coûteux/inadapté pour Support."
            if "Lame d'infini" in item_name or "Rabadon" in item_name or "Létalité" in stat_priority:
                # Add basic safety against ultra carry items
                if "Rabadon" in item_name or "Lame d'infini" in item_name:
                    return 0.0, 0.0, "Objet carry interdit pour Support."

        # ---- Step 6: Rune synergy bonus ----
        rune_bonus_raw = 0.0
        if marginal_gains:
            keystone  = marginal_gains.get("_keystone", "")
            rune_path = marginal_gains.get("_rune_path", "")
            rune_bonus_raw = self._get_rune_item_bonus(item_name, keystone, rune_path)
        # Normalize rune_bonus to 0-1 scale (max raw value is 0.25)
        rune_bonus_n = min(1.0, rune_bonus_raw / 0.25)
        passive_n    = min(1.0, passive_bonus)

        # P0 FIX — trigger_score is an additive bonus on top of the base composite,
        # NOT diluted inside a weighted average. A binary threshold (anti-soin, QSS)
        # is categorically different from a marginal stat gain.
        base_composite = (
            marginal_score * 0.55   # wiki marginal gain math — primary driver
            + gold_score   * 0.10   # gold efficiency (lowered because passives aren't in DDragon)
            + synergy_n    * 0.10   # build path continuity
            + passive_n    * 0.20   # multiplicative passive correction (increased to value passives)
            + rune_bonus_n * 0.05   # rune synergy bonus
        )
        composite = base_composite * affinity_factor + trigger_score
        composite *= situational_penalty

        # Penalize early Rabadon
        if item_name == "Coiffe de Rabadon":
            # 'ls' n'existe pas dans cette portée : l'ancien repli levait un
            # NameError dès que marginal_gains était vide.
            ap_now = marginal_gains.get("_current_ap", 0) if marginal_gains else 0.0
            if ap_now < 150:
                composite *= 0.6
            elif ap_now < 200:
                composite *= 0.85

        if is_component:
            composite *= 0.95
            if not reason:
                reason = "composant injecté"
            else:
                reason += " (composant)"

        if not reason:
            reason = f"eff-or {gold_eff*100:.0f}%"
            
        if affinity_note:
            reason = f"{reason} ({affinity_note})"

        return composite, gold_eff * 100.0, reason


    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def analyze(
        self,
        game_state,
        lane_opponent,
        candidate_items: list[str],
        my_position: str = "",
    ) -> StatReport:
        """Run full 3-axis analysis and return a StatReport."""
        self._ensure_loaded()

        ls    = game_state.live_stats
        local = game_state.local_player

        if ls is None or local is None:
            return StatReport()
            
        prof = self.affinity.profile(local.champion_name)

        # Resolve player class (handles Support subtypes)
        player_class = self._get_class(local.champion_name)
        # Occuper le poste de support ne fait pas de vous un enchanteur. Un
        # Pantheon support garde un profil de combattant : basculer aveuglément
        # la classe poussait les statistiques de soin/bouclier et lui faisait
        # conseiller Opposition céleste, jouée dans 10 % de ses parties pour
        # 33 % de victoires, au lieu de Mélodie du sang jouée dans 88 %.
        # heal_shield_power distingue les vrais supports (enchanteur 1.45,
        # engage 0.8) de ceux qui n'en ont que le poste (0).
        if my_position.lower() in ("support", "utility"):
            prof_moi = self.affinity.profile(local.champion_name) if self.affinity else {}
            vrai_support = ((prof_moi.get("affinity") or {}).get("heal_shield_power", 0) > 0)
            if vrai_support:
                player_class = "Tank_Support" if player_class in ("Tank", "Fighter") else "Support"

        # ---- Helper: ID to Name conversion ----
        def to_names(item_ids: list[int]) -> list[str]:
            return [self._item_id_to_name[i] for i in item_ids if i in self._item_id_to_name]

        # ---- Axis 2: Win Condition Metric ----
        level     = local.level
        my_items  = to_names(local.items)

        import copy
        
        # WARNING: game_state.all_players items are integer IDs. We mutate a deep copy to strings (names) here.
        # Any player object passed from outside (like lane_opponent) will still have integer IDs and MUST be 
        # re-matched against this copied list to access their string-converted items.
        copied_players = copy.deepcopy(game_state.all_players)
        for p in copied_players:
            p.items = to_names(p.items) # type: ignore

        enemies  = [p for p in copied_players if p.team != local.team]
        
        if lane_opponent:
            for e in enemies:
                if e.champion_name == lane_opponent.champion_name:
                    lane_opponent = e
                    break

        ad_pct, ap_pct = self._enemy_damage_split(enemies)
        tank_cnt = self._count_tanks(enemies)
        cc_cnt   = self._count_cc(enemies)

        # Determine primary threat for defensive itemization (who is actually killing me)
        primary_threat = None
        if game_state.threat_vector:
            # Sort threats by score descending
            sorted_threats = sorted(game_state.threat_vector.items(), key=lambda x: x[1], reverse=True)
            for t_name, score in sorted_threats:
                for e in enemies:
                    # Match by champion name or summoner name
                    if e.champion_name == t_name or e.riot_id == t_name:
                        primary_threat = e
                        break
                if primary_threat:
                    break
        
        if not primary_threat:
            if game_state.fed_enemies:
                primary_threat = game_state.fed_enemies[0]
            else:
                primary_threat = lane_opponent

        opp_items = lane_opponent.items if lane_opponent else []
        threat_items = primary_threat.items if primary_threat else []

        eff_armor = self._effective_armor(ls.armor, threat_items) # type: ignore
        eff_mr    = self._effective_mr(ls.magic_resist, threat_items) # type: ignore

        # EHP
        ehp_ad = self._ehp(ls.max_health, eff_armor)
        ehp_ap = self._ehp(ls.max_health, eff_mr)

        # Lane opponent info
        opp_class = self._get_class(lane_opponent.champion_name) if lane_opponent else "Fighter"
        opp_level = lane_opponent.level if lane_opponent else 1

        if player_class == "Assassin":
            win_ratio, win_type, win_exp = self._axis2_assassin(ls, my_items, enemies, level)
        elif player_class == "Mage":
            win_ratio, win_type, win_exp = self._axis2_mage(ls, my_items, enemies, level)
        elif player_class == "Marksman":
            win_ratio, win_type, win_exp = self._axis2_marksman(ls, my_items, enemies, level)
        elif player_class == "Fighter":
            win_ratio, win_type, win_exp = self._axis2_fighter(ls, enemies, level, eff_armor, eff_mr)
        elif player_class == "Tank":
            win_ratio, win_type, win_exp = self._axis2_tank(ls, eff_armor, eff_mr, ad_pct, ap_pct)
        else:
            win_ratio, win_type, win_exp = 0.5, "utility", "Support — priorité utility/protection."

        # ---- Empirical Win Ratio (Threat Vector) ----
        # Si le joueur meurt en boucle, on pondère l'estimation théorique avec la réalité
        if game_state.threat_vector:
            morts_recentes = sum(v for k, v in game_state.threat_vector.items() if v >= 1.0)
            if morts_recentes > 0:
                # 1 mort = 14% de poids, 5 morts = 70% de poids empirique (vers un win_ratio défensif de 0.25)
                w = min(0.7, morts_recentes / 5.0)
                win_ratio = (1.0 - w) * win_ratio + w * 0.25
                win_exp += f" (Morts récentes: {morts_recentes:.1f} -> win_ratio ajusté à {win_ratio:.2f})"

        # ---- Gold comparison (item value = most accurate lead metric) ----
        # Total gold value of all items owned. Captures: CS, kills, objectives, shutdown bounties.
        allied_players = [p for p in game_state.all_players if p.team == local.team and not p.is_local_player]
        my_item_gold   = self._compute_item_gold(my_items)
        opp_item_gold  = self._compute_item_gold(opp_items) # type: ignore
        my_team_gold   = my_item_gold + self._team_item_gold(allied_players)
        enemy_team_gold = self._team_item_gold(enemies)
        lane_gold_diff  = my_item_gold - opp_item_gold
        team_gold_diff  = my_team_gold - enemy_team_gold

        logger.debug(
            "Gold: me=%dg opp=%dg (lane diff %+dg) | team %dg vs %dg (team diff %+dg)",
            my_item_gold, opp_item_gold, lane_gold_diff,
            my_team_gold, enemy_team_gold, team_gold_diff,
        )
        
        # Calculate Phase 4 Context variables
        my_gold_share = my_item_gold / my_team_gold if my_team_gold > 0 else 0
        
        # We want to measure the frontline capacity of the rest of the team (or whole team)
        team_frontline = sum(
            self.affinity.profile(p.champion_name).get("axes", {}).get("frontline", 0) 
            for p in allied_players
        )
        # Adding myself as well to see total team frontline, because if I'm not playing it, it's lacking
        team_frontline += prof.get("axes", {}).get("frontline", 0)
        
        death_cost = min(1.0, game_state.game_time_seconds / 1800)

        # ---- Axis 3: Situational Triggers (gold-based) ----
        poids_soin = self._poids_soin_dynamiques(game_state, enemies)
        triggers = self._check_triggers(
            enemies, player_class, win_ratio, win_type, my_items,
            poids_soin=poids_soin,
            lane_gold_diff=lane_gold_diff,
            team_gold_diff=team_gold_diff,
            ad_pct=ad_pct,
            ap_pct=ap_pct,
            player_deaths=game_state.local_player.deaths,
            lane_opponent_name=getattr(lane_opponent, "champion_name", "") or "",
            my_champion_name=game_state.local_player.champion_name,
        )
        logger.debug("Triggers: %s", triggers)

        # ---- Priority determination ----
        stat_priority, priority_exp = self._determine_priority(
            player_class, win_ratio, triggers, ad_pct, ap_pct
        )

        # ---- Marginal gain calculation (wiki math) ----
        # Determine raw armor/MR of the priority target (before our pen).
        # Used to compute: lethality vs %pen threshold, and gain% per 1000g.
        priority_target_class = "squish" if player_class in ("Assassin", "Mage") else "tank"
        if priority_target_class == "squish":
            t_enemies = [p for p in enemies if self._get_class(p.champion_name) in ("Mage", "Marksman", "Support", "Assassin")]
        else:
            t_enemies = [p for p in enemies if self._get_class(p.champion_name) in ("Tank", "Fighter")]
        if not t_enemies:
            t_enemies = enemies

        complete_items_set = set(candidate_items)

        if t_enemies:
            tgt = max(t_enemies, key=lambda p: p.level)
            opp_armor_raw, _, opp_mr_raw = (
                lambda stats: (stats[1], stats[0], stats[2])
            )(self._estimate_enemy_stats(tgt.champion_name, tgt.level, tgt.items))
        else:
            opp_armor_raw, opp_mr_raw = 60.0, 50.0

        marginal_gains = self._compute_marginal_gains_per_1000g(
            ls, player_class, opp_armor_raw, opp_mr_raw, my_items, prof,
            my_gold_share=my_gold_share,
            team_frontline=team_frontline,
            death_cost=death_cost
        )
        # Inject context for score multipliers (passives, runes)
        marginal_gains["_current_ad"] = ls.attack_damage
        marginal_gains["_current_ap"] = ls.ability_power
        marginal_gains["_opp_armor"]  = opp_armor_raw
        marginal_gains["_keystone"]   = getattr(ls, "rune_keystone", "")
        marginal_gains["_ad_pct"]     = ad_pct
        marginal_gains["_ap_pct"]     = ap_pct
        marginal_gains["_rune_path"]  = getattr(ls, "rune_primary_path", "")
        
        top_gains_summary = self._format_top_gains(marginal_gains)

        logger.debug("Marginal gains: %s", top_gains_summary)
        rune_sum = self._rune_summary(marginal_gains["_keystone"], marginal_gains["_rune_path"], getattr(ls, "rune_secondary_path", ""))
        if rune_sum.strip() != "/ → ":
            logger.debug("Rune synergy: %s", rune_sum)

        # P1 + P2 — Score all candidate items, excluding items already owned.
        # Sort key is deterministic: (-score, name) prevents hash-dependent ordering.
        my_items_set = set(my_items)
        candidates_filtered = [it for it in candidate_items if it not in my_items_set]

        scored: list[tuple[str, float, float, str]] = []
        
        # GUARD: Situational Frequencies
        champ_role_key = f"{local.champion_name}|{my_position}"
        allowed_items = None
        if hasattr(self, "_situational_freqs") and champ_role_key in self._situational_freqs:
            allowed_items = set(self._situational_freqs[champ_role_key].keys())
        
        # Pre-filter all items once
        filtered = []
        for item_name in candidates_filtered:
            sb = self._item_stat_breakdown(item_name)
            aff_stats = self._to_affinity_keys(sb, item_name)
            
            # Lookup gold by iterating since _item_db is keyed by ID
            item_gold = 0.0
            for item_data in self._item_db.values():
                if item_data.get("name") == item_name:
                    item_gold = item_data.get("gold", {}).get("total", 0.0)
                    break
            
            keep = True
            aff_factor = 1.0
            aff_why = ""
            if item_name in complete_items_set:
                keep, aff_factor, aff_why = self.affinity.item_filter(prof, aff_stats, item_gold)
                
            # Situational Guard
            situational_penalty = 1.0
            if keep and hasattr(self, "_situational_freqs") and champ_role_key in self._situational_freqs:
                item_id_str = None
                for k, v in self._item_id_to_name.items():
                    if v == item_name:
                        item_id_str = str(k)
                        break
                
                if item_id_str:
                    freq_data = self._situational_freqs[champ_role_key].get(item_id_str, {})
                    freq = freq_data.get("rate", 0.0) if isinstance(freq_data, dict) else 0.0
                    situational_penalty = 0.35 if freq == 0 else min(1.0, 0.5 + freq / 0.06)

            if keep:
                filtered.append((item_name, aff_factor, aff_why, situational_penalty))
                
        import logging
        log = logging.getLogger(__name__)
        log.info(f"candidats: {len(candidate_items)} -> après filtre affinité: {len(filtered)}")
        
        current_gold = float(local.gold)
        
        for item_name, aff_factor, aff_why, situational_penalty in filtered:
            score, ge, reason = self._score_item(
                item_name, player_class, win_ratio,
                stat_priority, triggers, my_items,
                marginal_gains=marginal_gains,
                affinity_factor=aff_factor,
                affinity_note=aff_why,
                is_component=(item_name not in complete_items_set),
                current_gold=current_gold,
                situational_penalty=situational_penalty
            )
            scored.append((item_name, score, ge, reason))
        # P2 — deterministic sort: primary by score desc, secondary by name asc
        scored.sort(key=lambda x: (-x[1], x[0]))

        # Post-filter components if effective budget is sufficient for a complete item
        top_completes = []
        for name, _, _, _ in scored:
            if name in complete_items_set:
                top_completes.append(name)
            if len(top_completes) >= 3:
                break
                
        best_complete_cost = 0.0
        best_complete_name = top_completes[0] if top_completes else ""
        
        if top_completes:
            min_cost = float('inf')
            for cname in top_completes:
                for v in self._item_db.values():
                    if v.get("name") == cname:
                        cost = v.get("gold", {}).get("total", 0.0)
                        if cost > 0 and cost < min_cost:
                            min_cost = cost
                        break
            best_complete_cost = min_cost if min_cost != float('inf') else 0.0
                
        current_gold = float(local.gold)
        
        components_of_best = set(_ITEM_BUILD_PATH_PRIORITY.get(best_complete_name, []))
        owned_comps = components_of_best & set(my_items)
        owned_gold = 0.0
        for oc in owned_comps:
            for v in self._item_db.values():
                if v.get("name") == oc:
                    owned_gold += v.get("gold", {}).get("total", 0.0)
                    break
        effective_budget = current_gold + owned_gold
        
        # ---- Inject Defensive Components if we are poor ----
        if effective_budget < best_complete_cost * 0.85:
            _COMPONENT_BY_TRIGGER = {
                "need_armor":     [1029, 1031],      # Armure d'étoffe, Cotte de mailles
                "need_mr":        [1033, 1057],      # Cape de néant, Manteau de Négatron
                "need_grievous":  [3123, 3916],      # Marque du bourreau, Orbe de l'oubli
                "need_qss":       [3140],            # Ceinture de mercure
                "can_adapt_defense": [1160, 1400],   # Protège-bras du savant, Manteau de spectre
            }
            if player_class in ("Tank", "Fighter"):
                _COMPONENT_BY_TRIGGER["need_grievous"].append(3076) # Armure roncière

            for trig_key, comp_ids in _COMPONENT_BY_TRIGGER.items():
                if triggers.get(trig_key):
                    for cid in comp_ids:
                        cname = self._item_id_to_name.get(cid)
                        if cname and cname not in complete_items_set and cname not in my_items_set:
                            cscore, cge, creason = self._score_item(
                                cname, player_class, win_ratio,
                                stat_priority, triggers, my_items,
                                marginal_gains=marginal_gains,
                                affinity_factor=1.0,
                                affinity_note="",
                                is_component=True
                            )
                            scored.append((cname, cscore, cge, creason))
            
            # Re-sort after injecting components
            scored.sort(key=lambda x: (-x[1], x[0]))        # P1 — Sequential re-scoring: item #2 is scored against state AFTER buying item #1.
        # This prevents recommending two items of the same family (e.g. two pen items).
        if len(scored) >= 2:
            import copy
            first_name = scored[0][0]
            ls_sim = copy.copy(ls)
            # Apply first item's stats to simulated live stats
            sb1 = self._item_stat_breakdown(first_name)
            stat_map = {
                "ad":     "attack_damage", "ap": "ability_power",
                "armor":  "armor",         "mr": "magic_resist",
                "hp":     "max_health",
            }
            for stat_key, ls_attr in stat_map.items():
                delta = sb1.get(stat_key, 0.0)
                if delta:
                    setattr(ls_sim, ls_attr, getattr(ls_sim, ls_attr, 0.0) + delta)

            mg2 = self._compute_marginal_gains_per_1000g(
                ls_sim, player_class, opp_armor_raw, opp_mr_raw,
                my_items + [first_name], prof,
                my_gold_share=my_gold_share,
                team_frontline=team_frontline,
                death_cost=death_cost
            )
            mg2["_current_ad"] = ls_sim.attack_damage
            mg2["_current_ap"] = ls_sim.ability_power
            mg2["_opp_armor"]  = opp_armor_raw
            mg2["_keystone"]   = marginal_gains.get("_keystone", "")
            mg2["_rune_path"]  = marginal_gains.get("_rune_path", "")
            mg2["_ad_pct"]     = marginal_gains.get("_ad_pct", 0.5)
            mg2["_ap_pct"]     = marginal_gains.get("_ap_pct", 0.5)

            # Recalculate triggers assuming we bought the first item (turns off need_gw, need_pen etc if satisfied)
            triggers2 = self._check_triggers(
                enemies, player_class, win_ratio, win_type,
                my_items + [first_name],
                poids_soin=poids_soin,
                lane_gold_diff=lane_gold_diff,
                team_gold_diff=team_gold_diff,
                ad_pct=ad_pct,
                ap_pct=ap_pct,
                player_deaths=game_state.local_player.deaths,
                lane_opponent_name=getattr(lane_opponent, "champion_name", "") or "",
                my_champion_name=game_state.local_player.champion_name,
            )

            # Re-score all candidates except first, find diverse best second
            rescored2: list[tuple[str, float, float, str]] = []
            
            # The filter cache ONLY stores item_filter results (keep, factor, why) which are purely based on ChampionAffinity profile
            # and do not change after buying item #1. It does NOT cache the marginal gains or final score.
            filter_cache = {it: (f, w, p) for it, f, w, p in filtered}
            
            for item_name, _, ge, _ in scored[1:]:
                aff_factor, aff_why, sit_penalty = filter_cache.get(item_name, (1.0, "", 1.0))
                
                score2, ge2, reason2 = self._score_item(
                    item_name, player_class, win_ratio,
                    stat_priority, triggers2, my_items + [first_name],
                    marginal_gains=mg2,
                    affinity_factor=aff_factor,
                    affinity_note=aff_why,
                    is_component=(item_name not in complete_items_set),
                    current_gold=current_gold,
                    situational_penalty=sit_penalty
                )
                rescored2.append((item_name, score2, ge2, reason2))

            rescored2.sort(key=lambda x: (-x[1], x[0]))

            # P1 — Diversity constraint: avoid two items of the same situational family


            first_fams = self._get_item_families(first_name)
            second = None
            for cand in rescored2:
                cand_fams = self._get_item_families(cand[0])
                if first_fams and cand_fams and (first_fams & cand_fams):
                    continue   # skip: two items from the same situational family
                second = cand
                break
            if second is None and rescored2:
                second = rescored2[0]   # fallback

            if second:
                # Rebuild scored with corrected #2
                scored = [scored[0], second] + [s for s in rescored2 if s[0] != second[0]]

        # Build path advice for the top recommended item
        best_item = scored[0][0] if scored else ""
        comp_advice = ""
        if best_item:
            comp_advice = self._get_component_advice(best_item, float(local.gold))

        return StatReport(
            player_class=player_class,
            player_champion=local.champion_name,
            armor=ls.armor,
            magic_resist=ls.magic_resist,
            max_hp=ls.max_health,
            current_hp=ls.current_health,
            attack_damage=ls.attack_damage,
            ability_power=ls.ability_power,
            ability_haste=ls.ability_haste,
            crit_chance=ls.crit_chance,
            attack_speed=ls.attack_speed,
            effective_armor=eff_armor,
            effective_mr=eff_mr,
            ehp_vs_ad=ehp_ad,
            ehp_vs_ap=ehp_ap,
            enemy_ad_pct=ad_pct,
            enemy_ap_pct=ap_pct,
            enemy_tank_count=tank_cnt,
            enemy_cc_count=cc_cnt,
            lane_opp_name=lane_opponent.champion_name if lane_opponent else "inconnu",
            lane_opp_class=opp_class,
            lane_opp_level=opp_level,
            my_item_gold=my_item_gold,
            opp_item_gold=opp_item_gold,
            lane_gold_diff=lane_gold_diff,
            my_team_gold=my_team_gold,
            enemy_team_gold=enemy_team_gold,
            team_gold_diff=team_gold_diff,
            win_ratio=win_ratio,
            win_ratio_type=win_type,
            win_ratio_explanation=win_exp,
            need_grievous=triggers["need_grievous"],
            gw_source=triggers["gw_source"],
            need_armor_pen=triggers["need_armor_pen"],
            need_magic_pen=triggers["need_magic_pen"],
            need_tenacity=triggers["need_tenacity"],
            can_adapt_defense=triggers["can_adapt_defense"],
            enemy_fed_name=triggers["enemy_fed_name"],
            stat_priority=stat_priority,
            priority_explanation=priority_exp,
            top_gains_summary=top_gains_summary,
            component_advice=comp_advice,
            ranked_items=scored[:10],
        )



    def apply_item_stats(self, ls, item_name: str) -> None:
        """Mutates the LiveStats object by adding the item's additive stats."""
        stats = self._item_stat_breakdown(item_name)
        ls.attack_damage += stats.get("ad", 0)
        ls.ability_power += stats.get("ap", 0)
        ls.max_health += stats.get("hp", 0)
        ls.armor += stats.get("armor", 0)
        ls.magic_resist += stats.get("mr", 0)
        
        # Lethality is handled via lethality dictionary
        leth, arm_pen = _LETHALITY_ITEMS.get(item_name, (0.0, 0.0))
        ls.lethality = getattr(ls, 'lethality', 0) + leth



    def _get_item_families(self, iname: str) -> set[str]:
        fams = set()
        if self._item_has_pct_armor_pen(iname) or self._item_has_lethality(iname):
            fams.add("pen_ad")
        if self._item_has_pct_magic_pen(iname):
            fams.add("pen_ap")
        if iname in _GW_AD_ITEMS or iname in _GW_AP_ITEMS or iname in _GW_TANK_ITEM:
            fams.add("gw")
        if iname in self._antishield_items:
            fams.add("antishield")
        if iname in self._qss_items:
            fams.add("qss")
        return fams

    def plan_with_confidence(self, game_state, lane_opponent, candidate_items: list[str], n_slots: int = 6, prev_plan_items: list[str] = None, my_position: str = "") -> list[tuple[str, float, float, bool]]:
        """
        Sequentially score N items, applying stats after each choice.

        Renvoie (nom, confiance, marge, verrou_declencheur). Le dernier champ
        marque un emplacement remporté par un seuil binaire actif (anti-soin
        notamment) : il ne doit pas être écrasé par une prescription statistique,
        qui ignore le contexte de la partie.
        """
        import copy
        # Create a deep copy of the state so we can mutate it safely
        sim_state = copy.deepcopy(game_state)
        
        plan = []
        available_items = set(candidate_items)
        prev_plan_items = prev_plan_items or []
        
        for step in range(n_slots):
            if not available_items:
                break
                
            # Le poste doit descendre jusqu'ici : analyze() bascule player_class
            # en "Support"/"Tank_Support" et change tout le filtrage d'objets.
            # Sans lui, un Pantheon support était noté comme un Pantheon top.
            report = self.analyze(sim_state, lane_opponent, list(available_items),
                                  my_position=my_position)
            
            best_item = None
            best_score = 0.0
            second_score = 0.0
            
            # Get current families in the plan
            planned_fams = set()
            crit_count = 0
            for p_item, *_ in plan:
                planned_fams.update(self._get_item_families(p_item))
                
                # Also add unique groups
                for group_name, items_in_group in getattr(self, '_UNIQUE_GROUPS', {}).items():
                    if p_item in items_in_group:
                        planned_fams.add(f"unique_{group_name}")
                        
                # Add lifesteal/crit tracking
                if p_item in _HEALING_ITEMS:
                    planned_fams.add("mech_lifesteal")
                
                stats = self._item_stat_breakdown(p_item)
                if stats.get("crit_frac", 0) > 0:
                    crit_count += 1
                
            valid_cands = []
            prev_item_for_slot = prev_plan_items[step] if step < len(prev_plan_items) else None
            
            for item_data in report.ranked_items:
                cand = item_data[0]
                score = float(item_data[1])
                
                # Apply 1.08x hysteresis bonus if it was the previously planned item
                if cand == prev_item_for_slot:
                    score *= 1.08
                
                if cand not in available_items:
                    continue
                    
                cand_fams = set(self._get_item_families(cand))
                for group_name, items_in_group in getattr(self, '_UNIQUE_GROUPS', {}).items():
                    if cand in items_in_group:
                        cand_fams.add(f"unique_{group_name}")
                
                if cand in _HEALING_ITEMS:
                    cand_fams.add("mech_lifesteal")
                    
                stats = self._item_stat_breakdown(cand)
                if stats.get("crit_frac", 0) > 0 and crit_count >= 3:
                    cand_fams.add("crit_capped") # artificial family to prevent >3 crit items
                    
                # Conflict check
                if not (planned_fams & cand_fams):
                    valid_cands.append((cand, score))
                    
            # Re-sort valid_cands because the 1.08x bonus might have changed the order
            valid_cands.sort(key=lambda x: x[1], reverse=True)
            
            if valid_cands:
                best_item = valid_cands[0][0]
                best_score = float(valid_cands[0][1])
                if len(valid_cands) > 1:
                    second_score = float(valid_cands[1][1])
            elif report.ranked_items:
                # Fallback if everything conflicts (should be rare)
                fallback_cands = []
                for cand, score in report.ranked_items:
                    score = float(score)
                    if cand == prev_item_for_slot:
                        score *= 1.08
                    fallback_cands.append((cand, score))
                fallback_cands.sort(key=lambda x: x[1], reverse=True)
                
                best_item = fallback_cands[0][0]
                best_score = float(fallback_cands[0][1])
                if len(fallback_cands) > 1:
                    second_score = float(fallback_cands[1][1])
                
            if best_item:
                margin = 0.0
                if best_score > 0:
                    margin = (best_score - second_score) / best_score
                    
                # Confidence normalized between 0 and 1
                confidence = min(1.0, margin / 0.03) * (0.85 ** step)
                
                gw_all = self._gw_ad | self._gw_ap | self._gw_tank
                trigger_locked = bool(
                    getattr(report, "need_grievous", False) and best_item in gw_all
                )
                plan.append((best_item, confidence, margin, trigger_locked))
                available_items.discard(best_item)
                
                # Apply additive stats to sim_state
                self.apply_item_stats(sim_state.live_stats, best_item)
                
        return plan
