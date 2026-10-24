#!/usr/bin/env python3
"""
09_team_efficiency.py

The NBA stand-in for the NFL page's EPA tab: per-100-possession efficiency.

  nba_2027_team_efficiency.csv  one row per team: offensive / defensive / net rating
                                and pace, season and last 5 games, plus Dean Oliver's
                                four factors on both ends (eFG%, turnover %, offensive
                                rebound %, free-throw rate)
  nba_2027_team_style.csv       how each team plays: pace, share of shots from three,
                                free-throw rate, assists per basket, bench scoring,
                                paint and fast-break points

Ratings are built from totals (sum of points / sum of possessions), not averages of
per-game ratings, so a blowout and a close game count by their possessions.
Small samples are noisy: under about 10 games, treat a rating as a rough read.
"""
import sys

import numpy as np
import pandas as pd

from nba_common import EFFICIENCY_FILE, STYLE_FILE, TEAM_GAME_FILE

if not TEAM_GAME_FILE.exists():
    print("No team game file yet -- run 03_build_team_tables.py first.")
    sys.exit(1)

tg = pd.read_csv(TEAM_GAME_FILE, dtype={"Game ID": str}).sort_values(["Team", "Game Day"])
num = lambda c: pd.to_numeric(tg[c], errors="coerce").fillna(0)
for c in ["Points", "Opp Points", "Possessions", "Game Minutes", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA",
          "Off Rebounds", "Def Rebounds", "Turnovers", "Assists", "Opp FGM", "Opp FGA", "Opp 3PM", "Opp 3PA",
          "Opp FTM", "Opp FTA", "Opp Off Rebounds", "Opp Def Rebounds", "Opp Turnovers", "Bench Points",
          "Points in Paint", "Fast Break Points"]:
    tg[c] = num(c)


def block(g: pd.DataFrame) -> dict:
    s = g.sum(numeric_only=True)
    poss = s["Possessions"] or np.nan
    own_poss = s["FGA"] - s["Off Rebounds"] + s["Turnovers"] + 0.44 * s["FTA"]
    opp_poss = s["Opp FGA"] - s["Opp Off Rebounds"] + s["Opp Turnovers"] + 0.44 * s["Opp FTA"]
    return {
        "ORtg": 100 * s["Points"] / poss,
        "DRtg": 100 * s["Opp Points"] / poss,
        "Pace": s["Possessions"] * 48 / s["Game Minutes"] if s["Game Minutes"] else np.nan,
        "eFG%": (s["FGM"] + 0.5 * s["3PM"]) / s["FGA"] if s["FGA"] else np.nan,
        "TOV%": s["Turnovers"] / own_poss if own_poss else np.nan,
        "ORB%": s["Off Rebounds"] / (s["Off Rebounds"] + s["Opp Def Rebounds"]) if (s["Off Rebounds"] + s["Opp Def Rebounds"]) else np.nan,
        "FT Rate": s["FTM"] / s["FGA"] if s["FGA"] else np.nan,
        "Opp eFG%": (s["Opp FGM"] + 0.5 * s["Opp 3PM"]) / s["Opp FGA"] if s["Opp FGA"] else np.nan,
        "Opp TOV%": s["Opp Turnovers"] / opp_poss if opp_poss else np.nan,
        "DRB%": s["Def Rebounds"] / (s["Def Rebounds"] + s["Opp Off Rebounds"]) if (s["Def Rebounds"] + s["Opp Off Rebounds"]) else np.nan,
        "Opp FT Rate": s["Opp FTM"] / s["Opp FGA"] if s["Opp FGA"] else np.nan,
        "3P%": s["3PM"] / s["3PA"] if s["3PA"] else np.nan,
        "Opp 3P%": s["Opp 3PM"] / s["Opp 3PA"] if s["Opp 3PA"] else np.nan,
    }


eff_rows, style_rows = [], []
for team, g in tg.groupby("Team"):
    season, last5 = block(g), block(g.tail(5))
    row = {"Team": team, "Games Played": len(g)}
    for k, v in season.items():
        row[k] = round(v, 4 if "%" in k or "Rate" in k else 1) if pd.notna(v) else ""
    row["Net Rtg"] = round(season["ORtg"] - season["DRtg"], 1)
    for k in ("ORtg", "DRtg", "Pace", "eFG%", "Opp eFG%"):
        v = last5[k]
        row[f"Last5 {k}"] = round(v, 4 if "%" in k else 1) if pd.notna(v) else ""
    row["Last5 Net Rtg"] = round(last5["ORtg"] - last5["DRtg"], 1)
    eff_rows.append(row)

    s = g.sum(numeric_only=True)
    n = len(g)
    style_rows.append({
        "Team": team, "Games Played": n,
        "Pace": row["Pace"],
        "3PA Rate": round(s["3PA"] / s["FGA"], 4) if s["FGA"] else "",
        "Share of Points From 3": round(3 * s["3PM"] / s["Points"], 4) if s["Points"] else "",
        "FT Rate": row["FT Rate"],
        "Assisted Basket Rate": round(s["Assists"] / s["FGM"], 4) if s["FGM"] else "",
        "Bench Points Per Game": round(s["Bench Points"] / n, 1),
        "Bench Share of Points": round(s["Bench Points"] / s["Points"], 4) if s["Points"] else "",
        "Paint Points Per Game": round(s["Points in Paint"] / n, 1),
        "Fast Break Points Per Game": round(s["Fast Break Points"] / n, 1),
        "ORB%": row["ORB%"],
        "Opp 3PA Rate": round(s["Opp 3PA"] / s["Opp FGA"], 4) if s["Opp FGA"] else "",
    })

eff = pd.DataFrame(eff_rows).sort_values("Net Rtg", ascending=False)
eff.to_csv(EFFICIENCY_FILE, index=False)
pd.DataFrame(style_rows).sort_values("Pace", ascending=False).to_csv(STYLE_FILE, index=False)
print(f"Efficiency and style tables built for {len(eff)} teams. Saved: {EFFICIENCY_FILE.name}, {STYLE_FILE.name}")
