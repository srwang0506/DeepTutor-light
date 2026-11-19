#!/usr/bin/env python3
"""Execute the pinned Tectonic WASI reactor on a private, read-only paper snapshot."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import tempfile
import threading
import time

import wasmtime

MODULE_SHA256 = "fd7b19842b2d465149051f091f2e80fdec08f200e4ff90bf0ac169e8ab207b23"


def engine():
    config = wasmtime.Config()
    arch = {"arm64": "aarch64", "AMD64": "x86_64"}.get(platform.machine(), platform.machine())
    target = {"linux": "unknown-linux-gnu", "darwin": "apple-darwin"}.get(sys.platform)
    if target:
        config.target = arch + "-" + target
    config.parallel_compilation = False
    config.epoch_interruption = True
    return wasmtime.Engine(config)


def trusted_module(runtime, package):
    bytecode = (package / "engine.wasm").read_bytes()
    if hashlib.sha256(bytecode).hexdigest() != MODULE_SHA256:
        raise ValueError("Unexpected Tectonic engine checksum.")
    return wasmtime.Module(runtime, bytecode)


def compile_paper(bundle, main):
    name = PurePosixPath(main)
    if name.is_absolute() or ".." in name.parts or name.suffix.lower() != ".tex":
        raise ValueError("Expected a relative main TeX file.")
    runtime = engine()
    # The AOT module is installed with the application, outside the paper's tree.
    module = wasmtime.Module.deserialize_file(runtime, str(bundle.parent / "engine.cwasm"))
    with tempfile.TemporaryDirectory(prefix="paper-guest-") as tmp:
        scratch = Path(tmp)
        wasi = wasmtime.WasiConfig()
        for folder in ("output", "cache", "fonts"):
            target = scratch / folder
            target.mkdir()
            wasi.preopen_dir(str(target), "/" + folder, folder != "fonts")
        wasi.preopen_dir(str(Path.cwd()), "/input", False)
        wasi.preopen_dir(str(bundle), "/bundle", False)
        wasi.env = [
            ("TECTONIC_FONT_DIR", "/fonts"),
            ("TECTONIC_CACHE_DIR", "/cache"),
            ("SOURCE_DATE_EPOCH", str(int(os.environ.get("SOURCE_DATE_EPOCH", time.time())))),
        ]
        wasi.inherit_stderr()
        with wasmtime.Store(runtime) as store:
            store.set_wasi(wasi)
            store.set_limits(memory_size=384 * 1024 * 1024)
            store.set_epoch_deadline(1)
            linker = wasmtime.Linker(runtime)
            linker.define_wasi()
            exports = linker.instantiate(store, module).exports(store)
            if "_initialize" in exports:
                exports["_initialize"](store)
            if exports["tectonic_abi_version"](store) != 2:
                raise ValueError("Incompatible Tectonic engine.")
            values = []
            for item in ("/input/" + main, "/output", "/bundle"):
                data = item.encode()
                ptr = exports["malloc"](store, len(data))
                exports["memory"].write(store, data, ptr)
                values.extend((ptr, len(data)))
            timer = threading.Timer(85, runtime.increment_epoch)
            timer.start()
            try:
                code = exports["tectonic_compile"](store, *values)
            finally:
                timer.cancel()
                if (log := scratch / "output" / (name.stem + ".log")).is_file():
                    with log.open("rb") as handle:
                        handle.seek(max(0, log.stat().st_size - 12000))
                        sys.stderr.buffer.write(handle.read())
            if code:
                raise ValueError("Tectonic could not compile this paper.")
        pdf = scratch / "output" / (name.stem + ".pdf")
        if not pdf.is_file() or not 0 < pdf.stat().st_size <= 50 * 1024 * 1024:
            raise ValueError("Tectonic did not produce a usable PDF.")
        shutil.copyfile(pdf, Path.cwd() / pdf.name)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Expected an installed bundle and a relative main TeX file.")
    if sys.argv[1] == "--prepare":
        root = Path(sys.argv[2]).resolve()
        (root / "engine.cwasm").write_bytes(trusted_module(engine(), root).serialize())
    else:
        try:
            compile_paper(Path(sys.argv[1]).resolve(), sys.argv[2])
        except (ValueError, OSError, wasmtime.WasmtimeError, wasmtime.Trap) as exc:
            raise SystemExit(str(exc)) from None
