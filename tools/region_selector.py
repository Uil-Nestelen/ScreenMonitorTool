"""Thin backward-compatible wrapper.

The real implementation moved to interface/setup_wizard.py (plan section
4.10) as part of the GUI milestone - this file just keeps the old command
working: `python tools/region_selector.py ...` still does the same thing.
Prefer `python -m screen_monitor.interface.setup_wizard` going forward,
or the dashboard's "Configure Regions" button.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from screen_monitor.interface.setup_wizard import main

if __name__ == "__main__":
    main()
