# AGENTS.md — instructions for AI coding agents

This file tells an AI agent (Google Antigravity, Claude Code, Cursor, Codex, ...) how to install, configure and extend **Hey Agy**.
Humans: see [README.md](README.md).

**What it is:** a Windows daemon that listens for a wake word ("Hey Agy"), transcribes the following speech with Gemini 3.5 Transcribe Live, and pastes it into the AI agent window the user was using.

**Platform:** Windows 10/11 only. Python 3.10+. Do not try to run it on macOS/Linux.

## Rules for agents

- **Never invent or hard-code an API key.** Ask the user for their free Gemini API key (https://aistudio.google.com/app/apikey) and write it only to `config.json`. `config.json` is git-ignored; never commit it or print the key.
- Ask before enabling autostart (`--install-autostart`) — it registers a scheduled task.
- The daemon types into other windows. Do not start it while a destructive command is waiting for input.
- Do not run `voice_daemon.py` in the foreground of your own shell (it never exits). Use `start_voice.bat`, or `--background`, and check `logs/daemon.log`.

## Install (non-interactive)

Run from the repository root in `cmd`/PowerShell:

```cmd
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.json config.json
```

Then ask the user for their Gemini API key and set `"gemini_api_key"` in `config.json`.

Optional steps (ask the user first):

```cmd
venv\Scripts\python.exe voice_daemon.py --install-skill        :: lets Antigravity run /voice commands
venv\Scripts\python.exe voice_daemon.py --install-autostart    :: hidden start at Windows login
```

(`setup.bat` does the same interactively; prefer the commands above when you cannot answer prompts.)

## Start and verify

```cmd
venv\Scripts\python.exe voice_daemon.py --background   :: hidden, logs to logs\daemon.log
venv\Scripts\python.exe voice_client.py status         :: expect: "Status: active ..."
```

- The first start downloads a small Vosk model (~50 MB). Wait for `Vosk ready` in `logs\daemon.log`.
- If the log says the microphone only delivers silence, run `venv\Scripts\python.exe voice_daemon.py --find-mic` while the user speaks, then set `input_device` in `config.json`.
- Stop: `voice_client.py stop` aborts the target agent's current task; end the daemon from the tray icon or `uninstall.bat`.

## Change the wake word

Edit `config.json`, then restart the daemon.

```json
{ "wake_phrases": ["hey claude", "okay computer"] }
```

- Replaces the default "Hey Agy". Stop phrases are derived automatically (`hey claude stop`, `stop claude`, ...). Add custom ones with `"stop_phrases": [...]`.
- **Every word must exist in the Vosk English lexicon.** Unknown words (e.g. `windsurf`) are skipped with a warning in the log. Common words work: `claude`, `cursor`, `computer`, `jarvis`, `agent`, `codex`, `gemini`, `zed`.
- Use 2+ syllables and prefer a leading `hey`/`okay`; very short or common words cause false triggers.
- To keep "Hey Agy" and just add spellings use `"wake_extra_names": ["a gee"]` (added as `hey a gee`).
- Verify by saying the phrase and checking `logs\daemon.log` for `detected`.

## Set up other AI agents / tools

Hey Agy needs no plugin: it pastes into the window that was focused when the wake word was spoken. Configure in `config.json`:

| Goal | Setting |
| :--- | :--- |
| Agent runs in a known app (Windows Terminal, Cursor, VS Code, Windsurf, Zed, Claude desktop) | default `"target_mode": "auto"` — nothing to do |
| Agent app is not recognised | add its process name (lower-case, e.g. `"myagent.exe"`) to `"target_apps"`, or set `"target_mode": "focused"` |
| Only Antigravity | `"target_mode": "antigravity"` |
| "Stop" does not interrupt your agent | set `"stop_key"` to the agent's interrupt shortcut, e.g. `"escape"`, `"ctrl+c"` |
| Prompt should be pasted but not sent | `"auto_send": false` |

Find a window's process name: `powershell -Command "Get-Process | Where-Object MainWindowTitle | Select Name,MainWindowTitle"` (add `.exe`).

To let an agent control the daemon (mute, dictate, status), call the client:

```cmd
python voice_client.py [on|off|dictate|stop|status]
```

- **Antigravity:** `--install-skill` installs `~/.agents/skills/voice/SKILL.md` so the agent can run these commands.
- **Other agents:** add a rule/instruction to the agent's own config (`CLAUDE.md`, `.cursor/rules`, ...) such as: *"To mute or unmute voice input run `python <repo>\voice_client.py off` / `on`."*

## Files

| File | Purpose |
| :--- | :--- |
| `voice_daemon.py` | the daemon (wake word, streaming transcription, paste, tray, autostart) |
| `voice_client.py` | tiny CLI that talks to the daemon over `127.0.0.1` |
| `config.example.json` | all settings with defaults (copy to `config.json`) |
| `skill/SKILL.md` | Antigravity skill template |
| `setup.bat` / `uninstall.bat` / `start_voice.bat` | interactive installer / remover / console launcher |

## Contributing

- Windows-only APIs (`win32*`, `winsound`, scheduled tasks) are used on purpose; keep changes Windows-compatible.
- Keep all user-facing output in English.
- After code changes run `python -m py_compile voice_daemon.py voice_client.py`.
