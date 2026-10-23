"""System tray icon with a quick-control popup for Calex Lights.

Left-click the tray icon to open a small flyout with an on/off switch and a
brightness slider per light. Right-click for a menu with the full web control
panel, All on/off and Quit.

Tk runs on the main thread. pystray and the light I/O run on other threads and
hand work back to Tk through a queue, because Tk isn't thread-safe.
"""

import os
import queue
import sys
import time
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

ACCENT = "#f5a524"
THEMES = {
    "dark": {"bg": "#202024", "card": "#2a2a30", "text": "#ececf1", "muted": "#9a9aa6",
             "line": "#38383f", "off": "#4a4a53", "knob": "#ffffff", "hover": "#34343b"},
    "light": {"bg": "#f7f7f9", "card": "#ffffff", "text": "#1b1b1f", "muted": "#6b6b75",
              "line": "#e3e3e8", "off": "#c9c9d1", "knob": "#ffffff", "hover": "#ececf1"},
}
FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"


def make_icon(size=64):
    """Draw the bulb icon used for the tray and the exe."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.ellipse((12 * s, 4 * s, 52 * s, 44 * s), fill=ACCENT)
    d.rectangle((23 * s, 38 * s, 41 * s, 48 * s), fill=ACCENT)
    d.rounded_rectangle((22 * s, 49 * s, 42 * s, 54 * s), radius=2 * s, fill="#8a8a94")
    d.rounded_rectangle((25 * s, 56 * s, 39 * s, 61 * s), radius=2 * s, fill="#8a8a94")
    return img


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return '"%s" "%s"' % (pythonw, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"))


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, "CalexLights")[0] == autostart_command()
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "CalexLights", 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "CalexLights")
            except OSError:
                pass


def our_window_is_foreground():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value == os.getpid()


def windows_theme():
    if sys.platform != "win32":
        return "dark"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "dark"


def work_area(root):
    """(right, bottom) of the screen area not covered by the taskbar."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.right, rect.bottom
    return root.winfo_screenwidth(), root.winfo_screenheight() - 48


class Toggle(tk.Canvas):
    def __init__(self, master, theme, scale, command):
        self.w, self.h = int(40 * scale), int(22 * scale)
        super().__init__(master, width=self.w, height=self.h, bg=theme["card"], highlightthickness=0, cursor="hand2")
        self.theme, self.command, self.on, self.enabled = theme, command, False, True
        self.bind("<Button-1>", lambda e: self.enabled and self.command(not self.on))
        self.draw()

    def set(self, on, enabled=True):
        self.on, self.enabled = on, enabled
        self.draw()

    def draw(self):
        self.delete("all")
        w, h, r = self.w, self.h, self.h / 2
        fill = ACCENT if self.on and self.enabled else self.theme["off"]
        self.create_oval(0, 0, h, h, fill=fill, outline="")
        self.create_oval(w - h, 0, w, h, fill=fill, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="")
        pad = h * 0.14
        x = w - h + pad if self.on else pad
        self.create_oval(x, pad, x + h - 2 * pad, h - pad, fill=self.theme["knob"], outline="")


class Slider(tk.Canvas):
    def __init__(self, master, theme, scale, command):
        self.h = int(18 * scale)
        super().__init__(master, height=self.h, bg=theme["card"], highlightthickness=0, cursor="hand2")
        self.theme, self.command, self.value, self.scale = theme, command, 50, scale
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.drag)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", lambda e: self.command(self.value))

    def set(self, value):
        self.value = value
        self.draw()

    def drag(self, e):
        r = self.h / 2
        w = max(self.winfo_width() - 2 * r, 1)
        self.value = max(1, min(100, round((e.x - r) / w * 99 + 1)))
        self.draw()

    def draw(self):
        self.delete("all")
        r, w = self.h / 2, self.winfo_width()
        x = r + (w - 2 * r) * (self.value - 1) / 99
        t = max(3, round(4 * self.scale))
        self.create_line(r, r, w - r, r, fill=self.theme["off"], width=t, capstyle="round")
        self.create_line(r, r, x, r, fill=ACCENT, width=t, capstyle="round")
        self.create_oval(x - r + 2, 2, x + r - 2, self.h - 2, fill=self.theme["knob"], outline=ACCENT, width=2)


class LightRow:
    def __init__(self, app, parent, light):
        self.app, self.light, t, s = app, light, app.theme, app.scale
        self.frame = tk.Frame(parent, bg=t["card"], padx=int(12 * s), pady=int(10 * s))
        self.dot = tk.Canvas(self.frame, width=int(22 * s), height=int(22 * s), bg=t["card"], highlightthickness=0)
        self.dot.grid(row=0, column=0, rowspan=2, padx=(0, int(10 * s)))
        self.name = tk.Label(self.frame, text=light.name, bg=t["card"], fg=t["text"], font=(FONT, 10, "bold"), anchor="w")
        self.name.grid(row=0, column=1, sticky="ew")
        self.status = tk.Label(self.frame, text="Connecting…", bg=t["card"], fg=t["muted"], font=(FONT, 8), anchor="w")
        self.status.grid(row=1, column=1, sticky="ew")
        self.toggle = Toggle(self.frame, t, s, lambda on: self.send({"on": on}))
        self.toggle.grid(row=0, column=2, rowspan=2, padx=(int(8 * s), 0))
        self.slider = Slider(self.frame, t, s, lambda v: self.send({"brightness": v}))
        self.frame.columnconfigure(1, weight=1)
        self.state = {}
        self.draw_dot(t["off"])

    def draw_dot(self, colour):
        d = int(22 * self.app.scale)
        self.dot.delete("all")
        self.dot.create_oval(1, 1, d - 1, d - 1, fill=colour, outline="")

    def render(self, state):
        self.state = state
        t, online = self.app.theme, state.get("online")
        on = bool(state.get("on")) and online
        self.status.config(text=("On" if on else "Off") if online else "Offline")
        self.toggle.set(on, enabled=bool(online))
        if online and (state.get("caps") or {}).get("brightness"):
            self.slider.set(state.get("brightness") or 50)
            self.slider.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(int(6 * self.app.scale), 0))
        else:
            self.slider.grid_remove()
        glow = t["off"]
        if on:
            glow = state["colour"] if state.get("mode") == "colour" and state.get("colour") else ACCENT
        self.draw_dot(glow)
        self.app.place_popup()  # the slider may have appeared or gone

    def send(self, body):
        if "on" in body:
            self.render({**self.state, "on": body["on"]})  # optimistic
        self.app.run_bg(lambda: self.light.apply(body) or self.light.state(), self.render)

    def refresh(self):
        self.app.run_bg(self.light.state, self.render)


class TrayApp:
    def __init__(self, lights, panel_url):
        self.lights, self.panel_url = lights, panel_url
        self.loaded = False
        self.queue = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.icon = None
        self.hidden_at = 0.0

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.withdraw()
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.theme = THEMES[windows_theme()]
        self.rows = []
        self.build_popup()
        self.root.after(50, self.poll)

    # --- threading helpers -------------------------------------------------
    def call(self, fn):
        """Run fn on the Tk thread."""
        self.queue.put(fn)

    def run_bg(self, work, done):
        def job():
            try:
                result = work()
            except Exception as e:
                result = {"online": False, "error": str(e)}
            self.call(lambda: done(result))
        self.pool.submit(job)

    def poll(self):
        try:
            while True:
                self.queue.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    # --- popup -------------------------------------------------------------
    def build_popup(self):
        t, s = self.theme, self.scale
        self.popup = tk.Toplevel(self.root, bg=t["line"])
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.attributes("-topmost", True)
        self.popup.bind("<FocusOut>", self.on_focus_out)
        self.popup.bind("<Escape>", lambda e: self.hide())

        inner = tk.Frame(self.popup, bg=t["bg"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(inner, bg=t["bg"], padx=int(14 * s), pady=int(12 * s))
        header.pack(fill="x")
        tk.Label(header, text="Calex Lights", bg=t["bg"], fg=t["text"], font=(FONT, 12, "bold")).pack(side="left")
        self.button(header, "All off", lambda: self.set_all(False)).pack(side="right")
        self.button(header, "All on", lambda: self.set_all(True)).pack(side="right", padx=(0, int(6 * s)))

        self.list = tk.Frame(inner, bg=t["bg"], padx=int(10 * s))
        self.list.pack(fill="both", expand=True)
        self.placeholder = tk.Label(self.list, text="Finding your lights…", bg=t["bg"], fg=t["muted"],
                                    font=(FONT, 9), pady=int(24 * s))
        self.placeholder.pack(fill="x")

        footer = tk.Frame(inner, bg=t["bg"], padx=int(14 * s), pady=int(10 * s))
        footer.pack(fill="x")
        link = tk.Label(footer, text="Open control panel  ›", bg=t["bg"], fg=ACCENT, font=(FONT, 9), cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: (self.hide(), self.open_panel()))

    def button(self, parent, text, command):
        t = self.theme
        b = tk.Label(parent, text=text, bg=t["card"], fg=t["text"], font=(FONT, 9), cursor="hand2",
                     padx=int(10 * self.scale), pady=int(3 * self.scale))
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.config(bg=t["hover"]))
        b.bind("<Leave>", lambda e: b.config(bg=t["card"]))
        return b

    def set_lights_loaded(self):
        self.loaded = True
        self.placeholder.pack_forget()
        for row in self.rows:
            row.frame.destroy()
        self.rows = [LightRow(self, self.list, light) for light in self.lights.values()]
        for row in self.rows:
            row.frame.pack(fill="x", pady=(0, int(6 * self.scale)))
        if not self.rows:
            self.placeholder.config(text="No lights in devices.json")
            self.placeholder.pack(fill="x")
        if self.popup.winfo_viewable():
            self.show()

    def set_all(self, on):
        for row in self.rows:
            row.send({"on": on})

    def toggle_popup(self):
        if self.popup.winfo_viewable():
            self.hide()
        elif time.monotonic() - self.hidden_at > 0.5:  # the tray click itself just closed it
            self.show()

    def show(self):
        for row in self.rows:
            row.refresh()
        self.place_popup(force=True)
        self.popup.deiconify()
        self.popup.lift()
        self.popup.focus_force()
        self.schedule_refresh()
        if sys.platform == "win32":
            self.had_focus = False
            self.root.after(100, self.watch_foreground)

    def watch_foreground(self):
        # Borderless Tk windows don't reliably get FocusOut on Windows, so close
        # the popup once another app comes to the front. Only after we've had
        # focus, so a popup that never got focus doesn't vanish straight away.
        if not self.popup.winfo_viewable():
            return
        if our_window_is_foreground():
            self.had_focus = True
        elif self.had_focus:
            return self.hide()
        self.root.after(100, self.watch_foreground)

    def place_popup(self, force=False):
        """Size the popup to its content and pin it above the bottom-right of the taskbar."""
        if not (force or self.popup.winfo_viewable()):
            return
        width = int(320 * self.scale)
        self.popup.update_idletasks()
        height = self.popup.winfo_reqheight()
        right, bottom = work_area(self.root)
        margin = int(12 * self.scale)
        self.popup.geometry("%dx%d+%d+%d" % (width, height, right - width - margin, bottom - height - margin))

    def schedule_refresh(self):
        if getattr(self, "refresh_job", None):
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(15000, self.periodic_refresh)

    def periodic_refresh(self):
        self.refresh_job = None
        if self.popup.winfo_viewable():
            for row in self.rows:
                row.refresh()
            self.schedule_refresh()

    def hide(self):
        if self.popup.winfo_viewable():
            self.popup.withdraw()
            self.hidden_at = time.monotonic()

    def on_focus_out(self, event):
        # FocusOut also fires when focus moves between widgets inside the popup.
        self.root.after(150, lambda: self.popup.focus_get() is None and self.hide())

    def open_panel(self):
        webbrowser.open(self.panel_url)

    # --- tray icon ---------------------------------------------------------
    def start_tray(self):
        import pystray
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Show lights", lambda: self.call(self.toggle_popup), default=True),
            item("Open control panel", lambda: self.open_panel()),
            pystray.Menu.SEPARATOR,
            item("All on", lambda: self.call(lambda: self.set_all(True))),
            item("All off", lambda: self.call(lambda: self.set_all(False))),
            pystray.Menu.SEPARATOR,
            item("Start with Windows", lambda: set_autostart(not autostart_enabled()),
                 checked=lambda i: autostart_enabled(), visible=sys.platform == "win32"),
            item("Quit", lambda: self.call(self.quit)),
        )
        self.icon = pystray.Icon("CalexLights", make_icon(64), "Calex Lights", menu)
        self.icon.run_detached()

    def quit(self):
        if self.icon:
            self.icon.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.root.quit()

    def run(self):
        self.start_tray()
        self.root.mainloop()
        os._exit(0)  # don't wait on in-flight light requests
