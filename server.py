"""Calex Lights - a tiny local app for controlling Calex (Tuya) smart lights.

Calex Smart is a rebranded Tuya app, so the lights speak the Tuya local
protocol. This talks to them directly over your LAN with tinytuya, sits in the
system tray with a quick-control popup (tray.py), and serves a full web control
panel at http://localhost:8765. Pass --no-tray to run just the web server.

Needs a devices.json next to this file; if missing, the tinytuya setup wizard runs first.
"""

import json
import os
import urllib.request
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
LOG_FILE = os.path.join(HERE, "calex-lights.log")
INDEX_FILE = os.path.join(BUNDLE, "index.html")
HOST = os.environ.get("CALEX_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALEX_PORT", "8765"))

# Tuya product categories that are lights. Anything else is treated as a plain switch/plug.
LIGHT_CATEGORIES = {"dj", "dd", "xdd", "fwd", "dc", "tyndj", "fsd"}

# Tuya function codes (from the wizard's per-device "mapping") -> tinytuya bulb features.
FEATURE_CODES = {
    "switch_led": "switch", "work_mode": "mode",
    "bright_value": "brightness", "bright_value_v2": "brightness",
    "temp_value": "colourtemp", "temp_value_v2": "colourtemp",
    "colour_data": "colour", "colour_data_v2": "colour",
}


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
        self.features = {FEATURE_CODES[m["code"]]: str(dp)
                         for dp, m in (info.get("mapping") or {}).items() if m.get("code") in FEATURE_CODES}
        self.features_applied = False

    def _apply_features(self, status):
        """tinytuya only enables features whose data points appear in a status
        reply, and some bulbs leave out colour while in white mode. Fill the
        gaps from the device's Tuya mapping."""
        if self.features_applied or not self.dev.bulb_configured:
            return
        self.features_applied = True
        for feature, dp in self.features.items():
            if not self.dev.dpset.get(feature):
                self.dev.dpset[feature] = dp
                setattr(self.dev, "has_" + feature, True)
        print("%s: type %s, dps %s, features %s" % (
            self.name, self.dev.bulb_type, sorted((status or {}).get("dps", {})),
            {k: v for k, v in self.dev.dpset.items() if v and not k.startswith("value_")}))

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
            if self.is_bulb:
                self._apply_features(status)
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
                if caps["colour"] and status.get("dps", {}).get(self.dev.dpset["colour"]):
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
                self._apply_features(d.status())
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
    # The windowed exe has no console, so borrow one for the wizard's questions.
    borrowed = sys.platform == "win32" and sys.stdin is None
    if borrowed:
        import ctypes
        ctypes.windll.kernel32.AllocConsole()
        saved = sys.stdin, sys.stdout, sys.stderr
        sys.stdin = open("CONIN$", "r")
        sys.stdout = sys.stderr = open("CONOUT$", "w")
    try:
        from tinytuya import wizard
        print("No devices.json yet - running the one-time Tuya setup wizard. See README.md for the keys it asks for.\n")
        wizard.wizard(color=False)
        if borrowed:
            input("\nSetup done. Press Enter to start Calex Lights in the system tray...")
    finally:
        if borrowed:
            sys.stdin.close()
            sys.stdout.close()
            sys.stdin, sys.stdout, sys.stderr = saved
            ctypes.windll.kernel32.FreeConsole()


LIGHTS = {}


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second copy bind the same port, so don't use it there.
    allow_reuse_address = os.name != "nt"


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


def already_running(url):
    try:
        with urllib.request.urlopen(url + "/api/devices", timeout=1):
            return True
    except OSError:
        return False


def main():
    os.chdir(HERE)  # the tinytuya wizard reads/writes its files in the current dir
    url = "http://localhost:%d" % PORT
    if already_running(url):
        webbrowser.open(url)
        return
    if "--wizard" in sys.argv or not os.path.exists(DEVICES_FILE):
        run_wizard()
    if not os.path.exists(DEVICES_FILE):
        sys.exit("devices.json not found - setup wizard did not finish (see README).")
    server = Server((HOST, PORT), Handler)

    if "--no-tray" in sys.argv:
        LIGHTS.update(load_lights())
        print("\nCalex Lights running at %s  (Ctrl+C to stop)" % url)
        if "--no-browser" not in sys.argv:
            threading.Timer(0.5, webbrowser.open, [url]).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    import tray
    app = tray.TrayApp(LIGHTS, url)

    def load():
        try:
            LIGHTS.update(load_lights())
        except Exception as e:
            msg = "Couldn't load devices.json:\n%s" % e
            print(msg)
            app.call(lambda: app.placeholder.config(text=msg))
        else:
            app.call(app.set_lights_loaded)
        server.serve_forever()
    threading.Thread(target=load, daemon=True).start()
    app.run()


def run():
    # The windowed exe has no stdout; keep a log next to it instead.
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(LOG_FILE, "w", encoding="utf-8", buffering=1)
    try:
        main()
    except SystemExit as e:
        if FROZEN and e.code not in (None, 0):
            show_error(str(e.code))
        raise
    except Exception:
        if not FROZEN:
            raise
        import traceback
        traceback.print_exc()
        show_error("Calex Lights crashed:\n\n%s\nDetails are in %s" % (traceback.format_exc(limit=1), LOG_FILE))
        sys.exit(1)


def show_error(message):
    print(message)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Calex Lights", 0x10)


if __name__ == "__main__":
    run()
