#!/usr/bin/env python3
"""
run_daily.py

Runs the NBA Ledger data pipeline in dependency order:

  1. Box scores        (first run: whole season; after that: every newly finished date)
  1b. Real positions (PG/SG/SF/PF/C, Basketball-Reference)
  2. Team tables        (team game log, team summary)                 <- box scores
  3. Strength of schedule                                              <- team tables
  4. Defense vs position                                               <- box scores
  5. Upcoming matchups  (tonight + the next date with games)          <- DvP, team tables
  6. Injury report, efficiency / style, game flow (first basket, quarters),
     backtest, milestones                                               (non-critical)

There is no "build the dashboard" step: site/index.html fetches these CSVs every
time it loads, so refreshing the CSVs refreshes the dashboard.

  python3 run_daily.py                  full refresh
  python3 run_daily.py --quick          just injuries + tonight's slate (the afternoon run)
  python3 run_daily.py --date 2026-11-03   re-pull one date, then rebuild everything downstream
"""
import argparse
import subprocess
import sys
from pathlib import Path

from nba_common import PLAYER_FILE

PIPELINE_DIR = Path(__file__).resolve().parent


def run(script_name: str, critical: bool = True, extra_args: list[str] | None = None) -> None:
    cmd = [sys.executable, str(PIPELINE_DIR / script_name)] + (extra_args or [])
    print(f"\n=== Running {script_name} {' '.join(extra_args or [])} ===", flush=True)
    result = subprocess.run(cmd, cwd=PIPELINE_DIR)
    if result.returncode != 0:
        if critical:
            raise RuntimeError(f"{script_name} failed with exit code {result.returncode}")
        print(f"WARNING: {script_name} failed (exit {result.returncode}) -- continuing, "
              f"this step is not required for the core dashboard.")


def main(force_date: str | None = None, quick: bool = False):
    if quick and PLAYER_FILE.exists():
        # Afternoon run: tonight's injury news and lines move; box scores don't
        # (a date is only saved once all of its games are final, late at night).
        run("08_injury_report.py", critical=False)
        run("06_matchup_preview.py")
        print("\nQuick refresh complete.")
        return

    if force_date:
        run("02_daily_update.py", extra_args=["--date", force_date])
    elif PLAYER_FILE.exists():
        run("02_daily_update.py")
    else:
        run("01_initial_pull.py")

    # PG / SG / SF / PF / C from Basketball-Reference (ESPN only says G / F / C). Before anything that groups by position.
    run("11_positions.py", critical=False)
    run("03_build_team_tables.py")
    run("04_strength_of_schedule.py")
    run("05_defense_vs_position.py")
    run("06_matchup_preview.py")

    # Everything below is non-critical: a hiccup in one can't block the core refresh.
    run("08_injury_report.py", critical=False)
    run("09_team_efficiency.py", critical=False)
    # Opening tip, first basket, team first basket, points by quarter (one ESPN request per new game).
    run("10_game_flow.py", critical=False)
    run("16_backtest.py", critical=False)
    run("07_milestone_watch.py", critical=False)

    print("\nPipeline complete. site/data/*.csv is up to date -- reload the dashboard to see it.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--date", default=None, help="Re-pull one ET date (YYYY-MM-DD), then rebuild everything downstream.")
    cli.add_argument("--quick", action="store_true", help="Only refresh injuries and the upcoming slate.")
    a = cli.parse_args()
    try:
        main(force_date=a.date, quick=a.quick)
    except RuntimeError as exc:
        print(f"\nPIPELINE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
