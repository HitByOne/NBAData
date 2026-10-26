"""
Hardwood Ledger -- the daily Power 5 newsletter.

Runs after the afternoon odds pull. It opens the site the same way a visitor would (a headless browser on the
local site/ folder), takes a picture of tonight's Power 5 court, reads the picks, last night's grades and the
season record straight from the page, and emails it to your list through Gmail.

  * Only sends on days with games, and only once a day (site/newsletter/last_sent.txt remembers the date).
    Set NEWSLETTER_FORCE=1 to send again anyway.
  * Every issue is also saved to the site: site/newsletter/YYYY-MM-DD.html (+ .png), and
    site/newsletter/index.html lists them all. The email's "view in browser" link points there.

Settings (GitHub repository secrets, never in files):
  EMAIL_FROM      the Gmail address it sends from
  EMAIL_PASSWORD  a Gmail *app password* for that account (not your normal password)
  EMAIL_TO        who gets it: addresses separated by commas. Everyone is BCC'd, so friends don't see each other.

Run it yourself with NEWSLETTER_DRY=1 to build the issue without sending.
"""
import datetime as dt
import html
import json
import os
import smtplib
import socket
import sys
import threading
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

# colors match the Tonight layout's dark theme
BG, CARD, LINE, TXT, DIM = "#0b0d12", "#141821", "#262c38", "#eef1f6", "#8b93a3"
ORANGE, PINK, GREEN, RED = "#ff7a1a", "#ff3d8b", "#32d583", "#f97066"
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
                page.wait_for_timeout(600)
                page.evaluate("document.querySelectorAll('.p5-card.sel').forEach(e => e.classList.remove('sel'))")
                el = page.query_selector(".p5-court")
                if el:
                    png = el.screenshot()
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


def pill(text, color):
    return (f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;font:700 11px {FONT};'
            f'letter-spacing:.04em;color:{color};background:{color}22;border:1px solid {color}55;">{esc(text)}</span>')


def pos_chip(p):
    return (f'<span style="display:inline-block;min-width:26px;text-align:center;padding:3px 5px;border-radius:6px;'
            f'font:800 11px {FONT};color:{TXT};background:{LINE};">{esc(p)}</span>')


def section(title, body):
    return f'''<tr><td style="padding:0 24px 18px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CARD};border:1px solid {LINE};border-radius:14px;">
    <tr><td style="padding:16px 18px 6px;font:800 12px {FONT};letter-spacing:.12em;text-transform:uppercase;color:{ORANGE};">{title}</td></tr>
    <tr><td style="padding:0 18px 14px;">{body}</td></tr>
  </table></td></tr>'''


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


def top_pick(legs):
    """The leg the model likes most tonight (highest score across the five)."""
    return max(legs, key=lambda l: l.get("score") or 0)


def build_email(d, img_src, view_url, site):
    mode = d["mode"]
    legs = d["legs"]
    # the slip
    slip_rows = "".join(f'''<tr>
      <td style="padding:8px 0;border-top:1px solid {LINE};width:36px;">{pos_chip(l['pos'])}</td>
      <td style="padding:8px 6px;border-top:1px solid {LINE};font:600 14px {FONT};color:{TXT};">{esc(l['name'])}
        <div style="font:500 12px {FONT};color:{DIM};">{esc(l['off'])} {'vs' if l['home'] else '@'} {esc(l['def'])} &middot; {esc(l['time'])}</div></td>
      <td align="right" style="padding:8px 0;border-top:1px solid {LINE};font:800 14px {FONT};color:{TXT};white-space:nowrap;">{esc(l['pick'])}
        <div style="font:500 12px {FONT};color:{DIM};">{esc(l['sub'])}</div></td></tr>''' for l in legs)
    pay = (f'''<tr><td colspan="3" style="padding:12px 0 2px;border-top:1px solid {LINE};font:600 13px {FONT};color:{DIM};">
        All five together <span style="float:right;font:800 22px {FONT};color:{GREEN};">{esc(d['pay'])}</span></td></tr>''' if d.get("pay") else
           f'<tr><td colspan="3" style="padding:12px 0 2px;border-top:1px solid {LINE};font:500 12px {FONT};color:{DIM};">'
           + ("Matchup boost picks aren't tied to a book line." if mode == "boost" else "Payout shows once every leg has a price.") + "</td></tr>")
    slip = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{slip_rows}{pay}</table>'

    top = top_pick(legs)
    why = "".join(f'<li style="margin:0 0 6px;">{w}</li>' for w in why_lines(top, mode))
    why_html = (f'<div style="font:800 18px {FONT};color:{TXT};margin:2px 0 8px;">{esc(top["name"])} '
                f'<span style="color:{PINK};">{esc(top["pick"])}</span></div>'
                f'<ul style="margin:0;padding:0 0 0 18px;font:500 14px/1.5 {FONT};color:{TXT};">{why}</ul>')

    swaps = "".join(f'''<tr><td style="padding:7px 0;border-top:1px solid {LINE};width:36px;">{pos_chip(s['pos'])}</td>
      <td style="padding:7px 6px;border-top:1px solid {LINE};font:600 13px {FONT};color:{TXT};">{esc(s['name'])}
        <span style="color:{DIM};font-weight:500;"> &middot; {esc(s['off'])} {'vs' if s['home'] else '@'} {esc(s['def'])}</span></td>
      <td align="right" style="padding:7px 0;border-top:1px solid {LINE};font:700 13px {FONT};color:{TXT};white-space:nowrap;">{esc(s['pick'])}</td></tr>''' for s in d.get("swaps", []))
    swaps_html = (f'<div style="font:500 13px {FONT};color:{DIM};margin:0 0 6px;">Next best at each spot if you want to change one leg.</div>'
                  f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{swaps}</table>') if swaps else ""

    last = d.get("last")
    last_html = ""
    if last:
        rc = {"HIT": GREEN, "MISS": RED, "VOID": DIM}
        rows = "".join(f'''<tr><td style="padding:7px 0;border-top:1px solid {LINE};width:36px;">{pos_chip(l['pos'])}</td>
          <td style="padding:7px 6px;border-top:1px solid {LINE};font:600 13px {FONT};color:{TXT};">{esc(l['name'])}
            <div style="font:500 12px {FONT};color:{DIM};">{esc(l['pick'])}</div></td>
          <td align="right" style="padding:7px 6px;border-top:1px solid {LINE};font:800 14px {FONT};color:{TXT};">{fmt(l['actual']) if l['actual'] is not None else 'DNP'}</td>
          <td align="right" style="padding:7px 0;border-top:1px solid {LINE};width:52px;">{pill(l['result'], rc.get(l['result'], DIM))}</td></tr>''' for l in last["legs"])
        head = (f'<div style="font:700 14px {FONT};color:{TXT};margin:0 0 6px;">{esc(last["date"])} &middot; {last["hits"]}/{last["graded"]} hit '
                + (pill("ALL FIVE CASHED", GREEN) if last["cashed"] else "") + "</div>")
        last_html = head + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'

    r = d.get("record") or {}
    tiles = ""
    if r.get("nights"):
        tile = lambda big, small: (f'<td width="33%" align="center" style="padding:10px 4px;background:{BG};border:1px solid {LINE};border-radius:10px;">'
                                   f'<div style="font:800 24px {FONT};color:{TXT};">{big}</div>'
                                   f'<div style="font:500 11px {FONT};color:{DIM};">{small}</div></td>')
        tiles = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="6"><tr>'
                 + tile(pct(r["hits"], r["legs"]), f"legs hit &middot; {r['hits']} of {r['legs']}")
                 + tile(f"{r['cashed']}/{r['nights']}", "nights all five hit")
                 + tile(pct(r["l7hits"], r["l7legs"]), "legs hit, last 7 nights")
                 + "</tr></table>")

    img = (f'<tr><td style="padding:0 24px 18px;"><img src="{img_src}" width="592" alt="Tonight\'s Power 5 on the court" '
           f'style="display:block;width:100%;max-width:592px;height:auto;border-radius:14px;border:1px solid {LINE};"></td></tr>') if img_src else ""
    cta = (f'<tr><td align="center" style="padding:4px 24px 22px;"><a href="{site}?layout=tonight" '
           f'style="display:inline-block;padding:12px 22px;border-radius:999px;background:{ORANGE};background-image:linear-gradient(90deg,{ORANGE},{PINK});'
           f'font:800 14px {FONT};color:#fff;text-decoration:none;">Open Hardwood Ledger &rarr;</a></td></tr>') if site else ""
    view = (f'<a href="{view_url}" style="color:{DIM};">View in browser</a>') if view_url else ""

    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Hardwood Ledger &middot; Power 5 &middot; {esc(d['date'])}</title></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center" style="padding:18px 8px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;">
  <tr><td style="padding:0 24px 6px;font:500 11px {FONT};color:{DIM};" align="right">{view}</td></tr>
  <tr><td style="padding:6px 24px 4px;font:900 30px {FONT};letter-spacing:.02em;color:{TXT};">HARDWOOD <span style="color:{ORANGE};">LEDGER</span></td></tr>
  <tr><td style="padding:0 24px 18px;font:600 13px {FONT};color:{DIM};">{esc(d['date'])} &middot; {d['games']} game{'s' if d['games'] != 1 else ''} &middot; Power 5 ({esc(d['modeName'])})</td></tr>
  {img}
  {section("Tonight's slip", slip)}
  {section("Why " + esc(last_name(top['name'])), why_html)}
  {section("Swap options", swaps_html) if swaps_html else ""}
  {section("Last night", last_html) if last_html else ""}
  {section("Season record", tiles) if tiles else ""}
  {cta}
  <tr><td style="padding:0 24px 24px;font:500 11px/1.5 {FONT};color:{DIM};" align="center">
    For entertainment. Picks come from a model, not a guarantee. 21+. Gambling problem? Call 1-800-GAMBLER.<br>
    You're getting this because you're on the Hardwood Ledger friends list. Reply "stop" and you'll be taken off.</td></tr>
</table></td></tr></table></body></html>'''


def send(msg_html, png, date_label, subject):
    sender = os.environ.get("EMAIL_FROM", "").strip()
    pw = os.environ.get("EMAIL_PASSWORD", "").replace(" ", "").strip()
    to = [a.strip() for a in os.environ.get("EMAIL_TO", "").replace(";", ",").split(",") if a.strip()]
    if not (sender and pw and to):
        print("Newsletter: EMAIL_FROM / EMAIL_PASSWORD / EMAIL_TO aren't all set -- built the issue but didn't send.")
        return False
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = f"Hardwood Ledger <{sender}>"
    m["To"] = sender            # everyone else is BCC'd
    m["Bcc"] = ", ".join(to)
    m.set_content(f"Tonight's Power 5 for {date_label}. Open this email in an app that shows HTML to see the picks.")
    if png:
        cid = make_msgid(domain="hardwoodledger")
        m.add_alternative(msg_html.replace("cid:COURT", "cid:" + cid[1:-1]), subtype="html")
        m.get_payload()[1].add_related(png, maintype="image", subtype="png", cid=cid, filename="power5.png")
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
<title>Hardwood Ledger &middot; Newsletter</title></head><body style="margin:0;background:{BG};color:{TXT};font:500 15px {FONT};">
<div style="max-width:640px;margin:0 auto;padding:28px 20px;"><h1 style="font:900 26px {FONT};margin:0 0 4px;">HARDWOOD <span style="color:{ORANGE};">LEDGER</span></h1>
<p style="color:{DIM};margin:0 0 18px;">Every Power 5 newsletter.</p><ul style="line-height:2;padding-left:18px;">{rows}</ul>
<p><a href="../?layout=tonight" style="color:{ORANGE};">Back to the app</a></p></div></body></html>''', encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    if not FORCE and MARKER.exists() and MARKER.read_text().strip() == today:
        print(f"Newsletter: already sent for {today}. Set NEWSLETTER_FORCE=1 to send again.")
        return

    data, png = read_page()
    if not data.get("games") or not data.get("legs"):
        print("Newsletter: no games / no Power 5 tonight -- nothing to send.")
        return
    day = data.get("iso") or today
    if not FORCE and day != today:
        print(f"Newsletter: the next slate is {day}, not today ({today}) -- skipping.")
        return

    site = site_url()
    view_url = f"{site}newsletter/{day}.html" if site else ""
    # archive copy: image as a file next to it
    if png:
        (OUT / f"{day}.png").write_bytes(png)
    (OUT / f"{day}.html").write_text(build_email(data, f"{day}.png" if png else "", "", site), encoding="utf-8")
    (OUT / f"{day}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    write_index()
    print(f"Newsletter: saved site/newsletter/{day}.html")

    top = top_pick(data["legs"])
    subject = f"Power 5 · {data['date']} · {top['name']} {top['pick']}"
    if DRY:
        print("Newsletter: dry run, not sending. Subject would be:", subject)
        return
    email_html = build_email(data, "cid:COURT" if png else "", view_url, site)
    if send(email_html, png, data["date"], subject):
        MARKER.write_text(today)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # never break the rest of the daily run
        print("Newsletter failed:", repr(e))
        sys.exit(1)
