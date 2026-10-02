"""Loads config/*.yaml and the git-ignored .env file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f) or {}


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal KEY=VALUE loader. Real environment variables win over .env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def source_config(name: str) -> dict:
    return load_yaml("sources.yaml")["sources"][name]


def require_env(key: str) -> str:
    load_dotenv()
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f"Missing {key}. Set it in .env (see .env.example).")
    return value
