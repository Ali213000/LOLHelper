@echo off
REM ============================================================
REM  LoL Coaching Assistant — One-Click Setup
REM  Run this ONCE to create the virtual environment and install
REM  all dependencies including the CUDA-enabled PyTorch build.
REM ============================================================

setlocal

echo.
echo  ============================================================
echo   LoL Coaching Assistant - Setup
echo  ============================================================
echo.

REM ============================================================
REM  Find a compatible Python (3.12 or 3.11 required for PyTorch)
REM  PyTorch does not yet ship Windows wheels for Python 3.13+
REM ============================================================

set PYTHON_EXE=

REM Try py launcher for 3.12 first, then 3.11
py -3.12 --version >NUL 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_EXE=py -3.12
    echo [OK] Found Python 3.12 via py launcher.
    goto :found_python
)

py -3.11 --version >NUL 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_EXE=py -3.11
    echo [OK] Found Python 3.11 via py launcher.
    goto :found_python
)

REM 2. Direct path fallback (catches winget installs in the same terminal session)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
    echo [OK] Found Python 3.12 at %LOCALAPPDATA%\Programs\Python\Python312\
    goto :found_python
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
    echo [OK] Found Python 3.11 at %LOCALAPPDATA%\Programs\Python\Python311\
    goto :found_python
)
if exist "C:\Python312\python.exe" (
    set PYTHON_EXE=C:\Python312\python.exe
    echo [OK] Found Python 3.12 at C:\Python312\
    goto :found_python
)

REM 3. Not found — install via winget (Windows 10/11 built-in)
echo [INFO] Python 3.11/3.12 not found. Attempting to install Python 3.12 via winget...
winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Could not auto-install Python 3.12.
    echo.
    echo Please install Python 3.12 manually, then re-run setup.bat:
    echo   https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
    echo.
    echo Why not 3.13? PyTorch does not yet ship Windows wheels for Python 3.13+.
    pause & exit /b 1
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
    echo [OK] Python 3.12 installed successfully.
    goto :found_python
)
echo [WARNING] Python 3.12 installed but could not locate executable.
echo Please close this terminal, open a new one, and run setup.bat again.
pause & exit /b 1

:found_python
echo [INFO] Using: %PYTHON_EXE%

REM Create virtual environment using the compatible Python
echo [1/6] Creating virtual environment (using %PYTHON_EXE%)...
%PYTHON_EXE% -m venv .venv
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to create virtual environment.
    pause & exit /b 1
)

REM Activate
call .venv\Scripts\activate.bat

REM Upgrade pip
echo [2/6] Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet

REM Install PyTorch with CUDA support
REM RTX 5070 Ti (Blackwell sm_120) requires cu128 nightly build.
REM Stable cu121 works but shows a compatibility warning.
echo [3/6] Installing PyTorch (CUDA 12.8 nightly for RTX 5070 Ti Blackwell)...
.venv\Scripts\pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128 --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] cu128 nightly unavailable. Trying stable cu121...
    .venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121 --quiet
    if %ERRORLEVEL% NEQ 0 (
        echo [WARNING] CUDA install failed. Falling back to CPU build.
        .venv\Scripts\pip install torch torchvision --quiet
    )
)

REM Install all other dependencies
echo [4/6] Installing application dependencies...
pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Dependency installation failed. Check requirements.txt.
    pause & exit /b 1
)

REM Download Data Dragon champion + item data
echo [5/6] Downloading League champion data...
.venv\Scripts\python.exe download_assets.py

REM Verify GPU
echo [6/6] Verifying CUDA availability...
.venv\Scripts\python.exe smoke_test.py

echo.
echo  ============================================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Run the app:      .venv\Scripts\python.exe main.py
echo   2. Open Settings tab and enter your Gemini API key
echo      (get one free at: https://aistudio.google.com/apikey)
echo   3. Optionally add your Riot API key for enhanced features
echo      (https://developer.riotgames.com/)
echo  ============================================================
echo.
pause
