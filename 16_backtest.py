#!/usr/bin/env python3
"""
16_backtest.py

Replays the Best Picks page on every past game day and grades it against what
actually happened -- the NBA version of the NFL backtest.

For each game day D (from the 4th on) it pretends it is the morning of D and
uses ONLY games from earlier days:

  * Projected starters per team: the five players with the most starts in the
    team's last 5 games (minutes break ties) -- the same rule as the page.
  * A pick needs the player to have cleared a floor in at least 75% of his own
    games AND starting players at his position (G / F / C) to have cleared it in
    at least 60% of their games against this defense (at least 3 such games, the
    offense's own team left out). The highest floor that passes both is the
    pick; each player keeps his best stat (single stats win ties over combos).
  * The projection is the page's: 60% season average + 40% last 5, times the
    defense's factor (capped 0.6-1.6, shrunk until the defense has 10 games),
    no spread nudge (no historical spreads in the data).

The pick is graded against day D's real box score. A projected starter with no
row on day D (rested, injured, DNP) is skipped and counted, not graded a miss.

Also writes an "own_only" comparison: the same players with the defense check
removed, so you can see whether the defense side adds anything.

Output: site/data/nba_2027_backtest.csv (one row per pick). Non-critical.
"""
import sys

import pandas as pd

from nba_common import PLAYER_FILE, POSITIONS, PROCESSED_DIR, SEASON

OUT_FILE = PROCESSED_DIR / f"nba_{SEASON}_backtest.csv"

# Keep in step with BP_FLOORS / BP_STATS in site/index.html.
FLOORS = {
    "Points": [8, 10, 12, 15, 18, 20, 22, 25, 28, 30],
    "Rebounds": [3, 4, 5, 6, 7, 8, 9, 10, 12],
    "Assists": [2, 3, 4, 5, 6, 7, 8, 9, 10],
    "3PM": [1, 2, 3, 4, 5],
    "Pts+Reb+Ast": [15, 20, 25, 28, 30, 32, 35, 38, 40, 45],
    "Pts+Reb": [12, 15, 18, 20, 22, 25, 28, 30, 35],
    "Pts+Ast": [12, 15, 18, 20, 22, 25, 28, 30, 35],
    "Reb+Ast": [5, 6, 8, 10, 12, 14, 16],
}
COMBOS = {"Pts+Reb+Ast", "Pts+Reb", "Pts+Ast", "Reb+Ast"}
PLAYER_MIN_RATE, DEF_MIN_RATE, MIN_PLAYER_GAMES, MIN_DEF_SAMPLES = 0.75, 0.60, 3, 3
FIRST_DAY = 4
L_N, SHRINK_GAMES = 5, 10


def project(vals, lavg, allowed, league, def_games):
    avg = sum(vals) / len(vals)
    baseline = avg * 0.6 + lavg * 0.4
    raw = allowed / league if league > 0 else 1.0
    raw = max(0.6, min(1.6, raw))
    matchup = 1 + (raw - 1) * min(1.0, def_games / SHRINK_GAMES)
    proj = baseline * matchup
    sd = pd.Series(vals).std(ddof=1) if len(vals) > 1 else 0.0
    sd = max(0.0 if pd.isna(sd) else sd, 0.30 * proj)
    return {"proj": proj, "base": baseline, "lo": max(0.0, proj - 0.67 * sd), "hi": proj + 0.67 * sd, "line": round(proj * 2) / 2}


def starters_as_of(hist):
    """{(team, player): slot} -- five per team, slot like PG1 / SF1 / C1."""
    out = {}
    for team, g in hist.groupby("Team"):
        last_games = sorted(g["Game Day"].unique())[-5:]
        recent = g[g["Game Day"].isin(last_games)]
        agg = recent.groupby(["Player", "Position"]).agg(starts=("Starter", "sum"), mins=("Minutes", "mean")).reset_index()
        agg = agg.sort_values(["starts", "mins"], ascending=False).head(5)
        counts = {}
        for _, r in agg.iterrows():
            counts[r["Position"]] = counts.get(r["Position"], 0) + 1
            out[(team, r["Player"])] = f"{r['Position']}{counts[r['Position']]}"
    return out


def main():
    if not PLAYER_FILE.exists():
        print("No player stats file yet.")
        sys.exit(1)
    df = pd.read_csv(PLAYER_FILE, dtype={"Game ID": str})
    for c in ["Points", "Rebounds", "Assists", "3PM", "Minutes", "Starter", "Game Day"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df[df["Position"].isin(POSITIONS)].copy()
    df["Pts+Reb+Ast"] = df["Points"] + df["Rebounds"] + df["Assists"]
    df["Pts+Reb"] = df["Points"] + df["Rebounds"]
    df["Pts+Ast"] = df["Points"] + df["Assists"]
    df["Reb+Ast"] = df["Rebounds"] + df["Assists"]
    days = sorted(int(d) for d in df["Game Day"].unique())
    rows, skipped = [], 0

    for D in days:
        if D < FIRST_DAY:
            continue
        hist = df[df["Game Day"] < D]
        cur = df[df["Game Day"] == D]
        if hist.empty or cur.empty:
            continue
        starters = starters_as_of(hist)
        by_player = {k: g.sort_values("Game Day") for k, g in hist.groupby(["Team", "Player"])}
        actual = {(r["Team"], r["Player"]): r for r in cur.to_dict("records")}
        def_games = hist.groupby("Opponent")["Game Day"].nunique().to_dict()
        hs = hist[hist["Starter"] == 1]
        league = {(pos, s): float(hs[hs["Position"] == pos][s].mean()) if len(hs[hs["Position"] == pos]) else 0.0
                  for pos in ("G", "F", "C") for s in FLOORS}

        for off, d in {(r.Team, r.Opponent) for r in cur.itertuples(index=False)}:
            for (team, player), slot in starters.items():
                if team != off:
                    continue
                g = by_player.get((team, player))
                if g is None or len(g) < MIN_PLAYER_GAMES:
                    continue
                pos = g.iloc[-1]["Position"]
                act = actual.get((team, player))
                if act is None:
                    skipped += 1
                    continue
                dsub = hs[(hs["Opponent"] == d) & (hs["Position"] == pos) & (hs["Team"] != off)]
                best_bp, best_own = None, None
                for stat, floors in FLOORS.items():
                    vals = list(g[stat])
                    avg = sum(vals) / len(vals)
                    if avg <= 0:
                        continue
                    last = list(g[stat].tail(L_N))
                    lavg = sum(last) / len(last)
                    own_found, found = None, None
                    for f in floors:
                        pr = sum(v >= f for v in vals) / len(vals)
                        if pr >= PLAYER_MIN_RATE:
                            own_found = {"floor": f, "pr": pr}
                        if len(dsub) >= MIN_DEF_SAMPLES:
                            dr = float((dsub[stat] >= f).mean())
                            if pr >= PLAYER_MIN_RATE and dr >= DEF_MIN_RATE:
                                found = {"floor": f, "pr": pr, "dr": dr}
                    allowed = float(dsub[stat].mean()) if len(dsub) else league[(pos, stat)]
                    pj = project(vals, lavg, allowed, league[(pos, stat)], def_games.get(d, 0))
                    result = float(act[stat])
                    base = {"Day": D, "Date": act["Date"], "Player": player, "Slot": slot, "Position": pos, "Team": team,
                            "Opponent": d, "Stat": stat, "Projection": round(pj["proj"], 1), "ProjNoMatchup": round(pj["base"], 1),
                            "Lo": round(pj["lo"]), "Hi": round(pj["hi"]), "Line": pj["line"], "Actual": result}
                    better = lambda e, b: (not b or e["Score"] > b["Score"]
                                           or (e["Score"] == b["Score"] and e["Floor"] > b["Floor"] and e["Stat"] not in COMBOS))
                    if found:
                        e = {**base, "Model": "best_picks", "Floor": found["floor"], "OwnRate": round(found["pr"], 3),
                             "DefRate": round(found["dr"], 3), "Score": round((found["pr"] + found["dr"]) / 2, 3)}
                        if better(e, best_bp):
                            best_bp = e
                    if own_found:
                        e = {**base, "Model": "own_only", "Floor": own_found["floor"], "OwnRate": round(own_found["pr"], 3),
                             "DefRate": "", "Score": round(own_found["pr"], 3)}
                        if better(e, best_own):
                            best_own = e
                for e in (best_bp, best_own):
                    if e:
                        e["Hit"] = int(e["Actual"] >= e["Floor"])
                        rows.append(e)

    cols = ["Model", "Day", "Date", "Player", "Slot", "Position", "Team", "Opponent", "Stat", "Floor", "OwnRate", "DefRate",
            "Score", "Projection", "ProjNoMatchup", "Lo", "Hi", "Line", "Actual", "Hit"]
    out = pd.DataFrame(rows, columns=cols)
    out.to_csv(OUT_FILE, index=False)
    bp, ow = out[out["Model"] == "best_picks"], out[out["Model"] == "own_only"]
    print(f"Saved: {OUT_FILE.name} ({len(bp)} best-picks rows, {len(ow)} own-only rows, {skipped} skipped: no box-score row that day)")
    if len(bp):
        print(f"Best Picks floors cleared: {bp['Hit'].mean():.1%} of {len(bp)}; own-only: {ow['Hit'].mean():.1%} of {len(ow)}")


if __name__ == "__main__":
    main()
