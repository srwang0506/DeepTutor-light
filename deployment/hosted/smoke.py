"""Exercise the production ingress in CI, including a persisted login after restart."""

import base64
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlencode

HOST = "workspace.example.test"
ENTRY = "https://srwang0506.github.io/tools/deeptutor/"
COOKIE_FILE = Path(os.environ["RUNNER_TEMP"]) / "deeptutor-hosted-test-cookie"


def request(path, headers=None, data=None):
    connection = HTTPConnection("127.0.0.1", 18080, timeout=12)
    try:
        connection.request(
            "POST" if data is not None else "GET",
            path,
            body=data,
            headers={"Host": HOST, **(headers or {})},
        )
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def login(password, origin="https://srwang0506.github.io"):
    return request(
        "/access/workspace",
        {"Content-Type": "application/x-www-form-urlencoded", "Origin": origin},
        urlencode({"password": password, "lang": "zh"}),
    )


def wait_ready():
    for _ in range(100):
        try:
            if request("/health/ready")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(2)
    raise RuntimeError("Hosted API and frontend did not become ready.")


def verify(cookie):
    headers = {"Cookie": cookie}
    status, _, body = request("/chat", {**headers, "Accept": "text/html"})
    assert status == 200 and b"DeepTutor" in body, ("chat", status)
    status, _, body = request("/api/auth/status", headers)
    assert status == 200 and json.loads(body)["authenticated"], ("identity", status)
    status, _, body = request("/api/studio/paper-reviews/options", headers)
    assert status == 200 and json.loads(body)["skill"]["name"] == "best-paper-review", (
        "review",
        status,
    )


def main():
    wait_ready()
    if "--resume" in sys.argv:
        verify(COOKIE_FILE.read_text())
        print("The native session reopened after a container restart.")
        return
    status, headers, _ = request("/chat", {"Accept": "text/html"})
    assert status == 303 and headers["Location"] == ENTRY
    ws = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode(),
        "Origin": "https://" + HOST,
    }
    for path in ("/api/studio/paper-reviews", "/files/outputs/example", "/brand/color-logo.png"):
        assert request(path)[0] == 401, path
    assert request("/ws", ws)[0] == 401
    assert login("unused", "https://other.example")[0] == 403
    status, headers, _ = login("incorrect")
    assert status == 303 and headers["Location"] == ENTRY + "?lang=zh#denied"
    assert not headers["Set-Cookie"]
    status, headers, _ = login(os.environ["TEST_WORKSPACE_PASSWORD"])
    assert status == 303 and headers["Location"] == "/chat", ("sign-in", status)
    token = SimpleCookie(headers["Set-Cookie"])["dt_token"]
    assert token["httponly"] and token["secure"] and token["samesite"].lower() == "lax"
    assert not token["domain"]
    cookie = "dt_token=" + token.value
    verify(cookie)
    assert request("/ws", {**ws, "Cookie": cookie})[0] == 101
    COOKIE_FILE.write_text(cookie)
    COOKIE_FILE.chmod(0o600)
    print("Password, redirect, signed session, chat page and review service verified.")


if __name__ == "__main__":
    main()
