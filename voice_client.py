import sys
import json
import urllib.request
import urllib.error

PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"

def send_command(cmd: str):
    url = f"{BASE_URL}/{cmd}"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.URLError:
        print("[!] Der Antigravity Voice Daemon läuft nicht.")
        print("    Starte ihn mit: start_voice.bat")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Fehler: {e}")
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print("Usage: python voice_client.py [on|off|dictate|status]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd in ["on", "start", "unmute"]:
        res = send_command("unmute")
        print("✅ Wake-Word aktiviert. Antigravity hört auf das Codewort.")
    elif cmd in ["off", "stop", "mute"]:
        res = send_command("mute")
        print("🔇 Wake-Word stummgeschaltet (Mute-Modus aktiv).")
    elif cmd in ["dictate", "listen", "record"]:
        res = send_command("dictate")
        print("🎙️ Aufnahme gestartet! Sprich jetzt deinen Prompt...")
    elif cmd in ["status", "info"]:
        res = send_command("status")
        status_text = "Stummgeschaltet (Muted)" if res.get("is_muted") else "Aktiv (Hört auf Wake-Word)"
        print(f"ℹ️ Status: {status_text}")
    else:
        print(f"Unbekannter Befehl: {cmd}")

if __name__ == "__main__":
    main()
