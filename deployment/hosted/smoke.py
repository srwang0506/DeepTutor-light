"""Exercise the production ingress and recover a saved note on a fresh container."""

import base64
from http.client import HTTPConnection, HTTPSConnection
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlencode

REMOTE_HOST = os.environ.get("DEEPTUTOR_SMOKE_HOST")
HOST = REMOTE_HOST or "personal-research-workspace-production.hosting.example.test"
ORIGIN = "https://" + HOST if REMOTE_HOST else "http://127.0.0.1:18080"
ENTRY = "https://srwang0506.github.io/tools/deeptutor/"
STATE_FILE = Path(os.environ["RUNNER_TEMP"]) / "deeptutor-hosted-test-session.json"


def request(path, headers=None, data=None):
    connection = (
        HTTPSConnection(HOST, timeout=40)
        if REMOTE_HOST
        else HTTPConnection("127.0.0.1", 18080, timeout=12)
    )
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
    status, image_headers, image_body = request(
        "/_next/image?url=%2Fbrand%2Fcolor-logo.png&w=64&q=75",
        {**headers, "Accept": "image/webp"},
    )
    assert status == 200 and image_headers["Content-Type"].startswith("image/") and image_body, (
        "workspace logo",
        status,
    )
    status, _, body = request("/api/auth/status", headers)
    assert status == 200 and json.loads(body)["authenticated"], ("identity", status)
    status, _, body = request("/api/settings/ui", headers)
    assert status == 200 and json.loads(body)["language"] == "zh", ("settings language", status)
    status, _, body = request("/api/studio/paper-reviews/options", headers)
    assert status == 200 and json.loads(body)["skill"]["name"] == "best-paper-review", (
        "review",
        status,
    )


def json_api(cookie, path, payload=None, workspace=None):
    headers = {"Cookie": cookie, "Origin": "https://" + HOST}
    if workspace:
        headers["X-DeepTutor-Workspace"] = workspace
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload)
    status, _, body = request(path, headers, data)
    assert status in {200, 201}, (path, status)
    return json.loads(body)


def review_fixture(cookie):
    workspace = json_api(cookie, "/api/settings/workspace/registrations", {"name": "Paper study"})[
        "workspace"
    ]["workspace_id"]
    base = "/api/studio"
    edition = json_api(
        cookie,
        base + "/library/collect",
        {
            "request_id": secrets.token_hex(12),
            "work": {"title": "Review fixture"},
            "edition": {"work_id": ""},
        },
        workspace,
    )
    original = base + "/editions/" + edition["id"]
    fixture = (
        Path(__file__).resolve().parents[2]
        / "deeptutor/skills/builtin/best-paper-review/examples/demo/paper.pdf"
    )
    upload = subprocess.run(
        [
            "curl",
            *([] if REMOTE_HOST else ["--noproxy", "*"]),
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "--header",
            "Host: " + HOST,
            "--header",
            "Origin: https://" + HOST,
            "--header",
            "Cookie: " + cookie,
            "--header",
            "X-DeepTutor-Workspace: " + workspace,
            "--form-string",
            "expected_revision=" + str(edition["revision"]),
            "--form",
            f"file=@{fixture};type=application/pdf",
            ORIGIN + original + "/pdf",
        ],
        stdout=subprocess.PIPE,
    )
    assert upload.returncode == 0, "Could not attach the sample manuscript."
    attached = json.loads(upload.stdout)
    digest = attached["data"]["files"][0]["hash"]
    page = json_api(cookie, original + "/files/" + digest + "/pages/1", workspace=workspace)
    assert page["text"].strip() and page["blob_hash"] == digest
    review = json_api(
        cookie,
        base + "/paper-reviews",
        {
            "request_id": secrets.token_hex(12),
            "edition_id": edition["id"],
            "revision": attached["revision"],
            "blob_hash": digest,
            "language": "zh",
        },
        workspace,
    )
    choices = json_api(cookie, base + "/writing/engines", workspace=workspace)
    assert "tectonic" in choices["engines"]
    manuscript = json_api(cookie, base + "/manuscripts", {"title": "Writing fixture", "engine": "tectonic"}, workspace)
    writer = base + "/manuscripts/" + manuscript["id"]
    index = json_api(cookie, writer + "/editor", workspace=workspace)
    assert index["main"] == "main.tex" and index["sources"]["main.tex"].strip()
    version = json_api(cookie, writer + "/versions", {"title": "First draft"}, workspace)
    assert version["created"] and version["commit"]
    build = json_api(cookie, writer + "/compile", {}, workspace)
    for _ in range(100):
        build = json_api(cookie, base + "/record/" + build["id"], workspace=workspace)
        if build["data"]["status"] not in {"queued", "running"}:
            break
        time.sleep(1)
    if build["data"]["status"] != "succeeded":
        status, _, body = request(base + "/builds/" + build["id"] + "/build.log",
                                  {"Cookie": cookie, "X-DeepTutor-Workspace": workspace})
        # This run creates its own public fixture. Log only the TeX job's output.
        detail = body[-1500:].decode(errors="replace") if status == 200 else str(status)
        raise AssertionError("Portable manuscript compilation failed: " + detail)
    assert "preview.pdf" in build["data"]["outputs"]
    return {"id": review["id"], "workspace": workspace, "original": original + "/files/" + digest,
            "writer": writer, "version": version["commit"]}


def main():
    wait_ready()
    status, ready_headers, body = request(
        "/health/ready", {"Origin": "https://srwang0506.github.io"}
    )
    assert status == 200 and body == b"ready"
    assert ready_headers["Access-Control-Allow-Origin"] == "https://srwang0506.github.io"
    if "--resume" in sys.argv:
        state = json.loads(STATE_FILE.read_text())
        verify(state["cookie"])
        notebook = json_api(state["cookie"], "/api/notebooks/" + state["notebook_id"])
        assert any(
            record["id"] == state["record_id"] and record["output"] == state["text"]
            for record in notebook["records"]
        ), "Saved note missing after replacing the application container."
        paper = state["review"]
        review = json_api(
            state["cookie"],
            "/api/studio/paper-reviews/" + paper["id"],
            workspace=paper["workspace"],
        )
        page = json_api(
            state["cookie"], paper["original"] + "/pages/1", workspace=paper["workspace"]
        )
        assert review["id"] == paper["id"] and page["text"].strip()
        versions = json_api(state["cookie"], paper["writer"] + "/versions", workspace=paper["workspace"])
        assert any(version["commit"] == paper["version"] for version in versions)
        print(
            "The session, saved study note, original PDF and review reopened on a fresh container."
        )
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
    assert status == 303 and headers["Location"] == "/chat?ui_lang=zh", ("sign-in", status)
    token = SimpleCookie(headers["Set-Cookie"])["dt_token"]
    assert token["httponly"] and token["secure"] and token["samesite"].lower() == "lax"
    assert not token["domain"]
    cookie = "dt_token=" + token.value
    verify(cookie)
    assert request("/ws", {**ws, "Cookie": cookie})[0] == 101
    notebook = json_api(cookie, "/api/notebooks", {"name": "Hosted study check"})["notebook"]
    note = "Depth-first search reading note " + secrets.token_hex(8)
    record = json_api(
        cookie,
        "/api/notebooks/actions/add-record",
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
        "review": review_fixture(cookie),
    }
    with open(STATE_FILE, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
        json.dump(state, handle)
    print("Password, redirect, signed session, chat page and review service verified.")


if __name__ == "__main__":
    main()
