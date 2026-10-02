"""Local odds screen. Binds to 127.0.0.1 only (this machine).

    .venv/bin/python -m src.api.app          # then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

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
    app.state.ingest_rejections = Counter()
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
            "payloads_received": getattr(a, "payloads_received", None) if a else None,  # browser-fed sources
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
        stale_by_source={n: c["stale_seconds"] for n, c in app.state.sources_cfg.items()
                         if isinstance(c, dict) and "stale_seconds" in c},
    )


TAP_PATHS = ("/cds-api/bettingoffer/fixtures", "/cds-api/bettingoffer/fixture-view")


@app.post("/ingest/betmgm")
async def ingest_betmgm(request: Request) -> JSONResponse:
    """Receives BetMGM responses copied by the passive browser extension (browser_ext/betmgm_tap/).

    Guards: localhost only; a custom header (web pages can't send it to us without a CORS preflight, which
    this server never approves, so only the extension's background worker can post); size cap; known paths.
    """
    def reject(reason: str, status: int) -> JSONResponse:
        app.state.ingest_rejections[reason] += 1
        log.warning("ingest/betmgm rejected: %s", reason)
        return JSONResponse({"error": reason}, status_code=status)

    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        return reject("localhost only", 403)
    if request.headers.get("x-evc-tap") != "betmgm":
        return reject("missing x-evc-tap header", 403)
    adapter = next((a for a in app.state.scanner.adapters if a.name == "betmgm"), None)
    if adapter is None:
        return reject("betmgm source not enabled", 404)
    limit = app.state.sources_cfg["betmgm"].get("max_ingest_bytes", 40_000_000)
    body = await request.body()
    if len(body) > limit:
        return reject(f"payload too large ({len(body)} bytes)", 413)
    try:
        payload = json.loads(body)
        path, data = payload["path"], payload["data"]
    except (ValueError, KeyError, TypeError):
        return reject("expected {path, data} JSON", 400)
    if not isinstance(path, str) or not path.startswith(TAP_PATHS) or not isinstance(data, dict):
        return reject("unexpected path", 400)
    adapter.submit(path.split("?")[0], data)
    log.info("ingest/betmgm: %s (%d KB)", path, len(body) // 1024)
    return JSONResponse({"ok": True})


@app.get("/api/health")
def health() -> dict:
    s: Scanner = app.state.scanner
    return {"sources": source_status(s, app.state.sources_cfg), "normalization_failures": dict(s.normalizer.failures),
            "betmgm_ingest_rejections": dict(app.state.ingest_rejections)}


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
