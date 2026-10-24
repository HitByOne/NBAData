#!/usr/bin/env python3
"""
11_positions.py

Real positions -- PG, SG, SF, PF, C -- instead of ESPN's Guard / Forward / Center.

ESPN only ever says G, F or C (box scores, rosters and player profiles all do), so the positions come from
Basketball-Reference's league per-game page, which lists every player who has played with one position.
One request per run, at most once a day.

How a player gets his position, in order:
  1. position_overrides.csv at the top of the repo, if you add one (two columns: Player,Position) -- your call wins
  2. Basketball-Reference, this season's page (matched by name, and by team when two players share a name)
  3. Basketball-Reference, last season's page (rookies and players who haven't played yet are on neither)
  4. an estimate from his own numbers inside ESPN's G / F / C (guards: assists -> PG, else SG; forwards:
     rebounds and few threes -> PF, else SF; centers stay C). These are marked "estimated".

If Basketball-Reference can't be reached, the last good list (nba_2027_positions.csv) is used, so a bad day
never drops positions. The ESPN position is kept in the box-score file as "ESPN Pos".

Writes: site/data/nba_2027_positions.csv, and rewrites the Position column of nba_2027_player_game_stats.csv.
Runs right after the box-score pull, before anything that groups by position.
"""
import html
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from nba_common import PLAYER_FILE, PROCESSED_DIR, PROJECT_ROOT, SEASON

OUT_FILE = PROCESSED_DIR / f"nba_{SEASON}_positions.csv"
OVERRIDES = PROJECT_ROOT / "position_overrides.csv"
BREF = "https://www.basketball-reference.com/leagues/NBA_{yr}_per_game.html"
VALID = ["PG", "SG", "SF", "PF", "C"]
MAX_AGE_HOURS = 20

# Basketball-Reference team codes -> ESPN's
BREF_TEAM = {"ATL": "ATL", "BOS": "BOS", "BRK": "BKN", "CHO": "CHA", "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
             "DET": "DET", "GSW": "GS", "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
             "MIL": "MIL", "MIN": "MIN", "NOP": "NO", "NYK": "NY", "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHO": "PHX",
             "POR": "POR", "SAC": "SAC", "SAS": "SA", "TOR": "TOR", "UTA": "UTAH", "WAS": "WSH"}


def norm(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", " ", s)
    return re.sub(r"[^a-z]", "", s)


def clean_pos(p):
    p = str(p or "").upper().strip()
    first = re.split(r"[-/ ,]", p)[0] if p else ""
    return first if first in VALID else ""


def fetch_bref(year):
    """-> list of (name, team, pos). Handles both table layouts Basketball-Reference has used."""
    resp = requests.get(BREF.format(yr=year), timeout=40, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept-Language": "en-US,en;q=0.9"})
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    text = resp.text.replace("<!--", "").replace("-->", "")   # some tables ship inside HTML comments
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S):
        cell = lambda stat: (re.search(r'data-stat="' + stat + r'"[^>]*>(.*?)</t[dh]>', tr, flags=re.S) or [None, None])[1]
        name = cell("name_display") or cell("player")
        pos = cell("pos")
        team = cell("team_name_abbr") or cell("team_id") or ""
        if not name or pos is None:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", name)).strip().rstrip("*")
        team = re.sub(r"<[^>]+>", "", team).strip()
        if name and name != "Player" and clean_pos(pos):
            rows.append((name, BREF_TEAM.get(team, team), clean_pos(pos)))
    return rows


def estimate(g):
    """ESPN G/F/C -> a best guess from his own averages."""
    espn = str(g["ESPN Pos"].iloc[0])
    mins = g["Minutes"].sum() or 1
    ast, reb, tpa, fga = g["Assists"].sum() / mins, g["Rebounds"].sum() / mins, g["3PA"].sum(), g["FGA"].sum() or 1
    if espn == "C":
        return "C"
    if espn == "G":
        return "PG" if ast >= 0.14 else "SG"
    if espn == "F":
        return "PF" if (reb >= 0.21 or tpa / fga < 0.30) else "SF"
    return "SF"


def main():
    if not PLAYER_FILE.exists():
        print("No player file yet.")
        sys.exit(1)
    pg = pd.read_csv(PLAYER_FILE, dtype={"Player ID": str, "Game ID": str})
    if "ESPN Pos" not in pg.columns:
        pg["ESPN Pos"] = pg["Position"]
    pg["ESPN Pos"] = pg["ESPN Pos"].fillna(pg["Position"])
    for c in ["Minutes", "Assists", "Rebounds", "3PA", "FGA"]:
        pg[c] = pd.to_numeric(pg[c], errors="coerce").fillna(0)

    old = pd.read_csv(OUT_FILE, dtype={"Player ID": str}) if OUT_FILE.exists() else pd.DataFrame(columns=["Player ID", "Player", "Position", "Source"])
    fresh = OUT_FILE.exists() and (time.time() - OUT_FILE.stat().st_mtime) / 3600 < MAX_AGE_HOURS
    known_ids = set(old.loc[old["Source"].astype(str).str.startswith("Basketball"), "Player ID"])
    need = set(pg["Player ID"]) - known_ids

    bref = {}
    if not fresh or need:
        for yr in (SEASON, SEASON - 1):
            try:
                got = fetch_bref(yr)
                print(f"Basketball-Reference {yr - 1}-{str(yr)[2:]}: {len(got)} player rows.")
            except Exception as exc:
                print(f"Basketball-Reference {yr} unavailable ({exc}); using the saved list.")
                got = []
            for name, team, pos in got:
                d = bref.setdefault(yr, {})
                d.setdefault(norm(name), []).append((team, pos))
            time.sleep(3)   # be polite: two requests, a few seconds apart

    overrides = {}
    if OVERRIDES.exists():
        ov = pd.read_csv(OVERRIDES)
        overrides = {norm(r.Player): clean_pos(r.Position) for r in ov.itertuples() if clean_pos(r.Position)}
        print(f"{len(overrides)} position override(s) from {OVERRIDES.name}.")

    prev = {r["Player ID"]: (r["Position"], r["Source"]) for _, r in old.iterrows()}
    out = []
    for pid, g in pg.groupby("Player ID"):
        g = g.sort_values("Game Day")
        name, team = g["Player"].iloc[-1], g["Team"].iloc[-1]
        k = norm(name)
        pos, src = "", ""
        if k in overrides:
            pos, src = overrides[k], "your override"
        else:
            for yr in (SEASON, SEASON - 1):
                cands = bref.get(yr, {}).get(k, [])
                if cands:
                    same_team = [p for t, p in cands if t == team]
                    pos = (same_team or [p for _, p in cands])[0]
                    src = f"Basketball-Reference {yr - 1}-{str(yr)[2:]}"
                    break
        if not pos and pid in prev and str(prev[pid][1]).startswith(("Basketball", "your")):
            pos, src = prev[pid]
        if not pos:
            pos, src = estimate(g), "estimated from ESPN " + str(g["ESPN Pos"].iloc[0])
        out.append({"Player ID": pid, "Player": name, "Team": team, "Position": pos, "ESPN Pos": g["ESPN Pos"].iloc[0], "Source": src})

    pos_df = pd.DataFrame(out).sort_values(["Team", "Position", "Player"])
    pos_df.to_csv(OUT_FILE, index=False)
    m = dict(zip(pos_df["Player ID"], pos_df["Position"]))
    pg["Position"] = pg["Player ID"].map(m).fillna(pg["Position"])
    cols = [c for c in pg.columns if c != "ESPN Pos"]
    cols.insert(cols.index("Position") + 1, "ESPN Pos")
    pg[cols].to_csv(PLAYER_FILE, index=False)

    src = pos_df["Source"].str.split(" ").str[0].value_counts().to_dict()
    print(f"Saved {len(pos_df)} positions ({', '.join(f'{v} {k}' for k, v in src.items())}).")
    print(pos_df["Position"].value_counts().to_string())
    est = pos_df[pos_df["Source"].str.startswith("estimated")]
    if len(est):
        print(f"{len(est)} estimated (not on Basketball-Reference yet): {', '.join(est['Player'].head(12))}{'...' if len(est) > 12 else ''}")


if __name__ == "__main__":
    main()
