@echo off
set PYTHONIOENCODING=utf-8
set PYTHONPATH=.
echo =======================================================
echo Lancement de LOL HELPER en mode developpement (Auto-reload)
echo =======================================================
.venv\Scripts\hupper.exe -m main
pause
