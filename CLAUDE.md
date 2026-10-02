# MaizeTixScraper

Watches [MaizeTix](https://www.maizetix.com) — the U-M student-only ticket
marketplace — and pushes an ntfy notification the moment somebody lists a
football ticket cheap. Built because student-section prices swing hard: Western
Michigan sits around $107 on a $92 median, and a seller dumping one at $40 gets
bought within minutes by whoever happens to be looking.

Currently watching **UCLA (Nov 21, 2026)** only, on a flat **$20** bar.
Western Michigan was played 2026-09-05 and is disabled.

**Runs on GitHub Actions, not locally** — nothing at home needs to stay on.
Checks roughly every 20 minutes in practice (see Deployment).

---

## Architecture

Deliberately boring: stdlib-only Python, no dependencies, no browser, no venv.
One pass per invocation. All state is two small files.

```
main.py                     entry point / orchestration, CLI flags
config.json                 what to watch and what counts as cheap  <- edit this
.env                        the ntfy topic (gitignored, never committed)
maizetix/settings.py        config load + environment/secret overlay
maizetix/scraper.py         fetch + parse the schedule and game pages
maizetix/alerts.py          threshold logic + the already-alerted state file
maizetix/notify.py          ntfy push formatting and delivery
.github/workflows/watch.yml the recurring cloud job (~21 min in practice)
.github/workflows/keepalive.yml  stops GitHub disabling the schedule (see below)
install-cron.sh             local WSL cron, kept only as an offline fallback
tests/                      71 tests over real captured pages
state.json                  runtime: ticket_id -> price last alerted (gitignored)
```

### How a run goes

1. GET `/games/football/` → parse all 8 home games into `Game` records.
2. For each enabled target, resolve the opponent name → game id, skip if played.
3. GET `/games/<id>` → parse published Lowest Price / Median Sale + every listing.
4. Compare each listing to the target's effective threshold.
5. Drop anything already pushed (unless it dropped ≥ $5 since), push the rest.
6. Prune sold-out tickets from state, save.

### Why the site is easy to scrape

MaizeTix is a Django app on Heroku that renders **everything server-side** —
prices, seats, and ticket ids are all in the initial HTML, with no JS execution
and **no login required**. `robots.txt` is `Allow: /` except `/admin`. So plain
`urllib` + regex is genuinely the right tool here; requests/bs4/Selenium would be
strictly worse. Requests are spaced 2s apart with an identifying User-Agent.

---

## Deployment: GitHub Actions

`.github/workflows/watch.yml` asks for every 5 minutes. **Measured over the first
full day (56 scheduled runs, 2026-08-16 → 08-17): median gap 21 min, mean 25 min,
best 10 min, worst 81 min.** GitHub throttles scheduled workflows, and `*/5` is a
request, not a guarantee. Plan around ~20 min typical and up to ~an hour in the
worst stretch; the cron line is the ceiling on frequency, not the actual rate.
Nothing is wrong when you see gaps wider than 5 minutes, and there is no free
fix: shortening the cron does not make GitHub dispatch faster.

The throttling is worst in GitHub's peak hours: the 48–81 min gaps all landed
between 18:00 and 05:00 UTC (11 AM – 10 PM PDT), i.e. exactly the daytime window
when a seller is most likely to dump a ticket. That is the real cost of Actions
over local cron.

This is **free because the repo is public** — public repos get unlimited
GitHub-hosted runner minutes. That choice is load-bearing: each run bills a full
minute even though it takes ~12 seconds, so on a private repo 5-minute checks
would cost ~8,640 min/month against a 2,000 free tier. If this ever goes private,
the interval has to drop to ~30 min.

Three things make it survive unattended:

**The topic is a secret.** The repo is public, and an ntfy topic name is the only
thing protecting the feed — anyone who reads it can watch your alerts or spam
your phone. So `config.json` deliberately has **no** `ntfy_topic`. It comes from
`MAIZETIX_NTFY_TOPIC`: a gitignored `.env` locally, a GitHub Actions secret in
CI. A test asserts the committed config never contains it.

**State rides in the Actions cache.** Runners are ephemeral, so `state.json`
(which listing already pushed) is restored/saved via `actions/cache` using the
rolling `run_id` key + prefix-restore pattern, since cache keys are immutable.
Worst case the cache is evicted and you get one repeat notification — cheaper
than committing state back to the repo every five minutes.

**Failure is loud.** A broken scraper otherwise looks exactly like "no cheap
tickets" — you'd just stop getting alerts and never know. Two guards:
- Non-zero exit triggers a `failure()` step that pushes "MaizeTix watcher is broken".
- The run cross-checks the listing count the schedule page advertises against how
  many we actually parsed. Parsing 0 while the site claims 29 means our selectors
  broke, so the run exits 1 rather than silently reporting no deals.

### The 60-day trap

GitHub **disables scheduled workflows in a public repo after 60 days with no
repository activity**. That is not hypothetical here:

```
repo created   2026-08-15
+60 days       2026-10-14   <- watcher silently switches off
UCLA game      2026-11-21
```

`keepalive.yml` pushes a heartbeat commit on the 1st and 15th of each month to
reset that clock. If you ever see alerts stop, check the Actions tab for a
disabled-workflow banner first.

---

## The thing that broke v1: hardcoded game ids

The 2025 scraper hardcoded that season's numeric game ids, so it silently died
when the season rolled over. **Nothing here hardcodes an id.** `config.json`
names opponents in plain English (`"western michigan"`), and `find_game()`
re-resolves them against the live schedule on every run. 2026 football happens to
be ids 727–734; you will never need to know that.

Two related guards:

- `find_game()` matches only the **opponent half** of the name. Every game is
  `"X at Michigan Wolverines"`, so a naive substring match on `"michigan"` would
  hit all eight. There's a test pinning this.
- Games are skipped once their date passes. When *every* watched game is in the
  past, the run pushes one "season is done" notice, sets `season_over_notified`
  in state, and no-ops from then on.

---

## Configuring what counts as cheap

Each target in `config.json` supports two rules:

| key | meaning |
|---|---|
| `max_price` | absolute dollar ceiling |
| `pct_below_median` | percent under the median **sale** price MaizeTix publishes for that game |
| `mode` | `"and"` (both must pass, default) or `"or"` (either passes) |

`"and"` mode alerts only on the **lower** of the two bars — fewest false alarms.

Currently there is only one rule in play, on purpose:

- **UCLA** — `max_price: 20`, no `pct_below_median`. Flat bar: **$20.00**.
- **Western Michigan** — `enabled: false`. Played 2026-09-05 and no longer on the
  live schedule at all. Its old settings are kept in `config.json` as a record.

**Why the percent rule was dropped (2026-10-02).** `pct_below_median` chases a
falling market *downward*: UCLA's median sale slid $89.10 → $81.65 → $47.00 over
the season, which dragged a 40%-under bar from $53.46 down to $28.20. The game got
cheaper and the trigger got harder, which is backwards. A flat cap is immune to
that — $20 is $20 regardless of what the market does.

To watch another game, add a target with the opponent name — no code change:

```json
{ "opponent": "michigan state", "max_price": 90, "pct_below_median": 30, "mode": "and", "enabled": true }
```

Set `"enabled": false` to mute a game without deleting your settings. Commit and
push; the cloud job picks it up on the next tick.

### Notification behavior

A listing under 75% of its threshold is tagged a "steal" and escalates to
`urgent` priority. Tapping the notification opens the ticket page directly.

Dedupe lives in `state.json`: a listing pushes once, then stays quiet unless it
drops ≥ $5 below the price you were alerted at (then it re-pushes as
`↓ $X (was $Y)`). Without this, a cheap unsold listing would push every 5 minutes
all day. At the observed cadence that's ~56 checks/day per game.

---

## Commands

```bash
python3 main.py                 # normal run
python3 main.py --status        # current prices per watched game, no alerts
python3 main.py --dry-run       # show what WOULD push, push nothing
python3 main.py --test-push     # verify ntfy reaches your phone
python3 -m unittest discover -s tests -v
```

`--status` and `--dry-run` never push, so they don't require the secret.
In the GitHub UI, **Actions → Watch MaizeTix → Run workflow** triggers a manual
run, with a dry-run checkbox.

---

## Current state

**DEPLOYED AND LIVE** at https://github.com/PrestonJacks0n/MaizeTixScraper
(public, default branch `main`). Built 2026-08-15; retargeted to UCLA-only on a
flat $20 bar 2026-10-02.

Watching **UCLA (Nov 21, 2026)** and nothing else. Bar is a flat **$20.00**.
Western Michigan is `enabled: false` — played 2026-09-05 and gone from the live
schedule entirely (the schedule is down to 4 games).

Verified as of 2026-10-02:
- 71/71 tests passing against real captured pages.
- Cloud run `36976679933` green: UCLA only, 275 listings, low $25.00, median sale
  $47.00, bar $20.00, correctly silent. No WMU warning in the log.
- Local `--dry-run` agrees with the cloud run.
- **Keepalive is proven.** Three green scheduled runs (2026-09-01, 09-15, 10-01),
  each landing a real `chore: keepalive heartbeat [skip ci]` commit on `main`.
  Both workflows report `active`, so the 2026-10-14 60-day cutoff is handled and
  the UCLA game is covered. This was the last untested link; it's closed.
- `MAIZETIX_NTFY_TOPIC` secret still set (since 2026-08-15). Local topic lives in
  the gitignored `.env`. Secret hygiene holds — no topic string in any pushed file.

**The market has softened a lot and that matters.** UCLA's median sale slid
$89.10 → $81.65 → $47.00 across the season, and the live floor is $25.00. The $20
bar is only ~$5 under the cheapest ticket on the board, so expect genuine silence
rather than assuming a break. Preston set $20 as a deliberate hard line after the
percent rule proved perverse (see Configuring what counts as cheap).

**Alerts have never actually fired for a real deal.** The one time listings
cleared the bar, the push crashed — see the outage section below. The notifier's
happy path is therefore still unproven in production against a live deal; the
ntfy chain itself is confirmed (Preston got pushes on his phone, and the
"watcher is broken" alerts landed repeatedly during the outage).

Durable env facts: `gh` is installed at `~/.local/bin/gh`, authed as
PrestonJacks0n with `repo` + `workflow` scopes and `gh auth setup-git` done, so
sessions can push and drive Actions directly. GitHub's scheduler took ~18 min to
pick up a brand-new cron back in August — looks like a failure, isn't.

## Next steps

Nothing required. It watches UCLA until Nov 21, then reports season-over and
no-ops.

Optional:

1. If $20 proves too strict as the game nears, raise `max_price` on the UCLA
   target in `config.json`, commit and push — the cloud job picks it up next tick.
   The floor to beat on 2026-10-02 was $25.00.
2. If an alert ever does fire, check the notification actually rendered (the
   deal-push path has still never run successfully in production).

## The 2026-10-02 outage: a crash, not a dead game

Scheduled runs started failing in late September. The obvious theory — WMU is in
the past, so the script chokes on it — was **wrong**: a missing game is handled,
it logs `no game on the schedule matches 'western michigan' -- skipping` and
carries on.

The real fault was in `notify.push()`. UCLA's market had softened enough that
listings finally cleared the bar ($25.00 floor against a $28.20 percent-based bar),
so the code reached a code path it had never executed in production: formatting
and sending an actual deal alert. The title contained an em dash, HTTP headers are
encoded **latin-1**, and urllib raised `UnicodeEncodeError` — which `push()`'s
narrow `except (urllib.error.URLError, OSError)` did not catch. It propagated out
of `run()` and exited 1.

Two bad consequences, both now fixed:

1. **Real cheap tickets were found and never pushed** — for days. The one failure
   mode the alerting was built to prevent, caused by the alerting itself.
2. The failure alert fired every run and read "the site markup changed", which
   pointed the investigation at the scraper instead of the notifier.

Fixes: `ascii_header()` flattens header values, push titles are plain ASCII
(`DROP $25.00 (was $44.00) - ...`), and `push()` now catches `Exception` so no
notification problem can ever abort a run again. Four regression tests cover it,
including one asserting the shipped title format is latin-1 encodable.

## Watch items

- **The real cadence is ~21 min median, not 5** — and it degrades to 48–81 min
  during US daytime. Measured across a full day, 56 runs. GitHub throttles
  scheduled workflows; `*/5` is an upper bound on how often it *can* run. This is
  the main cost of not running locally, and it is not fixable for free — if a game
  ever justifies true 5-minute checks, run `install-cron.sh` on a machine that
  stays awake *in addition* to Actions.
- **Keepalive matters** — see the 60-day trap above. If it ever fails, alerts stop
  silently, which is the one failure mode the ntfy failure-push can't cover.
- Cache eviction (7 days unused, or 10 GB repo-wide) costs at most one duplicate
  notification. Not worth engineering around.
- **Fixtures are frozen**, so tests stay green through a site redesign. The
  advertised-vs-parsed count check is what actually catches drift at runtime; if
  it fires, re-capture `tests/fixtures/` and diff.
- Median Sale is the median of *completed sales*, not current listings — it can
  sit below the live floor (WMU: $92.25 median vs $107.48 lowest). That's part of
  why the flat `max_price` cap now does all the work.
- **Never put a non-ASCII character in a push title.** HTTP headers are encoded
  latin-1, so urllib raises `UnicodeEncodeError` on one — see the 2026-10-02
  outage below. `notify.ascii_header()` now scrubs it and `push()` catches
  everything, but the titles themselves are plain ASCII too.
- `install-cron.sh` still works for a local WSL cron, but it's a fallback only —
  the whole point of the move to Actions was not depending on this machine.
