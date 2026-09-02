"""
Onglet En jeu : bascule entre l'écran d'attente et les données de partie.

Après une partie, l'onglet gardait à l'écran des chiffres périmés — avantage
en or de la partie précédente, sélecteur d'allié figé sur un champion, et le
libellé « Aucun objet » empilé deux fois parce qu'il était recréé sans être
suivi. Les données de partie vivent désormais dans un conteneur qu'on masque
d'un bloc.
"""
import pytest

pytest.importorskip("customtkinter")


@pytest.fixture(scope="module")
def racine():
    """
    Racine Tk unique pour tout le module.

    Indispensable : les images Tk sont liées à leur interpréteur, et
    ImageCache est un singleton. Deux racines dans le même processus font
    échouer le rendu sur « image pyimageN doesn't exist ».
    """
    import customtkinter as ctk

    try:
        r = ctk.CTk()
    except Exception as exc:                      # pas d'affichage disponible
        pytest.skip(f"Tk indisponible : {exc}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


@pytest.fixture(scope="module")
def panneau(racine):
    from ui.ingame_panel import InGamePanel

    p = InGamePanel(racine)
    p.pack(fill="both", expand=True)
    racine.update_idletasks()
    return p


def _visible(widget) -> bool:
    return bool(widget.winfo_manager())


def test_l_etat_initial_est_l_attente(panneau):
    assert _visible(panneau._attente)
    assert not _visible(panneau._contenu)


def test_la_partie_affiche_les_donnees(panneau):
    panneau.afficher_partie()
    assert _visible(panneau._contenu)
    assert not _visible(panneau._attente)


def test_la_fin_de_partie_revient_a_l_attente(panneau):
    panneau.afficher_partie()
    panneau.set_no_game()
    assert _visible(panneau._attente)
    assert not _visible(panneau._contenu)


def test_le_message_d_attente_est_contextuel(panneau):
    panneau.afficher_attente("Draft en cours.")
    assert panneau._attente_detail.cget("text") == "Draft en cours."


def test_l_avantage_en_or_est_remis_a_zero(panneau):
    """Il gardait les valeurs de la partie précédente."""
    for _, diff in panneau._adv_widgets:
        diff.configure(text="+2825g")
    panneau.set_no_game()
    for _, diff in panneau._adv_widgets:
        assert diff.cget("text") == "—"


def test_le_selecteur_d_allie_est_remis_a_zero(panneau):
    panneau._ally_selector.configure(values=["(Moi)", "Garen"])
    panneau._ally_selector.set("Garen")
    panneau.set_no_game()
    assert panneau._ally_selector.get() == "(Moi)"


def test_aucun_objet_ne_s_empile_pas(panneau):
    """
    Le libellé était recréé à chaque appel sans être suivi dans _item_labels :
    deux fins de partie affichaient « Aucun objetAucun objet ».
    """
    for _ in range(3):
        panneau.set_no_game()
    textes = [
        e.cget("text") for e in panneau._items_container.winfo_children()
        if hasattr(e, "cget")
    ]
    assert textes.count("Aucun objet") == 1, f"libellés empilés : {textes}"


def test_le_client_lcu_reste_en_lecture_seule():
    """
    Un bouton « Lancer une partie » ferait un POST vers /lol-lobby et
    /lol-matchmaking/search : c'est l'automatisation du client que le README
    exclut, au même titre que l'auto-accept retiré précédemment.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "api" / "lcu_client.py")
    texte = src.read_text(encoding="utf-8")
    for interdit in ("lol-lobby", "matchmaking/search", "def _post", "def _put"):
        assert interdit not in texte, f"{interdit!r} : écriture vers le client Riot"


# ---------------------------------------------------- onglet Draft

@pytest.fixture(scope="module")
def draft(racine):
    from ui.champ_select_panel import ChampSelectPanel

    p = ChampSelectPanel(racine)
    p.pack(fill="both", expand=True)
    racine.update_idletasks()
    return p


def test_le_draft_demarre_en_attente(draft):
    assert draft.en_attente()
    assert draft._attente.winfo_manager() == "place"


def test_la_selection_revele_le_tableau(draft):
    draft.afficher_draft()
    draft.update()
    assert not draft.en_attente()
    assert draft._attente.winfo_manager() == ""


def test_la_fin_de_selection_revient_a_l_attente(draft):
    draft.afficher_draft()
    draft.afficher_attente()
    draft.update()
    assert draft.en_attente()
    assert draft._attente.winfo_manager() == "place"


def test_les_trois_suggestions_sont_affichees_ensemble(draft):
    """Plus de carrousel : les trois champions conseillés sont visibles d'un coup."""
    draft.afficher_draft()
    draft.set_suggestions(["Caitlyn", "Ezreal", "Sivir"], ["portée", "sûreté", "waveclear"])
    draft.update()
    noms = [nom.cget("text") for _, nom, _ in draft._sug_slots]
    assert noms == ["Caitlyn", "Ezreal", "Sivir"]
    assert draft._sug_slots_frame.winfo_manager() == "pack"


def test_la_navigation_du_carrousel_a_disparu(draft):
    for parti in ("_next_suggestion", "_prev_suggestion", "_sug_badge"):
        assert not hasattr(draft, parti), f"{parti} devrait avoir disparu"


def test_les_allies_s_affichent_sans_draft_actions(draft):
    """
    Les ennemis viennent d'enemy_champion_names, les alliés de draft_actions.
    Si ces actions manquent, la colonne alliée doit se rabattre sur la liste
    de noms plutôt que de rester vide.
    """
    from core.state_manager import ChampSelectState

    draft.afficher_draft()
    draft.update_draft(ChampSelectState(
        in_champ_select=True,
        ally_champion_names=["Ornn", "Vi", "Ahri", "Jinx", "Nautilus"],
        enemy_champion_names=["Darius", "Lee Sin", "Anivia", "Caitlyn", "Lulu"],
    ))
    draft.update()
    assert [l.cget("text") for l in draft._ally_name_labels][0] == "Ornn"
