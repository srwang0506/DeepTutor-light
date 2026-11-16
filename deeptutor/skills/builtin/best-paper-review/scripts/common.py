"""Shared helpers: text normalization, hashing, JSON I/O and cached HTTP.

Only the Python standard library is used so that every script runs on a
plain Python 3.9+ installation.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "BestPaperReview/2.0 (+https://github.com/; academic review tooling)"
MAX_RESPONSE_BYTES = 20_000_000

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', " ": " ",
}
_LIG_RE = re.compile("|".join(map(re.escape, _LIGATURES)))


def normalize_text(text: str) -> str:
    """Normalize extracted text so that quotes can be matched robustly.

    Applied identically to snapshots (by extract_pdf.py) and to quotes (by
    evidence.py): NFKC, ligature/dash/quote folding, removal of line-break
    hyphenation, and whitespace collapsing.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _LIG_RE.sub(lambda m: _LIGATURES[m.group(0)], text)
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)  # de-hyphenate wrapped words
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: str | os.PathLike) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | os.PathLike, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_date(value: Any) -> _dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def http_get(url: str, headers: dict[str, str] | None = None, cache_dir: str | os.PathLike | None = None,
             retries: int = 3, timeout: int = 30, max_wait: float = 60.0, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """GET with optional on-disk cache, bounded size and polite retry/backoff.

    Retries 429/5xx using Retry-After when given. Raises on persistent errors so
    callers can record "coverage unknown" instead of "no results".
    """
    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir) / (sha256_bytes(url.encode()) + ".bin")
        if cache_path.exists():
            return cache_path.read_bytes()
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"response exceeds {max_bytes} bytes: {url}")
            if (status == 202 or not data.strip()) and attempt < retries - 1:
                time.sleep(2.0 ** (attempt + 1))  # e.g. dblp answers 202 while it prepares results
                continue
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
            ra = e.headers.get("Retry-After", "") if e.headers else ""
            wait = float(ra) if ra.strip().isdigit() else 2.0 ** (attempt + 1)
            if wait > max_wait:
                raise
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt == retries - 1:
                raise
            time.sleep(2.0 ** (attempt + 1))
    raise RuntimeError(f"request failed: {url}: {last_err}")


DIMENSIONS = {
    "problem": "问题与动机",
    "novelty": "创新性",
    "soundness": "方法正确性",
    "theory": "理论",
    "experiments": "实验设计与支撑",
    "baselines": "基线与公平性",
    "reproducibility": "可复现性",
    "clarity": "写作与图表",
    "limitations": "局限与伦理",
}
STATUSES = {
    "strength": "优点",
    "confirmed": "确认问题",
    "limitation": "证据局限",
    "question": "澄清问题",
    "suggestion": "改进建议",
}
SEVERITIES = {"major": "主要", "minor": "次要"}
CONFIDENCES = {"high": "高", "medium": "中", "low": "低"}
SCOPES = {"global": "全文", "page": "页级", "region": "区域"}
NOVELTY_LEVELS = {"distinct": "明确区别", "incremental": "增量但有用", "overlap": "实质重叠", "unresolved": "无法判定",
                  "na": "不适用（实证/评测类贡献，未做前作检索）"}
