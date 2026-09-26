"""Compact account status beside the Windows notification-area click."""
import ctypes
from ctypes import wintypes
import tkinter as tk
from .native_controls import FluentButton, Motion
from .native_chrome import apply_chrome
from . import vitals_ui  # registers the pinned UI source path
from codexvitals_windows.compact_ui import draw_rounded_rectangle

BG = "#1e1e1e"
FG = "#eeeeee"
MUTED = "#aaaaaa"


def tooltip(accounts):
    lines = ["Account Switcher · test data"]
    for provider in ("codex", "claude"):
        account = next((a for a in accounts if a["provider"] == provider and a["active"]), None)
        if account:
            lines.append(f"{provider.title()}: 5h {100-account['five_hour']:.0f}% · week {100-account['weekly']:.0f}% left")
    return "\n".join(lines)[:127]


def position(anchor, work, size):
    x, y = anchor
    left, top, right, bottom = work
    width, height = size
    return max(left, min(x-width+24, right-width)), max(top, min(y-height-12, bottom-height))


def tray_anchor():
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user = ctypes.windll.user32
    point = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(point))
    user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user.MonitorFromPoint.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    monitor = user.MonitorFromPoint(point, 2)
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("Cannot locate tray monitor")
    r = info.rcWork
    return (point.x, point.y), (r.left, r.top, r.right, r.bottom)


class TrayRow(tk.Canvas):
    def __init__(self, parent, data, owner):
        super().__init__(parent, height=66, bg=BG, highlightthickness=0)
        self.data = data
        self.active = float(data['active'])
        self.motion = Motion(self, self.active, self.paint_active)
        self.button = FluentButton(self, text='Swap', width=8,
            command=lambda: owner.action('swap', {'id': data['id']}))
        self.bind('<Configure>', lambda _: self.draw())

    def paint_active(self, value):
        self.active = value
        self.draw()

    def update_account(self, data, busy):
        changed = self.data['active'] != data['active']
        self.data = data
        self.button.configure(text='Active' if data['active'] else 'Swap',
            state='disabled' if busy or data['active'] or not data['eligible'] else 'normal')
        if changed:
            self.motion.to(float(data['active']), duration=190)
        else:
            self.draw()

    def draw(self):
        self.delete('all')
        w = self.winfo_width()
        if w < 100:
            return
        def mix(a, b):
            return '#' + ''.join(f'{round(int(a[i:i+2],16)+(int(b[i:i+2],16)-int(a[i:i+2],16))*self.active):02x}' for i in (1,3,5))
        fill = mix('#252525', '#27372f')
        draw_rounded_rectangle(self, 1, 2, w-1, 62, 9, fill=fill, outline=mix('#363636', '#496451'))
        name = self.data['alias'].split(' · ')[-1].replace(' (synthetic)', '')
        self.create_text(14, 19, text=name, anchor='w', fill=FG, font=('Segoe UI', 10, 'bold'))
        if self.data['active']:
            self.create_text(14, 43, text='In use', anchor='w', fill='#9edaba', font=('Segoe UI', 9))
        else:
            self.create_text(14, 43, text='Ready' if self.data['eligible'] else 'Exhausted', anchor='w', fill=MUTED, font=('Segoe UI', 9))
        for x, label, key in [(132,'5h','five_hour'),(226,'Week','weekly')]:
            left = max(0, min(100, 100-self.data[key]))
            color = '#8ed4ae' if left > 30 else '#d6ba77' if left > 10 else '#e59784'
            self.create_text(x, 21, text=label, anchor='w', fill=MUTED, font=('Segoe UI', 9))
            self.create_text(x+76, 21, text=f'{left:g}%', anchor='e', fill=color, font=('Segoe UI', 9, 'bold'))
            draw_rounded_rectangle(self,x,39,x+76,43,2,fill='#454a46')
            if left:
                draw_rounded_rectangle(self,x,39,x+76*left/100,43,2,fill=color)
        self.button.configure(bg=fill)
        self.create_window(w-51, 32, window=self.button)


class StatusPanel:
    def __init__(self, owner):
        self.owner = owner
        self.closing = False
        self.focus_requested = True
        self.dismiss_job = None
        self.window = tk.Toplevel(owner.root)
        self.window.title('Account Switcher · Accounts')
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self.window.configure(bg=BG)
        self.window.bind('<Escape>', lambda _: self.close(animate=False))
        self.window.bind('<FocusOut>', self.schedule_dismiss)
        self.motion = Motion(self.window, 0., self.render_motion)
        inner = tk.Frame(self.window, bg=BG, padx=16, pady=16)
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        top = tk.Frame(inner, bg=BG)
        top.pack(fill='x', pady=(0, 5))
        tk.Label(top, image=owner.brand_small, bg=BG).pack(side='left', padx=(0,10))
        tk.Label(top, text='Account Switcher', fg=FG, bg=BG, font=('Segoe UI', 12, 'bold')).pack(side='left')
        FluentButton(top, text='Open  ↗', command=self.open_dashboard).pack(side='right')
        tk.Label(inner, text='Quota remaining', bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack(anchor='w', pady=(6, 6))
        self.rows = {}
        for provider in ('codex', 'claude'):
            tk.Label(inner, image=owner.logos[provider], compound='left', text='  ' + provider.title(), bg=BG, fg=FG, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(10, 5))
            for account in owner.controller.snapshot()['accounts']:
                if account['provider'] != provider:
                    continue
                row = TrayRow(inner, account, owner)
                row.pack(fill='x')
                self.rows[account['id']] = row
        tk.Label(inner, text='Test accounts  ·  Local only', bg=BG, fg='#919995', font=('Segoe UI',9)).pack(anchor='w', pady=(12,0))
        self.update(owner.controller.snapshot())

    def update(self, state):
        if not self.closing:
            for account in state['accounts']:
                self.rows[account['id']].update_account(account, state['busy'])

    def show(self, focus=True):
        self.closing = False
        self.focus_requested = focus
        if self.dismiss_job:
            self.window.after_cancel(self.dismiss_job)
            self.dismiss_job = None
        self.window.update_idletasks()
        anchor, work = tray_anchor()
        self.width, self.height = 476, self.window.winfo_reqheight()
        self.x, self.y = position(anchor, work, (self.width, self.height))
        self.window.geometry(f'{self.width}x{self.height}+{self.x}+{self.y}')
        self.window.attributes('-alpha', self.motion.value)
        self.window.deiconify()
        self.chrome = apply_chrome(self.window, popup=True)
        if focus:
            self.window.focus_force()
        self.motion.to(1., duration=210)

    def render_motion(self, value):
        if not self.window.winfo_exists():
            return
        # Short travel toward the tray anchor; text remains sharp at full scale.
        offset = round(10*(1-value))
        self.window.geometry(f'{self.width}x{self.height}+{self.x}+{self.y+offset}')
        self.window.attributes('-alpha', value)

    def schedule_dismiss(self, event):
        if not self.focus_requested or self.closing:
            return
        if self.dismiss_job:
            self.window.after_cancel(self.dismiss_job)
        self.dismiss_job = self.window.after(100, self.dismiss_if_unfocused)

    def dismiss_if_unfocused(self):
        self.dismiss_job = None
        focus = self.window.focus_get()
        if focus is None or focus.winfo_toplevel() != self.window:
            self.close()

    def open_dashboard(self):
        self.close()
        self.owner.show()

    def close(self, animate=True):
        if not self.window.winfo_exists():
            return
        self.closing = True
        if self.dismiss_job:
            self.window.after_cancel(self.dismiss_job)
            self.dismiss_job = None
        self.motion.to(0., duration=120, done=self.destroy, animate=animate)

    def destroy(self):
        self.motion.cancel()
        if self.window.winfo_exists():
            self.window.destroy()
        if self.owner.panel is self:
            self.owner.panel = None
