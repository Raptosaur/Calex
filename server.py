"""Calex Lights - a tiny local web app for controlling Calex (Tuya) smart lights.

Calex Smart is a rebranded Tuya app, so the lights speak the Tuya local
protocol. This server talks to them directly over your LAN with tinytuya and
serves a web UI at http://localhost:8765.

Needs a devices.json next to this file; if missing, the tinytuya setup wizard runs first.
"""

import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tinytuya

# When frozen into an .exe, user files (devices.json) live next to the exe and
# bundled files (index.html) live in PyInstaller's temp dir.
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", HERE)
DEVICES_FILE = os.path.join(HERE, "devices.json")
INDEX_FILE = os.path.join(BUNDLE, "index.html")
HOST = os.environ.get("CALEX_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALEX_PORT", "8765"))

# Tuya product categories that are lights. Anything else is treated as a plain switch/plug.
LIGHT_CATEGORIES = {"dj", "dd", "xdd", "fwd", "dc", "tyndj", "fsd"}


class Light:
    def __init__(self, info, ip):
        self.id = info["id"]
        self.name = info.get("name") or self.id
        self.category = info.get("category", "")
        self.is_bulb = self.category in LIGHT_CATEGORIES
        self.lock = threading.Lock()
        version = float(info.get("version") or 3.3)
        cls = tinytuya.BulbDevice if self.is_bulb else tinytuya.OutletDevice
        self.dev = cls(self.id, ip or "Auto", info["key"], version=version,
                       connection_timeout=3, connection_retry_limit=1, connection_retry_delay=1)
        self.switch_dp = self._find_switch_dp(info)

    @staticmethod
    def _find_switch_dp(info):
        for dp, m in (info.get("mapping") or {}).items():
            if m.get("code") in ("switch_led", "switch_1", "switch", "led_switch"):
                return int(dp)
        return 1

    def state(self):
        with self.lock:
            status = self.dev.status()
            if not status or "Error" in status:
                return {"online": False, "error": (status or {}).get("Error", "no response")}
            out = {"online": True}
            if self.is_bulb and self.dev.bulb_configured:
                s = self.dev.state()
                out["on"] = bool(s.get("is_on"))
                out["mode"] = s.get("mode")
                caps = {f: self.dev.bulb_has_capability(f) for f in ("brightness", "colourtemp", "colour")}
                out["caps"] = caps
                if caps["brightness"]:
                    out["brightness"] = round(self.dev.get_brightness_percentage())
                if caps["colourtemp"]:
                    out["colourtemp"] = round(self.dev.get_colourtemp_percentage())
                if caps["colour"]:
                    r, g, b = self.dev.colour_rgb()
                    out["colour"] = "#%02x%02x%02x" % (int(r), int(g), int(b))
            else:
                out["on"] = bool(status.get("dps", {}).get(str(self.switch_dp)))
                out["caps"] = {"brightness": False, "colourtemp": False, "colour": False}
            return out

    def apply(self, body):
        with self.lock:
            d = self.dev
            if "on" in body:
                if self.is_bulb:
                    (d.turn_on if body["on"] else d.turn_off)()
                else:
                    (d.turn_on if body["on"] else d.turn_off)(switch=self.switch_dp)
            if not self.is_bulb:
                return
            if not d.bulb_configured:
                d.status()
            if "colour" in body:
                h = body["colour"].lstrip("#")
                d.set_colour(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            if "colourtemp" in body and "brightness" in body:
                d.set_white_percentage(clamp(body["brightness"]), clamp(body["colourtemp"]))
            elif "colourtemp" in body:
                d.set_colourtemp_percentage(clamp(body["colourtemp"]))
            elif "brightness" in body:
                d.set_brightness_percentage(clamp(body["brightness"]))


def clamp(v):
    return max(1, min(100, int(v)))


def load_lights():
    if not os.path.exists(DEVICES_FILE):
        run_wizard()
    if not os.path.exists(DEVICES_FILE):
        sys.exit("devices.json not found - setup wizard did not finish (see README).")
    with open(DEVICES_FILE, encoding="utf-8") as f:
        infos = [i for i in json.load(f) if i.get("key") and not i.get("sub")]

    print("Scanning your network for lights (takes a few seconds)...")
    try:
        found = tinytuya.deviceScan(verbose=False, maxretry=8, color=False, poll=False, byID=True)
    except Exception as e:
        print("Scan failed (%s); falling back to IPs from devices.json" % e)
        found = {}

    lights = {}
    for info in infos:
        ip = (found.get(info["id"]) or {}).get("ip") or info.get("ip")
        light = Light(info, ip)
        if (found.get(info["id"]) or {}).get("version"):
            light.dev.set_version(float(found[info["id"]]["version"]))
        lights[light.id] = light
        print("  %-30s %s" % (light.name, ip or "(not found on network)"))
    return lights


def run_wizard():
    from tinytuya import wizard
    print("No devices.json yet - running the one-time Tuya setup wizard. See README.md for the keys it asks for.\n")
    wizard.wizard(color=False)


LIGHTS = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX_FILE, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/devices":
            self.send_json([{"id": l.id, "name": l.name, "bulb": l.is_bulb} for l in LIGHTS.values()])
        elif m := re.fullmatch(r"/api/devices/([\w-]+)", self.path):
            light = LIGHTS.get(m.group(1))
            if not light:
                return self.send_json({"error": "unknown device"}, 404)
            try:
                self.send_json(light.state())
            except Exception as e:
                self.send_json({"online": False, "error": str(e)})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        m = re.fullmatch(r"/api/devices/([\w-]+)", self.path)
        light = LIGHTS.get(m.group(1)) if m else None
        if not light:
            return self.send_json({"error": "unknown device"}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            light.apply(body)
            self.send_json(light.state())
        except Exception as e:
            self.send_json({"online": False, "error": str(e)}, 500)


def main():
    os.chdir(HERE)  # the tinytuya wizard reads/writes its files in the current dir
    if "--wizard" in sys.argv:
        run_wizard()
    LIGHTS.update(load_lights())
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://localhost:%d" % PORT
    print("\nCalex Lights running at %s  (Ctrl+C to stop)" % url)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def run():
    # Double-clicked exes close their window on exit, so pause to let people read errors.
    try:
        main()
    except SystemExit as e:
        if FROZEN and e.code not in (None, 0):
            print(e.code)
            input("\nPress Enter to close...")
        raise
    except Exception:
        if not FROZEN:
            raise
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
        sys.exit(1)


if __name__ == "__main__":
    run()
