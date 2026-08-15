#!/usr/bin/env python3
"""MaizeTix cheap-ticket watcher -- one pass per invocation, driven by cron.

  python3 main.py              # normal run: scrape, alert on deals, save state
  python3 main.py --dry-run    # scrape and print what WOULD alert, push nothing
  python3 main.py --status     # current prices for every watched game, no alerts
  python3 main.py --test-push  # prove the ntfy topic reaches your phone
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

from maizetix import notify
from maizetix.alerts import AlertState, effective_threshold, find_deals
from maizetix.scraper import ScrapeError, fetch, find_game, parse_game_page, parse_schedule
from maizetix.settings import ConfigError, load_config

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("maizetix")


def setup_logging(verbose: bool) -> None:
    ROOT.joinpath("logs").mkdir(exist_ok=True)
    handlers = [
        logging.FileHandler(ROOT / "logs" / "scraper.log"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def run(cfg: dict, *, dry_run: bool = False, status_only: bool = False) -> int:
    scrape = cfg["scrape"]
    behavior = cfg.get("behavior", {})
    base = scrape["base_url"].rstrip("/")
    fetch_kw = {
        "user_agent": scrape["user_agent"],
        "timeout": scrape.get("timeout_seconds", 20),
        "max_retries": scrape.get("max_retries", 3),
        "delay": scrape.get("delay_between_requests", 2.0),
    }

    targets = [t for t in cfg["targets"] if t.get("enabled", True)]
    if not targets:
        LOG.info("no enabled targets in config.json -- nothing to do")
        return 0

    schedule_url = f"{base}/games/{cfg.get('sport', 'football')}/"
    LOG.info("fetching schedule: %s", schedule_url)
    games = parse_schedule(fetch(schedule_url, **fetch_kw))
    LOG.info("schedule has %d games", len(games))

    state = AlertState(ROOT / "state.json")
    today = date.today()

    all_deals = []
    live_ticket_ids: set[str] = set()
    watched_game_ids: set[str] = set()
    resolved, expired, missing = 0, 0, []
    degraded = False   # set when a page parsed but looked structurally wrong

    for target in targets:
        opponent = target["opponent"]
        game = find_game(games, opponent)

        if game is None:
            missing.append(opponent)
            LOG.warning("no game on the schedule matches %r -- skipping", opponent)
            continue

        if behavior.get("expire_after_game", True) and game.is_past(today):
            expired += 1
            LOG.info("%s (%s) has already been played -- skipping", game.opponent, game.date_text)
            continue

        resolved += 1
        watched_game_ids.add(game.game_id)
        time.sleep(scrape.get("delay_between_requests", 2.0))

        game_url = f"{base}/games/{game.game_id}"
        LOG.info("checking %s -> %s", game.opponent, game_url)
        try:
            snapshot = parse_game_page(fetch(game_url, **fetch_kw), game)
        except ScrapeError as exc:
            # One bad game shouldn't sink the others, but it must still surface.
            LOG.error("could not read %s: %s", game.opponent, exc)
            degraded = True
            continue

        # The schedule page already told us how many listings this game has. If
        # we parsed none but it claims some, our selectors broke -- fail the run
        # so CI pushes a "watcher is broken" alert. Otherwise markup drift would
        # look exactly like "no cheap tickets" and you'd never hear about it.
        if game.active_listings > 0 and not snapshot.listings:
            LOG.error("%s: schedule says %d active listings but parsed 0 -- "
                      "the listing markup has probably changed",
                      game.opponent, game.active_listings)
            degraded = True
            continue

        live_ticket_ids.update(l.ticket_id for l in snapshot.listings)
        threshold, why = effective_threshold(target, snapshot)
        LOG.info(
            "  %d listings | low $%s | median sale $%s | alert at or under %s (%s)",
            len(snapshot.listings),
            f"{snapshot.lowest_price:.2f}" if snapshot.lowest_price else "n/a",
            f"{snapshot.median_sale:.2f}" if snapshot.median_sale else "n/a",
            f"${threshold:.2f}" if threshold else "n/a",
            why,
        )

        if status_only:
            for listing in snapshot.listings[:5]:
                LOG.info("    $%-8.2f %s", listing.price, listing.seat)
            continue

        deals, _ = find_deals(target, snapshot)
        if not deals:
            LOG.info("  nothing under the bar")
            continue

        for deal in deals:
            if state.should_alert(deal, realert_price_drop=behavior.get("realert_price_drop", 5.0)):
                all_deals.append(deal)
                LOG.info("  DEAL $%.2f %s -> %s", deal.listing.price,
                         deal.listing.seat, deal.listing.url)
            else:
                LOG.debug("  already alerted on %s at $%.2f",
                          deal.listing.ticket_id, deal.listing.price)

    if status_only:
        return 0

    # Every watched game is in the past: say so once, then go quiet.
    if resolved == 0 and expired > 0 and not missing:
        if not state.data.get("season_over_notified"):
            if not dry_run and notify.notify_season_over(cfg["notify"],
                                                         [t["opponent"] for t in targets]):
                state.data["season_over_notified"] = True
                state.save()
            LOG.info("season over for all watched games -- this cron job can be retired")
        else:
            LOG.info("season over for all watched games (already notified)")
        return 0

    if not all_deals:
        LOG.info("no new deals this run")
        state.prune(live_ticket_ids, watched_game_ids)
        state.save()
        return 1 if degraded else 0

    if dry_run:
        LOG.info("DRY RUN -- would push %d alert(s):", len(all_deals))
        for deal in all_deals:
            title, body = notify.format_deal(deal)
            LOG.info("  --- %s\n%s", title, body)
        return 1 if degraded else 0

    sent = notify.notify_deals(cfg["notify"], all_deals)
    LOG.info("pushed %d/%d alert(s)", sent, len(all_deals))
    for deal in all_deals:
        state.record(deal)
    state.prune(live_ticket_ids, watched_game_ids)
    state.save()

    # Deals still got through, but something else was broken -- exit non-zero so
    # CI tells you rather than letting a half-working watcher look healthy.
    return 1 if degraded else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch MaizeTix for cheap student tickets.")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="scrape and report, push nothing")
    ap.add_argument("--status", action="store_true", help="show current prices, no alerts")
    ap.add_argument("--test-push", action="store_true", help="send one test ntfy notification")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    try:
        # --status and --dry-run never push, so they don't need the secret.
        cfg = load_config(Path(args.config), env_file=ROOT / ".env",
                          require_topic=not (args.status or args.dry_run))
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 2

    if args.test_push:
        ok = notify.push(cfg["notify"], title="MaizeTix test",
                         body="If you can read this, alerts will reach you.",
                         priority="default", tags="white_check_mark")
        LOG.info("test push %s (topic: %s)", "sent" if ok else "FAILED",
                 cfg["notify"]["ntfy_topic"])
        return 0 if ok else 1

    try:
        return run(cfg, dry_run=args.dry_run, status_only=args.status)
    except ScrapeError as exc:
        # Site down or markup changed -- loud in the log, quiet on your phone.
        LOG.error("scrape failed: %s", exc)
        return 1
    except Exception:
        LOG.exception("unexpected failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
