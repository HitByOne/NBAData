"""
HitByOne Hardwood Ledger -- the daily social kit and the Pick of the Day record.

Runs right after 18_newsletter.py (it reads the picks that script saved in social_kit/YYYY-MM-DD.json).
The day's posts are PRIVATE: they're saved to social_kit/YYYY-MM-DD/ (not part of the site, not committed) and
emailed to you (KIT_TO, or EMAIL_FROM when that isn't set) with every picture and the video attached. The public
page is site/record/ (the Pick of the Day record, graded picks only) -- that's the link for bios and posts.
If the KIT_KEY secret is set, the kit is also put up at /social/<KIT_KEY>/ for you: a link only you know (it isn't
in the repo or the logs), with a one-tap zip download. It's replaced every afternoon and is gone after the next
refresh. Treat it like an unlisted link, not a password-protected one: the email is the fully private copy.

  x.jpg                    1600 x 900   Pick of the Day + the Starting 5 court     (X)
  ig-1.jpg ... ig-4.jpg    1080 x 1350  Starting 5, Pick of the Day, Top 5 by stat, record (Instagram carousel)
  tt-1.jpg ... tt-5.jpg    1080 x 1920  matchups and projections only, NO odds    (TikTok slides)
  tiktok.mp4               1080 x 1920  the TikTok slides as a ~16 second video
  index.html               the day's page: every image, the video, captions and the Reddit comment, with copy buttons

TikTok's rules keep content that "shows or glamorizes gambling" off the For You feed, so the TikTok version
leaves out lines, odds, units and payouts and sticks to matchups, projections and results.

PICK OF THE DAY RECORD (records/nba_2027_potd_log.csv -- in the repo, not served on the site)
  * The pick is written down the first time this runs on a game day and never changed after that, at the price
    the afternoon odds pull had. Rows are only ever added; nothing is reset.
  * Rules it follows (r/sportsbook POTD): one straight pick, odds between -200 and +200, flat 1 unit.
  * Every run grades finished picks from the box scores: W (went over), L, P (landed on the line), VOID (didn't play).
  * The Reddit comment's Record / Net Units / ROI / Avg Odds lines come straight from this file.

Run by hand:  python3 19_social.py            (make today's kit, grade the log)
              python3 19_social.py --grade    (only grade the log -- every scheduled run does this)
"""
import csv
import datetime as dt
import glob
import html
import json
import os
import shutil
import smtplib
import zipfile
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
DATA = SITE / "data"
NEWS = SITE / "newsletter"
OUT = SITE / "social"            # /social/ forwards to /record/; your private kit page lives at /social/<KIT_KEY>/
RECORD = SITE / "record"         # public: the Pick of the Day record (the link for your bios and posts)
KIT = ROOT / "social_kit"        # private: the daily posts (not on the site, not committed; emailed to you)
LOG = ROOT / "records" / "nba_2027_potd_log.csv"   # in the repo, not on the site (the site shows graded picks only)
OLD_LOG = DATA / "nba_2027_potd_log.csv"
UNITS = 1.0
# TEST: build and email the kit (to TEST_TO / KIT_TO / EMAIL_FROM) but record nothing: no pick is locked, the log
# and the public record page are left alone. Today's real pick still gets locked by the afternoon run.
TEST = os.environ.get("SOCIAL_TEST") == "1"
CT = ZoneInfo("America/Chicago")

LOG_COLS = ["Date", "Locked At (UTC)", "Player", "Pos", "Team", "Opponent", "Home/Away", "Tip (UTC)", "Stat", "Line",
            "Side", "American", "Decimal", "Units", "Projection", "Result", "Actual", "Profit"]

BG, CARD, LINE, TXT, DIM = "#07080F", "#11131E", "#262c38", "#F2F4FA", "rgba(242,244,250,0.66)"
ORANGE, PINK, GREEN, RED = "#FF7A2F", "#FF4D8D", "#2EE6A0", "#FF6B6B"
STAT_SHORT = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3PM": "3PM", "Pts+Reb+Ast": "PRA"}
NAME_SHORT = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3PM": "3PM", "PRA": "PRA"}   # the page's stat names
STAT_WORDS = {"Points": "points", "Rebounds": "rebounds", "Assists": "assists", "3PM": "threes", "Pts+Reb+Ast": "points + rebounds + assists"}


BRAND, PRODUCT = "HitByOne", "Hardwood Ledger"


def mark(size, sub=True, center=False):
    """The wordmark: HITBY(ONE) big, HARDWOOD LEDGER small underneath."""
    al = "center" if center else "left"
    return (f'<div style="text-align:{al};line-height:1"><div class="brand" style="font-size:{size}px;">HITBY<span>ONE</span></div>'
            + (f'<div style="font-size:{max(10, size * .3):.0f}px;font-weight:800;letter-spacing:.26em;color:rgba(242,244,250,.8);margin-top:{size * .2:.0f}px">HARDWOOD LEDGER</div>' if sub else "")
            + '</div>')


def today_ct():
    return os.environ.get("HW_TODAY") or dt.datetime.now(CT).date().isoformat()


def esc(x):
    return html.escape(str(x if x is not None else ""))


def fmt(v, d=1):
    if v is None or v == "":
        return "--"
    v = float(v)
    return str(int(v)) if v == int(v) else f"{v:.{d}f}"


def american(dec):
    dec = float(dec)
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else str(round(-100 / (dec - 1)))


def short_name(name):
    parts = name.split()
    if len(parts) >= 2 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv"):
        return " ".join(parts[-2:])
    return parts[-1] if parts else name


def card_name(name, n=15):
    if len(name) <= n:
        return name
    parts = name.split()
    return (parts[0][0] + ". " + " ".join(parts[1:])) if len(parts) > 1 else name


def compact(pick):
    pick = str(pick or "")
    return "o" + pick[5:] if pick.startswith("OVER ") else pick


# ---------------------------------------------------------------- the POTD log
def read_log():
    if OLD_LOG.exists():                         # earlier versions kept it under site/data (public): move it
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if not LOG.exists():
            shutil.move(str(OLD_LOG), str(LOG))
        else:
            OLD_LOG.unlink()
    if not LOG.exists():
        return []
    with LOG.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_log(rows):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LOG_COLS})
    tmp.replace(LOG)


def box_scores():
    """(date, player) -> box score row, from the season's player game file."""
    out = {}
    for path in glob.glob(str(DATA / "nba_*_player_game_stats.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[(r.get("Date", ""), r.get("Player", ""))] = r
                out[(r.get("Date", ""), "team:" + r.get("Team", ""))] = True     # his team's game is in
    return out


def grade(rows):
    """Grade every open pick whose game is in the box scores. VOID when his team's game is in but he didn't play
    (or, as a last resort, when there's still no box score a week later -- a postponed game)."""
    box, today, changed = None, today_ct(), 0
    for r in rows:
        if r.get("Result"):
            continue
        if r["Date"] >= today:
            continue
        box = box if box is not None else box_scores()
        b = box.get((r["Date"], r["Player"]))
        units, dec, line = float(r["Units"]), float(r["Decimal"]), float(r["Line"])
        if b and float(b.get("Minutes") or 0) > 0:
            v = float(b.get(r["Stat"]) or 0)
            res = "W" if v > line else "L" if v < line else "P"
            r["Actual"] = fmt(v)
            r["Result"] = res
            r["Profit"] = f"{units * (dec - 1):.2f}" if res == "W" else f"{-units:.2f}" if res == "L" else "0.00"
        elif b or box.get((r["Date"], "team:" + r["Team"])) or (dt.date.fromisoformat(today) - dt.date.fromisoformat(r["Date"])).days >= 7:
            r["Result"], r["Actual"], r["Profit"] = "VOID", "DNP", "0.00"
        else:
            continue
        changed += 1
    return changed


def record(rows):
    g = [r for r in rows if r.get("Result") in ("W", "L", "P")]
    w = sum(r["Result"] == "W" for r in g)
    l = sum(r["Result"] == "L" for r in g)
    p = sum(r["Result"] == "P" for r in g)
    net = sum(float(r["Profit"]) for r in g)
    risked = sum(float(r["Units"]) for r in g if r["Result"] in ("W", "L"))
    roi = net / risked * 100 if risked else 0.0
    avg_dec = sum(float(r["Decimal"]) for r in g) / len(g) if g else None
    streak = ""
    wl = [r["Result"] for r in g if r["Result"] in ("W", "L")]
    if wl:
        k = 1
        while k < len(wl) and wl[-k - 1] == wl[-1]:
            k += 1
        streak = f"{wl[-1]}{k}"
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    return {"w": w, "l": l, "p": p, "rec": rec, "net": net, "roi": roi, "avg": american(avg_dec) if avg_dec else "--",
            "streak": streak, "n": len(g)}


def lock_pick(rows, d):
    """Today's pick: the row already in the log, or the model's top candidate written down now."""
    day = d["iso"]
    for r in rows:
        if r["Date"] == day:
            return r, False
    if not d.get("potd"):
        return None, False
    c = d["potd"][0]
    r = {"Date": day, "Locked At (UTC)": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "Player": c["name"],
         "Pos": c["pos"], "Team": c["off"], "Opponent": c["def"], "Home/Away": "Home" if c["home"] else "Away", "Tip (UTC)": c.get("tip", ""),
         "Stat": c["stat"], "Line": fmt(c["line"]), "Side": "Over", "American": c["american"], "Decimal": f"{c['dec']:.3f}",
         "Units": fmt(UNITS), "Projection": f"{c['proj']:.1f}" if c.get("proj") is not None else ""}
    rows.append(r)
    return r, True


# ---------------------------------------------------------------- words
def tip_ct(row_or_c):
    t = row_or_c.get("Tip (UTC)") or row_or_c.get("tip") or ""
    try:
        x = dt.datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(CT)
        return x.strftime("%-I:%M %p CT")
    except ValueError:
        return "TBD"


def potd_why(c):
    """Plain write-up for the pick from the numbers (edit it before posting if you want to add your own read)."""
    sw = STAT_WORDS.get(c["stat"], c["stat"].lower())
    out = []
    if c.get("hits") is not None and c.get("n"):
        vals = ", ".join(fmt(v) for v in (c.get("lastVals") or []))
        out.append(f"{c['name']} has gone over {fmt(c['line'])} {sw} in {c['hits']} of his last {c['n']}" + (f" ({vals})." if vals else "."))
    out.append(f"He's averaging {fmt(c['avg'])} over {c.get('win') or 'the season'}, and the model projects {fmt(c['proj'])} tonight.")
    if c.get("defRank") and c.get("defVal") is not None:
        lg = f" (league average {fmt(c['league'])})" if c.get("league") else ""
        out.append(f"{c['def']} allow {fmt(c['defVal'])} {sw} a game to {c['pos']}s{lg}, #{c['defRank']} most in the league.")
    sp = c.get("split") or {}
    if sp.get("vAvg") is not None and sp.get("vN"):
        out.append(f"{'At home' if sp.get('home') else 'On the road'} he's averaging {fmt(sp['vAvg'])} in {sp['vN']} games.")
    return out


def reddit_comment(row, c, rec):
    sw = STAT_WORDS.get(row["Stat"], row["Stat"])
    away, home = (row["Opponent"], row["Team"]) if row["Home/Away"] == "Home" else (row["Team"], row["Opponent"])
    matchup = f"{away} @ {home}"
    first = rec["n"] == 0
    why = " ".join(potd_why(c)) if c else ""
    lines = [
        f"**Record:** {rec['rec']}" + (" (first pick)" if first else ""),
        f"**Net Units:** {rec['net']:+.2f}u",
        f"**ROI:** {rec['roi']:.1f}%",
        f"**Avg Odds:** {rec['avg']}",
        "",
        f"Basketball | NBA | {matchup} | {tip_ct(row)}",
        "",
        f"**Pick:** {row['Player']} Over {row['Line']} {sw} ({row['American']}) - {fmt(row['Units'])}U",
        "",
        f"**Write Up:** {why}",
    ]
    return "\n".join(lines)


def captions(d, row, rec, site):
    legs = d["legs"]
    pick = f"{row['Player']} o{row['Line']} {STAT_SHORT.get(row['Stat'], row['Stat'])} ({row['American']})" if row else ""
    rec_line = f"POTD record: {rec['rec']}, {rec['net']:+.1f}u" if rec["n"] else "POTD record starts today"
    x = (f"NBA Pick of the Day · {d['date']}\n\n{pick} · 1U\n\n{rec_line}\n\nTonight's Starting 5 ↓ #NBA #NBAPicks #HitByOne" if row else
         f"Tonight's NBA Starting 5 · {d['date']} #NBA #HitByOne")
    x_reply = f"Every Pick of the Day, graded since day one: {site}record/" if site else ""
    p5 = "\n".join(f"{l['pos']} {l['name']} {compact(l['pick'])}" for l in legs)
    ig = (f"Tonight's Starting 5 · {d['date']} · {d['games']} games\n\n{p5}\n\n"
          + (f"Pick of the Day: {pick} · 1U\n{rec_line}\n\n" if row else "")
          + "Swipe for the Top 5 by stat and how last night went. Full card at the link in bio.\n\n"
          + "21+ · For entertainment. Gambling problem? Call 1-800-GAMBLER.\n\n#HitByOne #NBA #NBAPicks #PlayerProps #NBABets #Basketball")
    tt = (f"Tonight's NBA matchups that set up big nights 👀 {d['date']}\n\n"
          + "\n".join(f"{l['pos']} {l['name']} vs {l['def']}" for l in legs)
          + "\n\nFull breakdown at the link in bio.\n#HitByOne #NBA #NBAStats #Basketball #NBATonight")
    return {"x": x, "x_reply": x_reply, "ig": ig, "tt": tt}


# ---------------------------------------------------------------- pictures
FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800;900&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">')
BASE_CSS = f"""
*{{box-sizing:border-box}} body{{margin:0;background:{BG}}}
#s{{position:relative;overflow:hidden;background:{BG};color:{TXT};font-family:Inter,Arial,sans-serif}}
.num{{font-family:'Space Grotesk',Inter,sans-serif;font-weight:700}}
.lab{{font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:{ORANGE}}}
.brand{{font-weight:900;letter-spacing:-.01em}} .brand span{{color:{ORANGE}}}
.card{{background:{CARD};border-radius:22px;box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.07)}}
.dim{{color:{DIM}}}
.glow{{position:absolute;border-radius:50%;filter:blur(120px);opacity:.28;pointer-events:none}}
.pc{{position:absolute;width:128px;padding:8px 9px;border-radius:12px;background:#161826;box-shadow:0 6px 18px rgba(0,0,0,.45),inset 0 0 0 1px rgba(255,255,255,.07);display:flex;flex-direction:column;gap:1px}}
.pc .t{{display:flex;align-items:center;gap:5px;font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden}}
.pos{{flex:none;width:19px;height:19px;border-radius:10px;box-shadow:inset 0 0 0 1.5px {GREEN};display:flex;align-items:center;justify-content:center;font-size:8.5px;font-weight:700}}
.pc .mt{{font-size:9.5px;color:rgba(242,244,250,.7);white-space:nowrap;overflow:hidden}}
.pc .big{{font-family:'Space Grotesk',Inter,sans-serif;font-size:15px;font-weight:700;color:{GREEN}}}
"""
SPOTS = {"PG": (104, 12), "SF": (8, 108), "SG": (200, 108), "PF": (24, 232), "C": (184, 232)}


def court(legs, scale, stats_only=False):
    """The Starting 5 half court (336 x 372 drawn, scaled up)."""
    cards = []
    for l in legs:
        x, y = SPOTS.get(l["pos"], (0, 0))
        if stats_only:
            big = f"{NAME_SHORT.get(l['stat'], l['stat'])} {fmt(l.get('proj'))}"
            sub = f"proj &middot; avg {fmt(l.get('avg'))}"
        else:
            big = esc(compact(l["pick"]))
            sub = (f"{l['hits']}/{l['n']} over" if l.get("hits") is not None and l.get("n") else esc(l.get("sub", "")))
        if l.get("defRank"):
            sub += f" &middot; {esc(l['def'])} #{l['defRank']}"
        cards.append(f'<div class="pc" style="left:{x}px;top:{y}px"><div class="t"><span class="pos">{esc(l["pos"])}</span>{esc(card_name(l["name"]))}</div>'
                     f'<div class="mt">{esc(l["off"])} {"vs" if l.get("home") else "@"} {esc(l["def"])} &middot; {esc(l.get("time") or "")}</div>'
                     f'<div class="big">{big}</div><div class="mt">{sub}</div></div>')
    return f'''<div style="width:{336 * scale}px;height:{372 * scale}px;position:relative;">
<div style="position:absolute;left:0;top:0;width:336px;height:372px;transform:scale({scale});transform-origin:0 0;border-radius:16px;background:#0f1119;overflow:hidden">
<svg viewBox="0 0 500 470" preserveAspectRatio="none" width="336" height="372" style="position:absolute;left:0;top:0">
 <g stroke="rgba(255,255,255,0.12)" stroke-width="2" fill="none">
  <rect x="1" y="1" width="498" height="468" rx="18"/><rect x="170" y="280" width="160" height="189" fill="rgba(255,122,47,0.05)"/>
  <circle cx="250" cy="280" r="60"/><path d="M 30 469 L 30 330 A 237.5 237.5 0 0 1 470 330 L 470 469"/><path d="M 190 1 A 60 60 0 0 0 310 1"/></g>
 <circle cx="250" cy="417.5" r="8" fill="none" stroke="{ORANGE}" stroke-width="2.5"/></svg>
{"".join(cards)}</div></div>'''


def page(w, h, body):
    return (f'<!doctype html><html><head><meta charset="utf-8">{FONTS}<style>{BASE_CSS}</style></head><body>'
            f'<div id="s" style="width:{w}px;height:{h}px;">'
            f'<div class="glow" style="width:700px;height:700px;left:-260px;top:-300px;background:{ORANGE}"></div>'
            f'<div class="glow" style="width:640px;height:640px;right:-280px;bottom:-280px;background:{PINK}"></div>'
            f'{body}</div></body></html>')


def bars(vals, line, w=360, h=120):
    if not vals:
        return ""
    m = max(max(vals), line or 0, 1) * 1.15
    bw = w / len(vals)
    out = []
    for i, v in enumerate(vals):
        bh = max(4, v / m * h)
        col = GREEN if line is not None and v > line else RED if line is not None else DIM
        out.append(f'<rect x="{i * bw + 8:.1f}" y="{h - bh:.1f}" width="{bw - 16:.1f}" height="{bh:.1f}" rx="8" fill="{col}"/>'
                   f'<text x="{i * bw + bw / 2:.1f}" y="{h - bh - 10:.1f}" fill="{TXT}" font-size="24" font-weight="700" text-anchor="middle" font-family="Space Grotesk,Inter">{fmt(v)}</text>')
    ly = h - (line / m * h) if line is not None else None
    ln = f'<line x1="0" x2="{w}" y1="{ly:.1f}" y2="{ly:.1f}" stroke="{TXT}" stroke-dasharray="8 8" stroke-width="2" opacity=".55"/>' if ly is not None else ""
    return f'<svg width="{w}" height="{h + 4}" viewBox="0 -40 {w} {h + 44}" style="overflow:visible">{"".join(out)}{ln}</svg>'


def head(d, size, sub):
    return (f'<div style="display:flex;justify-content:space-between;align-items:flex-end;">'
            f'{mark(size)}'
            f'<div class="dim" style="font-size:{size * .42:.0f}px;font-weight:600;text-align:right;">{esc(d["date"])}<br>{sub}</div></div>')


def slide_x(d, row, c, rec):
    pc = ""
    if row:
        why = "".join(f'<div style="margin:0 0 10px;">{esc(w)}</div>' for w in potd_why(c)[:3]) if c else ""
        pc = f'''<div class="lab" style="font-size:22px;">NBA Pick of the Day</div>
          <div style="font-size:50px;font-weight:900;margin:14px 0 4px;line-height:1.05">{esc(row["Player"])}</div>
          <div class="dim" style="font-size:22px;font-weight:600">{esc(row["Team"])} {"vs" if row["Home/Away"] == "Home" else "@"} {esc(row["Opponent"])} &middot; {esc(tip_ct(row))}</div>
          <div class="num" style="font-size:74px;color:{GREEN};margin:18px 0 0;line-height:1">OVER {esc(row["Line"])} {esc(STAT_SHORT.get(row["Stat"], row["Stat"]))}</div>
          <div class="num" style="font-size:30px;margin:8px 0 24px;">{esc(row["American"])} &middot; 1U</div>
          <div style="font-size:21px;line-height:1.4;color:rgba(242,244,250,.86)">{why}</div>'''
    pay = f'Starting 5 together <b class="num" style="color:{GREEN};font-size:34px">{esc(d["pay"])}</b>' if d.get("pay") else "Tonight's Starting 5"
    recs = f'POTD {esc(rec["rec"])} &middot; {rec["net"]:+.1f}u' if rec["n"] else ""
    body = f'''<div style="position:absolute;left:56px;top:48px;">{court(d["legs"], 2.15)}</div>
      <div style="position:absolute;left:830px;top:60px;width:720px;">{pc}</div>
      <div style="position:absolute;left:830px;right:56px;bottom:52px;display:flex;justify-content:space-between;align-items:center;font-size:22px;font-weight:600;">
        <span>{pay}</span><span class="dim">{recs}</span></div>
      <div style="position:absolute;right:56px;top:40px;opacity:.92">{mark(26)}</div>'''
    return page(1600, 900, body)


def ig_1(d):
    slip = "".join(f'''<div class="card" style="flex:1;padding:16px 16px;min-width:0">
        <div class="dim" style="font-size:18px;font-weight:800">{esc(l["pos"])}</div>
        <div style="font-size:22px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{esc(short_name(l["name"]))}</div>
        <div class="num" style="font-size:24px;color:{GREEN}">{esc(compact(l["pick"]))}</div></div>''' for l in d["legs"])
    pay = (f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:22px;font-size:24px;font-weight:600">'
           f'<span class="dim">All five together, best prices</span><span class="num" style="font-size:54px;color:{GREEN}">{esc(d["pay"])}</span></div>') if d.get("pay") else ""
    body = f'''<div style="position:absolute;inset:56px 56px;">{head(d, 46, f"{d['games']} games")}
      <div class="lab" style="font-size:24px;margin:34px 0 18px;">Tonight's Starting 5</div>
      <div style="display:flex;justify-content:center">{court(d["legs"], 2.3)}</div>
      <div style="display:flex;gap:10px;margin-top:26px">{slip}</div>{pay}</div>'''
    return page(1080, 1350, body)


def ig_2(d, row, c, rec):
    if not row:
        return None
    why = "".join(f'<div style="margin:0 0 14px;">{esc(w)}</div>' for w in potd_why(c)) if c else ""
    vals = (c or {}).get("lastVals") or []
    body = f'''<div style="position:absolute;inset:56px 56px;">{head(d, 46, "Pick of the Day")}
      <div class="card" style="margin-top:40px;padding:44px 44px;">
        <div class="lab" style="font-size:24px;">NBA Pick of the Day</div>
        <div style="font-size:64px;font-weight:900;margin:16px 0 6px;line-height:1.05">{esc(row["Player"])}</div>
        <div class="dim" style="font-size:26px;font-weight:600">{esc(row["Pos"])} &middot; {esc(row["Team"])} {"vs" if row["Home/Away"] == "Home" else "@"} {esc(row["Opponent"])} &middot; {esc(tip_ct(row))}</div>
        <div class="num" style="font-size:96px;color:{GREEN};margin:26px 0 0;line-height:1">OVER {esc(row["Line"])} {esc(STAT_SHORT.get(row["Stat"], row["Stat"]))}</div>
        <div class="num" style="font-size:38px;margin:10px 0 0;">{esc(row["American"])} &middot; 1 unit</div>
        <div style="margin:36px 0 10px;" class="dim"><b style="font-size:22px;color:{TXT}">Last {len(vals)} games</b> <span style="font-size:20px">vs the line</span></div>
        {bars(vals, float(row["Line"]), 900, 150)}
        <div style="font-size:25px;line-height:1.45;color:rgba(242,244,250,.88);margin-top:30px">{why}</div>
      </div>
      <div class="dim" style="position:absolute;left:0;right:0;bottom:0;display:flex;justify-content:space-between;font-size:24px;font-weight:600">
        <span>POTD record {esc(rec["rec"]) if rec["n"] else "starts today"}</span><span>{f"{rec['net']:+.2f}u &middot; ROI {rec['roi']:.1f}%" if rec["n"] else ""}</span></div></div>'''
    return page(1080, 1350, body)


def stat_rows(d, stats_only, big, tall=1.0):
    rows = []
    for blk in d.get("byStat") or []:
        chips = ""
        for i, x in enumerate(blk["list"][:5]):
            ring = f"box-shadow:inset 0 0 0 3px {ORANGE};" if i == 0 else ""
            opp = f"{'vs' if x.get('home') else '@'} {esc(x['def'])}"
            if stats_only:
                val, sub = f"{fmt(x.get('proj'))}", f"{opp} &middot; {esc(x['pos'])}"
            else:
                # the OVER line when it was ranked against the book; the projection when no line was up yet
                val = compact(x["pick"]).split(" ")[0] if blk["how"] == "best" else fmt(x.get("proj"))
                sub = (f"{opp} &middot; {x['hits']}/{x['n']}" if x.get("hits") is not None else opp) if blk["how"] == "best" else f"{opp} &middot; proj"
            chips += (f'<div class="card" style="flex:1;min-width:0;padding:{big * .55 * tall:.0f}px {big * .36:.0f}px;{ring}">'
                      f'<div style="display:flex;flex-wrap:wrap;align-items:baseline;column-gap:{big * .25:.0f}px;min-width:0"><span style="min-width:0;font-size:{big * .85:.0f}px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%">{esc(short_name(x["name"]))}</span>'
                      f'<span style="flex:none;font-size:{big * .6:.0f}px;font-weight:800;color:{ORANGE}">{esc(x["off"])}</span></div>'
                      f'<div class="num" style="font-size:{big * 1.25:.0f}px;color:{GREEN}">{esc(val)}</div>'
                      f'<div class="dim" style="font-size:{big * .78:.0f}px;white-space:nowrap">{sub}</div></div>')
        rows.append(f'<div style="display:flex;gap:{big * .35:.0f}px;align-items:stretch;margin-bottom:{big * .7 * tall:.0f}px">'
                    f'<div style="width:{big * 3.1:.0f}px;flex:none;display:flex;align-items:center;font-weight:800;font-size:{big * 1.05:.0f}px;letter-spacing:.06em;color:{PINK};flex-direction:column;align-items:flex-start;justify-content:center">{esc(blk["short"])}'
                    + (f'<span class="dim" style="font-size:{big * .5:.0f}px;font-weight:600;letter-spacing:0">no lines yet</span>' if blk["how"] != "best" and not stats_only else "")
                    + f'</div>{chips}</div>')
    return "".join(rows)


def ig_3(d):
    body = f'''<div style="position:absolute;inset:56px 56px;">{head(d, 46, "Top 5 by stat")}
      <div class="lab" style="font-size:24px;margin:40px 0 8px;">Top 5 by stat</div>
      <div class="dim" style="font-size:22px;margin-bottom:30px">Best first &middot; OVER line &middot; times over in his last 5</div>
      {stat_rows(d, False, 28, 1.25)}</div>'''
    return page(1080, 1350, body)


def ig_4(d, rec, log_rows):
    last = d.get("last")
    rc = {"HIT": GREEN, "MISS": RED, "VOID": DIM}
    legs = "".join(f'''<div style="display:flex;align-items:center;gap:18px;padding:16px 0;border-top:1.5px solid rgba(255,255,255,.07)">
        <span class="dim" style="width:44px;font-size:22px;font-weight:800">{esc(l["pos"])}</span>
        <span style="flex:1;font-size:27px;font-weight:700">{esc(short_name(l["name"]))} <span class="dim" style="font-size:21px;font-weight:500">{esc(compact(l["pick"]))}</span></span>
        <span class="num" style="font-size:32px">{fmt(l["actual"]) if l["actual"] is not None else "DNP"}</span>
        <span style="width:96px;text-align:center;font-size:19px;font-weight:800;padding:6px 0;border-radius:10px;color:{rc.get(l["result"], DIM)};box-shadow:inset 0 0 0 2px {rc.get(l["result"], DIM)}">{esc(l["result"])}</span></div>''' for l in (last or {}).get("legs", []))
    r = d.get("record") or {}
    tile = lambda big, small: (f'<div class="card" style="flex:1;padding:26px 10px;text-align:center"><div class="num" style="font-size:52px">{big}</div>'
                               f'<div class="dim" style="font-size:20px;margin-top:4px">{small}</div></div>')
    pct = lambda h, g: f"{round(h / g * 100)}%" if g else "--"
    prev = [x for x in log_rows if x.get("Result")][-1:] if log_rows else []
    prev_line = ""
    if prev:
        x = prev[0]
        col = GREEN if x["Result"] == "W" else RED if x["Result"] == "L" else DIM
        prev_line = (f'<div style="margin-top:18px;font-size:24px;font-weight:600">Last POTD: {esc(x["Player"])} o{esc(x["Line"])} {esc(STAT_SHORT.get(x["Stat"], x["Stat"]))} '
                     f'&rarr; {esc(x["Actual"])} <b style="color:{col}">{esc(x["Result"])}</b></div>')
    body = f'''<div style="position:absolute;inset:56px 56px;">{head(d, 46, "The record")}
      <div class="lab" style="font-size:24px;margin:40px 0 14px;">Last night's Starting 5{f" &middot; {last['hits']} of {last['graded']} hit" if last else ""}</div>
      <div class="card" style="padding:6px 30px 10px">{legs or '<div class="dim" style="padding:24px 0;font-size:24px">No graded night yet.</div>'}</div>
      <div class="lab" style="font-size:24px;margin:44px 0 16px;">Pick of the Day record</div>
      <div style="display:flex;gap:14px">{tile(esc(rec["rec"]) if rec["n"] else "0-0", "record")}{tile(f"{rec['net']:+.1f}u" if rec["n"] else "--", "net units")}{tile(f"{rec['roi']:.1f}%" if rec["n"] else "--", "ROI")}</div>
      {prev_line}
      <div class="lab" style="font-size:24px;margin:44px 0 16px;">Starting 5 season</div>
      <div style="display:flex;gap:14px">{tile(pct(r.get("hits", 0), r.get("legs", 0)), "legs hit")}{tile(f"{r.get('cashed', 0)}/{r.get('nights', 0)}", "nights all five hit")}{tile(pct(r.get("l7hits", 0), r.get("l7legs", 0)), "last 7 nights")}</div>
      <div class="dim" style="position:absolute;left:0;right:0;bottom:0;text-align:center;font-size:19px">21+ &middot; For entertainment. Gambling problem? Call 1-800-GAMBLER.</div></div>'''
    return page(1080, 1350, body)


# TikTok: matchups and projections only -- no lines, odds, units or payouts.
# TikTok's buttons cover the right edge and the caption covers the bottom, so everything stays inside TT_SAFE.
TT_SAFE = "top:150px;left:64px;right:150px;bottom:380px"
def tt_1(d):
    games = "".join(f'<div class="card" style="padding:22px 26px;display:flex;justify-content:space-between;align-items:center;font-size:36px;font-weight:700">'
                    f'<span>{esc(g["away"])} @ {esc(g["home"])}</span><span class="dim" style="font-size:30px">{esc(g["time"])}</span></div>'
                    for g in (d.get("schedule") or [])[:6])
    more = len(d.get("schedule") or []) - 6
    if more > 0:
        games += f'<div class="dim" style="font-size:30px;font-weight:600;padding:6px 4px">+ {more} more games tonight</div>'
    body = f'''<div style="position:absolute;{TT_SAFE};display:flex;flex-direction:column;">
      {mark(52)}
      <div class="dim" style="font-size:34px;font-weight:600;margin-top:10px">{esc(d["date"])}</div>
      <div style="font-size:112px;font-weight:900;line-height:1;margin:90px 0 30px;letter-spacing:-2px">Tonight's<br><span style="color:{ORANGE}">matchups</span></div>
      <div style="font-size:44px;line-height:1.3;color:rgba(242,244,250,.88);margin-bottom:60px">The 5 players these defenses set up for a big night.</div>
      <div style="display:flex;flex-direction:column;gap:14px">{games}</div></div>'''
    return page(1080, 1920, body)


def tt_2(d):
    legs = d["legs"]
    lines = "".join(f'''<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-top:2px solid rgba(255,255,255,.07)">
        <span style="font-size:34px;font-weight:700"><span class="dim" style="font-size:28px">{esc(l["pos"])}</span> {esc(short_name(l["name"]))}</span>
        <span class="dim" style="font-size:28px">{esc(l["def"])} #{esc(l.get("defRank") or "--")} vs {esc(l["pos"])}s</span></div>''' for l in legs)
    body = f'''<div style="position:absolute;{TT_SAFE};">
      <div class="lab" style="font-size:34px;">Tonight's Starting 5</div>
      <div style="font-size:38px;line-height:1.3;margin:12px 0 36px;color:rgba(242,244,250,.88)">One per position. The matchup that should feed him most.</div>
      <div style="display:flex;justify-content:center">{court(legs, 2.1, stats_only=True)}</div>
      <div style="margin-top:36px">{lines}</div></div>'''
    return page(1080, 1920, body)


def tt_3(d):
    body = f'''<div style="position:absolute;{TT_SAFE};">
      <div class="lab" style="font-size:34px;">Top 5 by stat</div>
      <div style="font-size:40px;line-height:1.3;margin:14px 0 60px;color:rgba(242,244,250,.88)">Tonight's projections. Best matchup first.</div>
      {stat_rows(d, True, 28, 2.5)}</div>'''
    return page(1080, 1920, body)


def tt_4(d):
    last = d.get("last")
    if not last:
        return None
    rows = "".join(f'''<div class="card" style="display:flex;align-items:center;gap:24px;padding:30px 34px;margin-bottom:18px">
        <span class="dim" style="width:60px;font-size:30px;font-weight:800">{esc(l["pos"])}</span>
        <span style="flex:1;font-size:40px;font-weight:700">{esc(short_name(l["name"]))}</span>
        <span class="num" style="font-size:52px;color:{GREEN if l["result"] == "HIT" else TXT}">{fmt(l["actual"]) if l["actual"] is not None else "DNP"}</span>
        <span class="dim" style="width:90px;font-size:28px">{esc(l.get("stat") or "")}</span></div>''' for l in last["legs"])
    body = f'''<div style="position:absolute;{TT_SAFE};">
      <div class="lab" style="font-size:34px;">Last night</div>
      <div style="font-size:40px;line-height:1.3;margin:14px 0 60px;color:rgba(242,244,250,.88)">How last night's five actually played. {esc(last["date"])}.</div>
      {rows}
      <div style="font-size:44px;font-weight:800;margin-top:50px">{last["hits"]} of {last["graded"]} came through.</div></div>'''
    return page(1080, 1920, body)


def tt_5(d):
    body = f'''<div style="position:absolute;{TT_SAFE};display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center">
      {mark(104, center=True)}
      <div style="font-size:48px;line-height:1.35;margin:70px 0 0;color:rgba(242,244,250,.9)">Every matchup, every position,<br>every night.</div>
      <div style="margin-top:90px;padding:30px 60px;border-radius:999px;background:{ORANGE};color:{BG};font-size:44px;font-weight:900">Full breakdown: link in bio</div></div>'''
    return page(1080, 1920, body)


def shoot(pages, out_dir):
    """Render each (filename, html, w, h) to a JPEG."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, src, w, h in pages:
            if not src:
                continue
            p = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            if os.environ.get("HW_CDN_DIR"):
                p.route("**/fonts.g*/**", lambda r: r.abort())
            p.set_content(src, wait_until="load")
            p.wait_for_timeout(700)
            p.screenshot(path=str(out_dir / name), type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": w, "height": h})
            p.close()
        b.close()


def make_video(slides, out):
    """Slides -> vertical mp4: 3.2s each, 0.4s crossfades, 30fps, H.264. Silent: add a sound in TikTok."""
    ff = shutil.which("ffmpeg")
    if not ff or not slides:
        print("Social: ffmpeg not found -- skipped the TikTok video (the slides are still there).")
        return False
    dur, fade = 3.2, 0.4
    cmd = [ff, "-y", "-loglevel", "error"]
    for s in slides:
        cmd += ["-loop", "1", "-t", f"{dur}", "-i", str(s)]
    chain, prev = [], "[0:v]"
    for i in range(1, len(slides)):
        off = i * (dur - fade)
        lab = f"[v{i}]"
        chain.append(f"{prev}[{i}:v]xfade=transition=fade:duration={fade}:offset={off:.2f}{lab}")
        prev = lab
    flt = (";".join(chain) + ";" if chain else "") + f"{prev}scale=1080:1920,format=yuv420p[out]"
    cmd += ["-filter_complex", flt, "-map", "[out]", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return True


# ---------------------------------------------------------------- the pages
def copy_box(label, text, id_):
    return (f'<section><div class="h"><b>{esc(label)}</b><button type="button" data-copy="{id_}">Copy</button></div>'
            f'<textarea id="{id_}" readonly rows="{min(14, text.count(chr(10)) + 2)}">{esc(text)}</textarea></section>')


def day_page(d, files, caps, reddit, row, rec, site, zip_name=""):
    img = lambda f, w: f'<a href="{f}" download><img src="{f}" alt="" style="width:{w}"></a>'
    ig = "".join(img(f, "48%") for f in files if f.startswith("ig-"))
    tt = "".join(img(f, "31%") for f in files if f.startswith("tt-"))
    vid = '<video src="tiktok.mp4" controls playsinline style="width:min(100%,360px);border-radius:14px"></video><p><a href="tiktok.mp4" download>Download the video</a></p>' if "tiktok.mp4" in files else ""
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Social kit &middot; {esc(d["date"])}</title><style>
body{{margin:0;background:{BG};color:{TXT};font:500 15px/1.5 Inter,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:24px 16px 60px}} h1{{font-size:24px;margin:0}} h1 span{{color:{ORANGE}}}
h2{{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:{ORANGE};margin:34px 0 10px}}
img{{border-radius:12px;margin:0 1% 10px 0;vertical-align:top}} a{{color:{ORANGE}}}
section{{background:{CARD};border:1px solid {LINE};border-radius:14px;padding:12px 14px;margin:10px 0}}
.h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
button{{font:700 14px Inter,sans-serif;background:{ORANGE};color:{BG};border:0;border-radius:999px;padding:10px 18px;min-height:44px;cursor:pointer}}
textarea{{width:100%;box-sizing:border-box;background:{BG};color:{TXT};border:1px solid {LINE};border-radius:10px;padding:10px;font:500 14px/1.45 ui-monospace,Menlo,monospace;resize:vertical}}
.dim{{color:{DIM}}}
</style></head><body><main>
<h1>HITBY<span>ONE</span> &middot; social kit</h1>
<div class="dim">{esc(d["date"])} &middot; {d["games"]} games &middot; tap an image to save it (or long-press on a phone).</div>
{f'<p style="margin:16px 0 0"><a href="{zip_name}" download style="display:inline-block;background:{ORANGE};color:{BG};font-weight:800;text-decoration:none;padding:14px 22px;border-radius:999px">Download everything (zip)</a></p><div class="dim" style="margin-top:6px">Then in Files: tap the zip to unzip it. One folder per app, captions included.</div>' if zip_name else ""}
<h2>Reddit &middot; r/sportsbook Pick of the Day</h2>
<div class="dim">Paste as a top-level comment in today's Pick of the Day thread. Edit the write-up if you want to add your own read.</div>
{copy_box("Reddit comment", reddit, "c-reddit") if reddit else "<p class='dim'>No pick today: nothing on the board fit -200 to +200.</p>"}
<h2>X</h2>{img("x.jpg", "100%")}{copy_box("Post", caps["x"], "c-x")}{copy_box("Reply with the link", caps["x_reply"], "c-xr") if caps["x_reply"] else ""}
<h2>Instagram &middot; carousel</h2>{ig}{copy_box("Caption", caps["ig"], "c-ig")}
<h2>TikTok &middot; video + slides</h2><div class="dim">Stats only: no odds or lines, so it stays eligible for the For You feed. Add a sound in TikTok.</div>{vid}{tt}{copy_box("Caption", caps["tt"], "c-tt")}
<p class="dim" style="margin-top:30px">Private kit &middot; don't share this link. The public record (for bios and posts): {f'<a href="{site}record/">{site}record/</a>' if site else "/record/"}</p>
</main><script>
document.querySelectorAll('button[data-copy]').forEach(b => b.addEventListener('click', async () => {{
  const t = document.getElementById(b.dataset.copy);
  try {{ await navigator.clipboard.writeText(t.value); }} catch (e) {{ t.select(); document.execCommand('copy'); }}
  const o = b.textContent; b.textContent = 'Copied'; setTimeout(() => b.textContent = o, 1400);
}}));
</script></body></html>'''


def index_page(log_rows, rec):
    res_col = {"W": GREEN, "L": RED, "P": DIM, "VOID": DIM}
    rows = "".join(f'''<tr><td>{esc(r["Date"])}</td><td>{esc(r["Player"])}<div class="dim">{esc(r["Team"])} {"vs" if r["Home/Away"] == "Home" else "@"} {esc(r["Opponent"])}</div></td>
      <td>Over {esc(r["Line"])} {esc(STAT_SHORT.get(r["Stat"], r["Stat"]))}</td><td class="n">{esc(r["American"])}</td><td class="n">{esc(r["Units"])}u</td>
      <td class="n">{esc(r.get("Actual") or "")}</td><td><b style="color:{res_col.get(r.get("Result"), DIM)}">{esc(r.get("Result") or "pending")}</b></td>
      <td class="n">{(f"{float(r['Profit']):+.2f}" if r.get("Profit") else "")}</td></tr>''' for r in reversed(log_rows) if r.get("Result"))   # graded only: today's pick stays private until it's settled
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HitByOne Hardwood Ledger &middot; Pick of the Day record</title><style>
body{{margin:0;background:{BG};color:{TXT};font:500 15px/1.5 Inter,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:24px 16px 60px}} h1{{font-size:24px;margin:0}} h1 span{{color:{ORANGE}}}
h2{{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:{ORANGE};margin:30px 0 10px}} a{{color:{ORANGE};margin-right:10px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}}
.tiles div{{background:{CARD};border:1px solid {LINE};border-radius:14px;padding:14px;text-align:center}} .tiles b{{display:block;font-size:26px}}
.wrap{{overflow-x:auto;background:{CARD};border:1px solid {LINE};border-radius:14px}}
table{{border-collapse:collapse;width:100%;min-width:640px}} td,th{{padding:9px 10px;border-top:1px solid {LINE};text-align:left;vertical-align:top}}
th{{font-size:12px;color:{DIM};border-top:0}} .n{{text-align:right}} .dim{{color:{DIM};font-size:13px}}
</style></head><body><main>
<h1>HITBY<span>ONE</span></h1><div class="dim" style="font-weight:800;letter-spacing:.26em;font-size:12px;margin:6px 0 8px">HARDWOOD LEDGER</div>
<div class="dim">NBA Pick of the Day &middot; every pick since day one, locked before tip at the posted price, flat 1 unit, never reset.</div>
<h2>Pick of the Day record</h2>
<div class="tiles"><div><b>{esc(rec["rec"]) if rec["n"] else "0-0"}</b><span class="dim">W-L{"-P" if rec["p"] else ""}</span></div>
<div><b>{rec["net"]:+.2f}u</b><span class="dim">net units</span></div><div><b>{rec["roi"]:.1f}%</b><span class="dim">ROI</span></div>
<div><b>{esc(rec["avg"])}</b><span class="dim">average odds</span></div><div><b>{esc(rec["streak"] or "--")}</b><span class="dim">streak</span></div></div>
<h2>Every pick</h2><div class="wrap"><table><thead><tr><th>Date</th><th>Player</th><th>Pick</th><th class="n">Odds</th><th class="n">Size</th><th class="n">Actual</th><th>Result</th><th class="n">Units</th></tr></thead>
<tbody>{rows or '<tr><td colspan="8" class="dim">The first graded pick shows here the morning after it plays.</td></tr>'}</tbody></table></div>
<p class="dim">21+ &middot; For entertainment. Gambling problem? Call 1-800-GAMBLER.</p>
</main></body></html>'''


def publish_record(rows):
    """/record/ = the public record; /social/ (the old link) forwards there."""
    RECORD.mkdir(parents=True, exist_ok=True)
    (RECORD / "index.html").write_text(index_page(rows, record(rows)), encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text('<!doctype html><html><head><meta charset="utf-8"><title>HitByOne Hardwood Ledger record</title>'
                                    '<meta http-equiv="refresh" content="0; url=../record/"><link rel="canonical" href="../record/"></head>'
                                    '<body><a href="../record/">HitByOne Hardwood Ledger: Pick of the Day record</a></body></html>', encoding="utf-8")


def kit_key():
    k = os.environ.get("KIT_KEY", "").strip()
    return k if len(k) >= 16 and all(ch.isalnum() or ch in "-_" for ch in k) else ""


def publish_kit(day_dir, files, zip_name):
    """Put today's kit at /social/<KIT_KEY>/ (deployed with the site, never committed)."""
    key = kit_key()
    for old in OUT.glob("*"):                        # anything else under /social/ (old kits, an old key) comes down
        if old.is_dir() and old.name != key:
            shutil.rmtree(old, ignore_errors=True)
    if not key:
        if os.environ.get("KIT_KEY"):
            print("Social: KIT_KEY must be 16+ letters/numbers (- and _ allowed) -- private kit page skipped.")
        return False
    dest = OUT / key
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for f in files + [zip_name, "index.html"]:
        if (day_dir / f).exists():
            shutil.copy2(day_dir / f, dest / f)
    print("Social: kit page is up at your private link (/social/<your KIT_KEY>/).")
    return True


def site_url():
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def make_zip(d, day_dir, files, caps, reddit):
    """One zip for the phone: a folder per app, pictures in posting order, captions as text files beside them."""
    top = f"HitByOne_{day_dir.name}"
    plan = []                                         # (path inside the zip, source file or text)
    if reddit:
        plan.append((f"{top}/1_Reddit_PickOfTheDay.txt", reddit))
    plan += [(f"{top}/2_X/x_image.jpg", day_dir / "x.jpg"), (f"{top}/2_X/post.txt", caps["x"])]
    if caps.get("x_reply"):
        plan.append((f"{top}/2_X/reply_with_link.txt", caps["x_reply"]))
    plan += [(f"{top}/3_Instagram/{i}.jpg", day_dir / f"ig-{i}.jpg") for i in range(1, 5)]
    plan.append((f"{top}/3_Instagram/caption.txt", caps["ig"]))
    plan.append((f"{top}/4_TikTok/tiktok_video.mp4", day_dir / "tiktok.mp4"))
    plan += [(f"{top}/4_TikTok/slide_{i}.jpg", day_dir / f"tt-{i}.jpg") for i in range(1, 6)]
    plan.append((f"{top}/4_TikTok/caption.txt", caps["tt"]))
    out = day_dir / f"{top}.zip"
    with zipfile.ZipFile(out, "w") as z:
        for name, src in plan:
            if isinstance(src, Path):
                if src.exists():                        # pictures and video are already compressed: store them
                    z.write(src, name, compress_type=zipfile.ZIP_STORED)
            elif src:
                z.writestr(name, src + "\n", compress_type=zipfile.ZIP_DEFLATED)
    return out


def email_kit(d, day_dir, files, caps, reddit):
    """Send the day's kit to you only: captions and the Reddit comment in the body, every picture + the video attached."""
    sender = os.environ.get("EMAIL_FROM", "").strip()
    pw = os.environ.get("EMAIL_PASSWORD", "").replace(" ", "").strip()
    to = ((os.environ.get("TEST_TO", "") if TEST else "") or os.environ.get("KIT_TO", "")).strip() or sender
    if not (sender and pw):
        print("Social: EMAIL_FROM / EMAIL_PASSWORD not set -- kit saved but not emailed.")
        return False
    blocks = [("Reddit · r/sportsbook Pick of the Day (paste as a top-level comment)", reddit),
              ("X · post", caps["x"]), ("X · reply with the link", caps["x_reply"]),
              ("Instagram · caption (carousel: ig-1 to ig-4, in order)", caps["ig"]),
              ("TikTok · caption (video: tiktok.mp4; slides tt-1 to tt-5)", caps["tt"])]
    blocks = [(t, b) for t, b in blocks if b]
    plain = "\n\n".join(f"===== {t} =====\n{b}" for t, b in blocks)
    body = "".join(f'''<h3 style="font:800 13px Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#c2410c;margin:22px 0 6px">{esc(t)}</h3>
      <pre style="white-space:pre-wrap;font:14px/1.45 Menlo,Consolas,monospace;background:#f4f4f5;border:1px solid #e4e4e7;border-radius:10px;padding:12px;margin:0">{esc(b)}</pre>''' for t, b in blocks)
    m = EmailMessage()
    m["Subject"] = ("[TEST] " if TEST else "") + f"Social kit · {d['date']}"
    m["From"] = f"{BRAND} <{sender}>"
    m["To"] = to
    m.set_content(plain)
    m.add_alternative(f'''<!doctype html><html><body style="margin:0;padding:16px;background:#fff;color:#111;font:15px/1.5 Arial,sans-serif">
      <div style="max-width:640px;margin:0 auto"><div style="font:900 20px Arial,sans-serif">HITBY<span style="color:#ea580c">ONE</span> · social kit</div>
      <div style="color:#555">{esc(d["date"])} · {d["games"]} games · everything is in the attached zip, one folder per app.</div>
      <ol style="padding-left:18px;color:#333;margin:12px 0 0">
        <li>Tap the zip, then the share button, then <b>Save to Files</b>.</li>
        <li>In the Files app, tap the zip once: it unzips into a folder.</li>
        <li>In each app's folder, tap <b>Select</b>, pick the pictures (or the video), Share, <b>Save to Photos</b>. Or attach straight from Files where the app allows it.</li>
        <li>Captions are in each folder as a .txt file, and below.</li></ol>{body}
      <p style="color:#777;font-size:12px;margin-top:24px">Only you get this. The public page shows the record, not the kit.</p></div></body></html>''', subtype="html")
    zp = make_zip(d, day_dir, files, caps, reddit)
    m.add_attachment(zp.read_bytes(), maintype="application", subtype="zip", filename=zp.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=120) as srv:
        srv.login(sender, pw)
        srv.send_message(m)
    print("Social: kit emailed to you.")
    return True


def main():
    rows = read_log()
    n = grade(rows)
    if n:
        print(f"Social: graded {n} Pick of the Day result(s).")
    if "--grade" in sys.argv:
        write_log(rows)
        publish_record(rows)
        return

    today = today_ct()
    src = KIT / f"{today}.json"
    if not src.exists() and (KIT / today).is_dir():          # already made earlier today (restored from the cache)
        day_dir = KIT / today
        zips = sorted(day_dir.glob("*.zip"))
        files = [p.name for p in day_dir.iterdir() if p.suffix in (".jpg", ".mp4")]
        write_log(rows)
        publish_record(rows)
        if zips:
            publish_kit(day_dir, files, zips[-1].name)
        print("Social: today's kit was already made -- kept the private kit page up, nothing re-sent.")
        return
    if TEST and not src.exists():
        found = sorted(KIT.glob("20??-??-??.json"))
        if found:
            src, today = found[-1], found[-1].stem
    if not src.exists():
        print(f"Social: no picks saved for {today} (no games, or the newsletter step didn't run) -- graded only.")
        write_log(rows)
        publish_record(rows)
        return
    d = json.loads(src.read_text(encoding="utf-8"))
    if not d.get("legs"):
        print("Social: no Starting 5 today -- nothing to post.")
        return

    rec = record(rows)                       # record BEFORE today's pick (what the Reddit header shows)
    if TEST:
        row, new = lock_pick([dict(r) for r in rows], d)     # a throwaway copy: nothing is written down
        print("Social: TEST -- today's pick is NOT locked and the record isn't touched.")
    else:
        row, new = lock_pick(rows, d)
        if new:
            print("Social: locked today's Pick of the Day (details are in your kit email, not this public log).")
        write_log(rows)
    c = next((x for x in d.get("potd") or [] if row and x["name"] == row["Player"] and x["stat"] == row["Stat"]), None)

    site = site_url()
    for old in NEWS.glob("20??-??-??.json"):     # and the raw pick data next to the newsletter archive
        old.unlink()
    day_dir = KIT / today
    day_dir.mkdir(parents=True, exist_ok=True)
    pages = [("x.jpg", slide_x(d, row, c, rec), 1600, 900),
             ("ig-1.jpg", ig_1(d), 1080, 1350), ("ig-2.jpg", ig_2(d, row, c, rec), 1080, 1350),
             ("ig-3.jpg", ig_3(d), 1080, 1350), ("ig-4.jpg", ig_4(d, rec, rows), 1080, 1350),
             ("tt-1.jpg", tt_1(d), 1080, 1920), ("tt-2.jpg", tt_2(d), 1080, 1920), ("tt-3.jpg", tt_3(d), 1080, 1920),
             ("tt-4.jpg", tt_4(d), 1080, 1920), ("tt-5.jpg", tt_5(d), 1080, 1920)]
    shoot(pages, day_dir)
    files = [p[0] for p in pages if p[1]]
    try:
        if make_video([day_dir / f for f in files if f.startswith("tt-")], day_dir / "tiktok.mp4"):
            files.append("tiktok.mp4")
    except Exception as e:
        print("Social: video failed:", repr(e))
    caps = captions(d, row, rec, site)
    reddit = reddit_comment(row, c, rec) if row else ""
    zp = make_zip(d, day_dir, files, caps, reddit)
    (day_dir / "index.html").write_text(day_page(d, files, caps, reddit, row, rec, site, zp.name), encoding="utf-8")
    if not TEST:
        publish_record(rows)
    print(f"Social: saved social_kit/{today}/ ({len(files)} files, private)")
    publish_kit(day_dir, files, zp.name)
    if os.environ.get("SOCIAL_DRY") != "1":
        try:
            email_kit(d, day_dir, files, caps, reddit)
        except Exception as e:
            print("Social: couldn't email the kit:", repr(e))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Social failed:", repr(e))
        sys.exit(1)
