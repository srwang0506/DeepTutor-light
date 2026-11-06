"""Frozen review protocols, human screening decisions and source-bound extraction."""

import csv
import io
import time
from typing import Literal

from pydantic import Field

from .database import Conflict
from .models import Command
from .read_set import ReadSet


class ProtocolInput(Command):
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=5, max_length=4000)
    search_query: str = Field(min_length=2, max_length=4000)
    inclusion: list[str] = Field(min_length=1, max_length=30)
    exclusion: list[str] = Field(min_length=1, max_length=30)
    extraction_fields: list[str] = Field(min_length=1, max_length=30)
    edition_ids: list[str] = Field(min_length=1, max_length=2000)
    discovery_ids: list[str] = Field(default_factory=list, max_length=100)


class DecisionInput(Command):
    edition_id: str
    expected_edition_revision: int = Field(ge=1)
    stage: Literal["abstract", "full_text"]
    decision: Literal["include", "exclude", "uncertain"]
    reason: str = Field(min_length=3, max_length=2000)
    source_reviewed: bool
    expected_previous_id: str = ""


class ExtractionInput(Command):
    edition_id: str
    expected_edition_revision: int = Field(ge=1)
    field: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=12000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    expected_previous_id: str = ""


def create(store, command):
    if len(set(command.edition_ids)) != len(command.edition_ids):
        raise ValueError("Review candidates must be distinct.")
    for values in (command.inclusion, command.exclusion, command.extraction_fields):
        if any(not s.strip() or len(s) > 1000 for s in values) or len(set(values)) != len(values):
            raise ValueError(
                "Criteria and extraction fields must be distinct, nonempty and bounded."
            )
    read_set = ReadSet.capture(
        store, [*command.edition_ids, *command.discovery_ids], rules={"review_protocol": 1}
    )
    for identifier in command.edition_ids:
        read_set.get(identifier, "edition")
    for identifier in command.discovery_ids:
        read_set.get(identifier, "discovery")
    with store.db(write=True) as db:
        row = store.put(
            db,
            "review_protocol",
            {**command.model_dump(), "frozen_at": time.time(), "read_set": read_set.manifest()},
        )
        read_set.attach(store, db, row["id"], command.edition_ids, "review_candidate")
        return row


def latest(store, db, kind, protocol_id, edition_id, qualifier, value):
    row = db.execute(
        "SELECT id FROM records WHERE kind=? AND scope=? AND json_extract(data,'$.protocol_id')=? AND json_extract(data,'$.edition_id')=? AND json_extract(data,?)=? ORDER BY created DESC,id DESC LIMIT 1",
        (kind, store.project, protocol_id, edition_id, "$." + qualifier, value),
    ).fetchone()
    return store.record(db, row[0]) if row else None


def member(store, db, protocol_id, edition_id, revision):
    protocol = store.record(db, protocol_id, "review_protocol")
    if edition_id not in protocol["data"]["edition_ids"]:
        raise ValueError("This edition is outside the frozen review candidate set.")
    edition = store.record(db, edition_id, "edition")
    if edition["revision"] != revision:
        raise Conflict("The edition changed; review the current original before deciding.")
    return protocol, edition


def decide(store, protocol_id, command):
    if not command.source_reviewed:
        raise ValueError("A ranking score cannot make a screening decision. Read the source first.")
    with store.db(write=True) as db:
        protocol, edition = member(
            store, db, protocol_id, command.edition_id, command.expected_edition_revision
        )
        if command.stage == "full_text":
            first = latest(
                store, db, "review_decision", protocol_id, command.edition_id, "stage", "abstract"
            )
            if (
                not first
                or first["data"]["decision"] != "include"
                or first["data"]["edition_revision"] != edition["revision"]
            ):
                raise ValueError("Include the current edition at title/abstract screening first.")
            if not edition["data"].get("files"):
                raise ValueError("Attach the full text before recording full-text screening.")
        prior = latest(
            store, db, "review_decision", protocol_id, command.edition_id, "stage", command.stage
        )
        if (prior["id"] if prior else "") != command.expected_previous_id:
            raise Conflict("Another screening decision was saved. Reload before revising it.")
        row = store.put(
            db,
            "review_decision",
            {
                **command.model_dump(),
                "protocol_id": protocol_id,
                "edition_revision": edition["revision"],
                "actor": "author",
                "supersedes": command.expected_previous_id,
                "recorded": time.time(),
            },
        )
        store.link(db, edition["id"], row["id"], "screened_version")
        store.link(db, protocol_id, row["id"], "frozen_protocol")
        return row


def extract(store, protocol_id, command):
    from .evidence import evidence_ready

    read_set = ReadSet.capture(
        store, [command.edition_id, *command.evidence_ids], rules={"review_extraction": 1}
    )
    for identifier in command.evidence_ids:
        evidence = read_set.get(identifier, "evidence")
        if (
            evidence["data"]["edition_id"] != command.edition_id
            or not evidence_ready(evidence["data"])
            or read_set.stale(identifier)
        ):
            raise ValueError(
                "Every extraction needs a current, matched source anchor from this edition."
            )
    with store.db(write=True) as db:
        protocol, edition = member(
            store, db, protocol_id, command.edition_id, command.expected_edition_revision
        )
        if command.field not in protocol["data"]["extraction_fields"]:
            raise ValueError("This extraction field is not in the frozen protocol.")
        decision = latest(
            store, db, "review_decision", protocol_id, command.edition_id, "stage", "full_text"
        )
        if (
            not decision
            or decision["data"]["decision"] != "include"
            or decision["data"]["edition_revision"] != edition["revision"]
        ):
            raise ValueError(
                "Complete full-text inclusion for this edition before extracting evidence."
            )
        for identifier in command.evidence_ids:
            if store.record(db, identifier)["revision"] != read_set.get(identifier)["revision"]:
                raise Conflict("An evidence anchor changed during extraction.")
        prior = latest(
            store, db, "review_extraction", protocol_id, command.edition_id, "field", command.field
        )
        if (prior["id"] if prior else "") != command.expected_previous_id:
            raise Conflict("This extraction changed. Reload before replacing it.")
        row = store.put(
            db,
            "review_extraction",
            {
                **command.model_dump(),
                "protocol_id": protocol_id,
                "edition_revision": edition["revision"],
                "read_set": read_set.manifest(),
                "supersedes": command.expected_previous_id,
            },
        )
        read_set.attach(store, db, row["id"], command.evidence_ids, "extracted_from")
        return row


def report(store, protocol_id):
    with store.db() as db:
        protocol = store.record(db, protocol_id, "review_protocol")
        items = []
        counts = {
            "identified": len(protocol["data"]["edition_ids"]),
            "abstract_pending": 0,
            "abstract_excluded": 0,
            "full_text_pending": 0,
            "full_text_excluded": 0,
            "included": 0,
            "uncertain": 0,
        }
        for identifier in protocol["data"]["edition_ids"]:
            edition = store.record(db, identifier, "edition")
            decisions = {
                stage: latest(store, db, "review_decision", protocol_id, identifier, "stage", stage)
                for stage in ("abstract", "full_text")
            }
            stale = any(
                d and d["data"]["edition_revision"] != edition["revision"]
                for d in decisions.values()
            )
            a, f = (
                decisions[k]["data"]["decision"] if decisions[k] and not stale else ""
                for k in ("abstract", "full_text")
            )
            state = (
                "abstract_pending"
                if not a
                else "abstract_excluded"
                if a == "exclude"
                else "uncertain"
                if a == "uncertain" or f == "uncertain"
                else "full_text_pending"
                if not f
                else "full_text_excluded"
                if f == "exclude"
                else "included"
            )
            counts[state] += 1
            extractions = {
                field: latest(
                    store, db, "review_extraction", protocol_id, identifier, "field", field
                )
                for field in protocol["data"]["extraction_fields"]
            }
            for extraction in extractions.values():
                if extraction:
                    extraction["stale"] = (
                        bool(store.stale(extraction["id"], db))
                        or extraction["data"]["edition_revision"] != edition["revision"]
                    )
            work = store.record(db, edition["data"]["work_id"], "work")
            items.append(
                {
                    "edition": edition,
                    "title": work["data"]["title"],
                    "state": state,
                    "stale": stale,
                    "decisions": decisions,
                    "extractions": extractions,
                }
            )
        return {"protocol": protocol, "counts": counts, "items": items}


def export_csv(store, protocol_id):
    result = report(store, protocol_id)
    fields = result["protocol"]["data"]["extraction_fields"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "edition_id",
            "title",
            "state",
            "abstract_reason",
            "full_text_reason",
            *fields,
            "extraction_receipts",
        ]
    )

    def safe(value):
        value = str(value)
        return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

    for row in result["items"]:
        values = [
            row["edition"]["id"],
            row["title"],
            row["state"],
            *[
                row["decisions"][stage]["data"]["reason"] if row["decisions"][stage] else ""
                for stage in ("abstract", "full_text")
            ],
            *[
                row["extractions"][field]["data"]["value"] if row["extractions"][field] else ""
                for field in fields
            ],
        ]
        from .database import encoded

        values.append(
            encoded(
                {
                    field: {
                        "id": item["id"],
                        "stale": item["stale"],
                        "evidence_ids": item["data"]["evidence_ids"],
                        "read_set_digest": item["data"]["read_set"]["digest"],
                    }
                    for field, item in row["extractions"].items()
                    if item
                }
            )
        )
        writer.writerow(map(safe, values))
    return output.getvalue()
