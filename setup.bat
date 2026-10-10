@echo off
setlocal enabledelayedexpansion
title Hey Agy - Setup
cd /d "%~dp0"

echo =======================================================
echo   Hey Agy - 1-Click Installer
echo =======================================================
echo.

:: 1. Check that Python is available
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] ERROR: Python was not found in PATH!
    echo     Please install Python 3.10 or newer from https://www.python.org/
    echo     and enable "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [*] Python found.
echo.

:: 2. Create a virtual environment if it does not exist
if not exist "venv" (
    echo [*] Creating isolated Python environment ^(venv^)...
    python -m venv venv
    if %ERRORLEVEL% NEQ 0 (
        echo [!] ERROR: could not create the venv!
        pause
        exit /b 1
    )
    echo [+] Virtual environment 'venv' created.
) else (
    echo [*] Virtual environment 'venv' already exists.
)
echo.

:: 3. Install dependencies
echo [*] Installing dependencies ^(sounddevice, vosk, google-genai, pywin32, pycaw ...^)...
call venv\Scripts\python.exe -m pip install --upgrade pip
call venv\Scripts\python.exe -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [!] ERROR: dependency installation failed!
    pause
    exit /b 1
)
echo [+] All packages installed.
echo.

:: 4. Configure the API key
if not exist "config.json" (
    echo =======================================================
    echo   Configuration
    echo =======================================================
    echo To use Gemini 3.5 Transcribe Live you need a free Gemini API key.
    echo Get one at: https://aistudio.google.com/app/apikey
    echo.
    set /p APIKEY="Paste your Gemini API key (or press Enter to add it later): "

    if "!APIKEY!"=="" (
        copy config.example.json config.json >nul
        echo [!] config.json was created from the template.
        echo     Add your API key to config.json before starting the daemon.
    ) else (
        powershell -NoProfile -Command "$cfg = Get-Content config.example.json -Raw | ConvertFrom-Json; $cfg.gemini_api_key = '!APIKEY!'; $cfg | ConvertTo-Json -Depth 5 | Set-Content config.json -Encoding UTF8"
        echo [+] config.json saved with your API key.
    )
) else (
    echo [*] config.json already exists.
)
echo.

:: 5. Optional: install the /voice skill for Google Antigravity
set "SKILL_DIR=%USERPROFILE%\.agents\skills\voice"
set /p INSTALLSKILL="Install the /voice skill for Antigravity into %SKILL_DIR%? [Y/n]: "
if /I not "!INSTALLSKILL!"=="n" (
    call venv\Scripts\python.exe voice_daemon.py --install-skill
)

echo.

:: 6. Optional: start automatically at login (hidden, with tray icon)
set /p AUTOSTART="Start Hey Agy automatically when you log in to Windows? [Y/n]: "
if /I not "!AUTOSTART!"=="n" (
    call venv\Scripts\python.exe voice_daemon.py --install-autostart
)

echo.
echo =======================================================
echo   Installation complete!
echo   Run 'start_voice.bat' to start it now in a console window,
echo   or log out and in again to use the hidden autostart.
echo   Remove autostart any time with 'uninstall.bat'.
echo =======================================================
echo.
pause
