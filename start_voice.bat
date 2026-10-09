@echo off
title Antigravity Voice Daemon
cd /d "%~dp0"

echo =======================================================
echo   Antigravity Voice Companion Daemon
echo   - Wake Word: Hey Agy (Local Vosk Grammar-Lock)
echo   - Transcribe: Gemini 3.5 Transcribe Live (Option B)
echo   - IPC Server: http://127.0.0.1:8765
echo =======================================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe voice_daemon.py
) else (
    python voice_daemon.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Der Prozess wurde mit einem Fehler oder Warnung beendet.
    pause
)
