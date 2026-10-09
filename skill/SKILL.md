---
name: voice
description: Control voice input and the Hey Agy wake-word companion daemon (/voice on, /voice off, /voice status, /dictate).
---

# Hey Agy - Voice Companion for Antigravity

This skill allows controlling the local hands-free voice companion daemon directly from your Antigravity chat session.

## Slash Commands

### 1. `/voice on` (Unmute Wake-Word)
Re-enables wake-word listening (*"Hey Agy"*).
```powershell
python path/to/antigravity-voice/voice_client.py on
```

### 2. `/voice off` (Mute Wake-Word)
Mutes wake-word listening for privacy during calls or meetings.
```powershell
python path/to/antigravity-voice/voice_client.py off
```

### 3. `/dictate` (One-Shot Voice Input)
Starts an immediate one-shot recording without needing to say the wake word. Transcribes via Gemini and types directly into the terminal.
```powershell
python path/to/antigravity-voice/voice_client.py dictate
```

### 4. `/voice status` (Check Status)
Reports whether the daemon is currently running, listening, or muted.
```powershell
python path/to/antigravity-voice/voice_client.py status
```

### 5. `/voice stop` or `/voice cancel` (Abort Active Task)
Sends a virtual Escape interrupt signal to the active Antigravity terminal session.
```powershell
python path/to/antigravity-voice/voice_client.py stop
```
