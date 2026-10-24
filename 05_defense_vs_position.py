#!/usr/bin/env python3
"""
05_defense_vs_position.py

What each defense gives up to guards, forwards and centers (ESPN's own G / F / C
positions): season totals and per-game rates for points, rebounds, assists,
threes, steals, blocks, turnovers, PRA and DraftKings fantasy points, plus the
same per-game numbers for STARTERS only (the box score marks who started).
Rank 1 = gives up the most = best matchup, same convention as the NFL page.
"""
import sys

import pandas as pd

from nba_common import DVP_FILE, PLAYER_FILE, POSITIONS

if not PLAYER_FILE.exists():
    print("No player file yet -- run 02_daily_update.py first.")
    sys.exit(1)

df = pd.read_csv(PLAYER_FILE, dtype={"Game ID": str})
df = df[df["Position"].isin(POSITIONS)].copy()
STATS = ["Points", "Rebounds", "Assists", "3PM", "Steals", "Blocks", "Turnovers", "Pts+Reb+Ast", "Fantasy Points", "Minutes"]
for c in STATS + ["Starter"]:
    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

g = df.groupby(["Opponent", "Position"])
out = g.agg(**{"Games Played": ("Game ID", "nunique"), "Players Faced": ("Player", "nunique")})
for c in STATS:
    out[f"{c} Allowed"] = g[c].sum()
    out[f"{c} Allowed Per Game"] = (out[f"{c} Allowed"] / out["Games Played"]).round(2)

st = df[df["Starter"] == 1].groupby(["Opponent", "Position"])
starter_games = st["Game ID"].nunique()
for c in ["Points", "Rebounds", "Assists", "3PM", "Pts+Reb+Ast", "Fantasy Points"]:
    # per starter-game: what one starting player at this position averaged against them
    out[f"Starter {c} Allowed Per Player"] = (st[c].sum() / st[c].count()).round(2)
out["Starter Games"] = starter_games
out = out.reset_index().rename(columns={"Opponent": "Defense"})
out["Fantasy Points Allowed Rank"] = out.groupby("Position")["Fantasy Points Allowed Per Game"].rank(method="min", ascending=False).astype(int)
out["Points Allowed Rank"] = out.groupby("Position")["Points Allowed Per Game"].rank(method="min", ascending=False).astype(int)
out["Matchup Label"] = out["Position"] + " vs " + out["Defense"]
out = out.sort_values(["Position", "Fantasy Points Allowed Per Game"], ascending=[True, False]).reset_index(drop=True)
out.to_csv(DVP_FILE, index=False)
print(f"Defense-vs-position table built: {len(out)} rows. Saved: {DVP_FILE.name}")
