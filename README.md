# MaizeTixScraper

Get pushed the moment a U-M student lists a cheap football ticket on
[MaizeTix](https://www.maizetix.com). Runs on GitHub Actions every 5 minutes, so
nothing has to stay on at home.

Watching **Western Michigan** (Sept 5) and **UCLA** (Nov 21) for 2026.

## Setup

**1. Create a public repo and push.** Public matters: it's what makes unlimited
Actions minutes free.

```bash
git remote add origin https://github.com/PrestonJacks0n/MaizeTixScraper.git
git push -u origin main
```

**2. Add the secret.** Settings → Secrets and variables → Actions → New
repository secret:

```
Name:   MAIZETIX_NTFY_TOPIC
Value:  <the topic from your local .env>
```

The topic is deliberately kept out of `config.json` — this repo is public, and
the topic name is the only thing protecting your alert feed.

**3. Subscribe** to that same topic in the [ntfy](https://ntfy.sh) phone app.

**4. Confirm it works.** Actions → *Watch MaizeTix* → Run workflow.

## Watching a different game

Edit `config.json` and push — opponents are matched by name against the live
schedule, so there are no game ids to look up:

```json
{ "opponent": "michigan state", "max_price": 90, "pct_below_median": 30, "mode": "and", "enabled": true }
```

`mode: "and"` alerts only when a listing beats **both** the dollar cap and the
percent-below-median bar.

## Running locally

Put the topic in a gitignored `.env`:

```
MAIZETIX_NTFY_TOPIC=your-topic-here
```

```bash
python3 main.py --status     # current prices, no alerts, no secret needed
python3 main.py --dry-run    # what would push, without pushing
python3 main.py --test-push  # confirm ntfy reaches your phone
python3 -m unittest discover -s tests -v
```

No dependencies — stdlib Python 3 only.

See `CLAUDE.md` for architecture and design notes.
