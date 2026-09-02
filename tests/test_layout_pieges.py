"""
Pièges de mise en page CustomTkinter, constatés en production.

1. Un CTkFrame vaut 200x200 par défaut. Un conteneur VIDE garde cette taille :
   il creuse un trou dans la carte qui l'accueille. Pire, combiné à
   pack_propagate(False), il fige la taille de son parent — c'est ce qui
   rendait chaque ligne de la barre latérale haute de 200 px et chassait la
   section CONNEXIONS et l'horloge hors de l'écran.

2. En pack Tk, chaque widget rogne la cavité restante. Une barre horizontale
   (side="bottom") placée APRÈS les colonnes (side="left") ne reçoit qu'une
   cavité résiduelle : la barre de statut ne mesurait que 200 px et amputait
   le contenu d'autant.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_SRC = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")


def test_les_barres_horizontales_sont_packees_avant_les_colonnes():
    ordre = [
        MAIN_SRC.index("self._build_titlebar()"),
        MAIN_SRC.index("self._build_statusbar()"),
        MAIN_SRC.index("self._build_sidebar()"),
        MAIN_SRC.index("self._build_content()"),
    ]
    assert ordre == sorted(ordre), (
        "la barre de statut doit être packée avant la barre latérale et le "
        "contenu, sinon elle n'obtient qu'une cavité résiduelle"
    )


def test_la_barre_active_de_navigation_a_une_hauteur_explicite():
    """Sans height=, pack_propagate(False) fige la ligne à 200 px."""
    bloc = MAIN_SRC[MAIN_SRC.index("active_bar = ctk.CTkFrame("):]
    bloc = bloc[: bloc.index(")")]
    assert "height=" in bloc, "active_bar sans height : la ligne de nav ferait 200 px"


def test_les_conteneurs_vides_sont_dimensionnes():
    """Un conteneur peuplé dynamiquement doit déclarer width/height."""
    cibles = {
        "ui/ingame_panel.py": [
            "_items_container", "_plan_container",
            "_legendary_frame", "_boots_frame", "_verdicts_frame",
        ],
    }
    for rel, noms in cibles.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        for nom in noms:
            m = re.search(rf"self\.{nom} = ctk\.CTkFrame\(([^)]*)\)", src)
            assert m, f"{nom} introuvable dans {rel}"
            args = m.group(1)
            assert "width=" in args and "height=" in args, (
                f"{nom} ({rel}) sans width/height : un CTkFrame vide occupe "
                f"200x200 et creuse un trou dans la carte"
            )
