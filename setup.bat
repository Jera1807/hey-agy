@echo off
setlocal enabledelayedexpansion
title Antigravity Voice Companion - Setup

echo =======================================================
echo   Antigravity Voice Companion - 1-Click Installer
echo =======================================================
echo.

:: 1. Pruefen ob Python vorhanden ist
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] FEHLER: Python wurde nicht im PATH gefunden!
    echo     Bitte installiere Python (3.10 oder neuer) von https://www.python.org/
    echo     und aktiviere bei der Installation "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo [*] Python gefunden.
echo.

:: 2. Virtual Environment anlegen, falls nicht vorhanden
if not exist "venv" (
    echo [*] Erstelle isolierte Python-Umgebung (venv)...
    python -m venv venv
    if %ERRORLEVEL% NEQ 0 (
        echo [!] FEHLER beim Erstellen des venv!
        pause
        exit /b 1
    )
    echo [+] Virtual Environment 'venv' erfolgreich erstellt.
) else (
    echo [*] Virtual Environment 'venv' existiert bereits.
)
echo.

:: 3. Abhaengigkeiten installieren
echo [*] Installiere notwendige Python-Pakete (sounddevice, vosk, google-genai, pywin32)...
call venv\Scripts\python.exe -m pip install --upgrade pip
call venv\Scripts\python.exe -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [!] FEHLER beim Installieren der Abhaengigkeiten!
    pause
    exit /b 1
)
echo [+] Alle Pakete erfolgreich installiert.
echo.

:: 4. Konfiguration einrichten
if not exist "config.json" (
    echo =======================================================
    echo   Konfigurations-Assistent
    echo =======================================================
    echo Um die Gemini 3.5 Live-Transkription zu nutzen,
    echo benoetigst du einen kostenlosen Gemini API Key.
    echo Falls du noch keinen hast: https://aistudio.google.com/app/apikey
    echo.
    set /p APIKEY="Bitte deinen Gemini API Key eingeben (oder Enter fuer spaeter): "
    
    if "!APIKEY!"=="" (
        copy config.example.json config.json >nul
        echo [!] config.json wurde als Vorlage erstellt.
        echo     Trage deinen API-Key bitte spaeter manuell in config.json ein.
    ) else (
        powershell -Command "$cfg = Get-Content config.example.json -Raw | ConvertFrom-Json; $cfg.gemini_api_key = '!APIKEY!'; $cfg | ConvertTo-Json -Depth 5 | Set-Content config.json -Encoding UTF8"
        echo [+] config.json erfolgreich mit deinem API-Key gespeichert!
    )
) else (
    echo [*] config.json ist bereits vorhanden.
)

echo.
echo =======================================================
echo   Installation abgeschlossen!
echo   Starte jetzt 'start_voice.bat', um den Voice-Companion
echo   zu aktivieren.
echo =======================================================
echo.
pause
