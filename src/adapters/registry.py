"""Which adapters exist and how to construct them. The only place that knows every source module."""

from __future__ import annotations

from collections.abc import Callable

from src.adapters.base import OddsSourceAdapter
from src.adapters.betmgm import BetMGMAdapter
from src.adapters.draftkings import DraftKingsAdapter
from src.adapters.fanduel import FanDuelAdapter
from src.adapters.kalshi import KalshiAdapter
from src.adapters.polymarket import PolymarketAdapter
from src.config import load_yaml, require_env

BUILDERS: dict[str, Callable[[dict], OddsSourceAdapter]] = {
    "fanduel": lambda cfg: FanDuelAdapter(cfg, require_env("FANDUEL_APP_KEY")),
    "kalshi": lambda cfg: KalshiAdapter(cfg),
    "draftkings": lambda cfg: DraftKingsAdapter(cfg),
    "betmgm": lambda cfg: BetMGMAdapter(cfg),  # fed by the browser extension via /ingest/betmgm
    "polymarket": lambda cfg: PolymarketAdapter(cfg),
}


def build_enabled() -> list[OddsSourceAdapter]:
    sources = load_yaml("sources.yaml")["sources"]
    return [BUILDERS[name](cfg) for name, cfg in sources.items() if cfg.get("enabled") and name in BUILDERS]
