"""Bounded SyncTeX v1 navigation over a frozen build, without executing source."""

import gzip
import io
from pathlib import PurePosixPath
import re

import fitz

from .manuscripts import read_file


def points(store, build_id):
    build = store.get(build_id, "build")
    data = build["data"]
    if data["status"] != "succeeded" or "mapping.synctex.gz" not in data.get("outputs", {}):
        raise ValueError("This build has no usable SyncTeX mapping.")
    with gzip.GzipFile(
        fileobj=io.BytesIO(store.blob_path(data["outputs"]["mapping.synctex.gz"]).read_bytes())
    ) as archive:
        raw = archive.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError("Source map exceeds the supported size.")
    text = raw.decode("utf-8")
    if not text.startswith("SyncTeX Version:1\n"):
        raise ValueError("Unsupported source map version.")
    tags = {
        int(tag): str(PurePosixPath(path))
        for tag, path in re.findall(r"^Input:(\d+):(.+)$", text, re.M)
    }
    main = data["main_file"]
    base = data.get("source_root")
    if not base:
        candidates = {p[: -len(main)].rstrip("/") for p in tags.values() if p.endswith("/" + main)}
        if len(candidates) != 1:
            raise ValueError("Source map root is ambiguous; rebuild this manuscript.")
        base = candidates.pop()
    inputs = {
        tag: path[len(base) + 1 :]
        for tag, path in tags.items()
        if path.startswith(base + "/") and path[len(base) + 1 :] in data["input_files"]
    }

    def header(name, default):
        found = re.search(rf"^{name}:(-?\d+)$", text, re.M)
        return int(found[1]) if found else default

    unit, mag = header("Unit", 1), header("Magnification", 1000)
    if not 0 < unit <= 1000 or not 0 < mag <= 100000:
        raise ValueError("Invalid source map scaling.")
    scale = unit * mag / 1000 / 65536 * 72 / 72.27
    x_offset, y_offset = header("X Offset", 0) * scale, header("Y Offset", 0) * scale
    with fitz.open(store.blob_path(data["outputs"]["preview.pdf"])) as pdf:
        sizes = {i + 1: (p.rect.width, p.rect.height) for i, p in enumerate(pdf)}
    found, page = [], None
    for line in text.splitlines():
        if re.fullmatch(r"\{\d+", line):
            page = int(line[1:])
        node = re.match(r"[xghr](\d+),(\d+)(?:,\d+)?:(-?\d+),(-?\d+)", line)
        if node and page in sizes and int(node[1]) in inputs:
            width, height = sizes[page]
            x, y = int(node[3]) * scale + x_offset, int(node[4]) * scale + y_offset
            if 0 <= x <= width and 0 <= y <= height:
                found.append(
                    {
                        "path": inputs[int(node[1])],
                        "line": int(node[2]),
                        "page": page,
                        "x": x / width,
                        "y": y / height,
                    }
                )
    return build, found


def locate(store, build_id, direction, path="", line=1, page=1, x=0.5, y=0.5):
    build, nodes = points(store, build_id)
    if direction == "forward":
        candidates = [p for p in nodes if p["path"] == path]
        if not candidates:
            raise ValueError("This source has no mapped PDF position.")
        position = min(candidates, key=lambda p: (abs(p["line"] - line), p["page"], p["y"]))
        exact = position["line"] == line
    elif direction == "inverse":
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("Select a point inside the PDF page.")
        candidates = [p for p in nodes if p["page"] == page]
        if not candidates:
            raise ValueError("This PDF page has no source mapping.")
        position = min(candidates, key=lambda p: (p["x"] - x) ** 2 + (p["y"] - y) ** 2)
        exact = False  # SyncTeX maps nearby glyphs, not arbitrary selected characters.
    else:
        raise ValueError("Choose forward or inverse source lookup.")
    current = read_file(store, build["data"]["manuscript_id"], position["path"])
    return {
        **position,
        "build_id": build_id,
        "source_hash": build["data"]["input_files"][position["path"]],
        "current_source_matches": current["hash"] == build["data"]["input_files"][position["path"]],
        "line_exact": exact,
        "method": "synctex_v1_nearest_glyph",
    }
