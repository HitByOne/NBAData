#!/usr/bin/env python3
"""
06_matchup_preview.py

The upcoming slate: tonight's games (if any haven't finished) plus the next date
with games, one row per team per game, with the betting line ESPN carries,
rest (days since the team's last game, back-to-backs, 3-in-4 nights), the team's
season ratings, and what the opponent gives up to G / F / C.

Each upcoming date gets the next Game Day number after the last one pulled, so
picks saved for it line up with the box scores once they arrive.
"""
import sys
from datetime import date, timedelta

import pandas as pd

from nba_common import (
    DVP_FILE, GAMES_FILE, TEAM_GAME_FILE, TEAM_SUMMARY_FILE, UPCOMING_MATCHUPS_FILE,
    et_date, event_status, extract_odds, fetch_scoreboard, today_et,
)

DATES_AHEAD = 2      # how many upcoming dates with games to include
SCAN_DAYS = 10       # how far ahead to look for them

games = pd.read_csv(GAMES_FILE, dtype={"Game ID": str}) if GAMES_FILE.exists() else pd.DataFrame(columns=["Date", "Game Day"])
pulled = set(games["Date"].astype(str))
last_day = int(pd.to_numeric(games["Game Day"], errors="coerce").max()) if len(games) else 0

rows, day_no = [], last_day
start = today_et()
found = 0
for i in range(SCAN_DAYS):
    d = start + timedelta(days=i)
    if d.isoformat() in pulled:
        continue
    events = fetch_scoreboard(d)
    live = [e for e in events if not event_status(e)[1]]
    if not live:
        continue
    found += 1
    day_no += 1
    for ev in live:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation") for c in comp.get("competitors", [])}
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue
        odds = extract_odds(comp)
        st = ((ev.get("status") or {}).get("type") or {})
        base = {"Game ID": str(ev["id"]), "Date": ev.get("date", ""), "Game Date": et_date(ev.get("date", "")) or d.isoformat(),
                "Game Day": day_no, "Spread": odds.get("Spread", ""), "Over/Under": odds.get("Over/Under", ""),
                "Odds Provider": odds.get("Odds Provider", ""), "Status": st.get("shortDetail", "")}
        rows.append({**base, "Team": away, "Opponent": home, "Home/Away": "Away", "Team Moneyline": odds.get("Away Moneyline", "")})
        rows.append({**base, "Team": home, "Opponent": away, "Home/Away": "Home", "Team Moneyline": odds.get("Home Moneyline", "")})
    print(f"{d}: {len(live)} games (Game Day {day_no})")
    if found >= DATES_AHEAD:
        break

up = pd.DataFrame(rows)
if up.empty:
    print("No upcoming games found in the next %d days (off-season or All-Star break)." % SCAN_DAYS)
    # Keep an empty file with headers so the dashboard loads cleanly.
    pd.DataFrame(columns=["Game ID", "Date", "Game Date", "Game Day", "Team", "Opponent", "Home/Away", "Spread",
                          "Over/Under", "Odds Provider", "Team Moneyline", "Status"]).to_csv(UPCOMING_MATCHUPS_FILE, index=False)
    sys.exit(0)

# ---- rest ----
played = pd.read_csv(TEAM_GAME_FILE, dtype={"Game ID": str}) if TEAM_GAME_FILE.exists() else pd.DataFrame(columns=["Team", "Date"])
dates_by_team = {t: sorted(pd.to_datetime(g["Date"]).dt.date) for t, g in played.groupby("Team")}
# Upcoming games count too (tomorrow's rest depends on tonight's game).
for t, g in up.groupby("Team"):
    dates_by_team.setdefault(t, [])
    dates_by_team[t] = sorted(set(dates_by_team[t]) | {date.fromisoformat(x) for x in g["Game Date"]})

def rest_info(team, gdate):
    gd = date.fromisoformat(gdate)
    prior = [d for d in dates_by_team.get(team, []) if d < gd]
    rest = (gd - prior[-1]).days if prior else None
    in4 = sum(1 for d in dates_by_team.get(team, []) if gd - timedelta(days=3) <= d <= gd)
    in6 = sum(1 for d in dates_by_team.get(team, []) if gd - timedelta(days=5) <= d <= gd)
    return rest, in4, in6

info = up.apply(lambda r: rest_info(r["Team"], r["Game Date"]), axis=1, result_type="expand")
up["Rest Days"], up["Games in 4 Nights"], up["Games in 6 Nights"] = info[0], info[1], info[2]
up["Back-to-Back"] = (up["Rest Days"] == 1).astype(int)

# ---- team context ----
if TEAM_SUMMARY_FILE.exists():
    ts = pd.read_csv(TEAM_SUMMARY_FILE)
    keep = ["Team", "Record", "Win Pct", "Net Rtg", "ORtg", "DRtg", "Pace", "Points Per Game", "Opp Points Per Game", "Last 10", "Streak"]
    up = up.merge(ts[[c for c in keep if c in ts.columns]], on="Team", how="left")

# ---- what the opponent gives up to each position ----
if DVP_FILE.exists():
    dvp = pd.read_csv(DVP_FILE)
    wide = dvp.pivot_table(index="Defense", columns="Position",
                           values=["Points Allowed Per Game", "Rebounds Allowed Per Game",
                                   "Assists Allowed Per Game", "3PM Allowed Per Game", "Points Allowed Rank"], aggfunc="first")
    wide.columns = [f"{s} vs {p}" for s, p in wide.columns]
    up = up.merge(wide.reset_index(), left_on="Opponent", right_on="Defense", how="left").drop(columns=["Defense"])

up = up.sort_values(["Game Day", "Date", "Game ID", "Home/Away"]).reset_index(drop=True)
up.to_csv(UPCOMING_MATCHUPS_FILE, index=False)
print(f"Upcoming matchups: {up['Game ID'].nunique()} games. Saved: {UPCOMING_MATCHUPS_FILE.name}")
