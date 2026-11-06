import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from deeptutor.api.local_access import LocalAccessMiddleware, browser_token, capability_cookie_name


@pytest.mark.asyncio
async def test_local_capability_applies_to_http_without_trusting_loopback():
    async def endpoint(_):
        return JSONResponse({"sensitive": True})

    app = LocalAccessMiddleware(
        Starlette(routes=[Route("/secret", endpoint), Route("/health", endpoint)]),
        enabled=True,
        capability="test-local-capability",
        origins=["http://127.0.0.1:3782"],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8001"
    ) as client:
        assert (await client.get("/secret")).status_code == 403
        assert (await client.get("/health")).status_code == 200
        headers = {
            "x-deeptutor-local-capability": "test-local-capability",
            "origin": "http://127.0.0.1:3782",
        }
        assert (await client.get("/secret", headers=headers)).status_code == 200
        assert (
            await client.get("/secret", headers={**headers, "origin": "https://attacker.example"})
        ).status_code == 403
        assert (
            await client.get("/secret", headers={**headers, "host": "attacker.example"})
        ).status_code == 403
        assert (
            await client.get(
                "/secret",
                headers={
                    "cookie": capability_cookie_name("test-local-capability")
                    + "="
                    + browser_token("test-local-capability"),
                    "origin": "http://127.0.0.1:3782",
                },
            )
        ).status_code == 200


@pytest.mark.asyncio
async def test_websocket_uses_same_origin_and_capability_boundary():
    accepted = []

    async def endpoint(scope, receive, send):
        accepted.append(True)

    app = LocalAccessMiddleware(
        endpoint, enabled=True, capability="test", origins=["http://localhost:3782"]
    )
    output = []

    async def send(message):
        output.append(message)

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [
            (b"host", b"localhost:8001"),
            (b"origin", b"https://attacker.example"),
            (b"cookie", (capability_cookie_name("test") + "=" + browser_token("test")).encode()),
        ],
    }
    await app(scope, None, send)
    assert output == [{"type": "websocket.close", "code": 4403}]
    scope["headers"][1] = (b"origin", b"http://localhost:3782")
    await app(scope, None, send)
    assert accepted == [True]
