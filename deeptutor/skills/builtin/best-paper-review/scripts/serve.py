"""Local viewer server with save-to-disk.

    python3 serve.py --pdf paper.pdf --review WORK/review.json [--port 8768]
    python3 serve.py --demo                       # bundled synthetic example

Open http://127.0.0.1:8768/ . Listens on 127.0.0.1 only. Saving writes the
review JSON in place after copying the previous version to
<review dir>/.backups/, then runs evidence.py and shows the result.
Without --pdf/--review the viewer starts empty (open files manually).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import write_json  # noqa: E402
from evidence import validate  # noqa: E402
from package_viewer import sources_payload, venue_form  # noqa: E402

VIEWER = HERE.parent / "assets" / "templates" / "pdf_viewer"
MAX_BODY = 30_000_000


class Handler(SimpleHTTPRequestHandler):
    pdf: Path | None = None
    review: Path | None = None
    port: int = 8768

    def log_message(self, fmt, *args):  # quieter console
        if args and "/api/" in str(args[0]):
            sys.stderr.write("[serve] " + fmt % args + "\n")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "forbidden host"}, 403)
        path = self.path.split("?")[0]
        if path == "/api/config":
            return self._json({"canSave": bool(self.review), "pdf": bool(self.pdf), "review": bool(self.review)})
        if path == "/api/pdf" and self.pdf:
            data = self.pdf.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/review" and self.review:
            return self._json(json.loads(self.review.read_text(encoding="utf-8")))
        if path == "/api/sources" and self.review:
            rv = json.loads(self.review.read_text(encoding="utf-8"))
            return self._json({"sources": sources_payload(rv, self.review.parent), "form": venue_form(rv)})
        if path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "forbidden host"}, 403)
        origin = self.headers.get("Origin")
        if origin and not any(origin.startswith(f"http://{h}:{self.port}") for h in ("127.0.0.1", "localhost")):
            return self._json({"error": "cross-origin request refused"}, 403)
        if self.path.split("?")[0] != "/api/review" or not self.review:
            return self._json({"error": "not found"}, 404)
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"error": "expected application/json"}, 415)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._json({"error": "bad body size"}, 413)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 2 or not isinstance(data.get("comments"), list):
                raise ValueError("not a schema v2 review")
        except (ValueError, UnicodeDecodeError) as e:
            return self._json({"ok": False, "error": f"invalid review JSON: {e}"}, 400)
        bdir = self.review.parent / ".backups"
        bdir.mkdir(exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = bdir / f"{self.review.stem}.{stamp}.json"
        if self.review.exists():
            shutil.copy2(self.review, backup)
        write_json(self.review, data)
        try:
            res = validate(data, self.review.parent)
        except Exception as e:  # noqa: BLE001
            res = {"valid": False, "errors": [f"validator crashed: {e}"], "warnings": []}
        return self._json({"ok": True, "backup": str(backup.relative_to(self.review.parent)), "validation": res})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf")
    ap.add_argument("--review")
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        demo = HERE.parent / "examples" / "demo"
        a.pdf, a.review = str(demo / "paper.pdf"), str(demo / "review.json")
    Handler.pdf = Path(a.pdf).resolve() if a.pdf else None
    Handler.review = Path(a.review).resolve() if a.review else None
    Handler.port = a.port
    for p in (Handler.pdf, Handler.review):
        if p and not p.exists():
            print(f"not found: {p}", file=sys.stderr)
            return 2
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=str(VIEWER)))
    print(f"查看器： http://127.0.0.1:{a.port}/   （Ctrl+C 结束）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
