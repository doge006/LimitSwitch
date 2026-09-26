import subprocess
import sys
import unittest

from account_switcher.tray_panel import position, tooltip


class VitalsIntegrationTests(unittest.TestCase):
    def test_ui_import_does_not_load_auth_or_updater(self):
        result = subprocess.run([sys.executable, '-c', '''
import sys
from account_switcher.vitals_ui import SwitcherAccountRow
from codexvitals_windows.compact_ui import AccountRow
assert issubclass(SwitcherAccountRow, AccountRow)
for name in ('app', 'account_manager', 'codex_api', 'update_manager'):
    assert 'codexvitals_windows.' + name not in sys.modules, name
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_panel_stays_in_monitor_work_area(self):
        self.assertEqual(position((1920, 1080), (0, 0, 1920, 1040), (430, 360)), (1490, 680))
        self.assertEqual(position((-1900, 15), (-1920, 0, 0, 1080), (430, 360)), (-1920, 0))

    def test_tooltip_shows_selected_accounts_and_remaining_quota(self):
        accounts = [dict(provider='codex', active=True, five_hour=20, weekly=40),
                    dict(provider='claude', active=True, five_hour=55, weekly=90)]
        value = tooltip(accounts)
        self.assertIn('Codex: 5h 80%', value)
        self.assertIn('Claude: 5h 45%', value)
        self.assertLessEqual(len(value), 127)
