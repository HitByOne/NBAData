#!/usr/bin/env python3
"""
07_milestone_watch.py

Career milestones from ESPN's per-athlete stats endpoint (the same one the NFL
milestone script uses, basketball flavour). One call per player gives:

  * career regular-season totals ("totals" category, plus double-doubles and
    triple-doubles from "miscellaneous", games from "averages"), and
  * the same numbers season by season, each tagged with the team -- summed for
    the player's CURRENT team, that's the franchise-record-book view.

Only players who have appeared in a box score this season are checked (about
450 calls, roughly two minutes).

Outputs (site/data):
  nba_2027_milestones.csv        career: Player, Team, Position, Stat, Current Total, Milestone, Remaining
  nba_2027_team_milestones.csv   with current team, finer steps

Non-critical in run_daily.py. If ESPN changes the shape, the first player it
can't read is printed (DEBUG) and the rest of the dashboard is unaffected.
"""
import sys
import time

import pandas as pd

from nba_common import PLAYER_FILE, PROCESSED_DIR, SEASON, get_json, get_teams

OUT = PROCESSED_DIR / f"nba_{SEASON}_milestones.csv"
OUT_TEAM = PROCESSED_DIR / f"nba_{SEASON}_team_milestones.csv"
STATS_URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}/stats"

# Our stat -> (category, ESPN stat name). Made-attempted pairs use the made half.
STAT_MAP = {
    "Points": ("totals", "points"),
    "Rebounds": ("totals", "totalRebounds"),
    "Assists": ("totals", "assists"),
    "3PM": ("totals", "threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
    "Steals": ("totals", "steals"),
    "Blocks": ("totals", "blocks"),
    "Double Doubles": ("miscellaneous", "doubleDouble"),
    "Triple Doubles": ("miscellaneous", "tripleDouble"),
    "Games Played": ("averages", "gamesPlayed"),
}
CAREER_STEP = {"Points": 1000, "Rebounds": 500, "Assists": 500, "3PM": 100, "Steals": 100, "Blocks": 100,
               "Double Doubles": 25, "Triple Doubles": 10, "Games Played": 100}
TEAM_STEP = {"Points": 500, "Rebounds": 250, "Assists": 250, "3PM": 50, "Steals": 50, "Blocks": 50,
             "Double Doubles": 10, "Triple Doubles": 5, "Games Played": 50}
MIN_TOTAL = {"Points": 100, "Rebounds": 50, "Assists": 50, "3PM": 10, "Steals": 10, "Blocks": 10,
             "Double Doubles": 1, "Triple Doubles": 1, "Games Played": 10}
_debug = False


def to_num(v):
    s = str(v or "").replace(",", "")
    if "-" in s and not s.startswith("-"):
        s = s.split("-")[0]
    try:
        return float(s)
    except ValueError:
        return None


def read(stats_json, team_id):
    """-> ({stat: career}, {stat: with team_id})"""
    cats = {c.get("name"): c for c in stats_json.get("categories", []) or []}
    career, with_team = {}, {}
    for stat, (cat, key) in STAT_MAP.items():
        c = cats.get(cat)
        if not c:
            continue
        names = c.get("names") or []
        if key not in names:
            continue
        i = names.index(key)
        tot = c.get("totals") or []
        if i < len(tot):
            v = to_num(tot[i])
            if v is not None:
                career[stat] = v
        s = 0.0
        hit = False
        for row in c.get("statistics") or []:
            if str(row.get("teamId")) != str(team_id):
                continue
            vals = row.get("stats") or []
            if i < len(vals):
                v = to_num(vals[i])
                if v is not None:
                    s += v
                    hit = True
        if hit:
            with_team[stat] = s
    return career, with_team


def next_milestone(cur, step):
    if cur <= 0:
        return step, step
    m = (int(cur // step) + 1) * step
    return m, m - cur


def main():
    global _debug
    if not PLAYER_FILE.exists():
        print("No player file yet.")
        sys.exit(1)
    pg = pd.read_csv(PLAYER_FILE, dtype={"Player ID": str, "Game ID": str})
    latest = pg.sort_values("Game Day").groupby("Player ID").tail(1)
    team_ids = {abbr: tid for tid, abbr in get_teams()}
    rows, trows = [], []
    for _, p in latest.iterrows():
        try:
            data = get_json(STATS_URL.format(id=p["Player ID"]), timeout=20)
        except Exception as exc:
            print(f"    stats for {p['Player']} failed: {exc}")
            continue
        time.sleep(0.12)
        career, with_team = read(data, team_ids.get(p["Team"], ""))
        if not career and not _debug:
            print(f"    DEBUG: nothing read for {p['Player']}. Categories: {[c.get('name') for c in data.get('categories', [])]}")
            _debug = True
        for stat, cur in career.items():
            if cur < MIN_TOTAL[stat]:
                continue
            m, rem = next_milestone(cur, CAREER_STEP[stat])
            rows.append({"Player": p["Player"], "Team": p["Team"], "Position": p["Position"], "Stat": stat,
                         "Current Total": int(cur), "Milestone": int(m), "Remaining": int(rem)})
        for stat, cur in with_team.items():
            if cur < MIN_TOTAL[stat]:
                continue
            m, rem = next_milestone(cur, TEAM_STEP[stat])
            trows.append({"Player": p["Player"], "Team": p["Team"], "Position": p["Position"], "Stat": stat,
                          "Current Total": int(cur), "Milestone": int(m), "Remaining": int(rem)})
    if not rows:
        print("No milestone rows produced -- see the DEBUG line above.")
        sys.exit(1)
    pd.DataFrame(rows).sort_values(["Remaining", "Current Total"], ascending=[True, False]).to_csv(OUT, index=False)
    if trows:
        pd.DataFrame(trows).sort_values(["Remaining", "Current Total"], ascending=[True, False]).to_csv(OUT_TEAM, index=False)
    print(f"Saved {len(rows)} career and {len(trows)} with-team milestone rows.")


if __name__ == "__main__":
    main()
