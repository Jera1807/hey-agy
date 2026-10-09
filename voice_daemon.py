import asyncio
import io
import json
import math
import os
import re
import sys
import threading
import time
import wave
import winsound
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import psutil
import pyautogui
import pyperclip
import sounddevice as sd
import vosk
import win32gui
import win32process
import win32event
import win32api
import winerror
from google import genai
from google.genai import types

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "gemini_api_key": "",
        "gemini_model": "models/gemini-3.5-transcribe-live",
        "gemini_fallback_model": "models/gemini-3.5-transcribe",
        "wake_word": "Hey Agy",
        "silence_duration_seconds": 1.4,
        "ipc_port": 8765,
        "auto_send": True
    }

def get_antigravity_sessions():
    """Findet alle aktiven Antigravity KI-Sessions anhand der laufenden agy-real Prozesse."""
    sessions = []
    for p in psutil.process_iter(['pid', 'name']):
        try:
            if p.info['name'] and 'agy-real' in p.info['name'].lower():
                tree_pids = {p.pid}
                curr = p
                # Eltern-Prozess-Baum ermitteln (cmd.exe -> powershell.exe -> WindowsTerminal.exe)
                while curr.parent() is not None:
                    try:
                        curr = curr.parent()
                        tree_pids.add(curr.pid)
                        if curr.name().lower() in ['windowsterminal.exe', 'explorer.exe']:
                            break
                    except Exception:
                        break
                
                try:
                    cwd = p.cwd()
                except Exception:
                    cwd = ""
                ws_name = os.path.basename(cwd) if cwd else "Antigravity CLI"

                sessions.append({
                    'pid': p.pid,
                    'workspace': ws_name,
                    'cwd': cwd,
                    'pids': tree_pids
                })
        except Exception:
            pass
    return sessions

class VoiceEngine:
    def __init__(self):
        self.config = load_config()
        self.is_muted = False
        
        # Status für Prompt-Aufnahme
        self.is_recording_prompt = False
        self.has_started_speaking = False
        self.voice_chunk_count = 0
        self.record_start_time = 0
        self.last_voice_time = 0
        self.trigger_manual_dictate = False
        self.prompt_audio_chunks = []
        self.silence_threshold = 480
        self.is_processing = False

        # Vosk Initialisierung
        print("[*] Lade Vosk Sprachmodell (KWS)...")
        vosk.SetLogLevel(-1)
        self.vosk_model = vosk.Model(lang="en-us")

        # Exakte Phrase 'Hey Agy'
        self.triggers = ["hey a g", "hey a g i"]
        grammar = json.dumps(self.triggers + ["[unk]"])
        self.recognizer = vosk.KaldiRecognizer(self.vosk_model, 16000, grammar)
        print("[+] Vosk bereit. Hört auf 'Hey Agy'.")

    def chime_wake(self):
        try:
            winsound.Beep(1200, 120)
            winsound.Beep(1600, 140)
        except Exception:
            pass

    def chime_done(self):
        try:
            winsound.Beep(1400, 110)
            winsound.Beep(1000, 110)
        except Exception:
            pass

    def chime_toggle(self, active: bool):
        try:
            if active:
                winsound.Beep(1500, 120)
            else:
                winsound.Beep(600, 220)
        except Exception:
            pass

    def check_vosk_trigger(self, audio_bytes: bytes) -> bool:
        if self.recognizer.AcceptWaveform(audio_bytes):
            res = json.loads(self.recognizer.Result())
            text = res.get("text", "").strip()
            if text in self.triggers:
                self.recognizer.Reset()
                return True
        return False

    async def transcribe_live_api(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        api_key = self.config.get("gemini_api_key")
        model_name = self.config.get("gemini_model", "models/gemini-3.5-transcribe-live")
        client = genai.Client(api_key=api_key)

        live_config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            system_instruction=types.Content(parts=[types.Part.from_text(
                text=(
                    "Du bist ein ultra-präziser Transkriptions-Assistent für einen Senior Graphics & Software Engineer. "
                    "Transkribiere das gesprochene Audio (Deutsch mit englischen Fachbegriffen) exakt in Text. "
                    "Bereinige Versprecher und Füllwörter ('ähm'). "
                    "Achte penibel auf korrekte Groß-/Kleinschreibung und Schreibweise technischer Begriffe und Frameworks "
                    "(z. B. HyperFrames, WebGPU, WGSL, TypeGPU, TypeScript, GSAP, Tailwind, React, Python). "
                    "Gib AUSSCHLIESSLICH den bereinigten Text aus."
                )
            )])
        )

        collected_text = []

        async with client.aio.live.connect(model=model_name, config=live_config) as session:
            await session.send_realtime_input(audio=types.Blob(data=audio_bytes, mime_type=f"audio/pcm;rate={sample_rate}"))
            await session.send_realtime_input(audio_stream_end=True)

            try:
                async with asyncio.timeout(4.0):
                    async for resp in session.receive():
                        sc = resp.server_content
                        if sc:
                            chunk_text = ""
                            if sc.input_transcription and hasattr(sc.input_transcription, 'text') and sc.input_transcription.text:
                                chunk_text = sc.input_transcription.text.strip()
                            elif sc.model_turn and sc.model_turn.parts:
                                for part in sc.model_turn.parts:
                                    if hasattr(part, 'text') and part.text:
                                        chunk_text += part.text.strip()
                            if chunk_text:
                                if not collected_text or collected_text[-1] != chunk_text:
                                    collected_text.append(chunk_text)
                            if sc.turn_complete or sc.generation_complete:
                                break
            except asyncio.TimeoutError:
                pass

        return " ".join(collected_text).strip()

    def transcribe_fallback(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        api_key = self.config.get("gemini_api_key")
        fallback_model = self.config.get("gemini_fallback_model", "models/gemini-3.5-transcribe")
        client = genai.Client(api_key=api_key)

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_data.tobytes())
        wav_bytes = buf.getvalue()

        response = client.models.generate_content(
            model=fallback_model,
            contents=[
                types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                "Transkribiere dieses Audio präzise für eine Entwickler-Eingabe (HyperFrames, WebGPU, TypeScript). Gib nur den bereinigten Text zurück."
            ]
        )

        parts = []
        if response.candidates:
            for c in response.candidates:
                if c.content and c.content.parts:
                    parts = [p.text for p in c.content.parts if hasattr(p, 'text') and p.text]
        return " ".join(parts).strip() if parts else (response.text.strip() if response.text else "")

    def process_and_type(self, full_audio: np.ndarray, sample_rate: int = 16000):
        if self.is_processing:
            print("[*] Eine vorherige Eingabe wird noch verarbeitet. Ignoriere Doppel-Trigger.")
            return

        self.is_processing = True
        try:
            print(f"[*] Verarbeite Prompt mit {self.config.get('gemini_model')} (Live-API)...")
            raw_pcm = full_audio.tobytes()
            transcribed_text = ""

            try:
                transcribed_text = asyncio.run(self.transcribe_live_api(raw_pcm, sample_rate))
            except Exception as e:
                print(f"[!] Live-API Fehler: {e}. Versuche Fallback...")

            if not transcribed_text:
                print("[*] Nutze Fallback-Modell (Gemini 3.5 Transcribe)...")
                try:
                    transcribed_text = self.transcribe_fallback(full_audio, sample_rate)
                except Exception as e:
                    print(f"[!] Fallback-Fehler: {e}")

            if not transcribed_text:
                print("[!] Keine Sprache im Prompt erkannt.")
                return

            print(f"\n[+] Bereinigter Prompt: \"{transcribed_text}\"\n")

            # 1. Antigravity KI-Sessions ermitteln (direkt über agy-real Prozessbaum!)
            sessions = get_antigravity_sessions()
            target_window = None

            if sessions:
                # Wähle die primäre / aktivste Session
                target_session = sessions[0]
                print(f"[*] Aktive KI-Session adressiert: Workspace \"{target_session['workspace']}\" (PID: {target_session['pid']})")

                # Fenster der Session finden
                try:
                    import pygetwindow as gw
                    for w in gw.getAllWindows():
                        # Ignoriere unser eigenes Voice-Daemon-Fenster
                        if "voice daemon" in w.title.lower() or "start_voice" in w.title.lower():
                            continue
                        try:
                            _, w_pid = win32process.GetWindowThreadProcessId(w._hWnd)
                            if w_pid in target_session['pids']:
                                target_window = w
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

            if target_window:
                print(f"[*] Aktiviere Terminal-Fenster: \"{target_window.title}\"")
                try:
                    pyautogui.press('alt')
                    time.sleep(0.04)
                    target_window.activate()
                    time.sleep(0.12)
                except Exception:
                    pass
            else:
                print("[*] Sende Prompt an aktives Eingabefeld.")

            # Text direkt in das Terminal einfügen und absenden
            pyperclip.copy(transcribed_text)
            time.sleep(0.08)
            pyautogui.keyDown('ctrl')
            pyautogui.press('v')
            pyautogui.keyUp('ctrl')

            if self.config.get("auto_send", True):
                time.sleep(0.12)
                pyautogui.press('enter')
        finally:
            self.is_processing = False

    def run(self):
        sample_rate = 16000
        chunk_size = 800  # 50ms Chunks
        silence_limit = self.config.get("silence_duration_seconds", 1.4)

        print(f"\n=======================================================")
        print(f" Antigravity Voice Companion Daemon AKTIV")
        print(f" • Wake Word: 'Hey Agy' (Vosk KWS)")
        print(f" • Transkription: Gemini 3.5 Transcribe Live (Option B)")
        print(f" • Mute Status: {'Stummgeschaltet' if self.is_muted else 'Lauscht'}")
        print(f" • Steuerung im Chat: /voice on | /voice off | /dictate")
        print(f"=======================================================")

        # Erkannte Antigravity-Sessions anzeigen
        sessions = get_antigravity_sessions()
        if sessions:
            print(f"[+] {len(sessions)} aktive Antigravity KI-Session(s) gefunden:")
            for i, s in enumerate(sessions, 1):
                print(f"    [{i}] Workspace: \"{s['workspace']}\" (PID {s['pid']})")
        else:
            print("[!] Keine laufende Antigravity-Session gefunden. Starte zuerst 'agy'.")
        print("=======================================================\n")

        with sd.InputStream(samplerate=sample_rate, channels=1, dtype='int16', blocksize=chunk_size) as stream:
            while True:
                if self.trigger_manual_dictate:
                    self.trigger_manual_dictate = False
                    self.is_recording_prompt = True
                    self.has_started_speaking = True
                    self.voice_chunk_count = 0
                    self.prompt_audio_chunks = []
                    self.last_voice_time = time.time()
                    self.record_start_time = time.time()
                    self.chime_wake()
                    time.sleep(0.3)
                    print("[*] Manueller Diktat-Modus aktiv. Sprich jetzt...")

                data, _ = stream.read(chunk_size)
                audio_chunk = np.frombuffer(data, dtype=np.int16)
                audio_bytes = data.tobytes()
                rms = np.sqrt(np.mean(audio_chunk.astype(np.float64)**2))

                if self.is_recording_prompt:
                    self.prompt_audio_chunks.append(audio_chunk)

                    if not self.has_started_speaking:
                        if rms > self.silence_threshold:
                            self.voice_chunk_count += 1
                            if self.voice_chunk_count >= 3:
                                self.has_started_speaking = True
                                self.last_voice_time = time.time()
                        else:
                            self.voice_chunk_count = 0

                        if (time.time() - self.record_start_time) > 5.0:
                            print("[*] Timeout: Kein Prompt nach Wake-Word gesprochen.")
                            self.is_recording_prompt = False
                            self.prompt_audio_chunks = []
                    else:
                        if rms > self.silence_threshold:
                            self.last_voice_time = time.time()

                        if (time.time() - self.last_voice_time) > silence_limit and len(self.prompt_audio_chunks) > 20:
                            self.is_recording_prompt = False
                            self.chime_done()
                            print("[*] Aufnahme beendet. Streame an Gemini 3.5 Transcribe Live...")
                            full_audio = np.concatenate(self.prompt_audio_chunks)
                            self.prompt_audio_chunks = []
                            threading.Thread(target=self.process_and_type, args=(full_audio, sample_rate), daemon=True).start()

                elif not self.is_muted:
                    if self.check_vosk_trigger(audio_bytes):
                        print("[+] 'Hey Agy' erkannt! Öffne Mikrofon für Prompt...")
                        self.chime_wake()
                        time.sleep(0.3)
                        self.is_recording_prompt = True
                        self.has_started_speaking = False
                        self.voice_chunk_count = 0
                        self.prompt_audio_chunks = []
                        self.record_start_time = time.time()
                        self.last_voice_time = time.time()

                time.sleep(0.001)

# Lokale HTTP Schnittstelle
class VoiceHTTPHandler(BaseHTTPRequestHandler):
    engine: VoiceEngine = None

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        path = self.path.lower()
        response_data = {"status": "ok"}

        if "/mute" in path or "/off" in path:
            self.engine.is_muted = True
            self.engine.chime_toggle(False)
            response_data = {"status": "muted", "is_muted": True}
            print("[*] Wake-Word 'Hey Agy' stummgeschaltet (MUTED).")

        elif "/unmute" in path or "/on" in path:
            self.engine.is_muted = False
            self.engine.chime_toggle(True)
            response_data = {"status": "active", "is_muted": False}
            print("[*] Wake-Word 'Hey Agy' aktiviert (ACTIVE).")

        elif "/dictate" in path or "/listen" in path:
            self.engine.trigger_manual_dictate = True
            response_data = {"status": "listening"}

        elif "/status" in path:
            response_data = {
                "status": "recording" if self.engine.is_recording_prompt else ("muted" if self.engine.is_muted else "listening"),
                "is_muted": self.engine.is_muted
            }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response_data).encode("utf-8"))

def start_daemon():
    # 1. Single-Instance Mutex (Verhindert doppelt geöffnete Fenster / Instanzen)
    mutex_name = "Global\\AntigravityVoiceDaemonMutex"
    mutex = win32event.CreateMutex(None, False, mutex_name)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        print("\n" + "=" * 60)
        print(" [!] ACHTUNG: Antigravity Voice Daemon läuft bereits!")
        print("     Es ist bereits eine Instanz / ein Fenster geöffnet.")
        print("     Dieses zweite Fenster wird beendet, um doppelte")
        print("     Spracheingaben im Terminal zu verhindern.")
        print("=" * 60 + "\n")
        time.sleep(3.5)
        sys.exit(1)

    engine = VoiceEngine()
    VoiceHTTPHandler.engine = engine

    port = engine.config.get("ipc_port", 8765)
    try:
        server = HTTPServer(("127.0.0.1", port), VoiceHTTPHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        print(f"[+] Steuerungs-Schnittstelle aktiv auf http://127.0.0.1:{port}")
    except Exception as e:
        print(f"[!] Hinweis zur Schnittstelle auf Port {port}: {e}")

    engine.run()

if __name__ == "__main__":
    start_daemon()
