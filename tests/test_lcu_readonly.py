"""
Le client LCU doit rester en LECTURE SEULE.

Accepter une file, hover ou lock un champion à la place du joueur relève de
l'automatisation du client Riot, ce que le README exclut explicitement.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "api" / "lcu_client.py").read_text(encoding="utf-8")


def test_aucune_methode_d_ecriture_http():
    for verb in ("post", "patch", "put", "delete"):
        assert not re.search(rf"self\._client\.{verb}\(", SRC), \
            f"lcu_client.py fait un {verb.upper()} — le client doit rester en lecture seule"


def test_pas_d_auto_accept_dans_le_projet():
    for path in ROOT.rglob("*.py"):
        if any(part in path.parts for part in (".venv", "__pycache__", "scratch", "tests")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "ready-check/accept" not in text, f"auto-accept réintroduit dans {path.name}"
        assert "AUTO_ACCEPT" not in text, f"AUTO_ACCEPT réintroduit dans {path.name}"
