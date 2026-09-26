"""Account Switcher integration of Vitals' real compact account rows.

UI only: no account manager, auth store, updater, or process restart imports.
Upstream copyright and MIT license remain in the repository LICENSE.
"""
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from .compact_ui import AccountRow, AccountRowActions
from .models import StoredAccount, StoredAccountSource, AccountRuntimeState, AccountUsageSnapshot, UsageWindowSnapshot
from .switcher_theme import PALETTE, FONTS
from account_switcher.native_controls import FluentButton, Motion, _mix


def usage_state(data):
    return AccountRuntimeState(snapshot=AccountUsageSnapshot(
        email=None, provider_account_id=None, plan=None,
        allowed=data['eligible'], limit_reached=not data['eligible'],
        primary_window=UsageWindowSnapshot(data['five_hour'], datetime.fromtimestamp(data['reset_at'], timezone.utc), 18000),
        secondary_window=UsageWindowSnapshot(data['weekly'], datetime.fromtimestamp(data['weekly_reset_at'], timezone.utc), 604800),
        credits=None, updated_at=datetime.now(timezone.utc)))


class SwitcherAccountRow(AccountRow):
    """Keep upstream layout, metrics, ellipsis, hover, and active-state drawing."""
    SOURCE_WIDTH = 0
    MENU_WIDTH = 0

    def __init__(self, parent, data, logo, switch):
        self.logo = logo
        self.action_button = None
        self.active_amount = float(data["active"])
        self._skip_motion = False
        now = datetime.now(timezone.utc)
        alias = data['alias'].split(' · ')[-1].replace(' (synthetic)', '')
        account = StoredAccount(
            id=uuid5(NAMESPACE_URL, data['id']), nickname=alias,
            email_hint='Synthetic account', auth_subject=None,
            provider_account_id=None, codex_home_path='',
            source=StoredAccountSource.MANAGED_BY_APP, created_at=now, updated_at=now)
        noop = lambda: None
        super().__init__(parent, account=account, state=usage_state(data),
            palette=PALETTE.copy(), fonts=FONTS, is_active=data['active'],
            can_switch=data['eligible'], needs_reauthentication=False,
            is_reauthenticating=False,
            actions=AccountRowActions(switch, noop, noop, noop, noop, noop, noop))
        self.active_motion = Motion(self, self.active_amount, self._paint_active)
        self.action_button = FluentButton(self, text='Swap', width=6, command=lambda:self._keyboard_switch(None))
        self.configure(takefocus=True)
        self.bind('<Return>', self._keyboard_switch)
        self.bind('<space>', self._keyboard_switch)
        self.bind('<FocusIn>', self._redraw)
        self.bind('<FocusOut>', self._redraw)

    def update_account(self, data, busy=False):
        changed = self.is_active != data['active']
        self.state = usage_state(data)
        self.is_active = data['active']
        self.can_switch = data['eligible'] and not busy
        if changed:
            self.active_motion.to(float(self.is_active), duration=190, animate=not self._skip_motion)
            self._skip_motion = False
        else:
            self._redraw()

    def _keyboard_switch(self, _):
        self._skip_motion = _ is not None
        command = self._row_action()[1]
        if command:
            command()
        return 'break'

    def _draw_status_icon(self, x, y):
        self.create_image(x, y, image=self.logo)

    def _draw_source_chip(self, left, top):
        pass

    def _draw_menu_button(self, left, right):
        self._menu_region = (-10, -10, -1, -1)
        if self.focus_get() == self:
            self.create_rectangle(5, 3, self.winfo_width()-5, self.HEIGHT-4,
                                  outline=self.palette['success'])

    def _show_menu(self, event):
        pass

    def _paint_active(self, value):
        self.active_amount = value
        fill = _mix(PALETTE['list'], PALETTE['active_row'], value)
        self.palette['list'] = fill
        self.palette['active_row'] = fill
        self.palette['active_border'] = _mix(PALETTE['list'], PALETTE['active_border'], value)
        self._redraw()

    def _draw_action_button(self, left, right):
        if self.action_button is None:
            return
        text, command, kind = self._row_action()
        fill = self.palette['active_row_hover'] if self.is_active and self._hovering else self.palette['active_row'] if self.is_active else self.palette['row_hover'] if self._hovering else self.palette['list']
        self.action_button.configure(text=text, state='normal' if command else 'disabled', bg=fill)
        self.create_window((left+right)/2, 29, window=self.action_button)
