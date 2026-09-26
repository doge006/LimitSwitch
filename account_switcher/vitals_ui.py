"""Load UI components from our pinned local Codex Vitals fork."""
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'codex-vitals-source' / 'windows'
if not SOURCE.is_dir():
    raise RuntimeError('The bundled codex-vitals-source checkout is required.')
sys.path.insert(0, str(SOURCE))
from codexvitals_windows.switcher_ui import SwitcherAccountRow
