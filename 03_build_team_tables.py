#!/usr/bin/env python3
"""
03_build_team_tables.py

Team-level tables from the box scores:

  nba_2027_team_game_stats.csv  one row per team per game: result, score, pace,
                                offensive / defensive rating, shooting, the four
                                factors, what the opponent did, bench points, rest
  nba_2027_team_summary.csv     one row per team: record, per-game averages for
                                and against, season ratings

Possessions use the standard estimate FGA - OREB + TOV + 0.44 x FTA, averaged
over both teams (each game has one pace). Ratings are points per 100
possessions. Rest = days since the team's previous game; 1 = second night of a
back-to-back.
"""
import sys

import numpy as np
import pandas as pd

from nba_common import GAMES_FILE, PLAYER_FILE, TEAM_BOX_FILE, TEAM_GAME_FILE, TEAM_SUMMARY_FILE

if not TEAM_BOX_FILE.exists():
    print("No team box file yet -- run 02_daily_update.py first.")
    sys.exit(1)

box = pd.read_csv(TEAM_BOX_FILE, dtype={"Game ID": str})
games = pd.read_csv(GAMES_FILE, dtype={"Game ID": str}) if GAMES_FILE.exists() else pd.DataFrame()
players = pd.read_csv(PLAYER_FILE, dtype={"Game ID": str}) if PLAYER_FILE.exists() else pd.DataFrame()

STATS = ["Points", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA", "Off Rebounds", "Def Rebounds", "Rebounds",
         "Assists", "Steals", "Blocks", "Turnovers", "Fouls", "Fast Break Points", "Points in Paint",
         "Points off Turnovers"]
for c in STATS:
    box[c] = pd.to_numeric(box[c], errors="coerce").fillna(0)

# Opponent's line on the same row.
opp = box[["Game ID", "Team"] + STATS].rename(columns={"Team": "Opponent", **{c: f"Opp {c}" for c in STATS}})
tg = box.merge(opp, on=["Game ID", "Opponent"], how="left", validate="one_to_one")

# Overtime periods -> game minutes
periods = {}
if len(games):
    periods = dict(zip(games["Game ID"], pd.to_numeric(games["Periods"], errors="coerce").fillna(4)))
tg["Periods"] = tg["Game ID"].map(periods).fillna(4).astype(int)
tg["Game Minutes"] = 48 + 5 * (tg["Periods"] - 4).clip(lower=0)

own_poss = tg["FGA"] - tg["Off Rebounds"] + tg["Turnovers"] + 0.44 * tg["FTA"]
opp_poss = tg["Opp FGA"] - tg["Opp Off Rebounds"] + tg["Opp Turnovers"] + 0.44 * tg["Opp FTA"]
tg["Possessions"] = ((own_poss + opp_poss) / 2).round(1)
tg["Pace"] = (tg["Possessions"] * 48 / tg["Game Minutes"]).round(1)
safe = lambda s: s.replace(0, np.nan)
tg["ORtg"] = (100 * tg["Points"] / safe(tg["Possessions"])).round(1)
tg["DRtg"] = (100 * tg["Opp Points"] / safe(tg["Possessions"])).round(1)
tg["Net Rtg"] = (tg["ORtg"] - tg["DRtg"]).round(1)
tg["Margin"] = tg["Points"] - tg["Opp Points"]
tg["Result"] = np.where(tg["Margin"] > 0, "W", "L")
tg["FG%"] = (tg["FGM"] / safe(tg["FGA"])).round(3)
tg["3P%"] = (tg["3PM"] / safe(tg["3PA"])).round(3)
tg["FT%"] = (tg["FTM"] / safe(tg["FTA"])).round(3)
tg["eFG%"] = ((tg["FGM"] + 0.5 * tg["3PM"]) / safe(tg["FGA"])).round(3)
tg["TOV%"] = (tg["Turnovers"] / safe(own_poss)).round(3)
tg["ORB%"] = (tg["Off Rebounds"] / safe(tg["Off Rebounds"] + tg["Opp Def Rebounds"])).round(3)
tg["FT Rate"] = (tg["FTM"] / safe(tg["FGA"])).round(3)
tg["3PA Rate"] = (tg["3PA"] / safe(tg["FGA"])).round(3)
tg["Opp eFG%"] = ((tg["Opp FGM"] + 0.5 * tg["Opp 3PM"]) / safe(tg["Opp FGA"])).round(3)
tg["Opp TOV%"] = (tg["Opp Turnovers"] / safe(opp_poss)).round(3)
tg["DRB%"] = (tg["Def Rebounds"] / safe(tg["Def Rebounds"] + tg["Opp Off Rebounds"])).round(3)
tg["Opp FT Rate"] = (tg["Opp FTM"] / safe(tg["Opp FGA"])).round(3)
tg["Opp 3P%"] = (tg["Opp 3PM"] / safe(tg["Opp 3PA"])).round(3)

# Bench scoring
if len(players):
    pl = players.copy()
    pl["Points"] = pd.to_numeric(pl["Points"], errors="coerce").fillna(0)
    pl["Starter"] = pd.to_numeric(pl["Starter"], errors="coerce").fillna(0)
    bench = pl[pl["Starter"] == 0].groupby(["Game ID", "Team"])["Points"].sum().rename("Bench Points")
    tg = tg.merge(bench, left_on=["Game ID", "Team"], right_index=True, how="left")
    tg["Bench Points"] = tg["Bench Points"].fillna(0)
else:
    tg["Bench Points"] = 0

# Rest days
tg["_d"] = pd.to_datetime(tg["Date"])
tg = tg.sort_values(["Team", "_d"])
tg["Rest Days"] = tg.groupby("Team")["_d"].diff().dt.days
tg["Back-to-Back"] = (tg["Rest Days"] == 1).astype(int)
tg = tg.drop(columns=["_d"]).sort_values(["Game Day", "Game ID", "Team"]).reset_index(drop=True)

tg.to_csv(TEAM_GAME_FILE, index=False)

# ---------- season summary ----------
g = tg.groupby("Team")
summary = pd.DataFrame({
    "Games Played": g["Game ID"].nunique(),
    "Wins": g["Result"].apply(lambda s: (s == "W").sum()),
    "Losses": g["Result"].apply(lambda s: (s == "L").sum()),
})
summary["Win Pct"] = (summary["Wins"] / summary["Games Played"]).round(3)
summary["Record"] = summary["Wins"].astype(str) + "-" + summary["Losses"].astype(str)
for side, keys in (("", STATS + ["Bench Points"]), ("Opp ", STATS)):
    for c in keys:
        col = f"{side}{c}" if side else c
        summary[f"{col} Per Game"] = g[col].mean().round(2)
tot = g[["Points", "Opp Points", "Possessions", "Game Minutes", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
         "Off Rebounds", "Def Rebounds", "Turnovers", "Opp FGM", "Opp FGA", "Opp 3PM", "Opp 3PA", "Opp FTM",
         "Opp FTA", "Opp Off Rebounds", "Opp Def Rebounds", "Opp Turnovers"]].sum()
summary["Margin Per Game"] = ((tot["Points"] - tot["Opp Points"]) / summary["Games Played"]).round(2)
summary["Pace"] = (tot["Possessions"] * 48 / tot["Game Minutes"]).round(1)
summary["ORtg"] = (100 * tot["Points"] / tot["Possessions"]).round(1)
summary["DRtg"] = (100 * tot["Opp Points"] / tot["Possessions"]).round(1)
summary["Net Rtg"] = (summary["ORtg"] - summary["DRtg"]).round(1)
summary["FG%"] = (tot["FGM"] / tot["FGA"]).round(3)
summary["3P%"] = (tot["3PM"] / tot["3PA"]).round(3)
summary["FT%"] = (tot["FTM"] / tot["FTA"].replace(0, np.nan)).round(3)
summary["eFG%"] = ((tot["FGM"] + 0.5 * tot["3PM"]) / tot["FGA"]).round(3)
summary["Opp FG%"] = (tot["Opp FGM"] / tot["Opp FGA"]).round(3)
summary["Opp 3P%"] = (tot["Opp 3PM"] / tot["Opp 3PA"]).round(3)
summary["Opp eFG%"] = ((tot["Opp FGM"] + 0.5 * tot["Opp 3PM"]) / tot["Opp FGA"]).round(3)

def split(team_rows, mask):
    s = team_rows[mask]
    return f"{(s['Result'] == 'W').sum()}-{(s['Result'] == 'L').sum()}"

home, away, last10, streak = {}, {}, {}, {}
for team, rows in tg.groupby("Team"):
    rows = rows.sort_values("Game Day")
    home[team] = split(rows, rows["Home/Away"] == "Home")
    away[team] = split(rows, rows["Home/Away"] == "Away")
    l10 = rows.tail(10)
    last10[team] = f"{(l10['Result'] == 'W').sum()}-{(l10['Result'] == 'L').sum()}"
    res = list(rows["Result"])
    n = 0
    for r in reversed(res):
        if r == res[-1]:
            n += 1
        else:
            break
    streak[team] = f"{res[-1]}{n}" if res else ""
summary["Home"] = pd.Series(home)
summary["Away"] = pd.Series(away)
summary["Last 10"] = pd.Series(last10)
summary["Streak"] = pd.Series(streak)
summary = summary.reset_index().rename(columns={"index": "Team"}).sort_values(["Net Rtg"], ascending=False)
summary.to_csv(TEAM_SUMMARY_FILE, index=False)

print("Team tables built.")
print(f"Team-game rows: {len(tg):,}  Teams: {len(summary)}")
print(f"Saved: {TEAM_GAME_FILE.name}, {TEAM_SUMMARY_FILE.name}")
