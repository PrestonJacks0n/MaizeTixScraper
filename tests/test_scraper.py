"""Parser and alert-logic tests.

Fixtures are real pages captured from maizetix.com on 2026-08-15. They are the
canary: when MaizeTix changes its markup these tests keep passing while the live
site returns nothing, so a live run that reports "0 listings" with tests green
means refresh the fixtures and compare.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maizetix.alerts import AlertState, Deal, effective_threshold, find_deals  # noqa: E402
from maizetix.notify import format_deal  # noqa: E402
from maizetix.scraper import (  # noqa: E402
    Game, ScrapeError, find_game, parse_game_date, parse_game_page, parse_schedule,
)

FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestSchedule(unittest.TestCase):
    def setUp(self):
        self.games = parse_schedule(fixture("football.html"))

    def test_parses_all_eight_home_games(self):
        self.assertEqual(len(self.games), 8)

    def test_extracts_ids_dates_and_counts(self):
        wmu = self.games[0]
        self.assertEqual(wmu.game_id, "727")
        self.assertEqual(wmu.name, "Western Michigan Broncos at Michigan Wolverines")
        self.assertEqual(wmu.game_date, date(2026, 9, 5))
        self.assertEqual(wmu.active_listings, 29)

    def test_opponent_strips_the_michigan_half(self):
        self.assertEqual(self.games[0].opponent, "Western Michigan Broncos")
        self.assertEqual(self.games[-1].opponent, "UCLA Bruins")

    def test_raises_when_markup_changes(self):
        with self.assertRaises(ScrapeError):
            parse_schedule("<html><body>redesigned</body></html>")


class TestFindGame(unittest.TestCase):
    def setUp(self):
        self.games = parse_schedule(fixture("football.html"))

    def test_matches_configured_opponents(self):
        self.assertEqual(find_game(self.games, "western michigan").game_id, "727")
        self.assertEqual(find_game(self.games, "ucla").game_id, "734")
        self.assertEqual(find_game(self.games, "Michigan State").game_id, "733")

    def test_unknown_opponent_returns_none(self):
        self.assertIsNone(find_game(self.games, "notre dame"))

    def test_does_not_match_the_home_team(self):
        # Every game name ends in "Michigan Wolverines"; a bare "wolverines"
        # target must not silently resolve to whatever game happens to be first.
        self.assertIsNone(find_game(self.games, "wolverines"))


class TestGameDates(unittest.TestCase):
    def test_abbreviated_and_full_month_names(self):
        self.assertEqual(parse_game_date("Sept. 5, 2026"), date(2026, 9, 5))
        self.assertEqual(parse_game_date("Sep. 5, 2026"), date(2026, 9, 5))
        self.assertEqual(parse_game_date("Nov. 21, 2026"), date(2026, 11, 21))
        self.assertEqual(parse_game_date("May 2, 2027"), date(2027, 5, 2))

    def test_garbage_degrades_to_none(self):
        self.assertIsNone(parse_game_date("TBD"))
        self.assertIsNone(parse_game_date(""))

    def test_is_past(self):
        g = Game("727", "X at Y", "Sept. 5, 2026", date(2026, 9, 5), 29)
        self.assertTrue(g.is_past(date(2026, 9, 6)))
        self.assertFalse(g.is_past(date(2026, 9, 5)))   # game day still counts
        self.assertFalse(g.is_past(date(2026, 8, 15)))

    def test_unknown_date_is_never_past(self):
        self.assertFalse(Game("1", "X at Y", "TBD", None, 0).is_past(date(2030, 1, 1)))


class TestGamePage(unittest.TestCase):
    def setUp(self):
        games = parse_schedule(fixture("football.html"))
        self.wmu = parse_game_page(fixture("wmu.html"), find_game(games, "western michigan"))
        self.ucla = parse_game_page(fixture("ucla.html"), find_game(games, "ucla"))

    def test_published_stats(self):
        self.assertAlmostEqual(self.wmu.lowest_price, 107.48)
        self.assertAlmostEqual(self.wmu.median_sale, 92.25)
        self.assertAlmostEqual(self.ucla.lowest_price, 81.65)
        self.assertAlmostEqual(self.ucla.median_sale, 89.10)

    def test_listing_counts_match_the_schedule(self):
        self.assertEqual(len(self.wmu.listings), 29)
        self.assertEqual(len(self.ucla.listings), 60)

    def test_listings_sorted_cheapest_first(self):
        prices = [l.price for l in self.wmu.listings]
        self.assertEqual(prices, sorted(prices))

    def test_listing_fields(self):
        first = self.wmu.listings[0]
        self.assertAlmostEqual(first.price, 107.48)
        self.assertEqual(first.ticket_id, "8550173a4")
        self.assertEqual(first.seat, "Sect. 29 | Row 24 | Seat 11")
        self.assertEqual(first.url, "https://www.maizetix.com/tickets/8550173a4")

    def test_ticket_ids_are_unique(self):
        ids = [l.ticket_id for l in self.ucla.listings]
        self.assertEqual(len(ids), len(set(ids)))

    def test_header_text(self):
        self.assertEqual(self.wmu.title, "Michigan vs W Michigan")
        self.assertIn("Sep. 5, 2026", self.wmu.when)


class TestThresholds(unittest.TestCase):
    def setUp(self):
        games = parse_schedule(fixture("football.html"))
        self.wmu = parse_game_page(fixture("wmu.html"), find_game(games, "western michigan"))

    def test_and_mode_takes_the_lower_bar(self):
        # cap $45 vs 40% under the $92.25 median ($55.35) -> $45 binds
        threshold, why = effective_threshold(
            {"max_price": 45, "pct_below_median": 40, "mode": "and"}, self.wmu)
        self.assertAlmostEqual(threshold, 45.0)
        self.assertIn("cap", why)

    def test_or_mode_takes_the_higher_bar(self):
        threshold, _ = effective_threshold(
            {"max_price": 45, "pct_below_median": 40, "mode": "or"}, self.wmu)
        self.assertAlmostEqual(threshold, 55.35)

    def test_median_rule_alone(self):
        threshold, _ = effective_threshold({"pct_below_median": 40}, self.wmu)
        self.assertAlmostEqual(threshold, 55.35)

    def test_cap_alone(self):
        threshold, _ = effective_threshold({"max_price": 40}, self.wmu)
        self.assertAlmostEqual(threshold, 40.0)

    def test_median_rule_without_a_median_is_inert(self):
        self.wmu.median_sale = None
        threshold, why = effective_threshold({"pct_below_median": 40}, self.wmu)
        self.assertIsNone(threshold)
        self.assertIn("no median", why)

    def test_empty_config_alerts_on_nothing(self):
        threshold, _ = effective_threshold({}, self.wmu)
        self.assertIsNone(threshold)


class TestFindDeals(unittest.TestCase):
    def setUp(self):
        games = parse_schedule(fixture("football.html"))
        self.wmu = parse_game_page(fixture("wmu.html"), find_game(games, "western michigan"))

    def test_todays_real_prices_trigger_nothing(self):
        # Live WMU floor is $107.48 against a $45 bar -- silence is correct.
        deals, _ = find_deals({"max_price": 45, "pct_below_median": 40, "mode": "and"}, self.wmu)
        self.assertEqual(deals, [])

    def test_a_cheap_listing_is_caught(self):
        self.wmu.listings[0].price = 38.00
        deals, _ = find_deals({"max_price": 45, "pct_below_median": 40, "mode": "and"}, self.wmu)
        self.assertEqual(len(deals), 1)
        self.assertAlmostEqual(deals[0].listing.price, 38.00)
        self.assertTrue(any("below the" in r for r in deals[0].reasons))

    def test_boundary_price_is_inclusive(self):
        self.wmu.listings[0].price = 45.00
        deals, _ = find_deals({"max_price": 45}, self.wmu)
        self.assertEqual(len(deals), 1)

    def test_steal_flag(self):
        self.wmu.listings[0].price = 30.00   # <= 75% of the $45 bar
        self.wmu.listings[1].price = 44.00
        deals, _ = find_deals({"max_price": 45}, self.wmu)
        by_price = {d.listing.price: d for d in deals}
        self.assertTrue(by_price[30.00].is_steal)
        self.assertFalse(by_price[44.00].is_steal)


class TestAlertState(unittest.TestCase):
    def setUp(self):
        games = parse_schedule(fixture("football.html"))
        self.snap = parse_game_page(fixture("wmu.html"), find_game(games, "western michigan"))
        self.snap.listings[0].price = 38.00
        self.deals, _ = find_deals({"max_price": 45}, self.snap)
        self.deal = self.deals[0]

        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"

    def test_first_sighting_alerts_then_goes_quiet(self):
        state = AlertState(self.path)
        self.assertTrue(state.should_alert(self.deal, realert_price_drop=5.0))
        state.record(self.deal)
        self.assertFalse(state.should_alert(self.deal, realert_price_drop=5.0))

    def test_state_survives_a_restart(self):
        state = AlertState(self.path)
        state.record(self.deal)
        state.save()
        self.assertFalse(AlertState(self.path).should_alert(self.deal, realert_price_drop=5.0))

    def test_meaningful_price_drop_realerts(self):
        state = AlertState(self.path)
        state.record(self.deal)
        self.deal.listing.price = 30.00       # $8 drop, bar is $5
        self.assertTrue(state.should_alert(self.deal, realert_price_drop=5.0))
        self.assertAlmostEqual(self.deal.previous_price, 38.00)

    def test_trivial_price_drop_stays_quiet(self):
        state = AlertState(self.path)
        state.record(self.deal)
        self.deal.listing.price = 36.00       # only $2
        self.assertFalse(state.should_alert(self.deal, realert_price_drop=5.0))

    def test_prune_forgets_sold_tickets(self):
        state = AlertState(self.path)
        state.record(self.deal)
        removed = state.prune(set(), {self.snap.game.game_id})
        self.assertEqual(removed, 1)
        self.assertEqual(state.data["alerted"], {})

    def test_prune_leaves_unchecked_games_alone(self):
        # A game we failed to scrape this run must keep its history, or the next
        # successful run re-pushes every listing on it.
        state = AlertState(self.path)
        state.record(self.deal)
        self.assertEqual(state.prune(set(), {"999"}), 0)
        self.assertIn(self.deal.listing.ticket_id, state.data["alerted"])

    def test_corrupt_state_file_does_not_crash(self):
        self.path.write_text("{not json")
        self.assertTrue(AlertState(self.path).should_alert(self.deal, realert_price_drop=5.0))

    def test_saved_file_is_valid_json(self):
        state = AlertState(self.path)
        state.record(self.deal)
        state.save()
        data = json.loads(self.path.read_text())
        self.assertIn(self.deal.listing.ticket_id, data["alerted"])
        self.assertIsNotNone(data["last_run"])


class TestNotificationFormat(unittest.TestCase):
    def setUp(self):
        games = parse_schedule(fixture("football.html"))
        self.snap = parse_game_page(fixture("wmu.html"), find_game(games, "western michigan"))
        self.snap.listings[0].price = 38.00
        self.deal = find_deals({"max_price": 45}, self.snap)[0][0]

    def test_title_and_body_carry_the_essentials(self):
        title, body = format_deal(self.deal)
        self.assertIn("$38.00", title)
        self.assertIn("Michigan vs W Michigan", title)
        self.assertIn("Sect. 29", body)
        self.assertIn("https://www.maizetix.com/tickets/", body)

    def test_price_drop_shows_the_old_price(self):
        self.deal.previous_price = 44.00
        title, _ = format_deal(self.deal)
        self.assertIn("was $44.00", title)


class TestShippedConfig(unittest.TestCase):
    """The config Preston actually runs -- guards against a bad hand-edit."""

    def setUp(self):
        self.cfg = json.loads((ROOT / "config.json").read_text())

    def test_watches_wmu_and_ucla(self):
        opponents = {t["opponent"] for t in self.cfg["targets"] if t.get("enabled", True)}
        self.assertEqual(opponents, {"western michigan", "ucla"})

    def test_every_target_resolves_against_the_live_schedule(self):
        games = parse_schedule(fixture("football.html"))
        for target in self.cfg["targets"]:
            with self.subTest(opponent=target["opponent"]):
                self.assertIsNotNone(find_game(games, target["opponent"]))

    def test_every_target_has_a_usable_threshold(self):
        games = parse_schedule(fixture("football.html"))
        snap = parse_game_page(fixture("wmu.html"), find_game(games, "western michigan"))
        for target in self.cfg["targets"]:
            with self.subTest(opponent=target["opponent"]):
                threshold, why = effective_threshold(target, snap)
                self.assertIsNotNone(threshold, why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
