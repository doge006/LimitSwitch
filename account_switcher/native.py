"""Native tray dashboard sharing one controller and localhost server with the Web UI.

Rounded canvas primitive and ToggleSwitch adapted from Codex Vitals compact_ui.py (MIT).
Copyright (c) 2026 Codex Switchboard contributors
Copyright (c) 2026 Codex Vitals contributors
Full upstream license: ../codex-vitals-source/LICENSE.
Account rows are provided by our local Codex Vitals fork.
No upstream authentication or application startup code is imported.
"""
import argparse
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
from pathlib import Path

from .web import Controller, make_server
from .tray_panel import StatusPanel, tooltip
from .vitals_ui import SwitcherAccountRow
from .native_controls import FluentButton, Motion
from .native_chrome import apply_chrome

BG = "#1e1e1e"
TEXT = "#eeeeee"
MUTED = "#aaaaaa"
GREEN = "#79c99b"


def rounded(canvas, x1, y1, x2, y2, radius, **kwargs):
    """Adapted from Codex Vitals' draw_rounded_rectangle (MIT)."""
    radius = max(0, min(radius, (x2-x1)/2, (y2-y1)/2))
    points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,
              x2,y2-radius,x2,y2,x2-radius,y2,x1+radius,y2,
              x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def countdown(timestamp):
    minutes = max(0, int((timestamp-time.time())/60))
    if minutes >= 1440:
        return f"{minutes//1440}d {minutes%1440//60}h"
    return f"{minutes//60}h {minutes%60}m"


class ToggleSwitch(tk.Canvas):
    """Vitals ToggleSwitch adapted for keyboard access and finite native motion.

    Track/knob geometry and interaction model are from compact_ui.py (MIT).
    """
    def __init__(self, parent, variable, command):
        super().__init__(parent, width=38, height=22, bg=BG, highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.variable, self.command = variable, command
        self.enabled = True
        self.position = float(variable.get())
        self.job = None
        self.animations = False
        try:
            import ctypes
            value = ctypes.c_int()
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
                self.animations = bool(value.value)
        except (AttributeError, OSError):
            pass
        self.bind("<Button-1>", self.click)
        self.bind("<space>", self.click)
        self.bind("<Return>", self.click)
        self.bind("<FocusIn>", self.draw)
        self.bind("<FocusOut>", self.draw)
        self.variable.trace_add("write", self.changed)
        self.draw()

    def click(self, _=None):
        if self.enabled:
            animate = self.animations
            if _ is not None and str(_.type) == '2':
                self.animations = False
            self.variable.set(not self.variable.get())
            self.animations = animate
            self.command()
        return "break"

    def changed(self, *_):
        target = float(self.variable.get())
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        if not self.animations or not self.winfo_viewable():
            self.position = target
            self.draw()
            return
        start, started = self.position, time.monotonic()
        def frame():
            progress = min(1., (time.monotonic()-started)/.14)
            self.position = start + (target-start)*(1-(1-progress)**3)
            self.draw()
            self.job = self.after(16, frame) if progress < 1 else None
        frame()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.draw()

    def draw(self, _=None):
        self.delete("all")
        track = "#438060" if self.variable.get() else "#484848"
        if not self.enabled:
            track = "#383838"
        rounded(self, 1, 1, 37, 21, 10, fill=track, outline=TEXT if self.focus_get() == self else track)
        x = 11 + 16*self.position
        self.create_oval(x-7, 4, x+7, 18, fill="white", outline="")


class Dashboard:
    def __init__(self, controller, server, smoke=False, hidden=False):
        self.controller, self.server = controller, server
        self.root = tk.Tk()
        self.root.title("Account Switcher")
        self.root.geometry("900x600")
        self.root.minsize(860, 600)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.commands = queue.Queue(maxsize=20)
        self.closing = False
        self.last_revision = -1
        self.last_minute = -1
        self.tray = None
        self.panel = None
        self.tray_revision = -1
        self.root.bind("<<TrayCommand>>", lambda _: self.drain_commands())
        self.callback_error = None
        self.root.report_callback_exception = self.report_error
        self.rows = {}
        self.buttons = []
        self.state = {}
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TCheckbutton", background=BG)
        style.configure("TButton", background="#303030", foreground=TEXT, bordercolor="#414141", lightcolor="#303030", darkcolor="#303030", padding=(12, 7), font=("Segoe UI", 9))
        style.map("TButton", background=[("active", "#3a3a3a")], foreground=[("disabled", "#858585")])
        style.configure("TCombobox", fieldbackground="#292929", background="#303030", foreground=TEXT, arrowcolor=TEXT, bordercolor="#414141")
        style.map("TCombobox", fieldbackground=[("readonly", "#292929")], foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", "#292929")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        from PIL import Image, ImageTk
        assets = Path(__file__).with_name("static") / "assets"
        self.logos = {name: ImageTk.PhotoImage(Image.open(assets / f"{name}.png").convert("RGBA").resize((22, 22), Image.Resampling.LANCZOS)) for name in ("codex", "claude")}
        brand = Image.open(assets / 'switcher.png').convert('RGBA')
        self.brand = ImageTk.PhotoImage(brand.resize((42,42), Image.Resampling.LANCZOS))
        self.brand_small = ImageTk.PhotoImage(brand.resize((28,28), Image.Resampling.LANCZOS))
        self.root.iconphoto(True, self.brand)
        self.root.iconbitmap(str(assets / 'switcher.ico'))
        self.root.after_idle(self.dark_chrome)
        self.window_motion = Motion(self.root, 1., lambda value: self.root.attributes('-alpha', value))
        self.tools_open = False
        self.tools_motion = Motion(self.root, 600., lambda value: self.root.geometry(f'{self.root.winfo_width()}x{round(value)}'))

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(header, image=self.brand, bg=BG).pack(side="left", padx=(0, 12))
        tk.Label(header, text="Account Switcher", font=("Segoe UI", 22, "bold"), bg=BG, fg=TEXT).pack(side="left")
        FluentButton(header, text="Open Web UI  ↗", command=self.open_web).pack(side="right")
        tk.Label(self.root, text="Quota remaining  ·  Test accounts and usage", bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=26, pady=(0, 16))
        self.account_area = tk.Frame(self.root, bg=BG)
        self.account_area.pack(fill="x", padx=20)
        for provider, color, label in [("codex", "#7057cf", "CODEX"), ("claude", "#b85e42", "CLAUDE")]:
            heading = tk.Frame(self.account_area, bg="#252525", height=33)
            heading.pack(fill="x", pady=(0 if provider == "codex" else 10, 0))
            tk.Label(heading, image=self.logos[provider], compound="left", text=f"  {label.title()}", font=("Segoe UI", 10, "bold"), fg=TEXT, bg=heading.cget("bg"), padx=16, pady=7).pack(side="left")
            for account in controller.snapshot()["accounts"]:
                if account["provider"] != provider:
                    continue
                row = SwitcherAccountRow(self.account_area, account, self.logos[provider],
                    lambda aid=account["id"]: self.action("swap", {"id": aid}))
                row.pack(fill="x")
                self.rows[account["id"]] = row


        options = tk.Frame(self.root, bg=BG)
        options.pack(fill="x", padx=24, pady=14)
        self.auto = tk.BooleanVar(value=True)
        self.afk = tk.BooleanVar(value=False)
        self.auto_control = ToggleSwitch(options, self.auto, self.preferences)
        self.auto_control.pack(side="left")
        tk.Label(options, text="Auto swap", bg=BG, fg=TEXT).pack(side="left", padx=(7, 20))
        self.afk_control = ToggleSwitch(options, self.afk, self.preferences)
        self.afk_control.pack(side="left")
        tk.Label(options, text="AFK · continue interrupted turns", bg=BG, fg=TEXT).pack(side="left", padx=7)
        self.reset_button = FluentButton(options, text="Reset test accounts", command=lambda: self.action("reset"))
        self.reset_button.pack(side="right")
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=24, pady=(0, 10))
        FluentButton(footer, text="Session tools", command=self.toggle_tools).pack(side="left")
        FluentButton(footer, text="Exit", command=self.exit).pack(side="right")
        self.lab = tk.Frame(self.root, bg=BG)
        self.status = tk.Label(self.lab, text="Ready", bg=BG, fg=GREEN, anchor="w")
        self.status.pack(fill="x", padx=26)
        self.output = tk.Text(self.lab, height=5, wrap="word", bg="#161616", fg=TEXT, relief="flat", padx=12, pady=10, font=("Consolas", 10), state="disabled")
        self.output.pack(fill="both", expand=True, padx=24, pady=8)
        controls = tk.Frame(self.lab, bg=BG)
        controls.pack(fill="x", padx=24, pady=(0, 10))
        self.scenario = ttk.Combobox(controls, values=["Mid-response quota failure", "Quota before response", "Normal response"], state="readonly", width=29)
        self.scenario.current(0)
        self.scenario.pack(side="left")
        for label, command in [("Run Claude test", self.run), ("Continue", lambda: self.action("continue")), ("Stop", lambda: self.action("stop"))]:
            button = FluentButton(controls, text=label, command=command)
            button.pack(side="left", padx=(8, 0))
            self.buttons.append(button)
        tk.Label(self.lab, text="Codex: selection demo  ·  Claude tests: tools disabled", bg=BG, fg=MUTED, anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=26, pady=(0, 12))
        self.setup_tray()
        if hidden:
            self.root.withdraw()
        self.poll()
        if smoke:
            self.root.after(500, self.smoke_start)

    def report_error(self, kind, error, traceback):
        import traceback as trace
        self.callback_error = error
        trace.print_exception(kind, error, traceback)
        self.exit()

    def dark_chrome(self):
        self.chrome = apply_chrome(self.root)

    def toggle_tools(self):
        self.tools_open = not self.tools_open
        if self.tools_motion.job is None:
            self.tools_motion.value = self.root.winfo_height()
        if self.tools_open:
            self.lab.pack(fill='both', expand=True)
            self.tools_motion.to(820., duration=220)
        else:
            self.tools_motion.to(600., duration=180, done=self.lab.pack_forget)

    def show_panel(self, focus=True):
        if self.panel:
            if self.panel.closing:
                self.panel.show()
            else:
                self.panel.close()
        else:
            self.panel = StatusPanel(self)
            self.panel.show(focus=focus)

    def drain_commands(self):
        while not self.commands.empty() and not self.closing:
            command = self.commands.get_nowait()
            {"show": self.show, "status": self.show_panel, "web": self.open_web, "exit": self.exit}[command]()

    def setup_tray(self):
        try:
            import pystray
            from PIL import Image
            icon = Image.open(Path(__file__).with_name('static') / 'assets/switcher.ico').convert('RGBA')
            def enqueue(action):
                try:
                    self.commands.put_nowait(action)
                    self.root.event_generate("<<TrayCommand>>", when="tail")
                except (queue.Full, RuntimeError, tk.TclError):
                    pass
            self.tray = pystray.Icon("account-switcher", icon, "Account Switcher · local test mode", pystray.Menu(
                pystray.MenuItem("Account status", lambda *_: enqueue("status"), default=True),
                pystray.MenuItem("Open dashboard", lambda *_: enqueue("show")),
                pystray.MenuItem("Open Web UI", lambda *_: enqueue("web")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda *_: enqueue("exit"))))
            self.tray.run_detached()
        except ImportError:
            self.status.configure(text="Tray unavailable: install pystray==0.19.5. Close will exit.")

    def action(self, name, body=None):
        try:
            self.controller.action(name, body or {})
        except (RuntimeError, ValueError) as error:
            messagebox.showerror("Account Switcher", str(error), parent=self.root)

    def preferences(self):
        self.action("preferences", {"autoSwap": self.auto.get(), "afk": self.afk.get()})

    def run(self):
        self.action("run", {"scenario": ["partial", "quota", "normal"][self.scenario.current()]})

    def open_web(self):
        webbrowser.open(self.server.launch_url)

    def hide(self):
        if self.tray:
            self.window_motion.to(0., duration=120, done=self.root.withdraw)
        else:
            self.exit()

    def show(self):
        if self.root.state() == 'withdrawn':
            self.window_motion.value = 0.
            self.root.attributes('-alpha', 0.)
        self.root.deiconify()
        self.dark_chrome()
        self.window_motion.to(1., duration=180)
        self.root.lift()
        self.root.focus_force()

    def redraw_rows(self):
        for account in self.state.get("accounts", []):
            self.rows[account["id"]].update_account(account, self.state.get("busy", False))

    def poll(self):
        if self.closing:
            return
        self.drain_commands()
        if self.closing:
            return
        if self.controller.closed:
            self.exit()
            return
        visible = self.root.state() != "withdrawn"
        state = self.controller.snapshot()
        if state["revision"] != self.tray_revision:
            self.tray_revision = state["revision"]
            if self.tray:
                self.tray.title = tooltip(state["accounts"])
            if self.panel:
                self.panel.update(state)
        if visible and (state["revision"] != self.last_revision or int(time.time()/60) != self.last_minute):
            self.state = state
            self.last_revision = state["revision"]
            self.last_minute = int(time.time()/60)
            if self.auto.get() != state["autoSwap"]:
                self.auto.set(state["autoSwap"])
            if self.afk.get() != state["afk"]:
                self.afk.set(state["afk"])
            self.status.configure(text=f"CLAUDE SESSION   ·   {state['status']}   |   Usage shown as remaining")
            content = state["output"] or "Enable AFK and run a mid-response test to watch Claude settle, switch and continue."
            if self.output.get("1.0", "end-1c") != content:
                self.output.configure(state="normal")
                self.output.delete("1.0", "end")
                self.output.insert("1.0", content)
                self.output.see("end")
                self.output.configure(state="disabled")
            self.auto_control.set_enabled(not state["busy"])
            self.afk_control.set_enabled(not state["busy"])
            for control in [self.reset_button, *self.buttons[:2]]:
                control.configure(state="disabled" if state["busy"] else "normal")
            self.redraw_rows()
        self.root.after(500 if visible or self.panel else 2000, self.poll)

    def smoke_start(self):
        assert len(self.controller.snapshot()["accounts"]) == 4
        row = self.rows["codex-b"]
        assert isinstance(row, SwitcherAccountRow)
        row._keyboard_switch(None)
        self.root.after(700, self.smoke_finish)

    def smoke_finish(self):
        assert next(a for a in self.controller.snapshot()["accounts"] if a["id"] == "codex-b")["active"]
        import json
        from urllib.request import Request, urlopen
        url, token = self.server.launch_url.split("/#token=")
        with urlopen(Request(url+"/api/state", headers={"Authorization": "Bearer "+token}), timeout=3) as response:
            assert json.load(response)["revision"] == self.controller.revision
        self.hide()
        self.show_panel(focus=False)
        assert len(self.panel.rows) == 4
        assert "5h" in tooltip(self.controller.snapshot()["accounts"])
        self.panel.close(animate=False)
        print("SMOKE PASS: native rows, swap, shared HTTP state, tray hide/show", flush=True)
        with urlopen(Request(url+"/api/shutdown", data=b"{}", headers={"Authorization": "Bearer "+token, "Content-Type": "application/json"}), timeout=3) as response:
            assert response.status == 200

    def exit(self):
        if self.closing:
            return
        self.closing = True
        self.window_motion.cancel()
        self.tools_motion.cancel()
        if self.panel:
            self.panel.close(animate=False)
        self.root.withdraw()
        if self.tray:
            self.tray.stop()
        def close():
            self.server.shutdown()
            self.controller.close()
            self.server.server_close()
        self.cleanup = threading.Thread(target=close)
        self.cleanup.start()
        self.wait_closed()

    def wait_closed(self):
        if self.cleanup.is_alive():
            self.root.after(50, self.wait_closed)
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--simulator", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--tray", action="store_true", help="Start with the dashboard hidden in the system tray")
    args = parser.parse_args()
    controller = Controller(args.simulator)
    server = make_server(controller, args.port, idle_seconds=0)
    if sys.stdout:
        print(server.launch_url, flush=True)
    def serve():
        try:
            server.serve_forever(poll_interval=.5)
        finally:
            controller.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        app = Dashboard(controller, server, args.smoke_test, args.tray)
    except Exception:
        server.shutdown()
        controller.close()
        server.server_close()
        raise
    try:
        app.root.mainloop()
    finally:
        if not app.closing:
            app.exit()
        thread.join(timeout=10)
    if args.smoke_test:
        if app.callback_error:
            raise app.callback_error
        assert controller.closed and not thread.is_alive()
        print("SMOKE PASS: server and controller closed", flush=True)


if __name__ == "__main__":
    main()
