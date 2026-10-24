#!/usr/bin/env python3
"""
04_strength_of_schedule.py

Strength of schedule played, from REAL results (final scores). For every team:
the combined record of the opponents it has faced (each game counts, so a team
seen twice counts twice), their average win %, and their average net rating.
"""
import sys

import pandas as pd

from nba_common import SOS_FILE, TEAM_GAME_FILE, TEAM_SUMMARY_FILE

if not TEAM_GAME_FILE.exists():
    print("No team game file yet -- run 03_build_team_tables.py first.")
    sys.exit(1)

tg = pd.read_csv(TEAM_GAME_FILE, dtype={"Game ID": str})
summary = pd.read_csv(TEAM_SUMMARY_FILE)
rec = summary.set_index("Team")[["Wins", "Losses", "Win Pct", "Net Rtg"]]

rows = []
for team, g in tg.groupby("Team"):
    opps = g["Opponent"].tolist()
    o = rec.reindex(opps)
    rows.append({
        "Team": team,
        "Games Played": len(g),
        "Opponents Played": ", ".join(sorted(set(opps))),
        "Opponent Combined Wins": int(o["Wins"].sum()),
        "Opponent Combined Losses": int(o["Losses"].sum()),
        "Played SOS": round(float(o["Win Pct"].mean()), 3),
        "Opponent Net Rtg": round(float(o["Net Rtg"].mean()), 2),
    })
sos = pd.DataFrame(rows)
sos["Opponent Combined Record"] = sos["Opponent Combined Wins"].astype(str) + "-" + sos["Opponent Combined Losses"].astype(str)
sos = sos.sort_values("Played SOS", ascending=False).reset_index(drop=True)
sos.insert(0, "Played SOS Rank", sos.index + 1)
sos.to_csv(SOS_FILE, index=False)
print(f"Strength-of-schedule table built for {len(sos)} teams. Saved: {SOS_FILE.name}")
