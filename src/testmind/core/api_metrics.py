"""Bounded process-local API counters; no identifiers or payloads."""

import time
from collections import Counter
from typing import Any, ClassVar


class ApiMetricsMiddleware:
    counters: ClassVar[Counter[str]] = Counter()
    durations: ClassVar[list[float]] = []

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500

        async def measured(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, measured)
        finally:
            self.counters[f"{status // 100}xx"] += 1
            self.durations.append(time.monotonic() - started)
            del self.durations[:-1000]

    @classmethod
    def snapshot(cls) -> dict[str, Any]:
        values = sorted(cls.durations)
        return {"status_classes": dict(cls.counters), "sample_count": len(values),
                "latency_seconds": {key: values[min(len(values)-1, int(len(values)*percentile))] if values else None
                    for key, percentile in (("p50", .5), ("p95", .95), ("p99", .99))},
                "scope": "api_process_latest_1000_including_sse"}
