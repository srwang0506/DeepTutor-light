#!/usr/bin/env python3
"""Prepare reproducible MMLU clean/perturbed pairs from the original data.tar.

Source: https://github.com/hendrycks/test (MIT).
Download the archive separately from
https://people.eecs.berkeley.edu/~hendrycks/data.tar .
This script reads archive members without extracting paths or making model calls.
"""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-subject", type=int, default=15)
    args = parser.parse_args()
    if not 1 <= args.per_subject <= 80:
        parser.error("--per-subject must be between 1 and 80 (suite limit: 500 cases)")
    subjects = ["college_computer_science", "high_school_statistics", "machine_learning"]
    cases, source_files = [], {}
    with tarfile.open(args.archive) as archive:
        members = archive.getmembers()
        for subject in subjects:
            candidates = [m for m in members if m.name.endswith("/test/" + subject + "_test.csv")]
            if (
                len(candidates) != 1
                or not candidates[0].isfile()
                or candidates[0].size > 10_000_000
            ):
                raise ValueError("Expected one bounded original test CSV per subject")
            member = candidates[0]
            raw = archive.extractfile(member).read()
            source_files[member.name] = hashlib.sha256(raw).hexdigest()
            rows = list(csv.reader(io.StringIO(raw.decode())))
            selected = sorted(
                enumerate(rows),
                key=lambda p: hashlib.sha256((subject + str(p[0])).encode()).hexdigest(),
            )[: args.per_subject]
            for index, row in selected:
                question, *options, answer = row
                if len(options) != 4 or answer not in "ABCD":
                    raise ValueError("Unexpected MMLU row format")
                key = f"{subject}:{index}"
                for variant in ("clean", "perturbed"):
                    order = list(range(4)) if variant == "clean" else [2, 0, 3, 1]
                    choices = "\n".join(
                        f"{chr(65 + i)}. {options[original]}" for i, original in enumerate(order)
                    )
                    prompt = question + "\n" + choices + "\nReturn the single option label."
                    if variant == "perturbed":
                        prompt = (
                            "Administrative note: exercise index 81357. This note is not part of the problem.\n\n"
                            + prompt
                        )
                    cases.append(
                        {
                            "id": key + ":" + variant,
                            "domain": subject,
                            "family": key,
                            "papers": [],
                            "split": "test",
                            "variant": variant,
                            "pair": key,
                            "prompt": prompt,
                            "sources": [],
                            "expected": {
                                "answerable": True,
                                "accepted_answers": [chr(65 + order.index(ord(answer) - 65))],
                                "citations": [],
                                "review_required": False,
                            },
                        }
                    )
    suite = {
        "title": f"MMLU external questions · {len(cases) // 2} sealed pairs",
        "curator": "Original questions: Hendrycks et al. MMLU; deterministic sampling and option permutation by DeepTutor acceptance harness",
        "source_url": "https://github.com/hendrycks/test",
        "license": "MIT (original repository); original question attribution retained",
        "independent_authorship_attested": True,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(suite, ensure_ascii=False, indent=2))
    digest = hashlib.sha256()
    with args.archive.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    provenance = {
        "source_url": "https://people.eecs.berkeley.edu/~hendrycks/data.tar",
        "license_url": "https://github.com/hendrycks/test/blob/master/LICENSE",
        "archive_sha256": digest.hexdigest(),
        "members": source_files,
        "selection": f"{args.per_subject} per subject, smallest SHA256(subject + zero-based original row index); no model output used in selection",
        "suite_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "cases": len(cases),
        "base_questions": len(cases) // 2,
        "limitations": [
            "All selected families are test-only; no task-specific training or tuning performed.",
            "Public benchmark contamination in pretrained models cannot be excluded.",
            "This benchmark does not certify HOI paper-reading accuracy or open-ended teaching quality.",
        ],
    }
    args.output.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2))
    print(f"Prepared {len(cases)} cases from {len(source_files)} source files.")


if __name__ == "__main__":
    main()
