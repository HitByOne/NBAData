#!/usr/bin/env python3
"""
02_daily_update.py

Pulls every finished regular-season NBA date that isn't in the CSVs yet:
box scores (player and team) and final scores, from ESPN.

  * Starts the day after the last date already saved (or at the start of the
    season scan on the very first run -- 01_initial_pull.py just calls this
    with --rebuild).
  * A date is saved only once EVERY game on it is final. If tonight's late
    West Coast game is still going, the whole date waits for the next run,
    so a half-finished slate is never locked in.
  * Dates with no games (All-Star break, Thanksgiving) are simply skipped.

Writes (site/data):
  nba_2027_player_game_stats.csv   one row per player per game
  nba_2027_team_box.csv            one row per team per game (team totals)
  nba_2027_games.csv               one row per game (final score, closing line)

Options:
  --date 2026-11-03   re-pull one date (overwrites that date's rows)
  --rebuild           re-pull the whole season from scratch
"""
import argparse
import sys
import time
from datetime import date, timedelta

import pandas as pd

from nba_common import (
    ESPN_POSITIONS, GAMES_FILE, GAME_COLUMNS, SEASON, PLAYER_COLUMNS, PLAYER_FILE, SEASON_SCAN_START, TEAM_BOX_COLUMNS,
    TEAM_BOX_FILE, assign_game_days, build_player_position_map, date_is_final, daterange,
    et_date, event_status, extract_odds, fetch_scoreboard, fetch_summary, parse_summary, today_et,
)


def load(path, cols):
    if path.exists():
        df = pd.read_csv(path, dtype={"Game ID": str})
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols]
    return pd.DataFrame(columns=cols)


def pull_date(day: date, position_map: dict | None):
    """Returns (players, team_rows, games, complete) for one date: every game on it that is final, and whether that
    was all of them. A late-night run saves the games that are over; the next run picks up the rest."""
    events = fetch_scoreboard(day)
    if not events:
        return [], [], [], True
    complete = date_is_final(events)
    players, teams, games = [], [], []
    for ev in events:
        done, skip = event_status(ev)
        if skip or not done:
            continue
        gid = str(ev["id"])
        gdate = et_date(ev.get("date", "")) or day.isoformat()
        try:
            summary = fetch_summary(gid)
        except Exception as exc:
            raise RuntimeError(f"summary for game {gid} failed: {exc}")
        p, t, g = parse_summary(summary, gid, gdate, position_map)
        odds = extract_odds((ev.get("competitions") or [{}])[0])
        for k in ("Spread", "Over/Under", "Away Moneyline", "Home Moneyline"):
            if not g.get(k) and odds.get(k) not in (None, ""):
                g[k] = odds[k]
        players += p
        teams += t
        games.append(g)
        print(f"  Loaded {ev.get('shortName', gid)}  ({len(p)} players)")
        time.sleep(0.15)
    return players, teams, games, complete


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--date", help="re-pull one ET date, YYYY-MM-DD")
    cli.add_argument("--rebuild", action="store_true", help="re-pull the whole season")
    args = cli.parse_args()

    players = load(PLAYER_FILE, PLAYER_COLUMNS)
    teams = load(TEAM_BOX_FILE, TEAM_BOX_COLUMNS)
    games = load(GAMES_FILE, GAME_COLUMNS)

    today = today_et()
    if args.rebuild:
        players, teams, games = (players.iloc[0:0], teams.iloc[0:0], games.iloc[0:0])
        dates = list(daterange(SEASON_SCAN_START, today))
    elif args.date:
        d = date.fromisoformat(args.date)
        dates = [d]
    else:
        # Start AT the last saved date (not the day after): a late-night run may have saved only the games that were
        # over, so that date is re-checked and its remaining games added. Already-saved games are simply replaced.
        last = max(games["Date"].astype(str)) if len(games) else None
        start = date.fromisoformat(last) if last else SEASON_SCAN_START
        dates = list(daterange(start, today))

    if not dates:
        print("Nothing to pull yet.")
        return 0

    # Positions come with the box score; rosters are only a fallback, so fetch them lazily.
    pos_map = None
    new_p, new_t, new_g = [], [], []
    for d in dates:
        p, t, g, complete = pull_date(d, pos_map)
        if not g and not complete:
            print(f"{d}: no game on this date is final yet -- stopping here; the next run picks it up.")
            break
        if any(not r["Position"] for r in p) and pos_map is None:
            print("  Some box-score rows had no position; loading rosters as a fallback...")
            pos_map = build_player_position_map()
            for r in p:
                if not r["Position"]:
                    r["Position"] = r["ESPN Pos"] = pos_map.get(r["Player ID"], "")
        if g:
            print(f"{d}: {len(g)} final game(s){'' if complete else ' -- the rest are still to play or in progress; the next run adds them'}")
        new_p += p
        new_t += t
        new_g += g
        if not complete:
            break

    if not new_g:
        print("No new finished dates.")
        return 0

    new_ids = {g["Game ID"] for g in new_g}
    players = pd.concat([players[~players["Game ID"].isin(new_ids)], pd.DataFrame(new_p, columns=PLAYER_COLUMNS)], ignore_index=True)
    teams = pd.concat([teams[~teams["Game ID"].isin(new_ids)], pd.DataFrame(new_t, columns=TEAM_BOX_COLUMNS)], ignore_index=True)
    games = pd.concat([games[~games["Game ID"].isin(new_ids)], pd.DataFrame(new_g, columns=GAME_COLUMNS)], ignore_index=True)

    # Counting stats are whole numbers: store them that way (13, not 13.0).
    text_cols = {"Game ID", "Date", "Team", "Opponent", "Home/Away", "Player", "Player ID", "Position", "ESPN Pos", "Tip", "Away", "Home", "Spread"}
    for df, skip in ((players, {"Minutes"}), (teams, set()), (games, {"Over/Under"})):
        for c in df.columns:
            if c in text_cols or c in skip or c == "Game Day":
                continue
            vals = pd.to_numeric(df[c], errors="coerce")
            if vals.notna().any() and (vals.dropna() % 1 == 0).all():
                df[c] = vals.round().astype("Int64")
    # Real positions (PG / SG / SF / PF / C): reuse the saved list right away; 11_positions.py refreshes it next.
    pos_file = PLAYER_FILE.parent / f"nba_{SEASON}_positions.csv"
    players["ESPN Pos"] = players["ESPN Pos"].where(players["ESPN Pos"].astype(str).isin(ESPN_POSITIONS), players["Position"])
    if pos_file.exists():
        saved = pd.read_csv(pos_file, dtype={"Player ID": str}).set_index("Player ID")["Position"]
        players["Position"] = players["Player ID"].astype(str).map(saved).fillna(players["Position"])
    assign_game_days([players, teams, games])
    players = players.sort_values(["Game Day", "Game ID", "Team", "Starter", "Minutes"], ascending=[True, True, True, False, False])
    teams = teams.sort_values(["Game Day", "Game ID", "Team"])
    games = games.sort_values(["Game Day", "Tip"])

    players.to_csv(PLAYER_FILE, index=False)
    teams.to_csv(TEAM_BOX_FILE, index=False)
    games.to_csv(GAMES_FILE, index=False)
    print(f"\nSaved {len(new_g)} new games. Season so far: {len(games)} games, {len(players):,} player rows, "
          f"{games['Game Day'].max()} game days.")
    missing = (players["Position"].astype(str).str.len() == 0).sum()
    if missing:
        print(f"Note: {missing} player rows have no position (shown under no position group).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
