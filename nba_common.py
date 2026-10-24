"""
nba_common.py

Shared config + helpers for the NBA Ledger's ESPN pull pipeline (the NBA
twin of nfl_common.py). Every script imports from here instead of
redefining paths, team lists, or the box-score parser.

    from nba_common import SEASON, PROCESSED_DIR, fetch_scoreboard, parse_summary

How the NBA differs from the NFL pipeline, and why:
  * There are games almost every night, so the unit of work is a DATE
    (US Eastern), not a week. A date is pulled only once EVERY game on it
    is final (postponed/canceled games are skipped), the same "never lock
    in a half-finished slate" rule the NFL pipeline uses for weeks.
  * Every pulled date gets a "Game Day" number (1 = opening night, 2 = the
    next date with games, ...). The dashboard uses it the way the NFL page
    uses Week: as the key that ties a pick to a slate. Days without games
    are not counted, so numbers never have gaps.
  * ESPN's NBA box score DOES carry a position (G / F / C) and a real
    starter flag on every athlete -- unlike the NFL feed. Positions fall
    back to team rosters only if the box score leaves one blank.
  * Final scores come straight from the game summary, so wins and losses
    are real (no touchdown-count proxy like the NFL's first version).
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import re

import requests

# ESPN labels a season by the year it ENDS: 2026-27 is "2027".
SEASON = 2027
SEASON_LABEL = "2026-27"
# Regular season only (ESPN season type 2). Scanning starts here and keeps
# only type-2 games, so the exact opening night never needs to be typed in.
SEASON_SCAN_START = date(2026, 10, 1)

ET = ZoneInfo("America/New_York")

PROJECT_ROOT = Path(__file__).resolve().parent
# CSVs live inside site/data so index.html (served from site/) can fetch
# them with a relative path -- no separate "build" step.
PROCESSED_DIR = PROJECT_ROOT / "site" / "data"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

PLAYER_FILE = PROCESSED_DIR / f"nba_{SEASON}_player_game_stats.csv"
TEAM_BOX_FILE = PROCESSED_DIR / f"nba_{SEASON}_team_box.csv"
GAMES_FILE = PROCESSED_DIR / f"nba_{SEASON}_games.csv"
TEAM_GAME_FILE = PROCESSED_DIR / f"nba_{SEASON}_team_game_stats.csv"
TEAM_SUMMARY_FILE = PROCESSED_DIR / f"nba_{SEASON}_team_summary.csv"
DVP_FILE = PROCESSED_DIR / f"nba_{SEASON}_defense_vs_position.csv"
SOS_FILE = PROCESSED_DIR / f"nba_{SEASON}_strength_of_schedule.csv"
UPCOMING_MATCHUPS_FILE = PROCESSED_DIR / f"nba_{SEASON}_upcoming_matchups.csv"
EFFICIENCY_FILE = PROCESSED_DIR / f"nba_{SEASON}_team_efficiency.csv"
STYLE_FILE = PROCESSED_DIR / f"nba_{SEASON}_team_style.csv"

# Real positions come from 11_positions.py (Basketball-Reference); ESPN itself only says G / F / C,
# which is kept in "ESPN Pos".
POSITIONS = ["PG", "SG", "SF", "PF", "C"]
ESPN_POSITIONS = ["G", "F", "C"]

PLAYER_COLUMNS = [
    "Game ID", "Date", "Game Day", "Team", "Opponent", "Home/Away", "Player", "Player ID",
    "Position", "ESPN Pos", "Starter", "Minutes", "Points", "Rebounds", "Off Rebounds", "Def Rebounds",
    "Assists", "Steals", "Blocks", "Turnovers", "Fouls", "FGM", "FGA", "3PM", "3PA", "FTM",
    "FTA", "Plus Minus", "Pts+Reb+Ast", "Fantasy Points", "Double Double", "Triple Double",
]
TEAM_BOX_COLUMNS = [
    "Game ID", "Date", "Game Day", "Team", "Opponent", "Home/Away", "Points", "FGM", "FGA",
    "3PM", "3PA", "FTM", "FTA", "Off Rebounds", "Def Rebounds", "Rebounds", "Assists",
    "Steals", "Blocks", "Turnovers", "Fouls", "Fast Break Points", "Points in Paint",
    "Points off Turnovers", "Largest Lead",
]
GAME_COLUMNS = [
    "Game ID", "Date", "Game Day", "Tip", "Away", "Home", "Away Score", "Home Score",
    "Periods", "Spread", "Over/Under", "Away Moneyline", "Home Moneyline",
]

_session = requests.Session()
_session.headers.update({"User-Agent": "nba-ledger/1.0 (personal dashboard)"})


def get_json(url: str, timeout: int = 30) -> dict:
    resp = _session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def et_date(iso: str) -> str:
    """ESPN tip times are UTC ('2026-10-21T01:50Z'). The NBA's 'game date' is the
    US Eastern calendar date: a 9:50pm ET tip is still that day's game."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)[:10]
    return dt.astimezone(ET).date().isoformat()


def today_et() -> date:
    return datetime.now(ET).date()


def normalize_position(pos: str | None) -> str:
    """ESPN's NBA feed uses G / F / C (occasionally PG, SF, G-F ...). Collapse to G, F, C."""
    p = str(pos or "").upper().strip()
    if not p:
        return ""
    if p.startswith("C"):
        return "C"
    if p in ("PG", "SG") or p.startswith("G"):
        return "G"
    if p in ("SF", "PF") or p.startswith("F"):
        return "F"
    return ""


def get_teams() -> list[tuple[str, str]]:
    """(team_id, abbreviation) for all 30 teams."""
    data = get_json(f"{BASE_URL}/teams?limit=40")
    teams = []
    for league in data.get("sports", [{}])[0].get("leagues", [{}]):
        for entry in league.get("teams", []):
            team = entry.get("team", {})
            tid, abbr = team.get("id"), team.get("abbreviation")
            if tid and abbr:
                teams.append((str(tid), abbr))
    return teams


def roster_athletes(roster_json: dict) -> list[dict]:
    """NBA rosters come back as a flat list; the NFL's came grouped. Accept both."""
    out = []
    for item in roster_json.get("athletes", []) or []:
        if isinstance(item, dict) and isinstance(item.get("items"), list):
            out.extend(item["items"])
        elif isinstance(item, dict):
            out.append(item)
    return out


_position_cache: dict[str, str] | None = None


def build_player_position_map() -> dict[str, str]:
    """{player_id: G/F/C} from every roster. Only a fallback: box scores carry positions."""
    global _position_cache
    if _position_cache is not None:
        return _position_cache
    out: dict[str, str] = {}
    for team_id, abbr in get_teams():
        try:
            data = get_json(f"{BASE_URL}/teams/{team_id}/roster")
            for a in roster_athletes(data):
                pos = normalize_position((a.get("position") or {}).get("abbreviation"))
                if a.get("id") and pos:
                    out[str(a["id"])] = pos
        except Exception as exc:  # one bad roster shouldn't stop the run
            print(f"    WARNING: couldn't fetch roster for {abbr}: {exc}")
    _position_cache = out
    return out


# ---------- schedule ----------
def fetch_scoreboard(day: date) -> list[dict]:
    """Every REGULAR-SEASON event on one US Eastern date."""
    data = get_json(f"{BASE_URL}/scoreboard?dates={day.strftime('%Y%m%d')}&limit=100")
    events = []
    for ev in data.get("events", []):
        stype = (ev.get("season") or {}).get("type")
        if stype not in (None, 2, "2"):
            continue  # preseason (1), play-in / playoffs (3, 5) are left out
        events.append(ev)
    return events


SKIP_STATUSES = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED", "STATUS_FORFEIT"}


def event_status(ev: dict) -> tuple[bool, bool]:
    """(completed, skip) for a scoreboard event."""
    st = (ev.get("status") or {}).get("type") or {}
    return bool(st.get("completed")), st.get("name") in SKIP_STATUSES


def date_is_final(events: list[dict]) -> bool:
    """True when every game that will be played on this date has finished."""
    real = [e for e in events if not event_status(e)[1]]
    return bool(real) and all(event_status(e)[0] for e in real)


def extract_odds(comp: dict) -> dict:
    """Best-effort betting line from a scoreboard competition (undocumented, same as the NFL pipeline)."""
    odds_list = comp.get("odds") or []
    if not odds_list:
        return {}
    o = odds_list[0]
    out = {}
    if o.get("details"):
        out["Spread"] = o["details"]
    if o.get("overUnder") is not None:
        out["Over/Under"] = o["overUnder"]
    if (o.get("provider") or {}).get("name"):
        out["Odds Provider"] = o["provider"]["name"]
    hm = (o.get("homeTeamOdds") or {}).get("moneyLine")
    am = (o.get("awayTeamOdds") or {}).get("moneyLine")
    if hm is not None:
        out["Home Moneyline"] = hm
    if am is not None:
        out["Away Moneyline"] = am
    return out


# ---------- box score parsing ----------
def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return 0.0


def _made_att(v) -> tuple[float, float]:
    parts = str(v or "0-0").split("-")
    if len(parts) != 2:
        return 0.0, 0.0
    return _num(parts[0]), _num(parts[1])


def _minutes(v) -> float:
    s = str(v or "").strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        return round(_num(m) + _num(sec) / 60, 1)
    return _num(s)


# Box-score column label (or key) -> our field. Labels are matched first, keys as a fallback,
# so the order ESPN sends columns in never matters.
_LABELS = {
    "MIN": "min", "PTS": "pts", "FG": "fg", "3PT": "tp", "FT": "ft", "REB": "reb", "AST": "ast",
    "TO": "tov", "STL": "stl", "BLK": "blk", "OREB": "oreb", "DREB": "dreb", "PF": "pf", "+/-": "pm",
}
_KEYS = {
    "minutes": "min", "points": "pts", "fieldGoalsMade-fieldGoalsAttempted": "fg",
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "tp",
    "freeThrowsMade-freeThrowsAttempted": "ft", "rebounds": "reb", "assists": "ast",
    "turnovers": "tov", "steals": "stl", "blocks": "blk", "offensiveRebounds": "oreb",
    "defensiveRebounds": "dreb", "fouls": "pf", "plusMinus": "pm",
}

_debug_logged = False


def _field_index(stat_block: dict) -> dict[str, int]:
    labels = stat_block.get("labels") or stat_block.get("names") or []
    keys = stat_block.get("keys") or []
    idx = {}
    for i, lab in enumerate(labels):
        f = _LABELS.get(str(lab).upper())
        if f and f not in idx:
            idx[f] = i
    for i, k in enumerate(keys):
        f = _KEYS.get(k)
        if f and f not in idx:
            idx[f] = i
    return idx


def fantasy_points(r: dict) -> float:
    """DraftKings NBA scoring: PTS 1, 3PM 0.5, REB 1.25, AST 1.5, STL 2, BLK 2, TO -0.5, DD +1.5, TD +3."""
    fp = (r["Points"] + 0.5 * r["3PM"] + 1.25 * r["Rebounds"] + 1.5 * r["Assists"]
          + 2 * r["Steals"] + 2 * r["Blocks"] - 0.5 * r["Turnovers"])
    if r["Triple Double"]:
        fp += 3
    if r["Double Double"]:
        fp += 1.5
    return round(fp, 2)


def parse_summary(summary: dict, game_id: str, game_date: str, position_map: dict | None = None):
    """
    One ESPN game summary -> (player_rows, team_box_rows, game_row).
    Players who did not play (didNotPlay, or no stats) are left out.
    """
    global _debug_logged
    header = (summary.get("header") or {}).get("competitions", [{}])[0]
    competitors = header.get("competitors", [])
    side_of, score_of = {}, {}
    for c in competitors:
        abbr = (c.get("team") or {}).get("abbreviation")
        side_of[abbr] = "Home" if c.get("homeAway") == "home" else "Away"
        score_of[abbr] = _num(c.get("score"))
    teams = list(side_of)
    opp_of = {t: next((o for o in teams if o != t), None) for t in teams}

    box = summary.get("boxscore") or {}
    players = []
    for tb in box.get("players", []):
        team = (tb.get("team") or {}).get("abbreviation")
        for block in tb.get("statistics", []):
            idx = _field_index(block)
            if "pts" not in idx and not _debug_logged:
                print(f"    DEBUG: box score block without a PTS column in {game_id}: labels={block.get('labels')} keys={block.get('keys')}")
                _debug_logged = True
            for a in block.get("athletes", []):
                stats = a.get("stats") or []
                if a.get("didNotPlay") or not stats:
                    continue
                ath = a.get("athlete") or {}
                pid = str(ath.get("id", ""))
                get = lambda f: stats[idx[f]] if f in idx and idx[f] < len(stats) else ""
                fgm, fga = _made_att(get("fg"))
                tpm, tpa = _made_att(get("tp"))
                ftm, fta = _made_att(get("ft"))
                pos = normalize_position((ath.get("position") or {}).get("abbreviation"))
                if not pos and position_map:
                    pos = position_map.get(pid, "")
                r = {
                    "Game ID": game_id, "Date": game_date, "Game Day": "", "Team": team,
                    "Opponent": opp_of.get(team), "Home/Away": side_of.get(team, ""),
                    "Player": ath.get("displayName"), "Player ID": pid, "Position": pos, "ESPN Pos": pos,
                    "Starter": 1 if a.get("starter") else 0, "Minutes": _minutes(get("min")),
                    "Points": _num(get("pts")), "Rebounds": _num(get("reb")),
                    "Off Rebounds": _num(get("oreb")), "Def Rebounds": _num(get("dreb")),
                    "Assists": _num(get("ast")), "Steals": _num(get("stl")),
                    "Blocks": _num(get("blk")), "Turnovers": _num(get("tov")),
                    "Fouls": _num(get("pf")), "FGM": fgm, "FGA": fga, "3PM": tpm, "3PA": tpa,
                    "FTM": ftm, "FTA": fta, "Plus Minus": _num(get("pm")),
                }
                tens = sum(1 for k in ("Points", "Rebounds", "Assists", "Steals", "Blocks") if r[k] >= 10)
                r["Double Double"] = 1 if tens >= 2 else 0
                r["Triple Double"] = 1 if tens >= 3 else 0
                r["Pts+Reb+Ast"] = r["Points"] + r["Rebounds"] + r["Assists"]
                r["Fantasy Points"] = fantasy_points(r)
                players.append(r)

    team_rows = []
    for tb in box.get("teams", []):
        team = (tb.get("team") or {}).get("abbreviation")
        st = {str(s.get("name")): s.get("displayValue") for s in tb.get("statistics", [])}
        fgm, fga = _made_att(st.get("fieldGoalsMade-fieldGoalsAttempted"))
        tpm, tpa = _made_att(st.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
        ftm, fta = _made_att(st.get("freeThrowsMade-freeThrowsAttempted"))
        mine = [p for p in players if p["Team"] == team]
        psum = lambda k: sum(p[k] for p in mine)
        team_rows.append({
            "Game ID": game_id, "Date": game_date, "Game Day": "", "Team": team,
            "Opponent": opp_of.get(team), "Home/Away": side_of.get(team, ""),
            "Points": score_of.get(team, psum("Points")),
            "FGM": fgm or psum("FGM"), "FGA": fga or psum("FGA"), "3PM": tpm or psum("3PM"),
            "3PA": tpa or psum("3PA"), "FTM": ftm or psum("FTM"), "FTA": fta or psum("FTA"),
            "Off Rebounds": _num(st.get("offensiveRebounds", psum("Off Rebounds"))),
            "Def Rebounds": _num(st.get("defensiveRebounds", psum("Def Rebounds"))),
            "Rebounds": _num(st.get("totalRebounds", psum("Rebounds"))),
            "Assists": _num(st.get("assists", psum("Assists"))),
            "Steals": _num(st.get("steals", psum("Steals"))),
            "Blocks": _num(st.get("blocks", psum("Blocks"))),
            # totalTurnovers includes team turnovers (shot clock etc.) -- the right one for possessions.
            "Turnovers": _num(st.get("totalTurnovers", st.get("turnovers", psum("Turnovers")))),
            "Fouls": _num(st.get("fouls", psum("Fouls"))),
            "Fast Break Points": _num(st.get("fastBreakPoints")),
            "Points in Paint": _num(st.get("pointsInPaint")),
            "Points off Turnovers": _num(st.get("turnoverPoints")),
            "Largest Lead": _num(st.get("largestLead")),
        })

    away = next((t for t in teams if side_of[t] == "Away"), None)
    home = next((t for t in teams if side_of[t] == "Home"), None)
    # Periods played (overtime makes a game longer, which matters for pace): count the line score,
    # else read "Final/2OT" from the status text.
    periods = max([len(c.get("linescores") or []) for c in competitors] + [0])
    if periods < 4:
        detail = str(((header.get("status") or {}).get("type") or {}).get("detail", ""))
        m = re.search(r"(\d*)OT", detail)
        periods = 4 + ((int(m.group(1)) if m.group(1) else 1) if m else 0)
    game = {
        "Game ID": game_id, "Date": game_date, "Game Day": "", "Tip": header.get("date", ""),
        "Away": away, "Home": home, "Away Score": score_of.get(away), "Home Score": score_of.get(home),
        "Periods": periods,
    }
    # Closing line, when ESPN still carries it on the summary (pickcenter).
    pc = summary.get("pickcenter") or []
    if pc:
        o = pc[0]
        game.update({"Spread": o.get("details", ""), "Over/Under": o.get("overUnder", ""),
                     "Away Moneyline": (o.get("awayTeamOdds") or {}).get("moneyLine", ""),
                     "Home Moneyline": (o.get("homeTeamOdds") or {}).get("moneyLine", "")})
    return players, team_rows, game


def fetch_summary(game_id: str) -> dict:
    return get_json(f"{BASE_URL}/summary?event={game_id}")


def assign_game_days(frames: list) -> None:
    """Number every distinct date 1..N (in place) across the given DataFrames."""
    dates = sorted({d for f in frames if f is not None and len(f) for d in f["Date"].astype(str)})
    day_of = {d: i + 1 for i, d in enumerate(dates)}
    for f in frames:
        if f is not None and len(f):
            f["Game Day"] = f["Date"].astype(str).map(day_of)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
