@echo off
setlocal enabledelayedexpansion
title Hey Agy - Uninstall
cd /d "%~dp0"

echo =======================================================
echo   Hey Agy - Uninstall helper
echo =======================================================
echo.

if exist "venv\Scripts\python.exe" (set "PY=venv\Scripts\python.exe") else (set "PY=python")

echo [*] Removing the autostart entry...
%PY% voice_daemon.py --uninstall-autostart

echo [*] Stopping a running background daemon (if any)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'voice_daemon\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

set "SKILL_DIR=%USERPROFILE%\.agents\skills\voice"
if exist "%SKILL_DIR%\SKILL.md" (
    set /p RMSKILL="Also remove the /voice skill from %SKILL_DIR%? [y/N]: "
    if /I "!RMSKILL!"=="y" (
        rmdir /s /q "%SKILL_DIR%"
        echo [+] Skill removed.
    )
)

echo.
echo Done. You can now delete this folder if you no longer need it.
echo.
pause
