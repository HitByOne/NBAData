"""
HitByOne Hardwood Ledger -- the daily newsletter (Pick of the Day + the Starting 5).

Runs after the afternoon odds pull. It opens the site the same way a visitor would (a headless browser on the
local site/ folder), takes a picture of tonight's Starting 5 court, reads the picks, last night's grades and the
season record straight from the page, and emails it to your list through Gmail.

  * Only sends on days with games, and only once a day (site/newsletter/last_sent.txt remembers the date).
    Set NEWSLETTER_FORCE=1 to send again anyway.
  * Every issue is also saved to the site: site/newsletter/YYYY-MM-DD.html (+ .png), and
    site/newsletter/index.html lists them all. The email's "view in browser" link points there.

Who gets it: the newsletter_subscribers table in Supabase (run supabase_newsletter.sql once). Add, remove or
pause people in the Supabase dashboard -> Table Editor; only rows with active = true get the email. Everyone is
BCC'd, so friends don't see each other's addresses.

Settings (GitHub repository secrets, never in files):
  EMAIL_FROM            the Gmail address it sends from
  EMAIL_PASSWORD        a Gmail *app password* for that account (not your normal password)
  SUPABASE_SERVICE_KEY  Supabase -> Project Settings -> API Keys -> the *secret* key (sb_secret_... or the
                        older service_role key). It's the only thing that can read the list.
  EMAIL_TO              optional backup list (commas) used only when Supabase isn't set up.
The Supabase project address is read from site/config.js (it's public anyway); SUPABASE_URL overrides it.

Run it yourself with NEWSLETTER_DRY=1 to build the issue without sending.
"""
import datetime as dt
import html
import json
import os
import re
import smtplib
import socket
import sys
import threading
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
OUT = SITE / "newsletter"
MARKER = OUT / "last_sent.txt"

FORCE = os.environ.get("NEWSLETTER_FORCE") == "1"
DRY = os.environ.get("NEWSLETTER_DRY") == "1"
# TEST: send only to you (TEST_TO, else KIT_TO, else EMAIL_FROM), never the list; no archive copy, no "sent" marker.
TEST = os.environ.get("NEWSLETTER_TEST") == "1"

# colors match the Tonight layout's dark theme
BG, CARD, LINE, TXT, DIM = "#0b0d12", "#141821", "#262c38", "#eef1f6", "#8b93a3"
ORANGE, PINK, GREEN, RED = "#ff7a1a", "#ff3d8b", "#32d583", "#f97066"
BRAND = "HitByOne"                      # the name people follow (From line, footer)
PRODUCT = "Hardwood Ledger"              # the NBA app under it
SOCIALS = [("X", "@HitByOne", "https://x.com/HitByOne"),
           ("IG", "@hitbyone", "https://www.instagram.com/hitbyone/"),
           ("TT", "@hitby1ne", "https://www.tiktok.com/@hitby1ne")]
FONT = "Inter,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def site_url():
    """https://owner.github.io/repo/ from the Actions environment (or SITE_URL if you set one)."""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def serve_site():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = partial(Quiet, directory=str(SITE))
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def read_page():
    """Open the site headless, return (data from window.hwNewsletter(), PNG bytes of the court or None)."""
    from playwright.sync_api import sync_playwright

    srv, port = serve_site()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1300, "height": 1100}, device_scale_factor=2, timezone_id="America/Chicago")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # Optional for testing offline: serve the two CDN scripts from a local folder.
            libs = os.environ.get("HW_CDN_DIR")
            if libs:
                def cdn(route):
                    u = route.request.url
                    f = "chart.umd.js" if "Chart" in u else "papaparse.min.js"
                    route.fulfill(body=(Path(libs) / f).read_bytes(), content_type="application/javascript")
                page.route("https://cdnjs.cloudflare.com/**", cdn)
                page.route("**/fonts.g*/**", lambda r: r.abort())
            page.goto(f"http://127.0.0.1:{port}/index.html?layout=tonight&theme=dark", wait_until="load")
            page.wait_for_function("typeof window.hwNewsletter === 'function' && typeof DATA !== 'undefined' && DATA.playerGame && DATA.playerGame.length > 0", timeout=60000)
            page.wait_for_timeout(1500)
            data = page.evaluate("window.hwNewsletter()")
            png = None
            if data.get("legs"):
                # the court graphic is drawn just for the email (sized to sit beside the Power 5 panel)
                page.set_content(court_html(data["legs"]), wait_until="load")
                page.wait_for_timeout(800)
                png = page.query_selector("#court").screenshot(omit_background=True)
            if errors:
                print("page errors:", errors[:5])
            browser.close()
    finally:
        srv.shutdown()
    return data, png


def esc(x):
    return html.escape(str(x if x is not None else ""))


def fmt(v, d=1):
    if v is None:
        return "--"
    v = float(v)
    return str(int(v)) if v == int(v) else f"{v:.{d}f}"


def pct(h, g):
    return f"{round(h / g * 100)}%" if g else "--"


SUFFIXES = ("jr", "sr", "ii", "iii", "iv")


def short_name(name):
    """'Michael Porter Jr.' -> 'Porter Jr.', 'Nikola Jokic' -> 'Jokic' (for the small chips)."""
    parts = name.split()
    if len(parts) >= 2 and parts[-1].rstrip(".").lower() in SUFFIXES:
        return " ".join(parts[-2:])
    return parts[-1] if parts else name


def card_name(name):
    """Full name, or 'M. Porter Jr.' when it's too long for a court card."""
    if len(name) <= 15:
        return name
    parts = name.split()
    return (parts[0][0] + ". " + " ".join(parts[1:])) if len(parts) > 1 else name


def compact(pick):
    """'OVER 23.5 PTS' -> 'o23.5 PTS' (Matchup boost picks like 'PTS 21.3' stay as they are)."""
    pick = str(pick or "")
    return "o" + pick[5:] if pick.startswith("OVER ") else pick


COURT_SPOTS = {"PG": (104, 12), "SF": (8, 108), "SG": (200, 108), "PF": (24, 232), "C": (184, 232)}


def court_html(legs):
    """The Power 5 on a half court, 336 x 372, photographed for the email."""
    cards = []
    for l in legs:
        x, y = COURT_SPOTS.get(l["pos"], (0, 0))
        sub = (f"{l['hits']}/{l['n']} over" if l.get("hits") is not None and l.get("n") else esc(l.get("sub", "")))
        if l.get("defRank"):
            sub += f" &middot; {esc(l['def'])} #{l['defRank']}"
        cards.append(f'''<div class="pc" style="left:{x}px;top:{y}px">
  <div class="t"><span class="pos">{esc(l['pos'])}</span>{esc(card_name(l['name']))}</div>
  <div class="mt">{esc(l['off'])} {'vs' if l.get('home') else '@'} {esc(l['def'])} &middot; {esc(l.get('time') or '')}</div>
  <div class="big">{esc(compact(l['pick']))}</div><div class="mt">{sub}</div></div>''')
    return f'''<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700&family=Space+Grotesk:wght@700&display=swap" rel="stylesheet">
<style>
body{{margin:0;background:{BG};}}
#court{{position:relative;width:336px;height:372px;border-radius:16px;background:#0f1119;overflow:hidden;font-family:Inter,Arial,sans-serif;color:{TXT}}}
.pc{{position:absolute;width:128px;box-sizing:border-box;padding:8px 9px;border-radius:12px;background:#161826;box-shadow:0 6px 18px rgba(0,0,0,.45),inset 0 0 0 1px rgba(255,255,255,.07);display:flex;flex-direction:column;gap:1px}}
.t{{display:flex;align-items:center;gap:5px;font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden}}
.pos{{flex:none;width:19px;height:19px;border-radius:10px;box-shadow:inset 0 0 0 1.5px {GREEN};display:flex;align-items:center;justify-content:center;font-size:8.5px;font-weight:700}}
.mt{{font-size:9.5px;color:rgba(242,244,250,.7);white-space:nowrap;overflow:hidden}}
.big{{font-family:'Space Grotesk',Inter,sans-serif;font-size:15px;font-weight:700;color:{GREEN}}}
</style></head><body><div id="court">
<svg viewBox="0 0 500 470" preserveAspectRatio="none" width="336" height="372" style="position:absolute;left:0;top:0">
 <g stroke="rgba(255,255,255,0.12)" stroke-width="2" fill="none">
  <rect x="1" y="1" width="498" height="468" rx="18"/><rect x="170" y="280" width="160" height="189" fill="rgba(255,122,47,0.05)"/>
  <circle cx="250" cy="280" r="60"/><path d="M 30 469 L 30 330 A 237.5 237.5 0 0 1 470 330 L 470 469"/><path d="M 190 1 A 60 60 0 0 0 310 1"/></g>
 <circle cx="250" cy="417.5" r="8" fill="none" stroke="{ORANGE}" stroke-width="2.5"/></svg>
{''.join(cards)}</div></body></html>'''


def pill(text, color):
    return (f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;font:700 11px {FONT};'
            f'letter-spacing:.04em;color:{color};background:{color}22;border:1px solid {color}55;">{esc(text)}</span>')


def pos_chip(p):
    return (f'<span style="display:inline-block;min-width:26px;text-align:center;padding:3px 5px;border-radius:6px;'
            f'font:800 11px {FONT};color:{TXT};background:{LINE};">{esc(p)}</span>')


def why_lines(leg, mode):
    """Plain-English reasons for the top pick (the first leg with the biggest gap)."""
    out = []
    n = leg.get("n") or 0
    if mode == "boost":
        out.append(f"He averages <b>{fmt(leg['avg'])}</b> {esc(leg['stat'] if leg['stat'] in ('PRA', '3PM') else leg['stat'].lower())}; tonight's projection is <b>{fmt(leg['proj'])}</b>.")
    else:
        out.append(f"Projected for <b>{fmt(leg['proj'])}</b> {esc(leg['stat'] if leg['stat'] in ('PRA', '3PM') else leg['stat'].lower())} against a line of <b>{fmt(leg['line'])}</b>.")
        if leg.get("hits") is not None and n:
            out.append(f"He's gone over that line in <b>{leg['hits']} of his last {n}</b>.")
    if leg.get("defRank"):
        out.append(f"{esc(leg['def'])} rank <b>#{leg['defRank']}</b> softest against {esc(leg['pos'])}s"
                   + (f", giving up {fmt(leg['defVal'])} a game." if leg.get("defVal") is not None else "."))
    out.append(f"{'Home' if leg.get('home') else 'On the road'} tonight, tip {esc(leg.get('time') or 'TBD')}.")
    return out


def last_name(name):
    parts = [p for p in name.split() if p.rstrip('.').lower() not in ("jr", "sr", "ii", "iii", "iv")]
    return parts[-1] if parts else name


STAT_SHORT = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3PM": "3PM", "Pts+Reb+Ast": "PRA"}


def potd_for(d):
    """Today's Pick of the Day for the header box (the same one the social kit and the record use)."""
    import csv
    day = d.get("iso")
    log = ROOT / "records" / "nba_2027_potd_log.csv"
    cand = {(c["name"], c["stat"]): c for c in d.get("potd") or []}
    if log.exists():
        with log.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("Date") == day:
                    c = cand.get((r["Player"], r["Stat"]), {})
                    return {"name": r["Player"], "off": r["Team"], "def": r["Opponent"], "home": r["Home/Away"] == "Home",
                            "time": c.get("time", ""), "line": r["Line"], "short": STAT_SHORT.get(r["Stat"], r["Stat"]),
                            "american": r["American"], "hits": c.get("hits"), "n": c.get("n")}
    if d.get("potd"):
        c = d["potd"][0]
        return {"name": c["name"], "off": c["off"], "def": c["def"], "home": c["home"], "time": c.get("time", ""),
                "line": fmt(c["line"]), "short": c.get("short") or STAT_SHORT.get(c["stat"], c["stat"]),
                "american": c["american"], "hits": c.get("hits"), "n": c.get("n")}
    return None


def top_pick(legs):
    """The leg the model likes most tonight (highest score across the five)."""
    return max(legs, key=lambda l: l.get("score") or 0)


def box(inner, extra=""):
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CARD};border:1px solid {LINE};'
            f'border-radius:14px;{extra}"><tr><td style="padding:14px 16px;">{inner}</td></tr></table>')


def lab(text, color=ORANGE, extra=""):
    return f'<div style="font:800 11px {FONT};letter-spacing:.1em;text-transform:uppercase;color:{color};margin:0 0 8px;{extra}">{text}</div>'


RES_COLOR = {"HIT": GREEN, "MISS": RED, "VOID": DIM}


def build_email(d, img_src, view_url, site):
    """Layout D: Pick of the Day, schedule, court + Starting 5 panel, Top 5 by stat rows, last night | season, footer."""
    mode, legs = d["mode"], d["legs"]
    top = top_pick(legs)
    num = f"font-family:'Space Grotesk',{FONT};font-weight:700;"

    # tonight's schedule (wraps on big nights and on phones)
    sched = "".join(f'''<div class="g" style="display:inline-block;vertical-align:top;width:72px;margin:0 6px 6px 0;padding:7px 8px;background:{CARD};border:1px solid {LINE};border-radius:10px;">
      <div style="font:700 11.5px {FONT};color:{TXT};white-space:nowrap;">{esc(g['away'])} @ {esc(g['home'])}</div>
      <div style="font:500 11px {FONT};color:{DIM};">{esc(g['time'] or 'TBD')}</div></div>''' for g in d.get("schedule") or [])

    # the Starting 5 panel beside the court
    pay = (f'<div style="{num}font-size:30px;line-height:1;color:{GREEN};">{esc(d["pay"])}</div>'
           f'<div style="font:500 11px {FONT};color:{DIM};margin:4px 0 14px;">all five together, best prices</div>') if d.get("pay") else (
           f'<div style="font:500 12px {FONT};color:{DIM};margin:0 0 14px;">'
           + ("Matchup boost picks aren't tied to a book line." if mode == "boost" else "Payout shows once every leg has a price.") + "</div>")
    why = "".join(f'<div style="margin:0 0 5px;">{w}</div>' for w in why_lines(top, mode)[:3])
    swap_rows = "".join(f'''<tr><td style="padding:3px 0;font:500 11.5px {FONT};color:{TXT};white-space:nowrap;"><b style="color:{DIM};">{esc(s['pos'])}</b> {esc(short_name(s['name']))}</td>
      <td align="right" style="padding:3px 0 3px 6px;{num}font-size:11.5px;color:{TXT};white-space:nowrap;">{esc(compact(s['pick']))}</td></tr>''' for s in d.get("swaps") or [])
    panel = (lab("Tonight's Starting 5") + pay
             + lab("Why " + esc(last_name(top["name"])))
             + f'<div style="font:500 12px/1.45 {FONT};color:{TXT};">{why}</div>'
             + (lab("Swaps", extra="margin-top:12px;") + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{swap_rows}</table>' if swap_rows else ""))
    court = (f'<img class="court" src="{img_src}" width="336" height="372" alt="Tonight\'s Starting 5 on the court" '
             f'style="display:block;width:336px;height:372px;border-radius:16px;border:0;">') if img_src else ""
    top_row = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      {f'<td class="stack stack-gap" width="336" valign="top" style="width:336px;padding:0 12px 0 0;">{court}</td>' if court else ''}
      <td class="stack" valign="top" style="background:{CARD};border:1px solid {LINE};border-radius:16px;padding:14px 16px;">{panel}</td>
    </tr></table>'''

    # Top 5 by stat: one row per stat, five chips side by side, best first
    stat_rows = []
    for blk in d.get("byStat") or []:
        chips = ""
        for i, x in enumerate(blk["list"][:5]):
            ring = ORANGE if i == 0 else LINE
            opp = f"{'vs' if x.get('home') else '@'} {esc(x['def'])}"
            sub = f"{opp} &middot; {x['hits']}/{x['n']}" if blk["how"] == "best" and x.get("hits") is not None else opp
            # 'o13.5' when it's ranked against the book line; the projection when no line was up yet (matchup boost)
            val = compact(x["pick"]).split(" ")[0] if blk["how"] == "best" else fmt(x.get("proj"))
            chips += f'''<td class="chip" width="20%" valign="top" style="width:20%;padding:7px 8px;background:{CARD};border:1px solid {ring};border-radius:11px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;"><tr>
                <td class="cn" style="font:700 11.5px {FONT};color:{TXT};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{esc(short_name(x['name']))}</td>
                <td class="ct" width="24" align="right" style="width:24px;padding-left:2px;font:800 9px {FONT};color:{ORANGE};white-space:nowrap;">{esc(x['off'])}</td></tr></table>
              <div style="{num}font-size:14px;color:{GREEN};">{esc(val)}</div>
              <div class="cs" style="font:500 10px {FONT};color:{DIM};white-space:nowrap;">{sub}</div></td>'''
        chips += "<td></td>" * (5 - len(blk["list"][:5]))
        note = "" if blk["how"] == "best" else f'<div style="font:500 9px {FONT};color:{DIM};letter-spacing:0;">projected<br>no lines yet</div>'
        stat_rows.append(f'''<tr><td class="sl" width="54" valign="middle" style="width:54px;padding:0 4px 0 0;font:800 12px {FONT};letter-spacing:.06em;color:{PINK};">{esc(blk['short'])}{note}</td>
          <td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="chips" style="border-collapse:separate;border-spacing:5px 0;table-layout:fixed;"><tr>{chips}</tr></table></td></tr>
          <tr><td colspan="2" style="height:8px;line-height:8px;font-size:0;">&nbsp;</td></tr>''')
    stats = (f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:4px;"><tr>
        <td>{lab("Top 5 by stat", extra="margin:0;")}</td>
        <td align="right" style="font:500 11px {FONT};color:{DIM};">best first &middot; times over in his last 5</td></tr></table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">{"".join(stat_rows)}</table>''') if stat_rows else ""

    # bottom: last night | season
    last = d.get("last")
    last_html = ""
    if last:
        rows = "".join(f'''<tr>
          <td style="padding:6px 0;border-top:1px solid {LINE};width:30px;">{pos_chip(l['pos'])}</td>
          <td style="padding:6px 4px;border-top:1px solid {LINE};font:600 12.5px {FONT};color:{TXT};white-space:nowrap;">{esc(short_name(l['name']))}
            <span style="font:500 11px {FONT};color:{DIM};">{esc(compact(l['pick']))}</span></td>
          <td align="right" style="padding:6px 6px;border-top:1px solid {LINE};{num}font-size:13px;color:{TXT};">{fmt(l['actual']) if l['actual'] is not None else 'DNP'}</td>
          <td align="right" style="padding:6px 0;border-top:1px solid {LINE};width:44px;">{pill(l['result'], RES_COLOR.get(l['result'], DIM))}</td></tr>''' for l in last["legs"])
        last_html = (lab(f"Last night's Starting 5 &middot; {last['hits']} of {last['graded']} hit" + (" &middot; all five cashed" if last["cashed"] else ""))
                     + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>')
    r = d.get("record") or {}
    line = lambda k, v: (f'<tr><td style="padding:5px 0;font:500 12px {FONT};color:{DIM};">{k}</td>'
                         f'<td align="right" style="padding:5px 0;{num}font-size:20px;color:{TXT};">{v}</td></tr>')
    season = (lab("Starting 5 season")
              + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
              + (line("Legs hit", pct(r.get("hits", 0), r.get("legs", 0))) + line("All five hit", f"{r.get('cashed', 0)} / {r.get('nights', 0)}")
                 + line("Last 7 nights", pct(r.get("l7hits", 0), r.get("l7legs", 0))) if r.get("nights") else
                 f'<tr><td style="font:500 12px {FONT};color:{DIM};">The record starts after the first graded night.</td></tr>')
              + "</table>"
              )
    bottom = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      {f'<td class="stack stack-gap" width="56%" valign="top" style="width:56%;padding:0 12px 0 0;">{box(last_html)}</td>' if last_html else ''}
      <td class="stack" valign="top">{box(season)}</td></tr></table>'''

    view = f'<a href="{view_url}" style="color:{DIM};">View in browser</a>' if view_url else ""
    pk = potd_for(d)
    potd_box = ""
    if pk:
        form = f" &middot; over in {pk['hits']} of his last {pk['n']}" if pk.get("hits") is not None and pk.get("n") else ""
        potd_box = f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CARD};border:1.5px solid rgba(255,122,47,.55);border-radius:16px;"><tr>
      <td class="stack" valign="middle" style="padding:16px 18px;">
        <div style="font:800 11px {FONT};letter-spacing:.1em;color:{ORANGE};">PICK OF THE DAY</div>
        <div style="font:900 21px {FONT};color:{TXT};margin-top:6px;">{esc(pk['name'])} <span style="font:800 12px {FONT};color:{ORANGE};">{esc(pk['off'])}</span></div>
        <div style="font:500 12px {FONT};color:{DIM};margin-top:2px;">{esc(pk['off'])} {'vs' if pk['home'] else '@'} {esc(pk['def'])}{(' &middot; ' + esc(pk['time'])) if pk.get('time') else ''}{form}</div></td>
      <td class="stack potd-r" align="right" valign="middle" style="padding:16px 18px;white-space:nowrap;">
        <div style="{num}font-size:30px;line-height:1;color:{GREEN};">o{esc(pk['line'])} {esc(pk['short'])}</div>
        <div style="font:700 13px {FONT};color:{TXT};margin-top:5px;">{esc(pk['american'])} &middot; 1U</div></td></tr></table>
  <div style="height:12px;line-height:12px;font-size:0;">&nbsp;</div>'''
    soc = "".join(f'''<a href="{u}" style="display:inline-block;margin:0 4px 8px;padding:9px 14px;border-radius:999px;background:{CARD};border:1px solid {LINE};font:700 13px {FONT};color:{TXT};text-decoration:none;white-space:nowrap;"><span style="display:inline-block;padding:2px 5px;border-radius:5px;background:{ORANGE};color:#07080F;font:900 10px {FONT};margin-right:6px;">{b}</span>{h}</a>''' for b, h, u in SOCIALS)
    app_btn = (f'<a href="{site}?layout=tonight" style="display:inline-block;margin:6px 0 12px;padding:12px 22px;border-radius:999px;background:{ORANGE};color:#07080F;font:800 14px {FONT};text-decoration:none;">Open {PRODUCT}</a>'
               f'<div style="font:500 11px {FONT};color:{DIM};margin-bottom:10px;">{site.replace("https://", "").rstrip("/")}</div>') if site else ""
    record_line = f'Every Pick of the Day, graded since day one: <a href="{site}record/" style="color:{ORANGE};">{site.replace("https://", "")}record</a><br>' if site else ""
    footer = f'''<div style="height:22px;line-height:22px;font-size:0;">&nbsp;</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid {LINE};"><tr><td align="center" style="padding:20px 0 0;">
    <div style="font:800 14px {FONT};color:{TXT};margin-bottom:10px;">Follow {BRAND}</div>
    <div>{soc}</div>
    {app_btn}
    <div style="font:500 11px/1.6 {FONT};color:{DIM};">{record_line}Reply to this email to hit me up.</div>
    <div style="font:500 10.5px/1.55 {FONT};color:{DIM};margin-top:12px;">For entertainment. Picks come from a model, not a guarantee. 21+. Gambling problem? Call 1-800-GAMBLER.<br>
      You're on the {BRAND} {PRODUCT} friends list. Reply "stop" to come off it.</div></td></tr></table>'''
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>{BRAND} {PRODUCT} &middot; {esc(d['date'])}</title>
<style>
@media (max-width:620px){{
  .wrap{{padding:18px 14px !important}}
  .stack{{display:block !important;width:100% !important;box-sizing:border-box}}
  .stack-gap{{padding:0 0 12px 0 !important}}
  .court{{width:100% !important;max-width:336px !important;height:auto !important;margin:0 auto}}
  .chip{{padding:6px 4px !important}}
  .sl{{width:32px !important;font-size:10px !important}}
  .g{{width:72px !important}}
  .cn{{font-size:10px !important;letter-spacing:-.2px}}
  .ct{{display:block !important;width:auto !important;text-align:left !important;padding-left:0 !important;font-size:8.5px !important}}
  .cn{{display:block !important}}
  .cs{{font-size:8.5px !important;letter-spacing:-.2px}}
  .chips{{border-spacing:3px 0 !important}}
  .mh{{font-size:24px !important;white-space:nowrap}}
  .potd-r{{text-align:left !important;padding-top:0 !important}}
}}
</style></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;"><tr><td class="wrap" style="padding:24px 24px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td class="mh" style="font:900 28px {FONT};letter-spacing:-.01em;line-height:1;color:{TXT};">HITBY<span style="color:{ORANGE};">ONE</span>
      <div style="font:800 11px {FONT};letter-spacing:.26em;color:rgba(242,244,250,.82);margin-top:7px;">HARDWOOD LEDGER</div>
      <div style="font:800 10px {FONT};letter-spacing:.14em;color:{PINK};margin-top:4px;">TONIGHT'S CARD</div></td>
    <td align="right" style="font:600 12px {FONT};color:{DIM};">{esc(d['date'])} &middot; {d['games']} game{'s' if d['games'] != 1 else ''}<br>{view}</td></tr></table>
  <div style="height:14px;line-height:14px;font-size:0;">&nbsp;</div>
  <div style="font-size:0;line-height:0;">{sched}</div>
  <div style="height:10px;line-height:10px;font-size:0;">&nbsp;</div>
  {potd_box}
  {top_row}
  <div style="height:18px;line-height:18px;font-size:0;">&nbsp;</div>
  {stats}
  <div style="height:10px;line-height:10px;font-size:0;">&nbsp;</div>
  {bottom}
  {footer}
</td></tr></table></td></tr></table></body></html>'''


def supabase_url():
    if os.environ.get("SUPABASE_URL"):
        return os.environ["SUPABASE_URL"].rstrip("/")
    cfg = SITE / "config.js"
    if cfg.exists():
        m = re.search(r"supabaseUrl\s*:\s*['\"](https://[^'\"]+)['\"]", cfg.read_text(encoding="utf-8"))
        if m:
            return m.group(1).rstrip("/")
    return ""


def recipients():
    """Active subscribers from Supabase; falls back to the EMAIL_TO secret when Supabase isn't set up."""
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    url = supabase_url()
    if key and url:
        q = urllib.parse.urlencode({"select": "email", "active": "eq.true"})
        headers = {"apikey": key, "Accept": "application/json"}
        if key.startswith("eyJ"):           # the older JWT-style service_role key also goes in Authorization
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(f"{url}/rest/v1/newsletter_subscribers?{q}", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read().decode("utf-8"))
        emails = sorted({(x.get("email") or "").strip().lower() for x in rows if "@" in (x.get("email") or "")})
        print(f"Newsletter: {len(emails)} active subscriber(s) in Supabase.")
        return emails
    print("Newsletter: SUPABASE_SERVICE_KEY not set -- using the EMAIL_TO secret instead.")
    return [a.strip() for a in os.environ.get("EMAIL_TO", "").replace(";", ",").split(",") if a.strip()]


def send(msg_html, png, date_label, subject):
    sender = os.environ.get("EMAIL_FROM", "").strip()
    pw = os.environ.get("EMAIL_PASSWORD", "").replace(" ", "").strip()
    if not (sender and pw):
        print("Newsletter: EMAIL_FROM / EMAIL_PASSWORD aren't set -- built the issue but didn't send.")
        return False
    if TEST:
        me = (os.environ.get("TEST_TO") or os.environ.get("KIT_TO") or sender).strip()
        to = [me]
        print("Newsletter: TEST -- sending to you only, not the list.")
    else:
        to = recipients()
    if not to:
        print("Newsletter: nobody on the list (no active rows) -- built the issue but didn't send.")
        return False
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = f"{BRAND} <{sender}>"
    m["To"] = sender            # everyone else is BCC'd
    m["Bcc"] = ", ".join(to)
    m.set_content(f"{BRAND} {PRODUCT}: tonight's card for {date_label}. Open this email in an app that shows HTML to see the picks.")
    if png:
        cid = make_msgid(domain="hardwoodledger")
        m.add_alternative(msg_html.replace("cid:COURT", "cid:" + cid[1:-1]), subtype="html")
        m.get_payload()[1].add_related(png, maintype="image", subtype="png", cid=cid, filename="starting5.png")
    else:
        m.add_alternative(msg_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(sender, pw)
        s.send_message(m)
    print(f"Newsletter: sent to {len(to)} address(es).")
    return True


def write_index():
    issues = sorted((p for p in OUT.glob("20??-??-??.html")), reverse=True)
    rows = "".join(f'<li><a href="{p.name}">{p.stem}</a></li>' for p in issues)
    (OUT / "index.html").write_text(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{BRAND} {PRODUCT} &middot; Newsletter</title></head><body style="margin:0;background:{BG};color:{TXT};font:500 15px {FONT};">
<div style="max-width:640px;margin:0 auto;padding:28px 20px;"><h1 style="font:900 26px {FONT};margin:0 0 4px;">HITBY<span style="color:{ORANGE};">ONE</span> <span style="font-size:13px;letter-spacing:.2em;color:{DIM};">HARDWOOD LEDGER</span></h1>
<p style="color:{DIM};margin:0 0 18px;">Every newsletter.</p><ul style="line-height:2;padding-left:18px;">{rows}</ul>
<p><a href="../?layout=tonight" style="color:{ORANGE};">Back to the app</a></p></div></body></html>''', encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    if not (FORCE or TEST) and MARKER.exists() and MARKER.read_text().strip() == today:
        print(f"Newsletter: already sent for {today}. Set NEWSLETTER_FORCE=1 to send again.")
        return

    data, png = read_page()
    if not data.get("games") or not data.get("legs"):
        print("Newsletter: no games / no Starting 5 tonight -- nothing to send.")
        return
    day = data.get("iso") or today
    if not (FORCE or TEST) and day != today:
        print(f"Newsletter: the next slate is {day}, not today ({today}) -- skipping.")
        return

    site = site_url()
    view_url = f"{site}newsletter/{day}.html" if site else ""
    # archive copy: image as a file next to it (not for test sends)
    if not TEST:
        if png:
            (OUT / f"{day}.png").write_bytes(png)
        (OUT / f"{day}.html").write_text(build_email(data, f"{day}.png" if png else "", "", site), encoding="utf-8")
    # the full pick data (incl. the Pick of the Day) for 19_social.py -- kept OFF the public site, never committed
    kit = ROOT / "social_kit"
    kit.mkdir(exist_ok=True)
    (kit / f"{day}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    old = OUT / f"{day}.json"
    if old.exists():
        old.unlink()
    if not TEST:
        write_index()
        print(f"Newsletter: saved site/newsletter/{day}.html")

    top = top_pick(data["legs"])
    pk = potd_for(data)
    subject = ("[TEST] " if TEST else "") + (f"Tonight: {pk['name']} o{pk['line']} {pk['short']} ({pk['american']}) + the Starting 5" if pk
                                             else f"Tonight's Starting 5 · {data['date']} · {data['games']} games")
    if DRY:
        print("Newsletter: dry run, not sending. Subject would be:", subject)
        return
    email_html = build_email(data, "cid:COURT" if png else "", view_url, site)
    if send(email_html, png, data["date"], subject) and not TEST:
        MARKER.write_text(today)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # never break the rest of the daily run
        print("Newsletter failed:", repr(e))
        sys.exit(1)
