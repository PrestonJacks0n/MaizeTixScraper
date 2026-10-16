# MaizeTixScraper

Get pushed the moment a U-M student lists a cheap football ticket on
[MaizeTix](https://www.maizetix.com). Runs on GitHub Actions, so nothing has to
stay on at home. Checks land roughly every 20 minutes in practice (sometimes up
to an hour) — the workflow asks for 5, but GitHub throttles scheduled runs.

## Status: deactivated (2026-10-15)

Job done — the tickets were bought. Both workflows are disabled in the Actions
tab; the code is kept as-is for a future season. Re-enable with
`gh workflow enable "Watch MaizeTix"` and `gh workflow enable "Keepalive"`.

Setup is already done — the repo is public (which is what makes Actions
minutes free), the `MAIZETIX_NTFY_TOPIC` secret is set, and cloud runs are
green. The topic is deliberately kept out of `config.json`: this repo is
public, and the topic name is the only thing protecting the alert feed.

Manual run any time: **Actions** → *Watch MaizeTix* → **Run workflow**
(there's a dry-run checkbox).

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
