# 🎙️ Hey Agy — Hands-Free Voice Companion for Google Antigravity

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Gemini 3.5 Live](https://img.shields.io/badge/Gemini%203.5-Transcribe%20Live-brightgreen.svg)](https://aistudio.google.com/)
[![Vosk KWS](https://img.shields.io/badge/Vosk-Grammar--Lock%20KWS-orange.svg)](https://alphacephei.com/vosk/)
[![Platform Windows](https://img.shields.io/badge/platform-Windows%20Terminal-lightgrey.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **"Hey Siri" for your AI Pair Programmer.**  
> Activate hands-free with **"Hey Agy"**, speak your prompt naturally, and watch it land directly inside your active Google Antigravity CLI session with ultra-low latency.

---

## ✨ Features

- **🗣️ Local, Free & Open-Source Wake Word ("Hey Agy"):**  
  Powered by an offline Vosk/Kaldi phoneme engine with strict **Grammar-Lock**. Zero account signups, zero paid subscriptions, and zero false positives during everyday German or English office conversations.
- **🛑 Hands-Free Stop / Abort ("Stop Agy" / "Cancel"):**  
  Need to interrupt a running code generation or command immediately? Just say *"Stop Agy"* or *"Hey Agy Stop"* – the daemon sends an instant virtual `Ctrl+C` interrupt signal directly to the session terminal.
- **⚡ Ultra-Low-Latency Transcription:**  
  Streams microphone audio over WebSockets directly to Google's **Gemini 3.5 Transcribe Live** (`models/gemini-3.5-transcribe-live`). Transcriptions complete in milliseconds with automatic developer-context cleaning.
- **🧠 Smart Window Focus Memory:**  
  Working across multiple monitors or several Antigravity sessions? The daemon locks onto the exact terminal window you were interacting with right when you started speaking.
- **🔔 Studio-Quality Audio Chimes:**  
  Elegant, warm harmonic sine chimes (gentle wake, done, cancel, and toggle sounds) with zero latency via asynchronous Windows audio.
- **🔒 Single-Instance Protection:**  
  Built-in Windows Mutex prevents running multiple instances simultaneously, avoiding accidental double inputs.
- **💬 In-Chat Slash Commands:**  
  Manage your companion directly from Antigravity with `/voice stop` (abort task), `/voice off` (mute for calls), `/voice on`, `/dictate` (one-shot recording), and `/voice status`.
- **🛡️ Privacy First:**  
  The wake-word detector runs 100% offline on your local CPU. Audio is only sent to Google Gemini *after* the wake-phrase is positively identified.

---

## 🏗️ Architecture

```
                       [ Microphone Audio ]
                                |
                                v
               +----------------------------------+
               |  Vosk KWS (Offline / Grammar-Lock) |  <-- 0% Cloud, 0% Cost
               +----------------------------------+
                                |
                    [ "Hey Agy" Recognized ]
                                |
                                v
               +----------------------------------+
               |   Audio Capture & Silence VAD    |
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |  Gemini 3.5 Transcribe Live API   |  <-- Ultra-low latency
               +----------------------------------+
                                |
                       [ Cleaned Text ]
                                |
                                v
               +----------------------------------+
               |   Antigravity Process-Tree Hook   |
               |   (Windows Terminal / agy-real)   |
               +----------------------------------+
```

---

## 🚀 Quickstart (Windows)

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/hey-agy.git
cd hey-agy
```

### 2. Run the 1-Click Installer
Double-click `setup.bat` or run in terminal:
```cmd
setup.bat
```
*The installer automatically creates a virtual environment (`venv`), installs dependencies, and prompts for your free [Google Gemini API Key](https://aistudio.google.com/app/apikey).*

### 3. Launch Voice Companion
Double-click `start_voice.bat`:
```cmd
start_voice.bat
```

### 4. Talk to Antigravity!
Say:  
> **"Hey Agy"** *(pronounced /heɪ eɪ-dʒiː/)*  
> 🔔 *(Wake Chime sounds)*  
> **"Refaktoriere die Shader-Pipeline in WebGPU und führe die Tests aus."**  
> 🔕 *(Done Chime sounds)*  
> 🚀 *Prompt is pasted and submitted directly into Antigravity!*

---

## ⚙️ Configuration (`config.json`)

You can customize thresholds and models in `config.json`:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "gemini_model": "models/gemini-3.5-transcribe-live",
  "gemini_fallback_model": "models/gemini-3.5-transcribe",
  "wake_word": "Hey Agy",
  "silence_duration_seconds": 1.4,
  "ipc_port": 8765,
  "auto_send": true
}
```

- `silence_duration_seconds`: How long you pause speaking before the prompt is automatically sent (default: `1.4s`).
- `auto_send`: Automatically presses `Enter` after typing the prompt (`true` or `false`).
- `ipc_port`: Local HTTP port used by Antigravity chat slash commands (default: `8765`).

---

## 💬 In-Chat Slash Commands

Install the companion skill into your Antigravity agent by copying `skill/SKILL.md` to:
`~/.agents/skills/voice/SKILL.md`

You can then control the daemon without leaving your Antigravity session:

| Slash Command | Action |
| :--- | :--- |
| `/voice stop` | Sends Ctrl+C interrupt signal to cancel the running task/generation. |
| `/voice off` | Mutes wake-word listening (ideal for team calls or YouTube videos). |
| `/voice on` | Resumes active listening for *"Hey Agy"*. |
| `/dictate` | Triggers a one-shot recording immediately without saying the wake word. |
| `/voice status` | Shows whether the daemon is active, listening, or muted. |

---

## 🛠️ Requirements

- Windows 10 / 11
- Python 3.10+
- Free Google AI Studio API Key ([Get one here](https://aistudio.google.com/app/apikey))
- Microphone

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
