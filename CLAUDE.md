# MaizeTixScraper

Watches [MaizeTix](https://www.maizetix.com) — the U-M student-only ticket
marketplace — and pushes an ntfy notification the moment somebody lists a
football ticket cheap. Built because student-section prices swing hard: Western
Michigan sits around $107 on a $92 median, and a seller dumping one at $40 gets
bought within minutes by whoever happens to be looking.

Currently watching **Western Michigan (Sept 5, 2026)** and **UCLA (Nov 21, 2026)**.

**Runs on GitHub Actions, not locally** — nothing at home needs to stay on.

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
.github/workflows/watch.yml the every-5-minute cloud job
.github/workflows/keepalive.yml  stops GitHub disabling the schedule (see below)
install-cron.sh             local WSL cron, kept only as an offline fallback
tests/                      63 tests over real captured pages
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

`.github/workflows/watch.yml` runs every 5 minutes. This is **free because the
repo is public** — public repos get unlimited GitHub-hosted runner minutes. That
choice is load-bearing: each run bills a full minute even though it takes ~12
seconds, so on a private repo 5-minute checks would cost ~8,640 min/month against
a 2,000 free tier. If this ever goes private, the interval has to drop to ~30 min.

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
Currently:

- **Western Michigan** — cap $45, or 40% under the $92.25 median ($55.35). Binding bar: **$45**.
- **UCLA** — cap $60, or 40% under the $89.10 median ($53.46). Binding bar: **$53.46**.

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
all day.

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
(public, default branch `main`). Built and shipped in one session, 2026-08-15.

The 2025 version was never recovered — it's in a private repo and this machine
had no credentials at the time. Since v1 was hardcoded to last season anyway,
rebuilding against the current markup was the faster path; nothing from it was
needed. (`gh` CLI is now installed at `~/.local/bin/gh` and authenticated as
PrestonJacks0n with `repo` + `workflow` scopes, with `gh auth setup-git` done, so
future sessions can push and manage Actions directly.)

Verified end to end:
- 63/63 tests passing against real captured pages.
- Two cloud runs succeeded, pulling real data: WMU 29 listings / $107.48 low /
  $92.25 median; UCLA 60 / $81.65 / $89.10. Both correctly found nothing.
- **ntfy confirmed by Preston on his phone** — the full chain works.
- Actions cache proven across runs: run 2 restored state saved by run 1, so push
  dedupe genuinely survives ephemeral runners.
- Secret hygiene checked: `.env` 404s on GitHub, no topic string in any pushed file.
- Drift detection verified by feeding the run renamed markup (correctly exited 1).

Today's floors are nowhere near the bars, which is the correct result: at $107
(WMU) and $81 (UCLA) there is nothing worth buying yet.

**One thing not yet observed:** the first *scheduled* (cron) firing. Both green
runs were manual `workflow_dispatch`. The workflow is registered `active` and the
cron is valid, but GitHub delays the first scheduled run after a workflow lands —
it had only been 3 minutes when the session ended.

## Next steps

1. **Confirm the schedule self-fires.** `gh run list --workflow=watch.yml --event=schedule`
   should show runs. If it's still empty hours later, check the Actions tab for a
   banner — that's the one failure mode nothing else would catch.
2. Watch the first day for cadence and false alarms; tune `max_price` in
   `config.json` if it's too chatty or too quiet, then commit and push.
3. Nothing else is required. It runs itself until the games pass, then reports
   season-over and no-ops.

## Watch items

- **5 minutes is a floor, not a promise.** GitHub queues scheduled workflows and
  delays them under load, especially on the hour. Some runs will land late; that
  is the cost of not running locally.
- **Keepalive matters** — see the 60-day trap above. If it ever fails, alerts stop
  silently, which is the one failure mode the ntfy failure-push can't cover.
- Cache eviction (7 days unused, or 10 GB repo-wide) costs at most one duplicate
  notification. Not worth engineering around.
- **Fixtures are frozen**, so tests stay green through a site redesign. The
  advertised-vs-parsed count check is what actually catches drift at runtime; if
  it fires, re-capture `tests/fixtures/` and diff.
- Median Sale is the median of *completed sales*, not current listings — it can
  sit below the live floor (WMU: $92.25 median vs $107.48 lowest). That's why the
  `max_price` cap is doing the work on WMU.
- `install-cron.sh` still works for a local WSL cron, but it's a fallback only —
  the whole point of the move to Actions was not depending on this machine.
