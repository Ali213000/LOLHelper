"""
data/hard_cc.py — Champions disposant d'un CC *dur* fiable.

« CC dur » = étourdissement, immobilisation, projection, charme, peur,
provocation, suppression ou sommeil. Les ralentissements en sont exclus :
c'est ce que la tenacité (Sandales de Mercure) réduit réellement.

Cette liste était auparavant recopiée à l'identique dans
`services/stat_analyzer.py` et `ai/boot_optimizer.py`, avec 31 entrées et des
absences notables (Anivia, Braum, Leona était présente mais pas Nautilus…).
Résultat : le seuil « 3 CC durs → Sandales de Mercure » ne se déclenchait
presque jamais. Un seul point de vérité désormais.

Noms en anglais : c'est la forme renvoyée par le LCU et la Live Client API.
"""

HARD_CC_CHAMPIONS: frozenset[str] = frozenset({
    # -- Projections --
    "Alistar", "Aurelion Sol", "Azir", "Blitzcrank", "Braum", "Cho'Gath",
    "Gragas", "Hecarim", "Janna", "Jarvan IV", "K'Sante", "Lulu", "Malphite",
    "Nami", "Nautilus", "Nunu & Willump", "Ornn", "Poppy", "Rakan", "Rell",
    "Riven", "Sion", "Thresh", "Tristana", "Vel'Koz", "Vi", "Wukong",
    "Xin Zhao", "Yasuo", "Yone", "Zac", "Ziggs", "Zyra",

    # -- Étourdissements --
    "Amumu", "Anivia", "Annie", "Ashe", "Bard", "Camille", "Cassiopeia",
    "Elise", "Gnar", "Kennen", "Leona", "Lissandra", "Morgana", "Pantheon",
    "Pyke", "Sejuani", "Sett", "Sona", "Syndra", "Tahm Kench", "Taric",
    "Twisted Fate", "Veigar", "Volibear", "Xerath",

    # -- Immobilisations --
    "Ivern", "Jhin", "Lux", "Maokai", "Neeko", "Ryze", "Soraka", "Swain",
    "Varus",

    # -- Charmes, peurs, provocations --
    "Ahri", "Evelynn", "Fiddlesticks", "Galio", "Rammus", "Renata Glasc",
    "Seraphine", "Shen",

    # -- Suppressions et sommeil --
    "Malzahar", "Skarner", "Urgot", "Warwick", "Zoe",
})

# Alias conservé pour les imports historiques.
_CC_CHAMPIONS = HARD_CC_CHAMPIONS


def count_hard_cc(champion_names) -> int:
    """Nombre de champions à CC dur dans *champion_names*."""
    return sum(1 for name in champion_names if name in HARD_CC_CHAMPIONS)
