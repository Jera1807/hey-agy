import asyncio
import concurrent.futures
import json
import os
import queue
import sys
import tempfile
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

# Default custom vocabulary that biases transcription towards developer terms.
# Override it with "custom_vocabulary" in config.json (up to ~100 terms work best).
DEFAULT_VOCAB = [
    "Antigravity", "Agy", "Gemini", "Python", "TypeScript", "JavaScript", "Rust",
    "React", "Tailwind", "Kubernetes", "GitHub", "WebGPU", "refactor", "pull request",
]

# Apps (process names) that are treated as AI-agent targets in the default "auto" mode.
# Override with "target_apps" in config.json, or set "target_mode": "focused" to accept any window.
DEFAULT_TARGET_APPS = [
    "windowsterminal.exe", "wt.exe", "conhost.exe", "powershell.exe", "pwsh.exe", "cmd.exe",
    "wezterm-gui.exe", "alacritty.exe", "antigravity.exe", "cursor.exe", "code.exe",
    "code - insiders.exe", "windsurf.exe", "zed.exe", "claude.exe", "chatgpt.exe",
]

_END = object()
_CANCEL = object()


class CallbackInput:
    """Callback-based microphone input (also works with WDM-KS devices that lack a blocking API).

    Exposes read()/read_available like sounddevice.InputStream.
    """
    def __init__(self, samplerate, blocksize, device=None):
        self.blocksize = blocksize
        self.last_data = time.time()
        self._q = queue.Queue(maxsize=400)
        self._carry = np.zeros((0, 1), dtype=np.int16)
        self.device = device
        self._stream = sd.InputStream(
            samplerate=samplerate, channels=1, dtype="int16",
            blocksize=0, device=device, callback=self._cb,
        )
        self.device = self._stream.device

    @property
    def stalled(self):
        return (time.time() - self.last_data) > 2.0

    def _cb(self, indata, frames, time_info, status):
        self.last_data = time.time()
        try:
            self._q.put_nowait(indata.copy())
        except queue.Full:
            pass

    @property
    def read_available(self):
        return self._carry.shape[0] + sum(b.shape[0] for b in list(self._q.queue))

    def read(self, n):
        while self._carry.shape[0] < n:
            try:
                self._carry = np.concatenate([self._carry, self._q.get(timeout=1.0)])
            except queue.Empty:
                # No data from the driver: feed silence so the main loop keeps running.
                self._carry = np.concatenate([self._carry, np.zeros((n, 1), dtype=np.int16)])
        out, self._carry = self._carry[:n], self._carry[n:]
        return out, False

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()


APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(APP_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "daemon.log")
TASK_NAME = "HeyAgyVoice"


class _Tee:
    """Write to the log file with a timestamp per line (and to the original console if there is one)."""
    def __init__(self, log, console=None):
        self.log, self.console, self._at_line_start = log, console, True

    def write(self, text):
        if self.console:
            try:
                self.console.write(text)
            except Exception:
                pass
        for chunk in text.splitlines(True):
            if self._at_line_start and chunk.strip():
                self.log.write(time.strftime("%Y-%m-%d %H:%M:%S "))
            self.log.write(chunk)
            self._at_line_start = chunk.endswith("\n")
        self.log.flush()

    def flush(self):
        try:
            self.log.flush()
            if self.console:
                self.console.flush()
        except Exception:
            pass


def setup_logging(background: bool):
    """Log to logs/daemon.log. In background mode (no console) stdout/stderr are fully redirected."""
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        if os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass
    log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    console = None if (background or sys.stdout is None) else sys.stdout
    sys.stdout = sys.stderr = _Tee(log, console)


def show_message(title: str, text: str):
    """Show a Windows message box (used in background mode where no console is visible)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


def _task_python() -> str:
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return pyw if os.path.exists(pyw) else exe


def _powershell(script: str):
    import subprocess
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, errors="replace",
    )


def install_autostart():
    """Register a per-user scheduled task that starts the daemon hidden at logon (no admin needed)."""
    q = lambda p: p.replace("'", "''")
    script = (
        f"$a = New-ScheduledTaskAction -Execute '{q(_task_python())}' "
        f"-Argument '\"{q(os.path.join(APP_DIR, 'voice_daemon.py'))}\" --background' "
        f"-WorkingDirectory '{q(APP_DIR)}';"
        "$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $t.Delay = 'PT15S';"
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s "
        "-Description 'Hey Agy voice companion for Google Antigravity' -Force | Out-Null"
    )
    r = _powershell(script)
    if r.returncode == 0:
        print(f"[+] Autostart installed: '{TASK_NAME}' starts the daemon hidden ~15 s after you log in.")
        print(f"    Logs: {LOG_PATH}")
        print("    Start it right now with:  schtasks /Run /TN " + TASK_NAME)
    else:
        print(f"[!] Could not install autostart: {(r.stderr or r.stdout).strip()}")
    return r.returncode == 0


def uninstall_autostart():
    r = _powershell(f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction Stop")
    if r.returncode == 0:
        print(f"[+] Autostart removed ('{TASK_NAME}').")
    else:
        print("[*] Autostart was not installed (nothing to remove).")
    return r.returncode == 0


def autostart_status() -> bool:
    r = _powershell(f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop).State")
    ok = r.returncode == 0
    print(f"Autostart: {'installed (' + r.stdout.strip() + ')' if ok else 'not installed'}")
    return ok


class TrayIcon:
    """System-tray icon: shows the daemon state and offers mute / dictate / log / quit."""
    COLORS = {
        "listening": (46, 160, 67),
        "recording": (218, 54, 51),
        "processing": (210, 153, 34),
        "muted": (110, 118, 129),
    }
    LABELS = {
        "listening": "listening for 'Hey Agy'",
        "recording": "recording your prompt",
        "processing": "transcribing",
        "muted": "muted",
    }

    def __init__(self, engine):
        import pystray
        from PIL import Image, ImageDraw
        self._pystray, self._Image, self._Draw = pystray, Image, ImageDraw
        self.engine = engine
        self._images = {k: self._make_image(c) for k, c in self.COLORS.items()}
        self._state = self._current_state()
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: f"Hey Agy - {self.LABELS[self._state]}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: "Unmute wake word" if self.engine.is_muted else "Mute wake word", self._toggle_mute),
            pystray.MenuItem("Dictate now", self._dictate),
            pystray.MenuItem("Open log", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        self.icon = pystray.Icon("HeyAgy", self._images[self._state], "Hey Agy", menu)

    def _make_image(self, color):
        size = 64
        img = self._Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = self._Draw.Draw(img)
        d.ellipse((2, 2, size - 2, size - 2), fill=color + (255,))
        white = (255, 255, 255, 255)
        d.rounded_rectangle((25, 13, 39, 36), radius=7, fill=white)       # microphone capsule
        d.arc((19, 24, 45, 46), start=0, end=180, fill=white, width=3)     # holder
        d.line((32, 46, 32, 52), fill=white, width=3)                      # stand
        d.line((25, 52, 39, 52), fill=white, width=3)
        return img

    def _current_state(self):
        e = self.engine
        if e.is_recording_prompt:
            return "recording"
        if e.is_processing:
            return "processing"
        return "muted" if e.is_muted else "listening"

    def _toggle_mute(self, icon=None, item=None):
        self.engine.is_muted = not self.engine.is_muted
        self.engine.chime_toggle(not self.engine.is_muted)
        print(f"[*] Wake word {'muted' if self.engine.is_muted else 'active'} (tray).")

    def _dictate(self, icon=None, item=None):
        self.engine.trigger_manual_dictate = True

    def _open_log(self, icon=None, item=None):
        try:
            os.startfile(LOG_PATH)
        except Exception as e:
            print(f"[!] Could not open the log: {e}")

    def _quit(self, icon=None, item=None):
        print("[*] Quit requested from the tray icon.")
        try:
            self.icon.stop()
        finally:
            os._exit(0)

    def _watch(self):
        while True:
            state = self._current_state()
            if state != self._state:
                self._state = state
                try:
                    self.icon.icon = self._images[state]
                    self.icon.title = f"Hey Agy - {self.LABELS[state]}"
                    self.icon.update_menu()
                except Exception:
                    pass
            time.sleep(0.4)

    def start(self):
        self.icon.title = f"Hey Agy - {self.LABELS[self._state]}"
        self.icon.run_detached()
        threading.Thread(target=self._watch, daemon=True).start()


def refresh_audio_devices():
    """Reload the PortAudio device list (only call while no stream is open)."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        print(f"[!] Could not refresh the audio device list: {e}")


class DefaultMicWatcher:
    """Reads the ID of the Windows default capture device (needs pycaw/comtypes, otherwise inactive)."""
    def __init__(self):
        self._enum = None
        try:
            import comtypes
            from pycaw.pycaw import IMMDeviceEnumerator
            from pycaw.constants import CLSID_MMDeviceEnumerator
            self._enum = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
            )
        except Exception:
            print("[*] Note: 'pycaw' is not installed - automatic switching to a new default microphone is disabled.")

    def get(self):
        if not self._enum:
            return None
        try:
            return self._enum.GetDefaultAudioEndpoint(1, 0).GetId()  # eCapture, eConsole
        except Exception:
            return None


def install_skill():
    """Install skill/SKILL.md as ~/.agents/skills/voice/SKILL.md with the install path filled in."""
    src = os.path.join(APP_DIR, "skill", "SKILL.md")
    dst_dir = os.path.join(os.path.expanduser("~"), ".agents", "skills", "voice")
    os.makedirs(dst_dir, exist_ok=True)
    with open(src, encoding="utf-8") as f:
        text = f.read().replace("<INSTALL_DIR>", APP_DIR)
    with open(os.path.join(dst_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[+] Skill installed to {dst_dir}")


def find_microphone(seconds: float = 2.0):
    """Helper command: test every input device and show its peak level while you speak."""
    print(f"Keep speaking continuously. Testing each input device for {seconds:.0f} s ...\n")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        api = sd.query_hostapis(d["hostapi"])["name"]
        try:
            rec = sd.rec(int(16000 * seconds), samplerate=16000, channels=1, dtype="int16", device=i)
            sd.wait()
            peak = int(np.abs(rec).max())
            print(f"[{i:>2}] peak={peak:>6}  {d['name'][:50]}  ({api})")
        except Exception as e:
            print(f"[{i:>2}] n/a            {d['name'][:50]}  ({api})")
    print("\nSet the device with a high peak as \"input_device\" (index or unique name) in config.json.")


class LiveTranscriber:
    """Streams microphone audio to gemini-3.5-transcribe-live while you speak.

    The connection is opened on the wake word; audio is sent in 100 ms blocks.
    When recording ends, audio_stream_end is sent (hybrid VAD), so the final
    text is available almost immediately.
    """
    CHUNK_BYTES = 3200  # 100 ms @ 16 kHz, int16 mono
    FINAL_WAIT_SECONDS = 8.0
    IDLE_AFTER_END_SECONDS = 1.5

    def __init__(self, config: dict, sample_rate: int = 16000):
        self.config = config
        self.sample_rate = sample_rate
        self._q = queue.Queue()
        self._buf = bytearray()
        self._stop = threading.Event()
        self._result = concurrent.futures.Future()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)

    def start(self):
        self._thread.start()

    def push(self, data: bytes):
        self._buf.extend(data)
        while len(self._buf) >= self.CHUNK_BYTES:
            self._q.put(bytes(self._buf[:self.CHUNK_BYTES]))
            del self._buf[:self.CHUNK_BYTES]

    def finish(self):
        if self._buf:
            self._q.put(bytes(self._buf))
            self._buf.clear()
        self._q.put(_END)

    def cancel(self):
        self._q.put(_CANCEL)

    def result(self, timeout: float = 20.0) -> str:
        try:
            return self._result.result(timeout)
        except Exception as e:
            print(f"[!] Live API error: {e}")
            return ""

    def _thread_main(self):
        try:
            self._result.set_result(asyncio.run(self._run()))
        except Exception as e:
            self._result.set_exception(e)

    def _get(self):
        try:
            return self._q.get(timeout=0.2)
        except queue.Empty:
            return None

    async def _run(self) -> str:
        cfg = self.config
        client = genai.Client(api_key=cfg.get("gemini_api_key"))
        live_config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                mode=cfg.get("transcription_mode", "SMART"),
                language_codes=cfg.get("language_codes", []),
                custom_vocabulary=cfg.get("custom_vocabulary", DEFAULT_VOCAB),
            ),
        )
        model = cfg.get("gemini_model", "models/gemini-3.5-transcribe-live")
        mime = f"audio/pcm;rate={self.sample_rate}"
        finals = []
        ended = asyncio.Event()
        done = asyncio.Event()
        cancelled = False
        last_recv = time.monotonic()

        async with client.aio.live.connect(model=model, config=live_config) as session:
            async def sender():
                nonlocal cancelled
                loop = asyncio.get_running_loop()
                while not self._stop.is_set():
                    item = await loop.run_in_executor(None, self._get)
                    if item is None:
                        continue
                    if item is _CANCEL:
                        cancelled = True
                        return
                    if item is _END:
                        await session.send_realtime_input(audio_stream_end=True)
                        ended.set()
                        nonlocal_touch()
                        return
                    await session.send_realtime_input(audio=types.Blob(data=item, mime_type=mime))

            def nonlocal_touch():
                nonlocal last_recv
                last_recv = time.monotonic()

            async def receiver():
                try:
                    while True:
                        async for resp in session.receive():
                            sc = resp.server_content
                            if not sc:
                                continue
                            tr = sc.input_transcription
                            if tr and tr.text and tr.text.strip():
                                finals.append(tr.text.strip())
                                nonlocal_touch()
                            if sc.turn_complete and ended.is_set():
                                return
                        await asyncio.sleep(0.05)
                except Exception as e:
                    print(f"[!] Live API receive error: {e}")
                finally:
                    done.set()

            recv_task = asyncio.create_task(receiver())
            send_task = asyncio.create_task(sender())
            try:
                await send_task
                if not cancelled:
                    deadline = time.monotonic() + self.FINAL_WAIT_SECONDS
                    while not done.is_set() and time.monotonic() < deadline:
                        if finals and time.monotonic() - last_recv > self.IDLE_AFTER_END_SECONDS:
                            break
                        await asyncio.sleep(0.05)
            finally:
                self._stop.set()
                recv_task.cancel()

        return "" if cancelled else " ".join(finals).strip()

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "gemini_api_key": "",
        "gemini_model": "models/gemini-3.5-transcribe-live",
        "gemini_fallback_model": "gemini-3.5-transcribe",
        "wake_word": "Hey Agy",
        "silence_duration_seconds": 1.4,
        "ipc_port": 8765,
        "auto_send": True,
        "transcription_mode": "SMART",
        "language_codes": [],
        "custom_vocabulary": DEFAULT_VOCAB
    }

def get_antigravity_sessions():
    """Find all active Antigravity CLI sessions by looking for running agy-real processes."""
    sessions = []
    for p in psutil.process_iter(['pid', 'name']):
        try:
            if p.info['name'] and 'agy-real' in p.info['name'].lower():
                tree_pids = {p.pid}
                curr = p
                # Walk the parent process tree (cmd.exe -> powershell.exe -> WindowsTerminal.exe)
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
    """Generate the WAV chimes (harmonic sine waves with exponential decay) if they do not exist yet."""
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
        full = (full / np.max(np.abs(full)) * 12000).astype(np.int16)
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
    """Play a chime asynchronously through the Windows audio system (zero added latency)."""
    if wav_path and os.path.exists(wav_path):
        try:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        except Exception:
            pass

def is_daemon_window(w_hwnd: int, title: str = "") -> bool:
    """Return True if the window belongs to this voice daemon itself."""
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
        
        # State for prompt recording & focus memory
        self.is_recording_prompt = False
        self.has_started_speaking = False
        self.voice_chunk_count = 0
        self.record_start_time = 0
        self.last_voice_time = 0
        self.trigger_manual_dictate = False
        self.prompt_audio_chunks = []
        self.silence_threshold = 480
        self.noise_floor = 150.0
        self.is_processing = False
        self.focused_session_window = None
        self.transcriber = None

        # API key validation
        api_key = self.config.get("gemini_api_key", "").strip()
        if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
            print("\n" + "=" * 60)
            print(" [!] NOTE: No Gemini API key found in config.json!")
            print("     Get a free key at: https://aistudio.google.com/app/apikey")
            print("     and add it to config.json.")
            print("=" * 60 + "\n")
            time.sleep(4)
            sys.exit(1)

        # Vosk initialisation
        print("[*] Loading Vosk speech model (wake-word spotting)...")
        vosk.SetLogLevel(-1)
        self.vosk_model = vosk.Model(lang="en-us")

        # Exact phrases for the wake word and hands-free stop.
        # Small, proven grammar ("agy" is not in the Vosk lexicon, so it is spelled "a g").
        # Optional extra spellings via config.json -> "wake_extra_names" (e.g. ["a gee"]).
        self.wake_triggers = ["hey a g", "hey a g i"]
        self.stop_triggers = ["hey a g stop", "stop a g", "hey a g cancel", "cancel a g"]
        for extra in self.config.get("wake_extra_names", []):
            n = extra.strip().lower()
            if n:
                self.wake_triggers.append(f"hey {n}")
                self.stop_triggers += [f"stop {n}", f"cancel {n}", f"hey {n} stop", f"hey {n} cancel"]

        # Custom wake phrases replace the defaults, e.g. ["hey claude", "okay computer"].
        # Every word must exist in the Vosk lexicon, otherwise the phrase is skipped with a warning.
        custom = [p.strip().lower() for p in self.config.get("wake_phrases", []) if p and p.strip()]
        if custom:
            valid = []
            for phrase in custom:
                missing = [w for w in phrase.split() if self.vosk_model.vosk_model_find_word(w) == -1]
                if missing:
                    print(f"[!] Wake phrase '{phrase}' skipped - unknown to the speech model: {', '.join(missing)}")
                else:
                    valid.append(phrase)
            if valid:
                self.wake_triggers, self.stop_triggers = [], []
                for phrase in valid:
                    rest = phrase.split(maxsplit=1)[1] if len(phrase.split()) > 1 else phrase
                    self.wake_triggers.append(phrase)
                    self.stop_triggers += [f"{phrase} stop", f"{phrase} cancel", f"stop {rest}", f"cancel {rest}"]
                self.stop_triggers += [p.strip().lower() for p in self.config.get("stop_phrases", []) if p and p.strip()]
            else:
                print("[!] No valid wake_phrases - falling back to 'Hey Agy'.")
        self.wake_phrase_label = self.wake_triggers[0] if self.wake_triggers else "hey a g"
        self.all_triggers = self.wake_triggers + self.stop_triggers
        grammar = json.dumps(self.all_triggers + ["[unk]"])
        self.recognizer = vosk.KaldiRecognizer(self.vosk_model, 16000, grammar)
        print(f"[+] Vosk ready. Wake phrases: {', '.join(self.wake_triggers)}")

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


    def _window_for_hwnd(self, hwnd):
        import pygetwindow as gw
        for w in gw.getAllWindows():
            if w._hWnd == hwnd and not is_daemon_window(w._hWnd, w.title):
                return w
        return None

    def capture_focus_memory(self):
        """Smart Focus Memory: remember the window you were working in when you spoke.

        target_mode (config.json):
          "auto"        - Antigravity sessions, plus windows of known AI-agent apps (see target_apps)
          "focused"     - whatever window is focused (works with any app)
          "antigravity" - only Antigravity sessions
        The daemon's own window is never selected.
        """
        self.focused_session_window = None
        mode = str(self.config.get("target_mode", "auto")).lower()
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if not fg_hwnd or is_daemon_window(fg_hwnd):
                return
            _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)

            for s in get_antigravity_sessions():
                if fg_pid in s['pids']:
                    w = self._window_for_hwnd(fg_hwnd)
                    if w:
                        self.focused_session_window = w
                        print(f"[*] Smart Focus Memory: locked onto session \"{s['workspace']}\" ({w.title}).")
                        return

            if mode == "antigravity":
                return
            proc_name = psutil.Process(fg_pid).name().lower()
            apps = [a.lower() for a in self.config.get("target_apps", DEFAULT_TARGET_APPS)]
            if mode == "focused" or proc_name in apps:
                w = self._window_for_hwnd(fg_hwnd)
                if w:
                    self.focused_session_window = w
                    print(f"[*] Smart Focus Memory: locked onto {proc_name} ({w.title}).")
        except Exception:
            pass

    def resolve_target_window(self):
        """Find the target terminal: prefer the remembered focus window, otherwise the first active session."""
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
        """Abort a running task/generation in Antigravity hands-free by sending Escape."""
        print("\n" + "=" * 55)
        print(" [!] 'Stop Agy' detected! Sending abort (Escape)...")
        print("=" * 55)
        self.chime_cancel()

        self.is_recording_prompt = False
        self.prompt_audio_chunks = []

        self.capture_focus_memory()
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

        # Escape cleanly interrupts an active generation in Antigravity and Claude Code.
        # Other agents may use a different shortcut: set "stop_key" (e.g. "ctrl+c") in config.json.
        stop_key = str(self.config.get("stop_key", "escape")).lower()
        pyautogui.hotkey(*[k.strip() for k in stop_key.split("+")])
        print(f"[+] Abort signal ({stop_key}) sent.\n")

    def transcribe_fallback(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        """Optional fallback via the Interactions API (gemini-3.5-transcribe, file transcription)."""
        client = genai.Client(api_key=self.config.get("gemini_api_key"))
        model = self.config.get("gemini_fallback_model", "gemini-3.5-transcribe").replace("models/", "")

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(audio_data.tobytes())
            audio_file = client.files.upload(file=tmp_path, config={"mime_type": "audio/wav"})
            interaction = client.interactions.create(
                model=model,
                input=[{"type": "audio", "uri": audio_file.uri, "mime_type": "audio/wav"}],
            )
            return (interaction.output_text or "").strip()
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def start_stream(self):
        """Open the Live API connection right at the wake word (audio is buffered meanwhile)."""
        self.transcriber = LiveTranscriber(self.config)
        self.transcriber.start()

    def cancel_stream(self):
        if self.transcriber:
            self.transcriber.cancel()
            self.transcriber = None

    def process_and_type(self, transcriber, full_audio: np.ndarray, sample_rate: int = 16000):
        if self.is_processing:
            print("[*] A previous prompt is still being processed. Ignoring duplicate trigger.")
            transcriber.cancel()
            return

        self.is_processing = True
        try:
            print(f"[*] Waiting for final text from {self.config.get('gemini_model')} (Live API)...")
            transcribed_text = transcriber.result()

            if not transcribed_text:
                print("[*] No text received - retrying with the same Live model (full audio)...")
                try:
                    retry = LiveTranscriber(self.config, sample_rate)
                    retry.start()
                    retry.push(full_audio.tobytes())
                    retry.finish()
                    transcribed_text = retry.result()
                except Exception as e:
                    print(f"[!] Live retry error: {e}")

            if not transcribed_text and self.config.get("use_unary_fallback", False):
                print("[*] Using fallback model (Gemini 3.5 Transcribe, Interactions API; small free quota)...")
                try:
                    transcribed_text = self.transcribe_fallback(full_audio, sample_rate)
                except Exception as e:
                    print(f"[!] Fallback error: {e}")

            if not transcribed_text:
                print("[!] No speech recognised in the prompt.")
                return

            print(f"\n[+] Cleaned prompt: \"{transcribed_text}\"\n")

            # 1. Resolve the target window via Smart Focus Memory
            target_window = self.resolve_target_window()

            if target_window:
                try:
                    title = target_window.title if hasattr(target_window, 'title') else win32gui.GetWindowText(target_window._hWnd)
                    print(f"[*] Activating terminal window: \"{title}\"")
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
                sessions = get_antigravity_sessions()
                if not sessions:
                    print("[!] No target window found! Focus your AI agent (Antigravity, Claude Code, Cursor, ...)")
                    print("    and say the wake word again, or set \"target_mode\": \"focused\" in config.json.")
                    self.chime_cancel()
                    return
                print("[*] Sending prompt to the active input field.")

            # Save the previous clipboard contents
            old_clipboard = ""
            try:
                old_clipboard = pyperclip.paste()
            except Exception:
                pass

            # Paste the text into the terminal and submit it
            pyperclip.copy(transcribed_text)
            time.sleep(0.08)
            pyautogui.keyDown('ctrl')
            pyautogui.press('v')
            pyautogui.keyUp('ctrl')

            if self.config.get("auto_send", True):
                time.sleep(0.12)
                pyautogui.press('enter')

            # Restore the previous clipboard contents after a short pause
            if old_clipboard:
                time.sleep(0.2)
                try:
                    pyperclip.copy(old_clipboard)
                except Exception:
                    pass
        finally:
            self.is_processing = False

    def run(self):
        sample_rate = 16000
        chunk_size = 800  # 50 ms chunks
        silence_limit = self.config.get("silence_duration_seconds", 1.4)

        print("\n=======================================================")
        print(" Hey Agy - Voice Companion for Google Antigravity (ACTIVE)")
        print(" * Wake word:     'Hey Agy' (offline Vosk spotting)")
        print(" * Stop / abort:  'Stop Agy' or 'Hey Agy Stop' (sends Escape)")
        print(" * Transcription: Gemini 3.5 Transcribe Live")
        print(f" * Target:        {self.config.get('target_mode', 'auto')} (the window you were in when you said the wake word)")
        print(" * Audio cues:    chimes enabled")
        print(f" * Status:        {'muted' if self.is_muted else 'listening'}")
        print(" * Control:       python voice_client.py [on|off|dictate|stop|status]")
        print("=======================================================")

        # Show detected Antigravity sessions
        sessions = get_antigravity_sessions()
        if sessions:
            print(f"[+] {len(sessions)} active Antigravity session(s) found:")
            for i, s in enumerate(sessions, 1):
                print(f"    [{i}] Workspace: \"{s['workspace']}\" (PID {s['pid']})")
        else:
            print("[!] No running Antigravity session found. Start 'agy' first.")
        print("=======================================================\n")

        pinned = self.config.get("input_device")
        if isinstance(pinned, str) and pinned.strip() == "":
            pinned = None
        watcher = DefaultMicWatcher()
        first = True

        while True:
            try:
                if not first:
                    refresh_audio_devices()
                stream_ctx = CallbackInput(sample_rate, chunk_size, pinned)
                dev_info = sd.query_devices(stream_ctx.device, 'input')
                print(f"[+] Microphone: {dev_info['name']} ({sd.query_hostapis(dev_info['hostapi'])['name']})")
            except Exception as e:
                print(f"\n[!] ERROR opening the microphone: {e}")
                if first:
                    print("    Please check your Windows audio input settings.\n")
                    time.sleep(5)
                    return
                print("    Retrying in 3 seconds...\n")
                time.sleep(3)
                continue
            first = False

            opened_id = watcher.get()
            try:
                with stream_ctx as stream:
                    reason = self._listen(stream, sample_rate, chunk_size, silence_limit,
                                          watcher, opened_id, pinned is not None)
            except Exception as e:
                reason = f"audio stream error: {e}"

            print(f"[*] Reconnecting microphone: {reason}")
            self.cancel_stream()
            self.is_recording_prompt = False
            self.prompt_audio_chunks = []
            try:
                self.recognizer.Reset()
            except Exception:
                pass
            self.chime_toggle(True)
            time.sleep(0.8)

    def _listen(self, stream, sample_rate, chunk_size, silence_limit, watcher, opened_id, pinned):
        """Main loop on an open microphone stream. Returns the reason why a reconnect is needed."""
        listen_start = time.time()
        idle_peak = 0.0
        warned_silent = False
        last_dev_check = time.time()
        while True:
            now = time.time()
            if now - last_dev_check > 2.0:
                last_dev_check = now
                if stream.stalled:
                    return "microphone stopped delivering data (device unplugged?)"
                if not pinned and not self.is_recording_prompt and not self.is_processing:
                    current_id = watcher.get()
                    if current_id and opened_id and current_id != opened_id:
                        return "default microphone changed"

            if self.trigger_manual_dictate:
                self.trigger_manual_dictate = False
                self.capture_focus_memory()
                self.chime_wake()
                self.start_stream()
                self.calibrate_silence_threshold()
                time.sleep(0.3)
                # Flush the audio buffer so the chime is not recorded
                while stream.read_available > 0:
                    stream.read(min(stream.read_available, chunk_size))
                self.is_recording_prompt = True
                self.has_started_speaking = True
                self.voice_chunk_count = 0
                self.prompt_audio_chunks = []
                self.last_voice_time = time.time()
                self.record_start_time = time.time()
                print("[*] Manual dictation active. Speak now...")

            data, _ = stream.read(chunk_size)
            audio_chunk = np.frombuffer(data, dtype=np.int16)
            audio_bytes = data.tobytes()
            rms = np.sqrt(np.mean(audio_chunk.astype(np.float64)**2))

            if self.is_recording_prompt:
                # Hands-free cancel while recording a prompt
                stop_trigger = self.check_vosk_trigger(audio_bytes)
                if stop_trigger == "stop":
                    print("[*] Prompt recording cancelled hands-free.")
                    self.chime_cancel()
                    self.cancel_stream()
                    self.is_recording_prompt = False
                    self.prompt_audio_chunks = []
                    continue

                self.prompt_audio_chunks.append(audio_chunk)
                if self.transcriber:
                    self.transcriber.push(audio_bytes)

                if not self.has_started_speaking:
                    if rms > self.silence_threshold:
                        self.voice_chunk_count += 1
                        if self.voice_chunk_count >= 3:
                            self.has_started_speaking = True
                            self.last_voice_time = time.time()
                    else:
                        self.voice_chunk_count = 0

                    if (time.time() - self.record_start_time) > 6.0:
                        print("[*] Timeout: no prompt spoken after the wake word.")
                        self.chime_cancel()
                        self.cancel_stream()
                        self.is_recording_prompt = False
                        self.prompt_audio_chunks = []
                else:
                    if rms > self.silence_threshold:
                        self.last_voice_time = time.time()

                    if (time.time() - self.last_voice_time) > silence_limit and len(self.prompt_audio_chunks) > 25:
                        self.is_recording_prompt = False
                        self.chime_done()
                        print("[*] Recording finished. Finalising transcription (Gemini 3.5 Transcribe Live)...")
                        full_audio = np.concatenate(self.prompt_audio_chunks)
                        self.prompt_audio_chunks = []
                        transcriber, self.transcriber = self.transcriber, None
                        if transcriber:
                            transcriber.finish()
                            threading.Thread(target=self.process_and_type, args=(transcriber, full_audio, sample_rate), daemon=True).start()

            elif not self.is_muted:
                idle_peak = max(idle_peak, rms)
                if not warned_silent and idle_peak < 2 and (time.time() - listen_start) > 8:
                    warned_silent = True
                    print("\n[!] WARNING: The microphone only delivers silence (level 0)!")
                    print("    Check: Windows Settings > Privacy > Microphone (allow desktop apps),")
                    print("    Sound > Input (not muted), or set \"input_device\" in config.json.")
                    print("    Run 'python voice_daemon.py --find-mic' to list input levels.\n")
                self.update_noise_floor(rms)
                trigger = self.check_vosk_trigger(audio_bytes)
                if trigger == "stop":
                    self.handle_stop_command()
                elif trigger == "wake":
                    print("[+] 'Hey Agy' detected! Listening for your prompt...")
                    self.capture_focus_memory()
                    self.chime_wake()
                    self.start_stream()
                    self.calibrate_silence_threshold()
                    time.sleep(0.32)
                    # Fully flush the audio buffer so the chime is not mistaken for speech
                    while stream.read_available > 0:
                        stream.read(min(stream.read_available, chunk_size))
                    self.is_recording_prompt = True
                    self.has_started_speaking = False
                    self.voice_chunk_count = 0
                    self.prompt_audio_chunks = []
                    self.record_start_time = time.time()
                    self.last_voice_time = time.time()

            time.sleep(0.001)


    def update_noise_floor(self, rms: float):
        """Track the idle noise floor (speech is largely ignored)."""
        if rms < self.noise_floor * 2 + 150:
            self.noise_floor += 0.02 * (rms - self.noise_floor)
        else:
            self.noise_floor += 0.0005 * (rms - self.noise_floor)

    def calibrate_silence_threshold(self):
        fixed = self.config.get("silence_threshold", 0)
        if fixed and fixed > 0:
            self.silence_threshold = fixed
        else:
            self.silence_threshold = int(min(1500, max(300, self.noise_floor * 2.5 + 100)))
        print(f"[*] Speech threshold: {self.silence_threshold} (noise floor ~{int(self.noise_floor)})")

# Local HTTP control interface
class VoiceHTTPHandler(BaseHTTPRequestHandler):
    engine: VoiceEngine = None

    def log_message(self, format, *args):
        return

    def do_GET(self):
        # GET is only allowed for /status so that no web page can trigger actions via a link/image.
        if "/status" in self.path.lower():
            self.handle_request()
        else:
            self.send_error(405, "Use POST")

    def do_POST(self):
        # The custom header forces a CORS preflight in browsers (which is never answered).
        if self.headers.get("X-Voice-Client") != "1":
            self.send_error(403, "Forbidden")
            return
        self.handle_request()

    def handle_request(self):
        path = self.path.lower()
        response_data = {"status": "ok"}

        if "/mute" in path or "/off" in path:
            self.engine.is_muted = True
            self.engine.chime_toggle(False)
            response_data = {"status": "muted", "is_muted": True}
            print("[*] Wake word 'Hey Agy' muted.")

        elif "/unmute" in path or "/on" in path:
            self.engine.is_muted = False
            self.engine.chime_toggle(True)
            response_data = {"status": "active", "is_muted": False}
            print("[*] Wake word 'Hey Agy' active.")

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

def start_daemon(background: bool = False):
    setup_logging(background)

    # 1. Single-instance mutex (prevents duplicate daemons)
    mutex_name = "Global\\AntigravityVoiceDaemonMutex"
    mutex = win32event.CreateMutex(None, False, mutex_name)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        if background:
            print("[*] Another instance is already running - exiting.")
            sys.exit(0)
        print("\n" + "=" * 60)
        print(" [!] WARNING: The voice daemon is already running!")
        print("     Another instance / window is already open.")
        print("     This second window will exit to avoid duplicate")
        print("     voice input in your terminal.")
        print("=" * 60 + "\n")
        time.sleep(3.5)
        sys.exit(1)

    try:
        engine = VoiceEngine()
    except SystemExit:
        if background:
            show_message("Hey Agy", "No Gemini API key found.\nAdd it to config.json and start Hey Agy again.")
        raise
    VoiceHTTPHandler.engine = engine

    port = engine.config.get("ipc_port", 8765)
    try:
        server = HTTPServer(("127.0.0.1", port), VoiceHTTPHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        print(f"[+] Control interface listening on http://127.0.0.1:{port}")
    except Exception as e:
        print(f"[!] Could not start the control interface on port {port}: {e}")

    if engine.config.get("tray_icon", True):
        try:
            TrayIcon(engine).start()
            print("[+] Tray icon active (right-click it for mute / dictate / log / quit).")
        except Exception as e:
            print(f"[*] Tray icon unavailable ({e}). Install 'pystray' and 'pillow' to enable it.")

    engine.run()

HELP = """Hey Agy voice daemon

Usage: python voice_daemon.py [option]

  (no option)            run in this console
  --background           run hidden: log to logs/daemon.log, control via the tray icon
  --install-autostart    start hidden automatically when you log in to Windows
  --uninstall-autostart  remove the autostart entry
  --autostart-status     show whether autostart is installed
  --find-mic             list input devices and their levels while you speak
  --install-skill        install the Antigravity skill (~/.agents/skills/voice)
"""

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--find-mic" in args:
        find_microphone()
    elif "--install-autostart" in args:
        sys.exit(0 if install_autostart() else 1)
    elif "--uninstall-autostart" in args:
        uninstall_autostart()
    elif "--autostart-status" in args:
        autostart_status()
    elif "--install-skill" in args:
        install_skill()
    elif "-h" in args or "--help" in args:
        print(HELP)
    else:
        start_daemon(background="--background" in args)
