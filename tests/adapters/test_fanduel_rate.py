"""The adapter must never send requests closer together than `request_spacing_seconds` (no live calls)."""

import time

import httpx

from src.adapters.fanduel import FanDuelAdapter

CFG = {
    "region": "pa",
    "endpoints": {"event_search": "https://x.test/s", "event_page": "https://x.test/p", "market_prices": "https://x.test/m"},
    "request_spacing_seconds": 0.2,
}


async def test_requests_are_spaced_out():
    sent: list[float] = []

    def handler(request):
        sent.append(time.monotonic())
        return httpx.Response(200, json=[])

    adapter = FanDuelAdapter(CFG, "key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for _ in range(4):
        await adapter._request("POST", CFG["endpoints"]["market_prices"], json={"marketIds": []})
    gaps = [b - a for a, b in zip(sent, sent[1:])]
    assert len(sent) == 4 and adapter.request_count == 4
    assert min(gaps) >= 0.19
    await adapter.aclose()


async def test_blocked_status_raises_instead_of_retrying_fast():
    from src.adapters.fanduel import SourceBlocked

    adapter = FanDuelAdapter({**CFG, "request_spacing_seconds": 0},
                             "key", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    try:
        await adapter._request("GET", CFG["endpoints"]["event_page"])
        raise AssertionError("expected SourceBlocked")
    except SourceBlocked:
        pass
    await adapter.aclose()
