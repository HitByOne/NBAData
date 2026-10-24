#!/usr/bin/env python3
"""
08_injury_report.py

League-wide NBA injury report from ESPN -> site/data/nba_2027_injury_report.csv
(Team, Player, Position, Status, Injury, Return, Updated).

Source 1 is ESPN's league injuries feed. If that comes back empty or in a shape
this doesn't recognize, it falls back to the team rosters, which carry each
player's current injury status too (status only, no body part). Either way the
first unrecognized item is printed once, so the parsing can be fixed for certain
after a real run -- the same safety net the NFL pipeline uses.

Non-critical in run_daily.py.
"""
import sys
import time

import pandas as pd

from nba_common import BASE_URL, PROCESSED_DIR, SEASON, get_json, get_teams, normalize_position, roster_athletes

OUTPUT_FILE = PROCESSED_DIR / f"nba_{SEASON}_injury_report.csv"
_debug = False


def from_league_feed(id_to_abbr: dict) -> list[dict]:
    global _debug
    data = get_json(f"{BASE_URL}/injuries")
    rows = []
    for team_block in data.get("injuries", []) or []:
        tid = str(team_block.get("id", ""))
        for it in team_block.get("injuries", []) or []:
            ath = it.get("athlete") or {}
            name = ath.get("displayName") or ath.get("fullName")
            team = ((ath.get("team") or {}).get("abbreviation")) or id_to_abbr.get(tid, "")
            if not name:
                if not _debug:
                    print(f"    DEBUG: unrecognized injury item: {str(it)[:400]}")
                    _debug = True
                continue
            det = it.get("details") or {}
            injury = " ".join(x for x in [det.get("side") if det.get("side") not in (None, "Not Specified") else "", det.get("type") or "", det.get("detail") or ""] if x).strip()
            rows.append({
                "Team": team, "Player": name,
                "Position": normalize_position((ath.get("position") or {}).get("abbreviation")),
                "Status": it.get("status") or (it.get("type") or {}).get("description") or "Unknown",
                "Injury": injury or (it.get("shortComment") or "")[:120],
                "Return": (det.get("returnDate") or "")[:10],
                "Updated": str(it.get("date") or "")[:10],
            })
    return rows


def from_rosters(teams) -> list[dict]:
    rows = []
    for tid, abbr in teams:
        try:
            data = get_json(f"{BASE_URL}/teams/{tid}/roster")
        except Exception as exc:
            print(f"    roster {abbr} failed: {exc}")
            continue
        for a in roster_athletes(data):
            for inj in a.get("injuries") or []:
                rows.append({"Team": abbr, "Player": a.get("displayName"), "Position": normalize_position((a.get("position") or {}).get("abbreviation")),
                             "Status": inj.get("status") or "Unknown", "Injury": (inj.get("details") or {}).get("type", "") if isinstance(inj.get("details"), dict) else "",
                             "Return": "", "Updated": str(inj.get("date") or "")[:10]})
                break
        time.sleep(0.15)
    return rows


def main():
    teams = get_teams()
    id_to_abbr = {tid: abbr for tid, abbr in teams}
    rows = []
    try:
        rows = from_league_feed(id_to_abbr)
        print(f"League injury feed: {len(rows)} entries.")
    except Exception as exc:
        print(f"League injury feed failed ({exc}); using rosters instead.")
    if not rows:
        rows = from_rosters(teams)
        print(f"Roster injury statuses: {len(rows)} entries.")
    if not rows:
        print("No injury rows collected.")
        sys.exit(1)
    df = pd.DataFrame(rows).drop_duplicates(subset=["Team", "Player"]).sort_values(["Team", "Player"])
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"Saved: {OUTPUT_FILE.name} ({len(df)} players, {df['Team'].nunique()} teams)")


if __name__ == "__main__":
    main()
