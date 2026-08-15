"""Load config.json, then let the environment override the secret bits.

This repo is public, so the ntfy topic must NOT live in config.json -- the topic
name is the only thing protecting the feed, and anyone who reads it can watch
your alerts or spam your phone. It comes from the environment instead:

  locally      a gitignored .env file next to this repo
  CI           the MAIZETIX_NTFY_TOPIC GitHub Actions secret

Everything else (which games, what thresholds) is public and stays in config.json
so it's diffable and reviewable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_PREFIX = "MAIZETIX_"


class ConfigError(RuntimeError):
    pass


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Read a minimal KEY=VALUE .env file. Absent file is fine, not an error.

    Deliberately tiny -- no python-dotenv dependency, no interpolation, no
    multiline values. Existing environment variables always win, so CI secrets
    are never clobbered by a stray .env.
    """
    path = Path(path)
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def apply_env_overrides(cfg: dict, env: dict | None = None) -> dict:
    """Overlay MAIZETIX_* environment variables onto the loaded config."""
    env = os.environ if env is None else env
    notify = cfg.setdefault("notify", {})

    if topic := env.get(f"{ENV_PREFIX}NTFY_TOPIC"):
        notify["ntfy_topic"] = topic
    if base := env.get(f"{ENV_PREFIX}NTFY_BASE_URL"):
        notify["ntfy_base_url"] = base
    return cfg


def load_config(path: str | Path, *, env_file: str | Path | None = None,
                require_topic: bool = True) -> dict:
    """Load config.json, apply .env then environment overrides, and validate."""
    path = Path(path)
    try:
        cfg = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no config file at {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}")

    for key in ("targets", "notify", "scrape"):
        if key not in cfg:
            raise ConfigError(f"{path} is missing the '{key}' section")

    if env_file is not None:
        load_dotenv(env_file)
    cfg = apply_env_overrides(cfg)

    if require_topic and not cfg["notify"].get("ntfy_topic"):
        raise ConfigError(
            "no ntfy topic configured. Set MAIZETIX_NTFY_TOPIC -- locally in a "
            ".env file next to config.json, or in CI as a GitHub Actions secret. "
            "It is kept out of config.json on purpose because this repo is public."
        )

    cfg["notify"].setdefault("ntfy_base_url", "https://ntfy.sh")
    return cfg
