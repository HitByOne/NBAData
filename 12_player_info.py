#!/usr/bin/env python3
"""
12_player_info.py

Every player on every NBA roster: birth date, age, jersey, height and weight, from ESPN's team roster endpoint
(30 requests, one per team). The dashboard uses the birth date for the birthday tag on the player cards.

Birth dates don't change, so this only goes to ESPN when it has to: when the file doesn't exist yet, or when someone
has played in a box score this season who isn't in the file (a rookie, a call-up, a new signing). Every other day it
does nothing. Run it with --force to refresh everything (jerseys and teams after trades, say).

Writes: site/data/nba_2027_player_info.csv  (Player ID, Player, Team, Birth Date, Jersey, Height, Weight)
If ESPN can't be reached the existing file is kept. Non-critical in run_daily.py.
"""
import csv
import sys
import time

from nba_common import BASE_URL, PLAYER_FILE, PROCESSED_DIR, SEASON, get_json, get_teams, roster_athletes

OUT = PROCESSED_DIR / f"nba_{SEASON}_player_info.csv"
FIELDS = ["Player ID", "Player", "Team", "Birth Date", "Jersey", "Height", "Weight"]


def needs_refresh():
    if "--force" in sys.argv or not OUT.exists():
        return True, "no player info file yet" if not OUT.exists() else "--force"
    try:
        with open(OUT, newline="", encoding="utf-8") as f:
            have = {r["Player ID"] for r in csv.DictReader(f)}
        with open(PLAYER_FILE, newline="", encoding="utf-8") as f:
            played = {r["Player ID"] for r in csv.DictReader(f)}
    except Exception as exc:
        return True, f"couldn't compare files ({exc})"
    missing = played - have
    return (True, f"{len(missing)} player(s) not in the file yet") if missing else (False, "")


def main():
    go, why = needs_refresh()
    if not go:
        print("Player info is up to date (everyone who has played is in the file) -- no requests made.")
        return 0
    print(f"Refreshing player info: {why}.")
    rows, failed = [], 0
    for team_id, abbr in get_teams():
        try:
            data = get_json(f"{BASE_URL}/teams/{team_id}/roster")
        except Exception as exc:
            failed += 1
            print(f"  roster for {abbr} failed: {exc}")
            continue
        for a in roster_athletes(data):
            if not a.get("id"):
                continue
            rows.append({
                "Player ID": str(a["id"]), "Player": a.get("displayName") or a.get("fullName") or "", "Team": abbr,
                "Birth Date": str(a.get("dateOfBirth") or "")[:10],
                "Jersey": a.get("jersey") or "", "Height": a.get("displayHeight") or "", "Weight": a.get("displayWeight") or "",
            })
        time.sleep(0.2)
    if not rows:
        print("No roster data came back; keeping the existing player info file.")
        return 0
    # Players who have played but are on no roster now (waived, two-way, retired) still get a row, without a birth
    # date, so tomorrow's run doesn't go back to ESPN looking for them again.
    try:
        got = {r["Player ID"] for r in rows}
        with open(PLAYER_FILE, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["Player ID"] not in got:
                    got.add(r["Player ID"])
                    rows.append({"Player ID": r["Player ID"], "Player": r["Player"], "Team": r["Team"], "Birth Date": "", "Jersey": "", "Height": "", "Weight": ""})
    except FileNotFoundError:
        pass
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["Team"], r["Player"])))
    with_dob = sum(1 for r in rows if r["Birth Date"])
    print(f"Saved {len(rows)} players ({with_dob} with a birth date){', ' + str(failed) + ' rosters failed' if failed else ''}: {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
