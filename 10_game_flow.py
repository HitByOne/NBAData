#!/usr/bin/env python3
"""
10_game_flow.py

How each game started and how it went quarter by quarter, from ESPN's play-by-play:

  * the opening tip: who jumped for each team and which team won it
  * the FIRST BASKET of the game: the first made field goal (free throws don't count, which is
    how most books settle "first basket"; check your book's rules), who made it, and how
  * each team's own first basket (the "team first basket" prop)
  * the first points of any kind, in case your book counts a free throw
  * points by quarter for both teams, and the total through three quarters

Output: site/data/nba_2027_game_flow.csv, one row per team per game.

It looks after itself: every run it checks which finished games (in nba_2027_games.csv) are not
in the file yet and fetches only those, so the first run backfills the season and after that it
is one request per new game. Non-critical in run_daily.py.
"""
import sys
import time

import pandas as pd

from nba_common import GAMES_FILE, PROCESSED_DIR, SEASON, fetch_summary

OUT_FILE = PROCESSED_DIR / f"nba_{SEASON}_game_flow.csv"
COLS = [
    "Game ID", "Date", "Game Day", "Team", "Opponent", "Home/Away",
    "Q1", "Q2", "Q3", "Q4", "OT", "Opp Q1", "Opp Q2", "Opp Q3", "Opp Q4", "Opp OT",
    "Thru 3Q", "Opp Thru 3Q", "Final", "Opp Final",
    "Jumper", "Opp Jumper", "Won Tip",
    "Scored First", "Team First FG Player", "Team First FG Player ID", "Team First FG Period", "Team First FG Clock", "Team First FG Play",
    "Game First FG Player", "Game First FG Team", "Game First FG Play",
    "First Points Team", "First Points Player",
]
_debug = False


def is_ft(play):
    return "free throw" in str((play.get("type") or {}).get("text", "")).lower()


def first_athlete(play):
    parts = play.get("participants") or []
    return str(((parts[0] or {}).get("athlete") or {}).get("id", "")) if parts else ""


def parse_flow(summary: dict, game_id: str) -> list[dict]:
    global _debug
    comp = (summary.get("header") or {}).get("competitions", [{}])[0]
    competitors = comp.get("competitors", [])
    by_id, side, final, quarters = {}, {}, {}, {}
    for c in competitors:
        t = c.get("team") or {}
        abbr = t.get("abbreviation")
        by_id[str(t.get("id"))] = abbr
        side[abbr] = "Home" if c.get("homeAway") == "home" else "Away"
        final[abbr] = pd.to_numeric(c.get("score"), errors="coerce")
        quarters[abbr] = [pd.to_numeric(l.get("displayValue", l.get("value")), errors="coerce") for l in (c.get("linescores") or [])]
    teams = list(side)
    if len(teams) != 2:
        return []

    # athlete id -> (name, team), from the box score
    who = {}
    for tb in (summary.get("boxscore") or {}).get("players", []):
        abbr = (tb.get("team") or {}).get("abbreviation")
        for block in tb.get("statistics", []):
            for a in block.get("athletes", []):
                ath = a.get("athlete") or {}
                if ath.get("id"):
                    who[str(ath["id"])] = (ath.get("displayName"), abbr)
    name = lambda aid: (who.get(aid) or ("", ""))[0]

    plays = summary.get("plays") or []
    if not plays and not _debug:
        print(f"    DEBUG: no play-by-play in summary for {game_id}; first-basket columns left blank.")
        _debug = True
    team_of = lambda p: by_id.get(str((p.get("team") or {}).get("id", "")), "")

    tip = next((p for p in plays if str((p.get("type") or {}).get("text", "")).lower() == "jumpball"
                and (p.get("period") or {}).get("number") == 1), None)
    jumpers, tip_team = {}, ""
    if tip:
        tip_team = team_of(tip)
        for part in (tip.get("participants") or [])[:2]:
            aid = str(((part or {}).get("athlete") or {}).get("id", ""))
            nm, tm = who.get(aid, ("", ""))
            if tm and tm not in jumpers:
                jumpers[tm] = nm

    made_fg = [p for p in plays if p.get("scoringPlay") and not is_ft(p)]
    scores = [p for p in plays if p.get("scoringPlay")]
    g_first = made_fg[0] if made_fg else None
    first_pts = scores[0] if scores else None
    team_first = {t: next((p for p in made_fg if team_of(p) == t), None) for t in teams}

    rows = []
    for t in teams:
        o = next(x for x in teams if x != t)
        q, oq = quarters.get(t, []), quarters.get(o, [])
        qv = lambda lst, i: lst[i] if i < len(lst) else None
        ot = lambda lst: sum(v for v in lst[4:] if pd.notna(v)) if len(lst) > 4 else 0
        thru3 = lambda lst: sum(v for v in lst[:3] if pd.notna(v)) if len(lst) >= 3 else None
        tf = team_first[t]
        rows.append({
            "Game ID": game_id, "Team": t, "Opponent": o, "Home/Away": side[t],
            "Q1": qv(q, 0), "Q2": qv(q, 1), "Q3": qv(q, 2), "Q4": qv(q, 3), "OT": ot(q),
            "Opp Q1": qv(oq, 0), "Opp Q2": qv(oq, 1), "Opp Q3": qv(oq, 2), "Opp Q4": qv(oq, 3), "Opp OT": ot(oq),
            "Thru 3Q": thru3(q), "Opp Thru 3Q": thru3(oq), "Final": final.get(t), "Opp Final": final.get(o),
            "Jumper": jumpers.get(t, ""), "Opp Jumper": jumpers.get(o, ""),
            "Won Tip": (1 if tip_team == t else 0) if tip_team else "",
            "Scored First": (1 if team_of(g_first) == t else 0) if g_first else "",
            "Team First FG Player": name(first_athlete(tf)) if tf else "",
            "Team First FG Player ID": first_athlete(tf) if tf else "",
            "Team First FG Period": (tf.get("period") or {}).get("number", "") if tf else "",
            "Team First FG Clock": (tf.get("clock") or {}).get("displayValue", "") if tf else "",
            "Team First FG Play": tf.get("text", "") if tf else "",
            "Game First FG Player": name(first_athlete(g_first)) if g_first else "",
            "Game First FG Team": team_of(g_first) if g_first else "",
            "Game First FG Play": g_first.get("text", "") if g_first else "",
            "First Points Team": team_of(first_pts) if first_pts else "",
            "First Points Player": name(first_athlete(first_pts)) if first_pts else "",
        })
    return rows


def main():
    if not GAMES_FILE.exists():
        print("No games file yet -- run 02_daily_update.py first.")
        sys.exit(1)
    games = pd.read_csv(GAMES_FILE, dtype={"Game ID": str})
    have = pd.read_csv(OUT_FILE, dtype={"Game ID": str}) if OUT_FILE.exists() else pd.DataFrame(columns=COLS)
    todo = [g for g in games["Game ID"] if g not in set(have["Game ID"])]
    print(f"{len(todo)} game(s) to fetch play-by-play for ({len(have) // 2} already saved).")
    new_rows, failed = [], 0
    for i, gid in enumerate(todo):
        try:
            new_rows += parse_flow(fetch_summary(gid), gid)
        except Exception as exc:
            failed += 1
            print(f"    game {gid} failed: {exc}")
        if (i + 1) % 50 == 0:
            print(f"    {i + 1} / {len(todo)}")
        time.sleep(0.12)
    out = pd.concat([have, pd.DataFrame(new_rows, columns=COLS)], ignore_index=True)
    # Dates and game-day numbers always come from the games file (game days can be renumbered on a rebuild).
    meta = games.set_index("Game ID")[["Date", "Game Day"]]
    out = out[out["Game ID"].isin(meta.index)].copy()
    out["Date"] = out["Game ID"].map(meta["Date"])
    out["Game Day"] = out["Game ID"].map(meta["Game Day"])
    for c in ["Q1", "Q2", "Q3", "Q4", "OT", "Opp Q1", "Opp Q2", "Opp Q3", "Opp Q4", "Opp OT", "Thru 3Q", "Opp Thru 3Q", "Final", "Opp Final"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").round().astype("Int64")
    out = out[COLS].sort_values(["Game Day", "Game ID", "Home/Away"]).reset_index(drop=True)
    out.to_csv(OUT_FILE, index=False)
    print(f"Saved: {OUT_FILE.name} ({len(out) // 2} games{', ' + str(failed) + ' failed (retried next run)' if failed else ''})")
    t = out[out["Won Tip"].astype(str).isin(["0", "1"]) & out["Scored First"].astype(str).isin(["0", "1"])]
    if len(t):
        tw = t[t["Won Tip"].astype(str) == "1"]
        print(f"Tip winner scored the first basket in {(tw['Scored First'].astype(str) == '1').mean():.0%} of {len(tw)} games.")


if __name__ == "__main__":
    main()
