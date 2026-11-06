#!/usr/bin/env python3
"""Single-host learning/research workbench launcher (Python 3.12, Node 24)."""

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def start_local_embedding(processes):
    """Use an existing Ollama server or own the bundled local child process."""
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            if response.status == 200:
                return
    except Exception:
        pass
    bundle = ROOT / ".local-runtime/ollama"
    binary = bundle / "ollama"
    if not binary.is_file():
        raise RuntimeError(
            "Local Ollama bundle missing. Install/start Ollama, or omit --local-embedding."
        )
    child = subprocess.Popen(
        [str(binary), "serve"],
        cwd=bundle,
        env={
            **os.environ,
            "OLLAMA_HOST": "127.0.0.1:11434",
            "OLLAMA_MODELS": str(bundle / "models"),
        },
    )
    processes.append(child)
    for _ in range(60):
        if child.poll() is not None:
            raise RuntimeError("Local Ollama failed to start")
        try:
            with urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Local Ollama readiness check timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "doctor"])
    parser.add_argument("--home", type=Path, default=ROOT / "study-data")
    parser.add_argument(
        "--codex-login",
        action="store_true",
        help="Use the current local Codex login, without copying credentials",
    )
    parser.add_argument("--dev", action="store_true")
    parser.add_argument(
        "--local-embedding", action="store_true", help="Start the bundled Ollama server when needed"
    )
    parser.add_argument("--port", type=int, default=3782)
    args = parser.parse_args()
    os.environ["DEEPTUTOR_HOME"] = str(args.home.resolve())
    os.environ["DEEPTUTOR_STUDY_RUNTIME"] = "1"
    os.environ["BACKEND_PORT"] = "8001"
    os.environ["DEEPTUTOR_LOCAL_CAPABILITY"] = secrets.token_urlsafe(32)
    os.environ["DEEPTUTOR_LOCAL_ORIGINS"] = (
        f"http://127.0.0.1:{args.port},http://localhost:{args.port}"
    )
    os.environ["NEXT_PUBLIC_API_BASE"] = "http://127.0.0.1:8001"
    os.environ["DEEPTUTOR_API_BASE_URL"] = "http://127.0.0.1:8001"
    for key in ("NO_PROXY", "no_proxy"):
        entries = [part for part in os.environ.get(key, "").split(",") if part]
        os.environ[key] = ",".join(dict.fromkeys([*entries, "localhost", "127.0.0.1"]))
    if args.codex_login:
        os.environ["DEEPTUTOR_USE_CODEX_LOGIN"] = "1"
    print("Runtime:", args.home.resolve(), flush=True)
    for module in ["uvicorn", "fastapi", "llama_index", "bm25s", "pymupdf", "PIL"]:
        if importlib.util.find_spec(module) is None:
            raise SystemExit(f"Missing {module}; install requirements/study.lock.txt first.")
    npm = shutil.which("npm")
    # Discover an installed TeX toolchain for native manuscript previews.
    tex_paths = [Path("/Library/TeX/texbin"), Path.home() / "Library/TinyTeX/bin/universal-darwin"]
    existing_tex = [str(p) for p in tex_paths if p.is_dir()]
    if existing_tex:
        os.environ["PATH"] = os.pathsep.join([*existing_tex, os.environ.get("PATH", "")])
    bundled_node = ROOT / ".local-runtime/node"
    node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
    if not node or (args.dev and not npm):
        raise SystemExit("Node.js 24 is required; npm is additionally needed for --dev.")
    if args.command == "doctor":
        from deeptutor.services.config.runtime_settings import load_document_parsing_settings
        from deeptutor.services.embedding.config import get_embedding_config
        from deeptutor.services.rag.pipelines.llamaindex.retrievers import BM25_OPTIONS

        print("Parser:", load_document_parsing_settings().get("engine", "configured"))
        print("Lexical retrieval:", BM25_OPTIONS)
        try:
            config = get_embedding_config()
            print(
                "Embedding configured:",
                config.model,
                "(use Settings readiness to verify connectivity)",
            )
        except Exception:
            print(
                "Embedding: not configured; document reading remains available. Configure a local Ollama embedding for hybrid RAG."
            )
        print("Modules and imports: OK")
        return
    if args.codex_login:
        from deeptutor.services.codex_auth import get_codex_oauth_service

        try:
            asyncio.run(get_codex_oauth_service().refresh_models())
        except Exception:
            raise SystemExit(
                "Could not refresh the Codex model catalog. Sign in through Codex and check the network."
            )
    if not args.dev:
        os.environ["DEEPTUTOR_NEXT_DIST_DIR"] = ".next-study"
        if not (ROOT / "web/.next-study/BUILD_ID").exists():
            raise SystemExit(
                "Build first: DEEPTUTOR_NEXT_DIST_DIR=.next-study NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npm --prefix web run build"
            )
    processes = []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        if args.local_embedding:
            start_local_embedding(processes)
        backend_index = len(processes)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "deeptutor.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                    "--timeout-graceful-shutdown",
                    "5",
                ],
                cwd=ROOT,
            )
        )
        for _ in range(120):
            if processes[backend_index].poll() is not None:
                raise RuntimeError("Backend failed to start")
            try:
                with urlopen("http://127.0.0.1:8001/health/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Backend readiness check timed out")
        frontend_command = (
            [
                npm,
                "run",
                "dev",
                "--",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(args.port),
            ]
            if args.dev
            else [node, str(ROOT / "web/.next-study/standalone/server.js")]
        )
        processes.append(
            subprocess.Popen(
                frontend_command,
                cwd=ROOT / "web",
                env={**os.environ, "HOSTNAME": "127.0.0.1", "PORT": str(args.port)},
            )
        )
        print(f"Open http://127.0.0.1:{args.port}/today", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
