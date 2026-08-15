"""Config loading, .env handling, and the public-repo secret guarantee."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maizetix.settings import (  # noqa: E402
    ConfigError, apply_env_overrides, load_config, load_dotenv,
)

MINIMAL = {
    "targets": [{"opponent": "ucla", "max_price": 60}],
    "notify": {"ntfy_base_url": "https://ntfy.sh"},
    "scrape": {"base_url": "https://www.maizetix.com", "user_agent": "test"},
}


class EnvSandbox(unittest.TestCase):
    """Keeps MAIZETIX_* out of the ambient environment during tests."""

    def setUp(self):
        self._saved = {k: v for k, v in os.environ.items() if k.startswith("MAIZETIX_")}
        for k in self._saved:
            del os.environ[k]
        self.addCleanup(self._restore)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _restore(self):
        for k in [k for k in os.environ if k.startswith("MAIZETIX_")]:
            del os.environ[k]
        os.environ.update(self._saved)

    def write_config(self, data=None) -> Path:
        path = self.dir / "config.json"
        path.write_text(json.dumps(data if data is not None else MINIMAL))
        return path


class TestDotenv(EnvSandbox):
    def test_reads_key_values(self):
        (self.dir / ".env").write_text("MAIZETIX_NTFY_TOPIC=from-dotenv\n")
        load_dotenv(self.dir / ".env")
        self.assertEqual(os.environ["MAIZETIX_NTFY_TOPIC"], "from-dotenv")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(load_dotenv(self.dir / "nope.env"), {})

    def test_ignores_comments_and_blanks(self):
        (self.dir / ".env").write_text("# comment\n\nMAIZETIX_NTFY_TOPIC=x\nnot-a-pair\n")
        self.assertEqual(load_dotenv(self.dir / ".env"), {"MAIZETIX_NTFY_TOPIC": "x"})

    def test_strips_surrounding_quotes(self):
        (self.dir / ".env").write_text('MAIZETIX_NTFY_TOPIC="quoted"\n')
        load_dotenv(self.dir / ".env")
        self.assertEqual(os.environ["MAIZETIX_NTFY_TOPIC"], "quoted")

    def test_real_environment_wins_over_dotenv(self):
        # CI secrets must never be clobbered by a .env that got left behind.
        os.environ["MAIZETIX_NTFY_TOPIC"] = "from-ci-secret"
        (self.dir / ".env").write_text("MAIZETIX_NTFY_TOPIC=from-dotenv\n")
        load_dotenv(self.dir / ".env")
        self.assertEqual(os.environ["MAIZETIX_NTFY_TOPIC"], "from-ci-secret")


class TestEnvOverrides(EnvSandbox):
    def test_topic_and_base_url_override(self):
        cfg = apply_env_overrides(
            {"notify": {"ntfy_base_url": "https://ntfy.sh"}},
            {"MAIZETIX_NTFY_TOPIC": "t", "MAIZETIX_NTFY_BASE_URL": "https://self.hosted"},
        )
        self.assertEqual(cfg["notify"]["ntfy_topic"], "t")
        self.assertEqual(cfg["notify"]["ntfy_base_url"], "https://self.hosted")

    def test_absent_env_changes_nothing(self):
        cfg = apply_env_overrides({"notify": {"ntfy_base_url": "https://ntfy.sh"}}, {})
        self.assertNotIn("ntfy_topic", cfg["notify"])


class TestLoadConfig(EnvSandbox):
    def test_topic_comes_from_the_environment(self):
        os.environ["MAIZETIX_NTFY_TOPIC"] = "secret-topic"
        cfg = load_config(self.write_config())
        self.assertEqual(cfg["notify"]["ntfy_topic"], "secret-topic")

    def test_missing_topic_fails_loudly_with_guidance(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.write_config())
        self.assertIn("MAIZETIX_NTFY_TOPIC", str(ctx.exception))

    def test_read_only_modes_do_not_need_the_topic(self):
        cfg = load_config(self.write_config(), require_topic=False)
        self.assertNotIn("ntfy_topic", cfg["notify"])

    def test_missing_section_is_reported(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.write_config({"targets": [], "notify": {}}))
        self.assertIn("scrape", str(ctx.exception))

    def test_bad_json_is_reported(self):
        path = self.dir / "config.json"
        path.write_text("{not json")
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_missing_file_is_reported(self):
        with self.assertRaises(ConfigError):
            load_config(self.dir / "absent.json")


class TestShippedConfigHasNoSecret(unittest.TestCase):
    """This repo is public -- the committed config must never carry the topic."""

    def test_config_json_does_not_contain_a_topic(self):
        cfg = json.loads((ROOT / "config.json").read_text())
        self.assertNotIn("ntfy_topic", cfg["notify"])

    def test_env_file_is_gitignored(self):
        ignored = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn(".env", [line.strip() for line in ignored])

    def test_shipped_config_loads_once_a_topic_is_supplied(self):
        cfg = load_config(ROOT / "config.json", env_file=None, require_topic=False)
        apply_env_overrides(cfg, {"MAIZETIX_NTFY_TOPIC": "t"})
        self.assertEqual(cfg["notify"]["ntfy_topic"], "t")
        self.assertEqual(cfg["notify"]["ntfy_base_url"], "https://ntfy.sh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
