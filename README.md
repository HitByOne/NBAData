# HitByOne Hardwood Ledger

The Gridiron Ledger, rebuilt for basketball: a GitHub Pages dashboard whose data refreshes itself from ESPN every day.

Same setup as the NFL repo: Python scripts pull box scores into `site/data/*.csv`, `site/index.html` reads those CSVs on every page load, and a GitHub Actions workflow runs the scripts and redeploys the site. No build step, no API key for the stats.

## Set it up (about 10 minutes)

1. **Create a new, empty GitHub repo** (no README), e.g. `nbadashboard`.
2. **Push these files to it** from the command line (the web uploader skips the hidden `.github` folder):
   ```
   cd nba_ledger
   git init && git add . && git commit -m "NBA Ledger"
   git branch -M main
   git remote add origin https://github.com/<you>/nbadashboard.git
   git push -u origin main
   ```
3. **Turn on Pages:** repo Settings -> Pages -> Build and deployment -> Source = **GitHub Actions**.
4. **Prop prices (optional):** Settings -> Secrets and variables -> Actions -> New repository secret `RAPIDAPI_KEY`. The same key the Gridiron Ledger uses works.
5. **Run it once:** Actions tab -> "Daily NBA Dashboard Refresh" -> Run workflow (mode: full). The first run pulls the whole season so far, then deploys. The site is at `https://<you>.github.io/nbadashboard/`.
6. **Friends / synced picks (optional):** in the same Supabase project the Gridiron Ledger uses, open the SQL Editor and run `supabase_nba.sql` once. Your sign-in and friend groups are shared with the NFL page; NBA picks and tickets live in their own tables. `site/config.js` already holds the project URL and public key.

## Schedule

| When (UTC) | Central | What runs |
|---|---|---|
| 11:00 | 6am | Full refresh: last night's box scores, team tables, ratings, backtest, milestones, injuries, tonight's slate |
| 18:00 | noon (1pm in Oct/Apr) | Early try: injuries, lines, props; then the newsletter + social kit **if the board is ready** (a Pick of the Day priced -200..+200 and the Starting 5 on book lines). Otherwise it waits. |
| 20:00 | 2pm (3pm in Oct/Apr) | Backup: sends whatever noon held back. If noon already sent, no second odds pull and no second email. |
| 05:00 | 11pm (midnight in Oct/Apr) | Late stats refresh: tonight's finished games and everything built from them. No odds pull. |

Each run saves every game that is final and re-checks the last saved date, so a late West Coast game that is still going at 11pm is simply added by the next run.

## How it differs from the NFL version

- **Game days instead of weeks.** Each date with games gets a number (1 = opening night); picks, records and history are keyed by it, and the page shows the date.
- **Real positions: PG / SG / SF / PF / C.** ESPN only says G / F / C, so `11_positions.py` takes each player's position from Basketball-Reference's league page (this season, else last season). Rookies not listed there yet get an estimate from their own numbers until they appear. To set a player yourself, add `position_overrides.csv` at the top of the repo with two columns, `Player,Position` (e.g. `Jalen Johnson,SF`); it wins over everything. ESPN's G / F / C stays in the box-score file as `ESPN Pos`.
- **Starters are real**: the box score marks who started. For an upcoming game the projected five are the players with the most starts in the team's last 5 games, with Out players replaced by the next man up.
- **Efficiency replaces EPA:** points per 100 possessions, pace and the four factors (`09_team_efficiency.py`). Play Style replaces Coaching Tendencies.
- **Schedule Load is about back-to-backs** and 3-in-4 / 4-in-6 stretches.
- **Double-Double Props replaces Anytime TD.** Plus two NBA-only tabs: **First Basket** (tip, team to score first, each starter's chance) and **3Q Scoring** (teams that score or allow 100+ through three quarters).
- **A player who doesn't play is VOID**, not a miss, as at the sportsbook.
- Dropped because they're football-only: Line Play, Defense Field, Film Room, Mike's Template.

## Two layouts

The page has a **Classic / Tonight** switch at the top. Classic is every tool in the side menu. Tonight is one main
screen: every starter on the slate with what he gets, what tonight's defense gives up to his position, the book line
and a lean, as a sortable list or on the court for one game. The choice is remembered per browser; add
`?layout=tonight` or `?layout=classic` to the address to open one directly.

## Starting 5 (Tonight layout)

One player per position who should have the best night against tonight's matchups, on a half court, with the bet for
each and three swaps per position. Three ways to pick: Best bet (each player's strongest OVER vs the book line),
Points only, and Matchup boost (no line needed). The slip shows the five legs and, when every leg has a price, what
they would pay together.

**How it's doing** (the switch on the Starting 5 screen) replays every finished night the way the Starting 5 would have
picked it that afternoon, using only the games before that night and the line saved that day, and grades each leg
on the box score. Best bet / Points only need the line history (`prop_history`, saved by each afternoon's odds
pull from the day it started); Matchup boost is graded for the whole season.

## Newsletter (friends list)

Every afternoon with games, right after the odds pull, `18_newsletter.py` emails tonight's Pick of the Day and Starting 5: the court graphic,
the slip, why the top pick, the swap options, last night's grades and the season record. Each issue is also saved to
`site/newsletter/` (the email's "view in browser" link), and `site/newsletter/index.html` lists them all.
It sends once a day at most; run the workflow by hand with mode **newsletter** to send one right now.

Set up once:
1. Gmail: turn on 2-Step Verification, then make an app password at myaccount.google.com/apppasswords.
2. Supabase: SQL Editor -> paste `supabase_newsletter.sql` -> Run. Then add people in Table Editor ->
   `newsletter_subscribers` (name, email; set `active` off to pause someone, delete the row to remove them).
3. GitHub secrets: `EMAIL_FROM` (the Gmail), `EMAIL_PASSWORD` (the app password), `SUPABASE_SERVICE_KEY`
   (Supabase -> Project Settings -> API Keys -> secret key). Everyone is BCC'd.

The list is private: the table has no public access, so only the secret key can read it.
Without the secrets it still builds and saves the issue, it just doesn't send.

## Social kit + Pick of the Day record

Right after the newsletter, `19_social.py` makes the day's posts and **emails them to you only** (to `KIT_TO`, or
`EMAIL_FROM` when that secret isn't set), as one zip:

* `2_X_and_Instagram/`: **one post for both**: 4 slides (1 Pick of the Day, 2 Starting 5 on the court, 3 Top 5 by
  stat, 4 last night + the record) and one caption that fits X's 280 characters. On X, reply with the record link.
* `3_TikTok/`: an **animated ~20s vertical video** (the court draws, the five cards pop in, projections count up,
  Top 5 rows slide in, player to watch, link in bio) plus the same 4 slides, and its own caption.
* `1_Reddit_PickOfTheDay.txt`: the r/sportsbook comment.

The kit is never on the site or committed (`social_kit/` is in `.gitignore`). Post from Buffer (X + Instagram + TikTok).

* **TikTok gets the same post without the betting** (no lines, odds, units or payouts; projections instead): TikTok
  keeps content that shows gambling off the For You feed. The video stays clear of TikTok's buttons and caption.
* **Pick of the Day:** the strongest single OVER on the board priced -200 to +200, flat 1 unit. It's written to
  `records/nba_2027_potd_log.csv` the first time the afternoon run makes it and never changed; every run grades
  finished picks (W / L / P, VOID if he didn't play). The public page **`/record/`** shows the record with graded
  picks only -- that's the link for bios and posts (`/social/` forwards there).
* **Your private kit page:** add a `KIT_KEY` secret (16+ random letters/numbers) and the kit is also put up at
  `/social/<KIT_KEY>/` with a one-tap "Download everything (zip)". The key is never in the repo or logs; the page is
  replaced every afternoon and gone after the next refresh. It's an unlisted link, not a locked one. The Reddit comment's Record, Net Units, ROI and Avg Odds come from that file.

## Evening check: late scratches + closing line

`20_alerts.py` runs at 23:00 UTC (5pm Central in winter, 6pm in Oct/Apr) after a fresh injury report. If tonight's Pick of
the Day or any Starting 5 player is listed (Out / Doubtful / Questionable / Day-To-Day), it emails **you** (KIT_TO or
EMAIL_FROM) so you can post an update. Each player + status is sent once a night (`records/alerts_sent.csv`).
Run it any time: Actions -> Run workflow -> mode **alerts**.

**Closing line (optional):** add a repository *variable* (Settings -> Secrets and variables -> Actions -> Variables tab)
named `CLV_ENABLED` = `true`. The evening check then pulls odds once more and saves the Pick of the Day's line at that
time next to the locked one ("beat the closing line" on the record). It costs one extra odds pull a day (~20 requests)
and never overwrites the afternoon's saved lines.

## Pick of the Day in the app

Today's pick shows at the top of Tonight and Starting 5 from the moment it locks (`site/data/nba_2027_potd.csv`),
and the Record tab leads with the Pick of the Day record (with closing line) and the Starting 5 record.
The Pick of the Day skips anyone on the injury report.

## Testing without bothering the list

Actions -> Run workflow -> mode **test**: runs the newsletter and the social kit but sends both **only to you**
(`TEST_TO` secret if set, else `KIT_TO`, else `EMAIL_FROM`), subjects start with [TEST], and nothing is recorded:
no Pick of the Day locked, no "already sent" marker, no archive copy, record page untouched. Run it as often as you like.
Mode **newsletter** is the real thing (the whole list).

## Run it by hand

```
pip install pandas requests
python3 run_daily.py                    # full refresh
python3 run_daily.py --quick            # injuries + tonight's slate
python3 run_daily.py --date 2026-11-03  # re-pull one date, rebuild everything after it
RAPIDAPI_KEY=xxxx ODDS_FORCE=1 python3 17_prop_odds.py
cd site && python3 -m http.server       # then open http://localhost:8000
```

## Files

| Script | Writes |
|---|---|
| `01_initial_pull.py` / `02_daily_update.py` | box scores: `player_game_stats`, `team_box`, `games` |
| `03_build_team_tables.py` | `team_game_stats` (pace, ratings, rest), `team_summary` |
| `04_strength_of_schedule.py` | `strength_of_schedule` |
| `11_positions.py` | `positions` (PG / SG / SF / PF / C per player, and where each came from) |
| `05_defense_vs_position.py` | `defense_vs_position` (PG / SG / SF / PF / C, all and starters) |
| `06_matchup_preview.py` | `upcoming_matchups` (tonight + next date, lines, rest) |
| `07_milestone_watch.py` | `milestones`, `team_milestones` |
| `08_injury_report.py` | `injury_report` |
| `09_team_efficiency.py` | `team_efficiency`, `team_style` |
| `10_game_flow.py` | `game_flow`: opening tip, first basket, team first baskets, points by quarter |
| `12_player_info.py` | `player_info` (birth date, jersey, height, weight from the rosters; birthday tags) |
| `16_backtest.py` | `backtest` (Best Picks replayed on past nights) |
| `17_prop_odds.py` | `prop_odds`, `prop_odds_status`, `prop_history` (each night's main lines, for grading) |
| `20_alerts.py` | evening late-scratch email + closing line for the Pick of the Day |
| `19_social.py` | social kit + `potd_log` (the Pick of the Day record) |
| `18_newsletter.py` | emails the Pick of the Day + Starting 5 to the Supabase list; saves `site/newsletter/YYYY-MM-DD.html` + `.png` |
| `supabase_newsletter.sql` | the subscriber list table (run once) |

Everything comes from ESPN's public site API, the same source the Gridiron Ledger uses. This dashboard is not affiliated with the NBA.
