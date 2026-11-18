"""Exchange a top-level Pages form submission for DeepTutor's native session."""

from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Request
import httpx
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse, RedirectResponse

from deeptutor.api.routers.auth import AUTH_COOKIE_NAME
from deeptutor.services import auth
from deeptutor.services.config import load_auth_settings
from deployment.hosted.start import PAGES_URL

ENTRY_ORIGIN = "https://" + urlsplit(PAGES_URL).netloc


async def workspace_login(request: Request):
    settings = load_auth_settings()
    if (
        not auth.AUTH_ENABLED
        or not settings["cookie_secure"]
        or auth.POCKETBASE_ENABLED
        or not auth.AUTH_USERNAME
    ):
        raise HTTPException(503, "Workspace sign-in is unavailable.")
    if request.headers.get("origin") != ENTRY_ORIGIN:
        raise HTTPException(403, "Use the workspace sign-in page.")
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "application/x-www-form-urlencoded"
    ):
        raise HTTPException(415, "Use the workspace sign-in form.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 2048:
            raise HTTPException(413, "Form too large.")
        body.extend(chunk)
    try:
        fields = parse_qs(
            body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=2
        )
    except ValueError as exc:
        raise HTTPException(400, "Invalid sign-in form.") from exc
    passwords = fields.get("password", [])
    languages = fields.get("lang", ["en"])
    if len(passwords) != 1 or len(languages) != 1 or set(fields) - {"lang", "password"}:
        raise HTTPException(400, "Invalid sign-in form.")
    language = "zh" if languages == ["zh"] else "en"
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    password = passwords[0]
    identity = None
    if 0 < len(password.encode("utf-8")) <= 72:
        identity = await run_in_threadpool(auth.authenticate, auth.AUTH_USERNAME, password)
    if identity is None:
        return RedirectResponse(PAGES_URL + "?lang=" + language + "#denied", 303, headers=headers)
    token = auth.create_token(identity.username, identity.role, identity.user_id)
    response = RedirectResponse("/chat?ui_lang=" + language, 303, headers=headers)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=auth.TOKEN_EXPIRE_HOURS * 3600,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


def create_app():
    from deeptutor.api.main import app

    if (
        not auth.AUTH_ENABLED
        or not auth.AUTH_PASSWORD_HASH
        or not load_auth_settings()["cookie_secure"]
    ):
        raise RuntimeError("Hosted DeepTutor requires a password-protected HTTPS session.")
    app.add_api_route(
        "/access/workspace", workspace_login, methods=["POST"], include_in_schema=False
    )
    app.add_api_route("/_hosted/ready", hosted_ready, methods=["GET"], include_in_schema=False)
    return app


async def hosted_ready(request: Request):
    if getattr(request.app.state, "ready", False):
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                web = await client.head("http://127.0.0.1:3782/login")
            if web.status_code == 200:
                return PlainTextResponse("ready")
        except httpx.HTTPError:
            pass
    return PlainTextResponse("starting", status_code=503)
