"""Small process-local admission limits for the local MVP runtime."""

from collections import OrderedDict
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RuntimeLimitsMiddleware:
    def __init__(self, app: ASGIApp, *, max_concurrent: int = 8, max_requests: int = 120) -> None:
        self.app = app
        self.max_concurrent, self.max_requests = max_concurrent, max_requests
        self.active = 0
        self.windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        limited = scope["type"] == "http" and (path == "/chat" or path.startswith("/api/v1/tasks"))
        if not limited:
            await self.app(scope, receive, send)
            return
        peer = (scope.get("client") or ("unknown", 0))[0]
        now = monotonic()
        started, count = self.windows.get(peer, (now, 0))
        if now - started >= 60:
            started, count = now, 0
        self.windows[peer] = (started, count + 1)
        self.windows.move_to_end(peer)
        if len(self.windows) > 1024:
            self.windows.popitem(last=False)
        if count >= self.max_requests or self.active >= self.max_concurrent:
            await JSONResponse(
                {"detail": "request_budget_exhausted"},
                status_code=429,
                headers={"Retry-After": "1"},
            )(scope, receive, send)
            return
        self.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.active -= 1
