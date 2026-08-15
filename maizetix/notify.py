"""Push alerts to ntfy.sh -- same pattern as padresTv and aegis.

Subscribe on your phone by installing the ntfy app and adding the topic from
config.json. There is no auth: the topic name is the secret, so keep it odd.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Iterable

from .alerts import Deal


def push(cfg: dict, *, title: str, body: str, priority: str = "default",
         tags: str = "", click: str = "") -> bool:
    """Send one ntfy notification. Returns False instead of raising.

    A notification failure must never take down the scrape loop -- we'd rather
    log it and try again on the next cron tick.
    """
    url = f"{cfg['ntfy_base_url'].rstrip('/')}/{cfg['ntfy_topic']}"
    headers = {"Title": title, "Priority": priority, "Content-Type": "text/plain"}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status < 300
    except (urllib.error.URLError, OSError):
        return False


def format_deal(deal: Deal) -> tuple[str, str]:
    """Build the (title, body) for one cheap listing."""
    snap = deal.snapshot
    price = f"${deal.listing.price:.2f}"

    if deal.previous_price is not None:
        title = f"↓ {price} (was ${deal.previous_price:.2f}) — {snap.title}"
    else:
        title = f"{price} — {snap.title}"

    lines = [snap.when]
    if deal.listing.seat:
        lines.append(deal.listing.seat)
    lines.append("")
    lines.extend(deal.reasons)
    if snap.lowest_price is not None:
        lines.append(f"Game low was ${snap.lowest_price:.2f} across {len(snap.listings)} listings")
    lines.append("")
    lines.append(deal.listing.url)
    return title, "\n".join(lines)


def notify_deals(cfg: dict, deals: Iterable[Deal]) -> int:
    """Push one notification per deal. Returns how many actually went out."""
    sent = 0
    for deal in deals:
        title, body = format_deal(deal)
        priority = cfg["priority_steal"] if deal.is_steal else cfg["priority_default"]
        tags = "fire,ticket" if deal.is_steal else "ticket"
        if push(cfg, title=title, body=body, priority=priority,
                tags=tags, click=deal.listing.url):
            sent += 1
    return sent


def notify_season_over(cfg: dict, opponents: list[str]) -> bool:
    """Tell Preston the watch list is spent so the cron job can be retired."""
    return push(
        cfg,
        title="MaizeTix watch is done for the season",
        body=(
            "Every watched game has passed: " + ", ".join(opponents) + ".\n\n"
            "The scraper will no-op from here. Retire the cron job with "
            "`./install-cron.sh --remove`, or add next season's games to "
            "config.json and it picks straight back up."
        ),
        priority="default",
        tags="checkered_flag",
    )
