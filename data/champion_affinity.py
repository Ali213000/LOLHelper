"""
ai/champion_affinity.py — adaptation du scoring d'objets au champion joué.

Corrige le défaut principal de StatAnalyzer : le scoring raisonne par CLASSE
(Assassin, Mage, Marksman, Fighter, Tank), donc Vayne reçoit les mêmes
recommandations que Jinx et Kassadin les mêmes que Xerath.

Trois leviers, du plus fort au plus fin :

  1. FILTRES DURS      retirent les objets structurellement inutiles
                       (mana sur Katarina, crit sur Kassadin)
  2. AFFINITÉS         multiplient les gains marginaux par statistique
  3. damage_mix        remplace _CLASS_DAMAGE_SPLIT par le vrai ratio AD/AP
                       du champion, déjà présent dans champions_*.json

Intégration : voir INTÉGRATION en bas de fichier.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Multiplicateur appliqué à un objet retenu malgré un flag défavorable.
# 0.0 = exclusion pure. On préfère une pénalité forte à l'exclusion, sauf
# pour le mana sur un champion sans ressource, qui est un gaspillage total.
_SOFT_PENALTY = 0.25
_CRIT_PENALTY = 0.15
_ONHIT_PENALTY = 0.45

# Un objet est considéré « centré » sur une stat si elle représente au moins
# cette part de sa valeur en or.
_DOMINANT_SHARE = 0.35


class ChampionAffinity:
    """
    Usage :
        aff = ChampionAffinity("data/champion_item_profiles.json",
                               champion_tables_dir="data")
        prof = aff.profile("Vayne", archetype="adc_hypercarry")
        mult = aff.stat_multiplier(prof, "attack_speed")     # 1.50
        keep, factor, why = aff.item_filter(prof, item_stats)
    """

    def __init__(self, profiles_path: str, champion_tables_dir: str | None = None):
        with open(profiles_path, encoding="utf-8") as f:
            d = json.load(f)
        self.defaults = d["archetype_defaults"]
        self._tags: dict | None = None
        self._warned_tags: set[str] = set()
        self.overrides = d["champion_overrides"]
        self.stat_keys = d["stat_keys"]

        # archetype + damage_mix par champion, récupérés des tables de draft
        self.by_champ: dict[str, dict] = {}
        if champion_tables_dir:
            self._load_champion_tables(champion_tables_dir)

        self._cache: dict[str, dict] = {}
        self._warned: set[str] = set()

    # ------------------------------------------------------------------

    def _load_champion_tables(self, directory: str):
        import glob
        for path in glob.glob(os.path.join(directory, "champions_*.json")):
            try:
                data = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                log.warning("table illisible %s : %s", path, e)
                continue
            for ch in data.get("champions", []):
                self.by_champ.setdefault(ch["id"], {
                    "archetype": ch.get("archetype"),
                    "damage_mix": ch.get("damage_mix"),
                    "attack_range": ch.get("attack_range"),
                })

    # ------------------------------------------------------------------

    # Tag principal Data Dragon -> archétype le plus proche.
    _PAR_TAG = {
        "Marksman": "adc_hypercarry",
        "Assassin": "assassin",
        "Mage": "mage_control",
        "Tank": "tank_engage",
        "Fighter": "bruiser_splitpush",
        "Support": "enchanter",
    }

    def _archetype_par_tags(self, champion: str) -> str | None:
        """Archétype déduit des tags, pour un champion absent des tables."""
        tags = (self._tags_ddragon() or {}).get(champion) or []
        for tag in tags:                      # l'ordre DDragon est significatif
            if tag in self._PAR_TAG:
                arch = self._PAR_TAG[tag]
                if champion not in self._warned_tags:
                    log.info("%s absent des tables — archétype %r déduit de %s.",
                             champion, arch, tags)
                    self._warned_tags.add(champion)
                return arch
        return None

    def _tags_ddragon(self) -> dict:
        if self._tags is None:
            self._tags = {}
            try:
                import json as _json
                with open("assets/champion_data.json", encoding="utf-8") as f:
                    for v in _json.load(f).get("data", {}).values():
                        if v.get("name"):
                            self._tags[v["name"]] = v.get("tags", [])
            except Exception as exc:
                log.warning("Tags Data Dragon illisibles : %s", exc)
        return self._tags

    def profile(self, champion: str, archetype: str | None = None) -> dict:
        """Profil fusionné : défaut d'archétype + surcharge champion."""
        if champion in self._cache:
            return self._cache[champion]

        # Normalisation partagée avec le scorer : la version maison produisait
        # "KaiSa" là où les tables attendent "Kaisa", et 15 champions sur 173
        # se retrouvaient sans profil — donc sans le moindre filtrage d'objets.
        # C'est ce qui faisait recommander un Couperet noir à Kai'Sa.
        from ai.champion_scorer import norm_name
        candidats = [
            norm_name(champion),
            champion.replace("'", "").replace(" ", "").replace(".", ""),
            champion,
        ]
        meta = {}
        for c in candidats:
            if c in self.by_champ:
                meta = self.by_champ[c]
                norm_champ = c
                break
        else:
            norm_champ = candidats[0]
        arch = archetype or meta.get("archetype")

        base = self.defaults.get(arch)
        if base is None:
            # Repli par tags Data Dragon. Le fichier annonce que « les nouveaux
            # champions fonctionnent sans intervention » : sans ce repli, tout
            # champion absent des tables tournait SANS filtrage d'objets, ce qui
            # laissait passer n'importe quelle recommandation.
            arch = self._archetype_par_tags(champion)
            base = self.defaults.get(arch)

        if base is None:
            if champion not in self._warned:
                log.warning(
                    "Aucun archétype pour %s (arch=%s) — profil neutre appliqué. "
                    "Ajoute-le à champions_*.json ou passe archetype= explicitement.",
                    champion, arch)
                self._warned.add(champion)
            base = {"affinity": {}, "flags": {}}

        merged = {
            "champion": champion,
            "archetype": arch,
            "affinity": dict(base.get("affinity", {})),
            "flags": dict(base.get("flags", {})),
            "damage_mix": meta.get("damage_mix"),
            "source": "archetype",
        }

        # Les surcharges sont indexées sur l'identifiant Riot ("Kaisa"), pas sur
        # le nom d'affichage ("Kai'Sa") : chercher avec ce dernier les ignorait
        # en silence. Kai'Sa se retrouvait avec les réglages par défaut d'un ADC
        # à coups critiques, donc ses objets on-hit pénalisés à 0.45.
        ov = None
        for c in candidats:
            if c in self.overrides:
                ov = self.overrides[c]
                break
        if ov:
            merged["affinity"].update(ov.get("affinity", {}))
            merged["flags"].update(ov.get("flags", {}))
            merged["source"] = "override"
            if "note" in ov:
                merged["note"] = ov["note"]

        self._cache[champion] = merged
        return merged

    # ------------------------------------------------------------------

    @staticmethod
    def stat_multiplier(prof: dict, stat: str) -> float:
        """Multiplicateur d'affinité pour une statistique. 1.0 par défaut."""
        return float(prof["affinity"].get(stat, 1.0))

    def apply_to_marginal_gains(self, prof: dict, gains: dict[str, float]) -> dict[str, float]:
        """
        Pondère les gains marginaux par les affinités du champion.

        `gains` : {stat: gain_pour_1000_or} tel que produit par
        _compute_marginal_gains_per_1000g.
        """
        return {k: v * self.stat_multiplier(prof, k) for k, v in gains.items()}

    # ------------------------------------------------------------------

    def item_filter(self, prof: dict, item_stats: dict[str, float],
                    item_gold: float = 0.0) -> tuple[bool, float, str]:
        """
        Filtres durs sur un objet candidat.

        Retourne (garder, facteur_multiplicatif, raison).
        garder=False  → retirer complètement des candidats
        facteur < 1.0 → conserver mais pénaliser
        """
        flags = prof["flags"]
        reasons = []
        factor = 1.0

        # --- mana sur un champion sans ressource : gaspillage total ---
        if not flags.get("uses_mana", True):
            mana = item_stats.get("mana", 0) + item_stats.get("mana_regen", 0)
            if mana > 0:
                share = self._stat_share(item_stats, ("mana", "mana_regen"), item_gold)
                if share >= _DOMINANT_SHARE:
                    return False, 0.0, f"{prof['champion']} n'utilise pas de mana"
                factor *= 0.70
                reasons.append("mana partiellement gaspillé")

        # --- coup critique sur un champion non-crit ---
        if item_stats.get("crit", 0) > 0 and not flags.get("crit_viable", False):
            share = self._stat_share(item_stats, ("crit",), item_gold)
            if share >= _DOMINANT_SHARE:
                return False, 0.0, f"le crit n'apporte rien à {prof['champion']}"
            factor *= _CRIT_PENALTY
            reasons.append("crit peu utile")

        # --- effets à l'impact ---
        if item_stats.get("on_hit", 0) > 0 and not flags.get("on_hit", False):
            factor *= _ONHIT_PENALTY
            reasons.append("effets à l'impact sous-exploités")

        # --- profil de dégâts : AP sur un champion 100% AD et inversement ---
        mix = prof.get("damage_mix")
        if mix:
            ap_stat = item_stats.get("ap", 0)
            ad_stat = item_stats.get("ad", 0)
            if ap_stat > 0 and mix.get("ap", 0) < 0.15:
                share = self._stat_share(item_stats, ("ap",), item_gold)
                if share >= _DOMINANT_SHARE:
                    return False, 0.0, "objet AP sur un champion sans ratio AP"
                factor *= _SOFT_PENALTY
                reasons.append("AP peu exploitée")
            if ad_stat > 0 and mix.get("ad", 0) < 0.15:
                share = self._stat_share(item_stats, ("ad",), item_gold)
                if share >= _DOMINANT_SHARE:
                    return False, 0.0, "objet AD sur un champion sans ratio AD"
                factor *= _SOFT_PENALTY
                reasons.append("AD peu exploitée")

        # --- vitesse d'attaque sur un champion qui la convertit ou l'ignore ---
        if item_stats.get("attack_speed", 0) > 0:
            as_aff = self.stat_multiplier(prof, "attack_speed")
            if as_aff <= 0.20:
                share = self._stat_share(item_stats, ("attack_speed",), item_gold)
                if share >= _DOMINANT_SHARE:
                    return False, 0.0, f"la vitesse d'attaque est inutile sur {prof['champion']}"

        return True, factor, " ; ".join(reasons)

    @staticmethod
    def _stat_share(item_stats: dict, keys: tuple, item_gold: float) -> float:
        """
        Part de la valeur en or de l'objet portée par ces statistiques.
        Repli sur une heuristique de comptage si l'or n'est pas fourni.
        """
        if item_gold and item_gold > 0:
            # nécessite _GOLD_PER_STAT côté appelant ; approximation ici
            total = sum(abs(v) for v in item_stats.values()) or 1.0
            part = sum(abs(item_stats.get(k, 0)) for k in keys)
            return part / total
        non_zero = [k for k, v in item_stats.items() if v]
        if not non_zero:
            return 0.0
        return sum(1 for k in keys if item_stats.get(k)) / len(non_zero)


# ======================================================================
# INTÉGRATION dans stat_analyzer.py
# ======================================================================
#
# 1) Au chargement de StatAnalyzer :
#
#       self.affinity = ChampionAffinity("data/champion_item_profiles.json",
#                                        champion_tables_dir="data")
#
# 2) Dans analyze(), juste après avoir déterminé le champion joué :
#
#       prof = self.affinity.profile(my_champion_name)
#
#    Et remplacer le ratio de dégâts par classe :
#
#       # AVANT : split = _CLASS_DAMAGE_SPLIT[player_class]
#       split = prof["damage_mix"] or _CLASS_DAMAGE_SPLIT[player_class]
#
# 3) Dans _compute_marginal_gains_per_1000g, en toute fin :
#
#       gains = self.affinity.apply_to_marginal_gains(prof, gains)
#
# 4) Dans _score_item, avant tout calcul :
#
#       keep, factor, why = self.affinity.item_filter(prof, item_stats, item_gold)
#       if not keep:
#           return None                    # l'appelant saute l'objet
#       ...
#       composite *= factor
#       if why:
#           reason = f"{reason} ({why})"
#
# 5) Dans analyze(), filtrer AVANT le tri pour ne pas gaspiller de calcul :
#
#       candidate_items = [i for i in candidate_items
#                          if self.affinity.item_filter(
#                              prof, self._item_stat_breakdown(i))[0]]
#
# Les clés de item_stats doivent correspondre à stat_keys du JSON. Si tes
# clés internes diffèrent (ex. "attackspeed" vs "attack_speed"), ajoute une
# table de correspondance au chargement plutôt que de renommer partout.
