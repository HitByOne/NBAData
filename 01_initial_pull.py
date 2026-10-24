#!/usr/bin/env python3
"""
01_initial_pull.py

First-time pull of the whole season so far. It's the same code as the daily
update, started from the beginning of the season scan instead of from the last
saved date. run_daily.py calls this only when no player CSV exists yet.
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.exit(subprocess.call([sys.executable, str(here / "02_daily_update.py"), "--rebuild"], cwd=here))
