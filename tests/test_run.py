"""End-to-end tests of the run loop, with the network stubbed out.

The important one is markup drift. A broken parser looks identical to "no cheap
tickets" from the outside, so the run must exit non-zero and let CI shout.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as run_module  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def make_config(**notify_overrides) -> dict:
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["notify"]["ntfy_topic"] = "test-topic"
    cfg["notify"].update(notify_overrides)
    cfg["scrape"]["delay_between_requests"] = 0
    return cfg


class RunHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Keep the real state.json out of the way.
        patcher = mock.patch.object(run_module, "ROOT", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

        # Per-game pages, so editing one game's prices doesn't leak into the
        # other and double the expected push count.
        self.pages = {"727": fixture("wmu.html"), "734": fixture("ucla.html")}
        self.pushes: list = []

        def fake_fetch(url, **kwargs):
            if url.rstrip("/").endswith("football"):
                return fixture("football.html")
            return self.pages[url.rstrip("/").rsplit("/", 1)[-1]]

        f = mock.patch.object(run_module, "fetch", side_effect=fake_fetch)
        f.start()
        self.addCleanup(f.stop)

        n = mock.patch.object(
            run_module.notify, "notify_deals",
            side_effect=lambda cfg, deals: self.pushes.extend(deals) or len(self.pushes),
        )
        n.start()
        self.addCleanup(n.stop)


class TestHealthyRun(RunHarness):
    def test_real_prices_push_nothing_and_succeed(self):
        self.assertEqual(run_module.run(make_config()), 0)
        self.assertEqual(self.pushes, [])

    def test_cheap_listing_pushes_once_then_stays_quiet(self):
        # Rewrite UCLA's cheapest listing to a price that clears the $20 bar.
        self.pages["734"] = self.pages["734"].replace(
            "<td>$81.65</td>", "<td>$18.00</td>", 1)
        cfg = make_config()

        self.assertEqual(run_module.run(cfg), 0)
        self.assertEqual(len(self.pushes), 1)
        self.assertAlmostEqual(self.pushes[0].listing.price, 18.00)

        self.pushes.clear()
        self.assertEqual(run_module.run(cfg), 0)
        self.assertEqual(self.pushes, [], "a second run must not re-push the same listing")

    def test_a_listing_just_over_the_bar_stays_quiet(self):
        # $20.01 is not $20. The whole point of the flat cap is that it is exact.
        self.pages["734"] = self.pages["734"].replace(
            "<td>$81.65</td>", "<td>$20.01</td>", 1)
        self.assertEqual(run_module.run(make_config()), 0)
        self.assertEqual(self.pushes, [])

    def test_a_listing_exactly_on_the_bar_pushes(self):
        self.pages["734"] = self.pages["734"].replace(
            "<td>$81.65</td>", "<td>$20.00</td>", 1)
        self.assertEqual(run_module.run(make_config()), 0)
        self.assertEqual(len(self.pushes), 1)

    def test_disabled_wmu_is_never_fetched(self):
        # WMU is off the live schedule now; checking it would log a scary
        # "no game matches" warning on every run.
        fetched: list = []
        real_fetch = run_module.fetch

        def recording_fetch(url, **kwargs):
            fetched.append(url)
            return real_fetch(url, **kwargs)

        with mock.patch.object(run_module, "fetch", side_effect=recording_fetch):
            self.assertEqual(run_module.run(make_config()), 0)
        self.assertFalse([u for u in fetched if u.rstrip("/").endswith("727")])

    def test_dry_run_pushes_nothing(self):
        self.pages["734"] = self.pages["734"].replace(
            "<td>$81.65</td>", "<td>$18.00</td>", 1)
        self.assertEqual(run_module.run(make_config(), dry_run=True), 0)
        self.assertEqual(self.pushes, [])


class TestMarkupDrift(RunHarness):
    def test_unparseable_listings_exit_nonzero(self):
        # The schedule still says 29/60 active listings, so parsing zero means
        # our selectors broke -- not that the game sold out.
        for gid in self.pages:
            self.pages[gid] = self.pages[gid].replace("<tr >", '<tr class="renamed">')
        self.assertEqual(run_module.run(make_config()), 1)
        self.assertEqual(self.pushes, [])

    def test_a_genuinely_empty_game_is_not_treated_as_drift(self):
        # Schedule reporting 0 active listings and a page with none agree.
        schedule = fixture("football.html").replace("29 active listings", "0 active listings")
        page = self.pages["727"].replace("<tr >", '<tr class="renamed">')

        def only_wmu(url, **kwargs):
            if url.rstrip("/").endswith("football"):
                return schedule
            return page

        cfg = make_config()
        cfg["targets"] = [{"opponent": "western michigan", "max_price": 45,
                           "enabled": True}]
        with mock.patch.object(run_module, "fetch", side_effect=only_wmu):
            self.assertEqual(run_module.run(cfg), 0)

    def test_schedule_collapse_raises(self):
        with mock.patch.object(run_module, "fetch", return_value="<html>redesigned</html>"):
            with self.assertRaises(run_module.ScrapeError):
                run_module.run(make_config())


class TestExpiry(RunHarness):
    def test_past_games_are_skipped_and_reported_once(self):
        # Every 2026 game is behind us by 2027.
        from datetime import date as real_date
        with mock.patch.object(run_module, "date") as fake_date:
            fake_date.today.return_value = real_date(2027, 1, 1)
            with mock.patch.object(run_module.notify, "notify_season_over",
                                   return_value=True) as season_over:
                self.assertEqual(run_module.run(make_config()), 0)
                season_over.assert_called_once()

                # Second run must stay silent rather than re-announcing.
                self.assertEqual(run_module.run(make_config()), 0)
                season_over.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
