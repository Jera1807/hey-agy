---
name: voice
description: Control the Hey Agy voice companion daemon - mute/unmute the wake word, start one-shot dictation, abort a running task, or check status (/voice on, /voice off, /voice stop, /voice status, /dictate).
---

# Hey Agy - Voice Companion for Antigravity

This skill controls the local hands-free voice companion daemon from inside your Antigravity chat session.
The daemon must be running (`start_voice.bat`).

> `setup.bat` fills in the install folder of this repository in the commands below.
> If you installed the skill manually, replace the angle-bracket placeholder with that folder yourself.

## Commands

When the user asks for one of the following (for example by typing `/voice off`), run the matching command:

### `/voice on` - enable the wake word
Re-enables listening for *"Hey Agy"*.
```powershell
python "<INSTALL_DIR>\voice_client.py" on
```

### `/voice off` - mute the wake word
Mutes wake-word listening, e.g. during calls or meetings.
```powershell
python "<INSTALL_DIR>\voice_client.py" off
```

### `/dictate` or `/voice dictate` - one-shot dictation
Starts a single recording immediately without the wake word. The speech is transcribed with Gemini and typed into the terminal.
```powershell
python "<INSTALL_DIR>\voice_client.py" dictate
```

### `/voice status` - check status
Reports whether the daemon is listening, recording, or muted.
```powershell
python "<INSTALL_DIR>\voice_client.py" status
```

### `/voice stop` or `/voice cancel` - abort the running task
Sends a virtual `Escape` key press to the active Antigravity terminal session to interrupt the current generation or command.
```powershell
python "<INSTALL_DIR>\voice_client.py" stop
```

## Notes

- If the client reports that the daemon is not running, tell the user to start `start_voice.bat`.
- The client only uses the Python standard library, so any Python 3.10+ works.
