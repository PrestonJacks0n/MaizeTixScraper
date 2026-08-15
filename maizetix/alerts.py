"""Decide which listings are worth a push, and remember what we already sent.

Two independent tests can fire on a listing:

  max_price         an absolute dollar ceiling you set per game
  pct_below_median  a floor relative to the median SALE price MaizeTix publishes
                    for that game, so it tracks the market as it moves

`mode` combines them: "and" (default, both must pass -- fewest false alarms),
"or" (either passes), or name a single rule by using only that key.

The state file exists because this runs on a short cron interval. Without it a
$38 listing that sits unsold for a day would push you every few minutes.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .scraper import GameSnapshot, Listing


@dataclass
class Deal:
    listing: Listing
    snapshot: GameSnapshot
    threshold: float          # the effective price we compared against
    reasons: list[str]        # human-readable, goes into the push body
    previous_price: float | None = None   # set when this is a price-drop re-alert

    @property
    def is_steal(self) -> bool:
        """Well under the bar, not just barely under -- bumps push priority."""
        return self.threshold > 0 and self.listing.price <= self.threshold * 0.75


def effective_threshold(target: dict, snapshot: GameSnapshot) -> tuple[float | None, str]:
    """Resolve a target's rules into one price ceiling, plus how we got there.

    Returns (None, reason) when no rule can be evaluated -- e.g. the config asks
    for pct_below_median but the site published no median for that game yet.
    """
    max_price = target.get("max_price")
    pct = target.get("pct_below_median")
    median = snapshot.median_sale

    median_cap = None
    if pct is not None and median:
        median_cap = round(median * (1 - pct / 100.0), 2)

    mode = str(target.get("mode", "and")).lower()

    caps: list[tuple[float, str]] = []
    if max_price is not None:
        caps.append((float(max_price), f"cap ${float(max_price):.2f}"))
    if median_cap is not None:
        caps.append((median_cap, f"{pct:.0f}% under ${median:.2f} median = ${median_cap:.2f}"))

    if not caps:
        if pct is not None and not median:
            return None, "no median published yet and no max_price set"
        return None, "no thresholds configured"

    # "and" means a listing must beat every rule, so the binding ceiling is the
    # lowest one. "or" means beating any rule is enough, so it's the highest.
    if mode == "or":
        price, why = max(caps, key=lambda c: c[0])
    else:
        price, why = min(caps, key=lambda c: c[0])
    return price, why


def find_deals(target: dict, snapshot: GameSnapshot) -> tuple[list[Deal], str]:
    """Return every listing at or under the target's effective threshold."""
    threshold, why = effective_threshold(target, snapshot)
    if threshold is None:
        return [], why

    deals = []
    for listing in snapshot.listings:
        if listing.price <= threshold:
            reasons = [f"${listing.price:.2f} <= ${threshold:.2f} ({why})"]
            if snapshot.median_sale:
                pct_off = (1 - listing.price / snapshot.median_sale) * 100
                side = "below" if pct_off >= 0 else "above"
                reasons.append(
                    f"{abs(pct_off):.0f}% {side} the ${snapshot.median_sale:.2f} median sale")
            deals.append(Deal(listing=listing, snapshot=snapshot,
                              threshold=threshold, reasons=reasons))
    return deals, why


class AlertState:
    """Tracks the price each ticket was last alerted at, in a small JSON file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"alerted": {}, "last_run": None, "season_over_notified": False}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (json.JSONDecodeError, OSError):
                # A corrupt state file should cost us one duplicate push, not a
                # crashed run -- start clean and carry on.
                pass
        self.data.setdefault("alerted", {})

    def should_alert(self, deal: Deal, *, realert_price_drop: float) -> bool:
        """New tickets always alert; seen ones only on a meaningful price drop."""
        prior = self.data["alerted"].get(deal.listing.ticket_id)
        if prior is None:
            return True
        previous = float(prior.get("price", 0) or 0)
        if deal.listing.price <= previous - realert_price_drop:
            deal.previous_price = previous
            return True
        return False

    def record(self, deal: Deal) -> None:
        self.data["alerted"][deal.listing.ticket_id] = {
            "price": deal.listing.price,
            "game_id": deal.snapshot.game.game_id,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def prune(self, live_ticket_ids: set[str], watched_game_ids: set[str]) -> int:
        """Forget tickets that are gone from games we actually checked this run.

        Scoped to watched games so a scrape failure on one game can't wipe its
        history and cause a re-push storm on the next successful run.
        """
        stale = [
            tid for tid, meta in self.data["alerted"].items()
            if meta.get("game_id") in watched_game_ids and tid not in live_ticket_ids
        ]
        for tid in stale:
            del self.data["alerted"][tid]
        return len(stale)

    def save(self) -> None:
        """Write atomically so a killed run can't leave a truncated state file."""
        self.data["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
