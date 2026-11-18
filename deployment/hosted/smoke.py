"""Exercise the production ingress and recover a saved note on a fresh container."""

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
STATE_FILE = Path(os.environ["RUNNER_TEMP"]) / "deeptutor-hosted-test-session.json"


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


def notebook_api(cookie, path, payload=None):
    headers = {"Cookie": cookie, "Origin": "https://" + HOST}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload)
    status, _, body = request("/api/notebooks" + path, headers, data)
    assert status == 200, ("notebook", path, status)
    return json.loads(body)


def main():
    wait_ready()
    if "--resume" in sys.argv:
        state = json.loads(STATE_FILE.read_text())
        verify(state["cookie"])
        notebook = notebook_api(state["cookie"], "/" + state["notebook_id"])
        assert any(
            record["id"] == state["record_id"] and record["output"] == state["text"]
            for record in notebook["records"]
        ), "Saved note missing after replacing the application container."
        print("The session and saved study note reopened on a fresh container using the same disk.")
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
    notebook = notebook_api(cookie, "", {"name": "Hosted study check"})["notebook"]
    note = "Depth-first search reading note " + secrets.token_hex(8)
    record = notebook_api(
        cookie,
        "/actions/add-record",
        {
            "notebook_ids": [notebook["id"]],
            "record_type": "reading",
            "title": "Depth-first search",
            "summary": "The traversal keeps a stack of unfinished vertices.",
            "user_query": "How does depth-first search work?",
            "output": note,
        },
    )["record"]
    state = {
        "cookie": cookie,
        "notebook_id": notebook["id"],
        "record_id": record["id"],
        "text": note,
    }
    with open(STATE_FILE, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
        json.dump(state, handle)
    print("Password, redirect, signed session, chat page and review service verified.")


if __name__ == "__main__":
    main()
