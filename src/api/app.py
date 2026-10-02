"""Local odds screen. Binds to 127.0.0.1 only (this machine).

    .venv/bin/python -m src.api.app          # then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from src.adapters.registry import build_enabled
from src.config import load_yaml
from src.pricing.devig import get_method
from src.scanner.board import build_board
from src.scanner.engine import Scanner

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scanner = Scanner(build_enabled())
    app.state.scanner = scanner
    app.state.scanner_cfg = load_yaml("scanner.yaml")
    app.state.sources_cfg = load_yaml("sources.yaml")["sources"]
    task = asyncio.create_task(scanner.run())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await scanner.aclose()


app = FastAPI(title="EVC Odds", lifespan=lifespan)


def source_status(scanner: Scanner, sources_cfg: dict) -> list[dict]:
    by_name = {a.name: a for a in scanner.adapters}
    counts: dict[str, int] = {}
    for q in scanner.store.all():
        counts[q.source] = counts.get(q.source, 0) + 1
    out = []
    for name, cfg in sources_cfg.items():
        a = by_name.get(name)
        last = getattr(a, "last_success", None) if a else None
        out.append({
            "name": name,
            "label": cfg.get("label", name),
            "enabled": a is not None,
            "health": str(a.health) if a else "NOT_BUILT",
            "last_success_age": round(time.time() - last, 1) if last else None,
            "quotes": counts.get(name, 0),
            "restarts": scanner.restarts.get(name, 0),
            "requests_sent": getattr(a, "request_count", None) if a else None,
            "candidate_eligible": cfg.get("candidate_eligible", True),
        })
    return out


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/board")
def board() -> dict:
    s: Scanner = app.state.scanner
    cfg = app.state.scanner_cfg
    return build_board(
        quotes=s.store.all(),
        events=list(s.normalizer.events.values()),
        sources=source_status(s, app.state.sources_cfg),
        teams=s.normalizer.teams,
        devig=get_method(cfg["consensus"]["devig_method"]),
        stale_seconds=cfg["freshness"]["stale_seconds"],
        changed_at=s.changed_at,
        slot_of=s.store.slot,
    )


@app.get("/api/health")
def health() -> dict:
    s: Scanner = app.state.scanner
    return {"sources": source_status(s, app.state.sources_cfg), "normalization_failures": dict(s.normalizer.failures)}


if __name__ == "__main__":
    import argparse
    import socket

    import uvicorn

    parser = argparse.ArgumentParser(description="EVC odds screen (local only)")
    parser.add_argument("--port", type=int, default=8000)
    port = parser.parse_args().port

    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(
                f"Port {port} is already in use — the odds screen is probably already running.\n"
                f"Open http://127.0.0.1:{port}, stop the other copy (Ctrl+C in its terminal), "
                f"or use another port: .venv/bin/python -m src.api.app --port {port + 1}"
            )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(f"EVC Odds running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
