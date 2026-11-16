"""Rule-file fingerprints: make modified guardrails detectable.

    python3 integrity.py check          # compare rule files with assets/integrity.json
    python3 integrity.py fingerprint    # print the combined fingerprint
    python3 integrity.py stamp WORK/review.json   # write provenance into an existing review
    python3 integrity.py update         # MAINTAINERS ONLY, at release time

The skill is open source: anyone can edit the rules, and this script cannot
prevent that. What it does is make the edit visible. init_review.py writes the
fingerprint into review.json["provenance"]; render.py shows whether the review
was produced under the official rules; evidence.py --strict fails when rule
files differ from the manifest. A fork that regenerates the manifest gets a
fingerprint that differs from the one published in the official README and
release notes, which is how third parties tell them apart.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
MANIFEST = SKILL / "assets" / "integrity.json"
RULE_FILES = [
    "SKILL.md",
    "references/neutrality.md", "references/ethics.md", "references/false-positives.md",
    "references/calibration.md", "references/critique.md", "references/submission.md",
    "scripts/neutrality.py", "scripts/integrity.py", "scripts/evidence.py", "scripts/critique.py",
    "scripts/novelty.py", "scripts/render.py", "scripts/stance_eval.py",
    "scripts/submission.py", "scripts/finalize.py",
]


def file_hash(p: Path) -> str:
    data = p.read_bytes().replace(b"\r\n", b"\n")  # immune to git autocrlf
    return hashlib.sha256(data).hexdigest()


def current(skill: Path = SKILL) -> dict:
    files = {}
    for rel in RULE_FILES:
        p = skill / rel
        files[rel] = file_hash(p) if p.exists() else "missing"
    combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(files.items())).encode()).hexdigest()
    return {"files": files, "rules_sha256": combined}


def status(skill: Path = SKILL) -> dict:
    cur = current(skill)
    version = (skill / "VERSION").read_text(encoding="utf-8").strip() if (skill / "VERSION").exists() else "unknown"
    try:
        man = json.loads((skill / "assets" / "integrity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        man = {}
    mf = man.get("files", {})
    changed = sorted(k for k in set(cur["files"]) | set(mf) if cur["files"].get(k) != mf.get(k))
    return {"skill_version": version, "rules_sha256": cur["rules_sha256"],
            "rules_official": bool(man) and not changed and man.get("rules_sha256") == cur["rules_sha256"],
            "manifest_version": man.get("skill_version"), "changed": changed}


def provenance(skill: Path = SKILL) -> dict:
    s = status(skill)
    return {k: s[k] for k in ("skill_version", "rules_sha256", "rules_official")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "fingerprint", "stamp", "update"])
    ap.add_argument("review", nargs="?")
    a = ap.parse_args()
    if a.cmd == "stamp":
        if not a.review:
            ap.error("stamp needs a review.json path")
        rp = Path(a.review)
        d = json.loads(rp.read_text(encoding="utf-8"))
        d["provenance"] = provenance()
        rp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 provenance：{d['provenance']}")
        return 0
    if a.cmd == "fingerprint":
        print(current()["rules_sha256"])
        return 0
    if a.cmd == "update":
        cur = current()
        s = status()
        MANIFEST.write_text(json.dumps({
            "note": "Fingerprints of the guardrail files. Regenerate only when releasing; the official value is published in README.md.",
            "skill_version": s["skill_version"], "date": dt.date.today().isoformat(),
            "rules_sha256": cur["rules_sha256"], "files": cur["files"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已更新 {MANIFEST.name}：{cur['rules_sha256']}")
        print("请同步更新 README.md 中的官方规则指纹。")
        return 0
    s = status()
    if s["rules_official"]:
        print(f"规则文件与清单一致（v{s['skill_version']}，指纹 {s['rules_sha256'][:16]}…）")
        return 0
    print(f"规则文件已修改或清单缺失（指纹 {s['rules_sha256'][:16]}…）。改动的文件：{', '.join(s['changed']) or '清单缺失'}")
    print("用这份技能生成的审稿会被标记为“规则已修改”。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
