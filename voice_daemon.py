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

SOUNDS_DIR = os.path.join(os.path.dirname(__file__), "sounds")

def ensure_sound_effects():
    """Erstellt hochwertige WAV-Chimes (harmonische Sinus-Wellen mit exponentiellem Decay), falls nicht vorhanden."""
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    def make_chime(filename, freqs, durations, sample_rate=44100):
        path = os.path.join(SOUNDS_DIR, filename)
        if os.path.exists(path):
            return path
        audio = []
        for f, d in zip(freqs, durations):
            n = int(sample_rate * d)
            t = np.linspace(0, d, n, False)
            wave_data = np.sin(2 * np.pi * f * t) + 0.2 * np.sin(4 * np.pi * f * t)
            attack = int(sample_rate * 0.01)
            decay = n - attack
            env = np.ones(n)
            if attack > 0:
                env[:attack] = np.linspace(0, 1, attack)
            env[attack:] = np.exp(-3.5 * np.linspace(0, 1, decay))
            audio.append(wave_data * env)
        full = np.concatenate(audio)
        full = (full / np.max(np.abs(full)) * 28000).astype(np.int16)
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(full.tobytes())
        return path

    return {
        "wake": make_chime('wake.wav', [587.33, 880.0], [0.08, 0.15]),
        "done": make_chime('done.wav', [880.0, 1174.66], [0.06, 0.14]),
        "cancel": make_chime('cancel.wav', [659.25, 440.0], [0.07, 0.13]),
        "toggle": make_chime('toggle.wav', [987.77], [0.09]),
    }

def play_chime(wav_path: str):
    """Spielt einen Audio-Chime asynchron über das Windows-Audiosystem ab (Null Latenz)."""
    if wav_path and os.path.exists(wav_path):
        try:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        except Exception:
            pass

def is_daemon_window(w_hwnd: int, title: str = "") -> bool:
    """Prüft, ob ein Fenster zu unserem eigenen Voice-Daemon gehört."""
    title_lower = (title or win32gui.GetWindowText(w_hwnd)).lower()
    if any(k in title_lower for k in ["voice daemon", "start_voice", "voice_daemon"]):
        return True
    try:
        _, pid = win32process.GetWindowThreadProcessId(w_hwnd)
        my_proc = psutil.Process(os.getpid())
        if pid == my_proc.pid:
            return True
        if my_proc.parent() and pid == my_proc.parent().pid:
            return True
    except Exception:
        pass
    return False

class VoiceEngine:
    def __init__(self):
        self.config = load_config()
        self.is_muted = False
        self.sounds = ensure_sound_effects()
        
        # Status für Prompt-Aufnahme & Focus Memory
        self.is_recording_prompt = False
        self.has_started_speaking = False
        self.voice_chunk_count = 0
        self.record_start_time = 0
        self.last_voice_time = 0
        self.trigger_manual_dictate = False
        self.prompt_audio_chunks = []
        self.silence_threshold = 480
        self.is_processing = False
        self.focused_session_window = None

        # Vosk Initialisierung
        print("[*] Lade Vosk Sprachmodell (KWS)...")
        vosk.SetLogLevel(-1)
        self.vosk_model = vosk.Model(lang="en-us")

        # Exakte Phrasen für Wake Word und Abort (Hands-Free Stop)
        self.wake_triggers = ["hey a g", "hey a g i"]
        self.stop_triggers = ["hey a g stop", "stop a g", "hey a g cancel", "cancel a g"]
        self.all_triggers = self.wake_triggers + self.stop_triggers
        grammar = json.dumps(self.all_triggers + ["[unk]"])
        self.recognizer = vosk.KaldiRecognizer(self.vosk_model, 16000, grammar)
        print("[+] Vosk bereit. Hört auf 'Hey Agy' und 'Stop Agy'.")

    def chime_wake(self):
        play_chime(self.sounds.get("wake"))

    def chime_done(self):
        play_chime(self.sounds.get("done"))

    def chime_cancel(self):
        play_chime(self.sounds.get("cancel"))

    def chime_toggle(self, active: bool):
        play_chime(self.sounds.get("toggle"))

    def check_vosk_trigger(self, audio_bytes: bytes):
        if self.recognizer.AcceptWaveform(audio_bytes):
            res = json.loads(self.recognizer.Result())
            text = res.get("text", "").strip()
            self.recognizer.Reset()
            if text in self.stop_triggers:
                return "stop"
            elif text in self.wake_triggers:
                return "wake"
        return None


    def capture_focus_memory(self):
        """Smart Focus Memory: Merkt sich das KI-Terminal, in dem der User arbeitet (schließt den Daemon selbst strikt aus)."""
        self.focused_session_window = None
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd or is_daemon_window(fg_hwnd):
                return
            _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
            sessions = get_antigravity_sessions()
            for s in sessions:
                if fg_pid in s['pids']:
                    import pygetwindow as gw
                    for w in gw.getAllWindows():
                        if w._hWnd == fg_hwnd:
                            if not is_daemon_window(w._hWnd, w.title):
                                self.focused_session_window = w
                                print(f"[*] Smart Focus Memory: Aktive KI-Session \"{s['workspace']}\" ({w.title}) fixiert.")
                                return
        except Exception:
            pass

    def resolve_target_window(self):
        """Findet das Zielterminal: Priorisiert das gemerkte Fokus-Fenster, sonst die aktivste Session."""
        if self.focused_session_window:
            try:
                if win32gui.IsWindow(self.focused_session_window._hWnd):
                    if not is_daemon_window(self.focused_session_window._hWnd, self.focused_session_window.title):
                        return self.focused_session_window
            except Exception:
                pass

        sessions = get_antigravity_sessions()
        if sessions:
            primary_session = sessions[0]
            try:
                import pygetwindow as gw
                for w in gw.getAllWindows():
                    if is_daemon_window(w._hWnd, w.title):
                        continue
                    try:
                        _, w_pid = win32process.GetWindowThreadProcessId(w._hWnd)
                        if w_pid in primary_session['pids']:
                            return w
                    except Exception:
                        pass
            except Exception:
                pass
        return None

    def handle_stop_command(self):
        """Bricht einen laufenden Task / Generierung in Antigravity hands-free per Escape ab."""
        print("\n" + "=" * 55)
        print(" [!] 'Stop Agy' erkannt! Sende Abbruch (Escape)...")
        print("=" * 55)
        self.chime_cancel()

        self.is_recording_prompt = False
        self.prompt_audio_chunks = []

        target_window = self.resolve_target_window()
        if target_window:
            try:
                pyautogui.press('alt')
                time.sleep(0.04)
                if hasattr(target_window, 'activate'):
                    target_window.activate()
                else:
                    win32gui.SetForegroundWindow(target_window._hWnd)
                time.sleep(0.08)
            except Exception:
                pass

        # In Antigravity CLI wird ein aktiver Task/Generierung sauber mit Escape abgebrochen
        pyautogui.press('escape')
        print("[+] Abbruch-Signal (Escape) erfolgreich gesendet.\n")

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

            # 1. Ziel-Fenster via Smart Focus Memory auflösen
            target_window = self.resolve_target_window()

            if target_window:
                try:
                    title = target_window.title if hasattr(target_window, 'title') else win32gui.GetWindowText(target_window._hWnd)
                    print(f"[*] Aktiviere Terminal-Fenster: \"{title}\"")
                    pyautogui.press('alt')
                    time.sleep(0.04)
                    if hasattr(target_window, 'activate'):
                        target_window.activate()
                    else:
                        win32gui.SetForegroundWindow(target_window._hWnd)
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
        print(f" • Stop/Abort: 'Stop Agy' / 'Hey Agy Stop' (Ctrl+C)")
        print(f" • Transkription: Gemini 3.5 Transcribe Live (Option B)")
        print(f" • Smart Focus: Automatische Session-Erkennung aktiv")
        print(f" • Audio-Feedback: Studio-Chimes aktiv")
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
                    self.capture_focus_memory()
                    self.is_recording_prompt = True
                    self.has_started_speaking = True
                    self.voice_chunk_count = 0
                    self.prompt_audio_chunks = []
                    self.last_voice_time = time.time()
                    self.record_start_time = time.time()
                    self.chime_wake()
                    time.sleep(0.25)
                    print("[*] Manueller Diktat-Modus aktiv. Sprich jetzt...")

                data, _ = stream.read(chunk_size)
                audio_chunk = np.frombuffer(data, dtype=np.int16)
                audio_bytes = data.tobytes()
                rms = np.sqrt(np.mean(audio_chunk.astype(np.float64)**2))

                if self.is_recording_prompt:
                    # Hands-free Cancel währen Prompt-Aufnahme
                    stop_trigger = self.check_vosk_trigger(audio_bytes)
                    if stop_trigger == "stop":
                        print("[*] Prompt-Aufnahme hands-free abgebrochen (Cancel).")
                        self.chime_cancel()
                        self.is_recording_prompt = False
                        self.prompt_audio_chunks = []
                        continue

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
                            self.chime_cancel()
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
                    trigger = self.check_vosk_trigger(audio_bytes)
                    if trigger == "stop":
                        self.handle_stop_command()
                    elif trigger == "wake":
                        print("[+] 'Hey Agy' erkannt! Öffne Mikrofon für Prompt...")
                        self.capture_focus_memory()
                        self.chime_wake()
                        time.sleep(0.25)
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

        elif "/stop" in path or "/cancel" in path or "/abort" in path:
            self.engine.handle_stop_command()
            response_data = {"status": "stopped"}

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
