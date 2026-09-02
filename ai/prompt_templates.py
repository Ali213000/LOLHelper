"""
ai/prompt_templates.py — Prompt factory for all LLM calls.

All prompts enforce:
  - French language
  - No markdown (no * # bullets)
  - Concise, actionable coaching only
"""
from services.image_cache import ImageCache


# ---------------------------------------------------------------------------
# 1. Champ Select
# ---------------------------------------------------------------------------

def champ_select_prompt(
    my_champion: str,
    allies: list[str],
    enemies: list[str],
    my_position: str = "",
) -> tuple[str, str]:
    """Conseil de draft. Retourne (system_prompt, user_prompt)."""

    position_str = my_position if my_position else "inconnu"

    system = (
        f"Tu es un coach League of Legends expert du poste {position_str}. "
        "Réponds TOUJOURS en français. "
        f"IMPORTANT : conseille UNIQUEMENT des champions du poste {position_str}. "
        "Analyse ces éléments dans cet ordre strict :\n"
        "1. Ratio AD/AP : si notre équipe a trop de dommages d'un seul type, signale-le et équilibre.\n"
        "2. Counter-pick : identifie le champion ennemi le plus dangereux pour mon poste et suggère un counter.\n"
        "3. Synergie : mon champion s'intègre-t-il bien avec notre équipe ?\n"
        "4. Scaling (Early vs Late) : analyse la puissance de l'équipe adverse en early ou late game. Si les adversaires sont très faibles en early mais forts en late, conseille un champion agressif pour les écraser tôt, ou assure-toi qu'on a assez de late game si l'équipe manque de scaling.\n"
        "Format de réponse STRICT (6 lignes en texte brut, ne mets aucune liste à puces ni markdown) :\n"
        "AD/AP : [bilan + ajustement si nécessaire]\n"
        "Scaling : [analyse early/late et ce qu'il faut viser]\n"
        "Counter : [champion ennemi] -> joue [champion conseillé]\n"
        "Synergie : [une phrase]\n"
        "Runes : [keystone + secondaire en 5 mots]\n"
        "Plan : [une phrase courte et actionnable]"
    )

    allies_str  = ", ".join(allies)  if allies  else "inconnus"
    enemies_str = ", ".join(enemies) if enemies else "inconnus"
    my_str      = my_champion        if my_champion else "non sélectionné"

    user = (
        f"Mon poste : {position_str}\n"
        f"Mon champion : {my_str}\n"
        f"Mon équipe : {allies_str}\n"
        f"Équipe ennemie : {enemies_str}\n\n"
        "Donne-moi le conseil de draft pour mon poste."
    )

    return system, user


# ---------------------------------------------------------------------------
# 1b. Champion Suggestions — tier list x3 (pre-generated before pick turn)
# ---------------------------------------------------------------------------

def champion_suggestion_prompt(
    enemies: list[str],
    allies: list[str],
    my_position: str,
    banned_champions: list[str],
) -> tuple[str, str]:
    """
    Generate a ranked list of exactly 3 champion picks for this position.

    LLM MUST reply with exactly 3 lines:
        1. ChampionName | Short reason (8 words max in French)
        2. ChampionName | Short reason
        3. ChampionName | Short reason

    Champion names MUST be in English (DataDragon/LCU names).
    """
    position_str = my_position if my_position else "inconnu"
    banned_str   = ", ".join(banned_champions) if banned_champions else "aucun"
    allies_str   = ", ".join(allies)  if allies  else "aucun"
    enemies_str  = ", ".join(enemies) if enemies else "inconnus"

    system = (
        f"Tu es un coach League of Legends expert du poste {position_str}. "
        "Recommande exactement 3 champions pour ce poste. "
        "RÈGLES ABSOLUES :\n"
        "- Réponds UNIQUEMENT avec 3 lignes numérotées, rien d'autre\n"
        "- Format exact : N. NomAnglaisChampion | Raison en 8 mots max en français\n"
        "- Les noms DOIVENT être en anglais exact (ex: Ahri, Yasuo, LeBlanc, Twisted Fate)\n"
        "- Classe par ordre de priorité : 1 = meilleur pick du draft\n"
        "- Tiens compte des picks ennemis actuels, des alliés et des bans\n"
        "- N'utilise JAMAIS * # markdown ou ligne supplémentaire"
    )

    user = (
        f"Poste : {position_str}\n"
        f"Alliés : {allies_str}\n"
        f"Ennemis : {enemies_str}\n"
        f"Bannis : {banned_str}\n\n"
        "Donne exactement 3 champions recommandés (noms en anglais)."
    )

    return system, user


# ---------------------------------------------------------------------------
# 2. Item recommandation (ennemi fed)
# ---------------------------------------------------------------------------

def item_advice_prompt(
    my_champion: str,
    my_items: list[str],
    my_gold: float,
    fed_enemy_champion: str,
    fed_enemy_kda: str,
    game_time_minutes: float,
    my_team: list[str],
    enemy_team: list[str],
) -> tuple[str, str]:
    """Conseil d'item contre un ennemi fed. Retourne (system_prompt, user_prompt)."""

    system = (
        "Tu es un expert itemisation League of Legends. "
        "Réponds TOUJOURS en français. "
        "Réponds en 2 phrases MAXIMUM. "
        "Format : [Nom de l'item]. [Raison en une phrase simple et directe.] "
        "N'utilise JAMAIS les caractères * # ou des listes. "
        "Sois direct : dis exactement quoi acheter et pourquoi en quelques mots."
    )

    my_items_str = ", ".join(my_items) if my_items else "aucun"
    gold_str     = f"{my_gold:.0f} gold"

    user = (
        f"Ennemi fed : {fed_enemy_champion} ({fed_enemy_kda})\n"
        f"Mon champion : {my_champion}\n"
        f"Mes items actuels : {my_items_str}\n"
        f"Mon gold : {gold_str}\n"
        f"Temps de jeu : {game_time_minutes:.0f} minutes\n\n"
        "Quel item acheter maintenant ?"
    )

    return system, user


def death_or_back_prompt(
    trigger: str,
    my_champion: str,
    my_position: str,
    my_items: list[str],
    my_gold: float,
    game_time_minutes: float,
    lane_opponent: str,
    lane_opponent_kda: str,
    lane_opponent_items: list[str],
    fed_enemies: list[dict],
    enemy_team: list[str],
    stat_report: str = "",
) -> tuple[str, str]:
    """Conseil d'achat (Plan de build complet). Retourne (system_prompt, user_prompt)."""

    # Inject valid item names so the LLM only references real items
    try:
        valid_items = ImageCache().valid_items
        items_context = ", ".join(sorted(valid_items)[:150])
    except Exception:
        items_context = ""

    if trigger == "début de partie":
        system = (
            f"Tu es un coach League of Legends expert du poste {my_position or 'inconnu'}. "
            "Réponds TOUJOURS en français. "
            "Le joueur vient de commencer la partie (0 minute, 500 gold). "
            "Ton rôle est d'indiquer les items de départ (ex: Lame de Doran + Potion) et le tout premier 'Core Item' non négociable à viser. "
            "Format obligatoire (MAXIMUM 2 lignes, texte brut, aucune liste à puce) :\n"
            "1. [Items de départ] / [Raison en 5 mots]\n"
            "2. Objectif : [Nom du 1er Core Item] / [Raison en 5 mots]\n"
            "N'ajoute AUCUN autre texte. Jamais de bonjour ou titre."
        )
    else:
        system = (
            f"Tu es un coach League of Legends expert du poste {my_position or 'inconnu'}. "
            "Réponds TOUJOURS en français. "
            "Tu reçois une analyse statistique pré-calculée (EHP, pénétration ennemie, score de rentabilité par item). "
            "Ton rôle est UNIQUEMENT de confirmer les 2 meilleurs items de la liste classée et d'expliquer en 5 mots pourquoi. "
            "Règles absolues : UNIQUEMENT des items complets, jamais un composant, jamais un consommable. "
            f"Items disponibles (noms exacts) : {items_context}. "
            "Format obligatoire (MAXIMUM 2 lignes, texte brut, aucune liste à puce) :\n"
            "1. [Nom exact de l'item] / [Raison en 5 mots]\n"
            "2. [Nom exact de l'item] / [Raison en 5 mots]\n"
            "N'ajoute AUCUN autre texte. Jamais de composants. Jamais de bonjour ou titre."
        )

    my_items_str  = ", ".join(my_items)  if my_items  else "aucun"
    opp_items_str = ", ".join(lane_opponent_items) if lane_opponent_items else "inconnus"
    fed_str = ", ".join(f"{e['champion']} ({e['kda']})" for e in fed_enemies) if fed_enemies else "aucun"
    enemy_str = ", ".join(enemy_team) if enemy_team else "inconnus"

    user = (
        f"Contexte : {trigger} à {game_time_minutes:.0f} minutes\n"
        f"Mon champion : {my_champion} ({my_position or 'poste inconnu'})\n"
        f"Mes items actuels : {my_items_str}\n"
        f"Adversaire de lane : {lane_opponent} ({lane_opponent_kda}) | Ses items : {opp_items_str}\n"
        f"Ennemis fed : {fed_str}\n"
        f"Équipe ennemie : {enemy_str}\n\n"
    )

    if stat_report:
        user += stat_report + "\n\n"

    user += "Confirme les 2 meilleurs items de la liste classée ci-dessus, dans l'ordre."

    return system, user


# ---------------------------------------------------------------------------
# 3. Post-Game (pour usage futur)
# ---------------------------------------------------------------------------

def postgame_analysis_prompt(
    my_champion: str,
    match_result: str,
    timeline_summary: str,
    my_death_events: list[dict],
    objective_events: list[dict],
) -> tuple[str, str]:
    """Analyse post-game. Retourne (system_prompt, user_prompt)."""

    system = (
        "Tu es un analyste League of Legends sans pitié. "
        "Réponds TOUJOURS en français. "
        "Identifie les 3 erreurs principales qui ont causé la défaite. "
        "Format (sans astérisques, sans markdown) :\n"
        "Erreur 1 : [timestamp] [ce qui s'est passé et pourquoi c'est une erreur]\n"
        "Erreur 2 : [timestamp] [idem]\n"
        "Erreur 3 : [timestamp] [idem]\n"
        "Verdict : [une phrase brutalement honnête]\n"
        "N'utilise JAMAIS les caractères * # ou des tirets en liste."
    )

    deaths_str = ""
    for d in my_death_events[:8]:
        ts  = d.get("timestamp_minutes", "?")
        pos = d.get("position", "")
        deaths_str += f"  Mort à {ts}min près de {pos}\n"
    if not deaths_str:
        deaths_str = "  Aucune mort enregistrée\n"

    objs_str = ""
    for o in objective_events[:6]:
        ts     = o.get("timestamp_minutes", "?")
        obj    = o.get("objective", "?")
        winner = o.get("winning_team", "?")
        objs_str += f"  {ts}min — {obj} pris par {winner}\n"
    if not objs_str:
        objs_str = "  Aucun objectif enregistré\n"

    user = (
        f"Champion joué : {my_champion}\n"
        f"Résultat : {match_result}\n\n"
        f"Mes morts :\n{deaths_str}\n"
        f"Objectifs majeurs :\n{objs_str}\n"
        f"Résumé timeline :\n{timeline_summary}\n\n"
        "Donne ton analyse."
    )

    return system, user
