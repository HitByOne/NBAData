"""
HitByOne Hardwood Ledger -- the evening check (runs about an hour before the first tip).

1. LATE-SCRATCH ALERT. Reads tonight's Pick of the Day (records/nba_2027_potd_log.csv) and tonight's Starting 5
   (records/nba_2027_starting5_log.csv), checks each player against the fresh injury report, and emails YOU (KIT_TO,
   else EMAIL_FROM) when one of them is listed -- Out, Doubtful, Questionable or Day-To-Day -- so you can post an
   update before people bet it. Each player + status is only sent once a night (records/alerts_sent.csv).

2. CLOSING LINE. If this run pulled fresh odds (CLV_ENABLED, see the workflow), it saves the main line for the Pick of
   the Day as it stands now next to the line it was locked at. "Beat the closing line" (the line moved up after we
   took the over) is the best early sign a pick was good, whatever the result. The record page and the app show it.

Run by hand:  python3 20_alerts.py   (ALERT_DRY=1 prints the email instead of sending it)
"""
import csv
import datetime as dt
import html
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "site" / "data"
REC = ROOT / "records"
POTD = REC / "nba_2027_potd_log.csv"
S5 = REC / "nba_2027_starting5_log.csv"
SENT = REC / "alerts_sent.csv"
INJ = DATA / "nba_2027_injury_report.csv"
ODDS = DATA / "nba_2027_prop_odds.csv"
STATUS = DATA / "nba_2027_prop_odds_status.csv"
CT = ZoneInfo("America/Chicago")
WATCH = ("out", "doubtful", "questionable", "day-to-day", "game-time", "gtd", "suspended")


def today():
    return os.environ.get("HW_TODAY") or dt.datetime.now(CT).date().isoformat()


def read(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write(path, rows, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def norm(name):
    return " ".join(str(name or "").lower().replace(".", "").replace("'", "").split())


# ---------------------------------------------------------------- 1. late scratches
def check_injuries(day):
    potd = [r for r in read(POTD) if r.get("Date") == day]
    s5 = [r for r in read(S5) if r.get("Date") == day]
    if not potd and not s5:
        print("Alerts: nothing locked for tonight -- nothing to check.")
        return []
    inj = {}
    for r in read(INJ):
        st = str(r.get("Status") or "").strip()
        if st and any(w in st.lower() for w in WATCH):
            inj[norm(r.get("Player"))] = (st, str(r.get("Injury") or "").strip())
    sent = read(SENT)
    seen = {(r["Date"], norm(r["Player"]), r["Status"]) for r in sent}
    listed = {norm(r["Player"]) for r in potd + s5} & set(inj)
    hits = []
    for r in potd:
        st = inj.get(norm(r["Player"]))
        if st and (day, norm(r["Player"]), st[0]) not in seen:
            hits.append({"kind": "Pick of the Day", "player": r["Player"], "team": r["Team"], "pick": f"o{r['Line']} {r['Stat']} ({r['American']})",
                         "status": st[0], "injury": st[1]})
    for r in s5:
        st = inj.get(norm(r["Player"]))
        if st and (day, norm(r["Player"]), st[0]) not in seen:
            hits.append({"kind": f"Starting 5 ({r['Pos']})", "player": r["Player"], "team": r["Team"], "pick": r["Pick"],
                         "status": st[0], "injury": st[1]})
    if not hits:
        if listed:
            print(f"Alerts: {len(listed)} of tonight's players are on the injury report, already emailed -- no change since.")
        else:
            print(f"Alerts: tonight's {len(potd)} Pick of the Day + {len(s5)} Starting 5 players are all clear on the injury report.")
        return []
    if send_alert(day, hits):
        sent += [{"Date": day, "Player": h["player"], "Status": h["status"]} for h in hits]
        write(SENT, sent, ["Date", "Player", "Status"])
    return hits


def send_alert(day, hits):
    out = [h for h in hits if "out" in h["status"].lower()]
    lead = hits[0]
    if out:
        subject = f"⚠️ Late scratch: {out[0]['player']} OUT ({out[0]['kind']})" + (f" + {len(hits) - 1} more" if len(hits) > 1 else "")
    else:
        subject = f"⚠️ Injury report: {lead['player']} {lead['status']} ({lead['kind']})" + (f" + {len(hits) - 1} more" if len(hits) > 1 else "")
    lines = []
    for h in hits:
        what = ("The Pick of the Day will grade VOID if he doesn't play. Consider posting an update."
                if h["kind"] == "Pick of the Day" else "That leg drops out of the Starting 5 if he sits (VOID). The app's Starting 5 tab has the next-best swap.")
        lines.append(f"{h['kind']}: {h['player']} ({h['team']}) {h['pick']}\n  Status: {h['status']}" + (f" - {h['injury']}" if h["injury"] else "") + f"\n  {what}")
    body = f"Tonight's picks vs the latest injury report ({day}):\n\n" + "\n\n".join(lines) + \
        "\n\nStatuses change right up to tip. Check the team's final injury report about 30 minutes before the game."
    if os.environ.get("ALERT_DRY") == "1":
        print("Alerts (dry run):", subject, "\n" + body)
        return True
    sender = os.environ.get("EMAIL_FROM", "").strip()
    pw = os.environ.get("EMAIL_PASSWORD", "").replace(" ", "").strip()
    to = os.environ.get("KIT_TO", "").strip() or sender
    if not (sender and pw):
        print("Alerts: EMAIL_FROM / EMAIL_PASSWORD not set -- found", len(hits), "but couldn't email.")
        return False
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, f"HitByOne <{sender}>", to
    m.set_content(body)
    m.add_alternative("<pre style=\"font:14px/1.5 Menlo,Consolas,monospace;white-space:pre-wrap\">" + html.escape(body) + "</pre>", subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(sender, pw)
        s.send_message(m)
    print(f"Alerts: emailed you about {len(hits)} player(s).")
    return True


# ---------------------------------------------------------------- 2. closing line
def fresh_pull(minutes=45):
    rows = read(STATUS)
    if not rows or rows[0].get("outcome") != "ok":
        return False
    try:
        t = dt.datetime.strptime(rows[0]["ran_at"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=dt.timezone.utc)
    except (KeyError, ValueError):
        return False
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() < minutes * 60


def main_line(player, stat):
    """The book's main line now: both sides posted beats one-sided, then the pair priced closest to even (same rule
    as everywhere else in the Ledger)."""
    lines = {}
    for r in read(ODDS):
        if norm(r.get("player")) != norm(player) or r.get("stat") != stat or r.get("ou") not in ("over", "under"):
            continue
        try:
            ln, dec = float(r["line"]), float(r["decimal_odds"])
        except (TypeError, ValueError, KeyError):
            continue
        g = lines.setdefault(ln, {"over": 0.0, "under": 0.0})
        g[r["ou"]] = max(g[r["ou"]], dec)
    if not lines:
        return None

    def score(item):
        o, u = item[1]["over"], item[1]["under"]
        return abs(1 / o - 1 / u) if o and u else 1 + abs((o or u) - 1.91)
    ln, best = min(lines.items(), key=score)
    return ln, best["over"]


def american(dec):
    return f"+{round((dec - 1) * 100)}" if dec >= 2 else str(round(-100 / (dec - 1)))


def record_close(day):
    if os.environ.get("CLV") != "1":
        return
    if not fresh_pull():
        print("Alerts: no fresh odds pull this run -- closing line not recorded.")
        return
    rows = read(POTD)
    cols = list(rows[0].keys()) if rows else []
    for extra in ("Close Line", "Close American", "Close Decimal"):
        if extra not in cols:
            cols.append(extra)
    done = False
    for r in rows:
        if r.get("Date") == day and not r.get("Result"):
            ml = main_line(r["Player"], r["Stat"])
            if not ml:
                print(f"Alerts: no line posted for {r['Player']} {r['Stat']} right now -- closing line not recorded.")
                continue
            ln, dec = ml
            r["Close Line"] = f"{ln:g}"
            r["Close Decimal"] = f"{dec:.3f}" if dec else ""
            r["Close American"] = american(dec) if dec and dec > 1 else ""
            locked = float(r["Line"])
            verdict = "beat the close" if ln > locked or (ln == locked and dec and dec < float(r["Decimal"])) else \
                      "matched the close" if ln == locked else "line moved against us"
            print(f"Alerts: closing line for the Pick of the Day: locked o{r['Line']} {r['American']}, now o{r['Close Line']} {r['Close American']} ({verdict}).")
            done = True
    if done:
        write(POTD, rows, cols)


def main():
    day = today()
    check_injuries(day)
    record_close(day)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Alerts failed:", repr(e))
        sys.exit(1)
