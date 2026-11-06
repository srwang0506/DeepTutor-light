"""Local app capability, shared by HTTP and WebSocket before domain authorization.

A loopback address is not an identity. Launchers pass an ephemeral capability to
both API and UI processes; it never enters public configuration or JS bundles.
"""

import hashlib
import hmac
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

COOKIE = "deeptutor_local_session"
HEADER = b"x-deeptutor-local-capability"


def capability_cookie_name(capability):
    # Cookies are host-scoped, not port-scoped. Concurrent local instances must
    # not overwrite one another's WebSocket session.
    return COOKIE + "_" + hashlib.sha256(capability.encode()).hexdigest()[:12]


class LocalAccessMiddleware:
    def __init__(self, app, *, enabled, capability, origins):
        self.app = app
        self.enabled = enabled
        self.capability = capability
        self.origins = set(origins)

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {k.lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origin = headers.get(b"origin")
        try:
            host = urlsplit("http://" + headers.get(b"host", "")).hostname
        except ValueError:
            host = None
        allowed = host in {"localhost", "127.0.0.1", "::1"} and (
            origin is None or origin in self.origins
        )
        path = scope.get("path", "")
        public = (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and path in {"/health", "/health/live", "/health/ready", "/api/health"}
        )
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get(b"cookie", ""))
        except Exception:
            pass
        name = capability_cookie_name(self.capability or "")
        token = headers.get(HEADER, "") or (cookie[name].value if name in cookie else "")
        authenticated = bool(
            self.capability
            and token
            and hmac.compare_digest(token.encode(), self.capability.encode())
        )
        if allowed and (
            authenticated or public or (scope.get("method") == "OPTIONS" and origin in self.origins)
        ):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        detail = "Open the local workbench to establish an app session."
        code = 403
        if allowed and not self.capability:
            detail = "Local app capability is missing. Start the API and UI together with deeptutor start or scripts/study.py."
            code = 503
        await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)
