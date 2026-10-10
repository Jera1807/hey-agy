@echo off
title Hey Agy - Voice Daemon
cd /d "%~dp0"

echo =======================================================
echo   Hey Agy - Voice Companion for Google Antigravity
echo   - Wake word:     Hey Agy (offline Vosk spotting)
echo   - Transcription: Gemini 3.5 Transcribe Live
echo   - Control API:   http://127.0.0.1:8765
echo =======================================================
echo.

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe voice_daemon.py
) else (
    python voice_daemon.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] The daemon exited with an error or warning.
    pause
)
