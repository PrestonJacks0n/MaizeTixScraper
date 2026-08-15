"""Fetch and parse MaizeTix football pages.

MaizeTix renders everything server-side, so plain HTML parsing is enough -- no
browser, no JS engine, no login. Two page shapes matter:

  /games/football/  -> the season schedule, one <tr class="game-row"> per game,
                       each carrying its numeric game id in an onclick handler.
  /games/<id>       -> one game, with published Lowest Price / Median Sale stats
                       and one <tr> per active listing.

Game ids are NOT stable across seasons (2026 football is 727-734). Nothing here
hardcodes them; callers resolve a game by opponent name via find_game().
"""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date

# Django's "N" date filter (AP style): abbreviates all months except the short
# ones, which render in full. Both "Sept." and "Sep." appear on the live site.
_MONTHS = {
    "jan": 1, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "aug": 8,
    "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_GAME_ROW_RE = re.compile(r'<tr class="game-row".*?</tr>', re.S)
_GAME_ID_RE = re.compile(r"/games/(\d+)")
_GAME_NAME_RE = re.compile(r'gr-game-name">([^<]+)')
_GAME_DATE_RE = re.compile(r'innerText = "([^"]+)"')
_ACTIVE_RE = re.compile(r"(\d+) active listings")

_HEADER_TITLE_RE = re.compile(r'header-title">([^<]+)')
_HEADER_EXTRA_RE = re.compile(r'header-extra">([^<]+)')
_STAT_RE = re.compile(
    r'info-stats-header">([^<]+)</div>\s*<div class="info-stats-number">\s*\$?([\d,.]+)'
)

_LISTING_ROW_RE = re.compile(r"<tr >.*?</tr>", re.S)
_PRICE_RE = re.compile(r"<td>\$([\d,]+\.?\d*)</td>")
_SEAT_RE = re.compile(r'class="mb-1">(.*?)</p>', re.S)
_TICKET_ID_RE = re.compile(r"/tickets/([0-9a-f]+)")
_SELLER_RE = re.compile(r'class="nav-pfp">\s*([A-Z]{0,3})')


class ScrapeError(RuntimeError):
    """Raised when a page cannot be fetched or does not look like we expect."""


@dataclass
class Game:
    game_id: str
    name: str            # "Western Michigan Broncos at Michigan Wolverines"
    date_text: str       # "Sept. 5, 2026"
    game_date: date | None
    active_listings: int

    @property
    def opponent(self) -> str:
        """The visiting team, i.e. the name minus the ' at Michigan ...' tail."""
        return re.split(r"\s+at\s+", self.name)[0].strip()

    def is_past(self, today: date | None = None) -> bool:
        """True once the game date has gone by. Unknown dates are never past."""
        if self.game_date is None:
            return False
        return self.game_date < (today or date.today())


@dataclass
class Listing:
    ticket_id: str
    price: float
    seat: str
    seller: str

    @property
    def url(self) -> str:
        return f"https://www.maizetix.com/tickets/{self.ticket_id}"


@dataclass
class GameSnapshot:
    game: Game
    title: str                    # "Michigan vs W Michigan"
    when: str                     # "Sep. 5, 2026, 7:30 PM @ Michigan Stadium"
    lowest_price: float | None
    median_sale: float | None
    listings: list[Listing] = field(default_factory=list)


def parse_game_date(text: str, *, default_year: int | None = None) -> date | None:
    """Parse the site's date strings, e.g. 'Sept. 5, 2026' or 'Nov. 21, 2026'.

    Returns None rather than raising -- an unparseable date should degrade to
    "we don't know if it's past", not kill the run.
    """
    m = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{1,2})(?:,\s*(\d{4}))?", text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower().rstrip("."))
    if not month:
        return None
    year = int(m.group(3)) if m.group(3) else (default_year or date.today().year)
    try:
        return date(year, month, int(m.group(2)))
    except ValueError:
        return None


def _clean(raw: str) -> str:
    """Strip tags and collapse whitespace out of an inner-HTML fragment."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _money(raw: str) -> float:
    return float(raw.replace(",", "").replace("$", ""))


def fetch(url: str, *, user_agent: str, timeout: float = 20.0,
          max_retries: int = 3, delay: float = 2.0) -> str:
    """GET a page as text, retrying transient failures with a linear backoff."""
    last: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise ScrapeError(f"{url} returned HTTP {resp.status}")
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (urllib.error.URLError, ScrapeError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < max_retries:
                time.sleep(delay * attempt)
    raise ScrapeError(f"failed to fetch {url} after {max_retries} tries: {last}")


def parse_schedule(page_html: str) -> list[Game]:
    """Parse /games/<sport>/ into the season's games."""
    games: list[Game] = []
    for row in _GAME_ROW_RE.findall(page_html):
        gid = _GAME_ID_RE.search(row)
        name = _GAME_NAME_RE.search(row)
        if not (gid and name):
            continue
        date_match = _GAME_DATE_RE.search(row)
        date_text = date_match.group(1) if date_match else ""
        active = _ACTIVE_RE.search(row)
        games.append(Game(
            game_id=gid.group(1),
            name=html.unescape(name.group(1)).strip(),
            date_text=date_text,
            game_date=parse_game_date(date_text),
            active_listings=int(active.group(1)) if active else 0,
        ))
    if not games:
        raise ScrapeError("no games found -- the schedule page layout probably changed")
    return games


def parse_game_page(page_html: str, game: Game) -> GameSnapshot:
    """Parse /games/<id> into published stats plus every active listing."""
    stats = {k.strip().lower(): _money(v) for k, v in _STAT_RE.findall(page_html)}

    listings: list[Listing] = []
    for row in _LISTING_ROW_RE.findall(page_html):
        price = _PRICE_RE.search(row)
        ticket = _TICKET_ID_RE.search(row)
        if not (price and ticket):
            continue
        seat = _SEAT_RE.search(row)
        seller = _SELLER_RE.search(row)
        listings.append(Listing(
            ticket_id=ticket.group(1),
            price=_money(price.group(1)),
            seat=_clean(seat.group(1)) if seat else "",
            seller=seller.group(1).strip() if seller else "",
        ))

    title = _HEADER_TITLE_RE.search(page_html)
    when = _HEADER_EXTRA_RE.search(page_html)
    return GameSnapshot(
        game=game,
        title=html.unescape(title.group(1)).strip() if title else game.name,
        when=html.unescape(when.group(1)).strip() if when else game.date_text,
        lowest_price=stats.get("lowest price"),
        median_sale=stats.get("median sale"),
        listings=sorted(listings, key=lambda l: l.price),
    )


def find_game(games: list[Game], opponent: str) -> Game | None:
    """Match a configured opponent against the live schedule, loosely.

    Config says "western michigan"; the site says "Western Michigan Broncos at
    Michigan Wolverines". Matching on the opponent half only keeps "michigan"
    in the target from colliding with the "Michigan Wolverines" in every name.
    """
    needle = opponent.strip().lower()
    if not needle:
        return None
    for game in games:
        if needle in game.opponent.lower():
            return game
    return None
