"""Tiny CLI client for the Hey Agy voice daemon (also used by the optional Antigravity skill)."""
import json
import os
import sys
import urllib.error
import urllib.request


def _load_port() -> int:
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            return int(json.load(f).get("ipc_port", 8765))
    except Exception:
        return 8765


PORT = _load_port()
BASE_URL = f"http://127.0.0.1:{PORT}"

USAGE = "Usage: python voice_client.py [on|off|dictate|stop|status]"


def send_command(cmd: str):
    url = f"{BASE_URL}/{cmd}"
    try:
        req = urllib.request.Request(url, method="POST", headers={"X-Voice-Client": "1"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        print("[!] The Hey Agy voice daemon is not running.")
        print("    Start it with: start_voice.bat")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Error: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() in ("-h", "--help", "help"):
        print(USAGE)
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    cmd = sys.argv[1].lower()
    if cmd in ("on", "start", "unmute"):
        send_command("unmute")
        print("Wake word enabled. Say 'Hey Agy' to dictate.")
    elif cmd in ("off", "mute"):
        send_command("mute")
        print("Wake word muted.")
    elif cmd in ("stop", "cancel", "abort"):
        send_command("stop")
        print("Abort signal (Escape) sent to Antigravity.")
    elif cmd in ("dictate", "listen", "record"):
        send_command("dictate")
        print("Recording started. Speak your prompt now...")
    elif cmd in ("status", "info"):
        res = send_command("status")
        status_text = "muted" if res.get("is_muted") else "active (listening for the wake word)"
        if res.get("status") == "recording":
            status_text = "recording a prompt"
        print(f"Status: {status_text}")
    else:
        print(f"Unknown command: {cmd}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
