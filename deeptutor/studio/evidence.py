"""Grounded evidence and explicit comparability; missing conditions never match."""

from difflib import SequenceMatcher
import time

from .catalog import page_source
from .database import Studio
from .models import ClaimInput, EvidenceInput, ObservationInput, Protocol, ScreeningInput


def assess_anchor(store: Studio, command: EvidenceInput):
    page = page_source(store, command.edition_id, command.blob_hash, command.page, command.region)
    quote = command.quote.strip()

    # Page coordinates remain pinned to original bytes even after re-parsing.
    def normalized(s):
        return " ".join(s.split())

    matches = normalized(page["text"]).count(normalized(quote)) if quote else 0
    quote_match = (
        "not_requested"
        if not quote
        else "exact"
        if matches == 1
        else "ambiguous"
        if matches > 1
        else "no_text"
        if not page["text"].strip()
        else "unmatched"
    )
    if (
        quote_match == "unmatched"
        and SequenceMatcher(None, normalized(quote), normalized(page["text"])).ratio() >= 0.9
    ):
        quote_match = "approximate"
    resolution = (
        "resolved"
        if matches == 1 or (command.region and not quote)
        else "ambiguous"
        if matches > 1
        else "unresolved"
    )
    data = {
        **command.model_dump(),
        "parser_signature": page["parser_signature"],
        "resolution": resolution,
        "source_excerpt": page["text"][:100000],
        "anchor_valid": True,
        "quote_match": quote_match,
        "visual_review": command.visual_review,
        "claim_support": "unassessed",
        "review_schema": 2,
        "reviewed_at": time.time() if command.verification != "unreviewed" else None,
        "reviewed_by": "author" if command.verification != "unreviewed" else "program_match",
    }
    if command.verification == "confirmed" and not evidence_ready(data):
        raise ValueError(
            "The quote must match its source; visual evidence requires an explicit review and note."
        )
    return data


def evidence_ready(data):
    if data.get("review_schema") != 2 or not data.get("anchor_valid"):
        return False
    if data.get("visual_review") == "disputed":
        return False
    if data.get("quote_match") == "exact":
        return True
    return (
        data.get("quote_match") in {"no_text", "not_requested"}
        and data.get("visual_review") == "confirmed"
        and bool(data.get("review_note", "").strip())
    )


def create_evidence(store: Studio, command: EvidenceInput):
    data = assess_anchor(store, command)
    with store.db(write=True) as db:
        row = store.put(db, "evidence", data)
        store.link(db, command.edition_id, row["id"], "original_edition")
        return row


PROTOCOL_PROFILES = {
    "general": {"dataset", "split", "metric", "unit", "aggregation"},
    "hoi_detection": {
        "dataset",
        "dataset_version",
        "split",
        "population",
        "input",
        "supervision",
        "metric",
        "unit",
        "aggregation",
    },
    "hoi_reconstruction": {
        "dataset",
        "dataset_version",
        "split",
        "population",
        "input",
        "supervision",
        "alignment",
        "sampling",
        "metric",
        "unit",
        "threshold",
        "aggregation",
    },
    "trajectory": {
        "dataset",
        "dataset_version",
        "split",
        "input",
        "alignment",
        "sampling",
        "metric",
        "unit",
        "aggregation",
    },
    "mathematics": {"population", "input", "metric", "unit", "aggregation"},
}
PROFILE_EXTENSIONS = {
    "hoi_reconstruction": {"target", "template_dependency", "calibration", "coordinate_space"},
    "trajectory": {
        "trajectory_source",
        "coordinate_space",
        "scale_normalization",
        "evaluation_version",
    },
    "mathematics": {"assumptions", "domain"},
}


def compare_protocols(a: Protocol, b: Protocol):
    fields = set(Protocol.model_fields) - {"not_applicable", "direction", "profile", "extensions"}
    unknown = []
    differences = []
    matched = []
    conversions = []
    essential = PROTOCOL_PROFILES[a.profile] | PROTOCOL_PROFILES[b.profile]
    if a.profile != b.profile:
        differences.append({"field": "profile", "left": a.profile, "right": b.profile})
    for field in sorted(fields):
        x, y = getattr(a, field).strip(), getattr(b, field).strip()
        # Scientific symbols and units are case-sensitive (mW != MW).
        if field not in {"unit", "metric", "threshold"}:
            x, y = x.casefold(), y.casefold()
        na = field in a.not_applicable
        nb = field in b.not_applicable
        if na and nb:
            if field in essential:
                unknown.append(field)
            else:
                matched.append(field)
            continue
        if (
            not x
            or not y
            or x in {"unknown", "n/a", "?", "未知"}
            or y in {"unknown", "n/a", "?", "未知"}
        ):
            unknown.append(field)
        elif field == "threshold" and x != y:
            from deeptutor.learning.verification import verify_equality

            if verify_equality(x, y)["status"] == "passed":
                matched.append(field)
                conversions.append({"field": field, "left": x, "right": y, "method": "exact_si"})
            else:
                differences.append({"field": field, "left": x, "right": y})
        elif field == "unit" and x != y:
            from deeptutor.learning.verification import UNITS

            if (
                x in UNITS
                and y in UNITS
                and UNITS[x].dimensions == UNITS[y].dimensions
                and a.metric == b.metric
            ):
                matched.append(field)
                conversions.append(
                    {
                        "field": field,
                        "left": x,
                        "right": y,
                        "right_to_left": float(UNITS[y].value / UNITS[x].value),
                        "method": "exact_si",
                    }
                )
            else:
                differences.append({"field": field, "left": x, "right": y})
        elif na != nb or x != y:
            differences.append(
                {"field": field, "left": getattr(a, field), "right": getattr(b, field)}
            )
        else:
            matched.append(field)
    for field in sorted(
        PROFILE_EXTENSIONS.get(a.profile, set())
        | PROFILE_EXTENSIONS.get(b.profile, set())
        | a.extensions.keys()
        | b.extensions.keys()
    ):
        x, y = a.extensions.get(field, "").strip(), b.extensions.get(field, "").strip()
        if (
            not x
            or not y
            or x.lower() in {"unknown", "n/a", "?", "未知"}
            or y.lower() in {"unknown", "n/a", "?", "未知"}
        ):
            unknown.append(field)
        elif x != y:
            differences.append({"field": field, "left": x, "right": y})
        else:
            matched.append(field)
    if a.direction == "unknown" or b.direction == "unknown":
        unknown.append("direction")
    elif a.direction != b.direction:
        differences.append({"field": "direction", "left": a.direction, "right": b.direction})
    return {
        "status": "incomparable" if differences else "unknown" if unknown else "comparable",
        "unknown": unknown,
        "differences": differences,
        "matched": matched,
        "conversions": conversions,
        "comparison_kind": "conditional" if differences else "unknown" if unknown else "comparable",
    }


def create_observation(store, command: ObservationInput, identifier=None, expected=None):
    with store.db(write=True) as db:
        source = store.record(db, command.source_id)
        if command.origin == "reported" and source["kind"] != "evidence":
            raise ValueError("Reported results require a pinned original-source evidence record.")
        if command.origin == "executed" and (
            source["kind"] != "run" or source["data"]["status"] != "succeeded"
        ):
            raise ValueError("Executed results require a run that actually succeeded.")
        if command.origin == "static_check" and source["kind"] != "run":
            raise ValueError("A static check needs a run record marked static_only.")
        if command.origin == "static_check" and source["data"]["status"] != "static_only":
            raise ValueError("Static checks and actual executions are distinct records.")
        row = store.put(
            db,
            "observation",
            {
                **command.model_dump(),
                "source_ref": {
                    "id": source["id"],
                    "kind": source["kind"],
                    "scope": source["scope"],
                    "revision": source["revision"],
                },
            },
            identifier=identifier,
            expected=expected,
        )
        if identifier:
            db.execute("DELETE FROM dependencies WHERE target=?", (identifier,))
        store.link(db, command.source_id, row["id"], command.origin)
        return row


def compare_observations(store, left, right):
    a = store.get(left, "observation")
    b = store.get(right, "observation")
    result = compare_protocols(Protocol(**a["data"]["protocol"]), Protocol(**b["data"]["protocol"]))
    if any(
        r["data"]["state"] != "reported" or r["data"]["origin"] not in {"reported", "executed"}
        for r in (a, b)
    ):
        result["status"] = "unknown"
        result["unknown"].append("result_provenance")
    if store.stale(left) or store.stale(right):
        result["status"] = "unknown"
        result["unknown"].append("source_revision")
    for row in (a, b):
        if row["data"]["origin"] == "reported":
            original = store.get(row["data"]["source_id"], "evidence")
            if original["data"]["verification"] != "confirmed" or not evidence_ready(
                original["data"]
            ):
                result["status"] = "unknown"
                result["unknown"].append("unreviewed_evidence")
    if any(row["data"]["state"] == "conflict" for row in (a, b)):
        result["status"] = "conflict"
    if result["status"] in {"unknown", "conflict"}:
        result["comparison_kind"] = result["status"]
    conversion = next(
        (v["right_to_left"] for v in result["conversions"] if v["field"] == "unit"), 1
    )
    result["delta"] = (
        b["data"]["value"] * conversion - a["data"]["value"]
        if result["status"] == "comparable"
        else None
    )
    result["delta_unit"] = a["data"]["protocol"]["unit"]
    result["conclusion"] = (
        "descriptive_difference_only"
        if result["delta"] is not None
        else "insufficient_comparability"
    )
    result["statistical_support"] = "not_assessed"
    return result


def review_evidence(
    store, identifier, expected, verification, visual_review=None, review_note=None
):
    original = store.get(identifier, "evidence")
    command = {
        key: value for key, value in original["data"].items() if key in EvidenceInput.model_fields
    }
    command["verification"] = verification
    if visual_review is not None:
        command["visual_review"] = visual_review
    if review_note is not None:
        command["review_note"] = review_note
    data = assess_anchor(store, EvidenceInput(**command))
    with store.db(write=True) as db:
        return store.put(
            db,
            "evidence",
            data,
            identifier=identifier,
            expected=expected,
        )


def validate_support(store, db, identifier, seen=None):
    seen = set() if seen is None else seen
    if identifier in seen or len(seen) > 200:
        raise ValueError("Evidence dependency is cyclic or too large to confirm.")
    seen = {*seen, identifier}
    row = store.record(db, identifier)
    data = row["data"]
    if store.stale(identifier, db):
        raise ValueError("A supporting source changed. Review current evidence before confirming.")
    if row["kind"] == "evidence":
        if data["verification"] != "confirmed" or not evidence_ready(data):
            raise ValueError("Review original evidence before confirming the claim.")
    elif row["kind"] == "observation":
        if data["state"] != "reported" or data["origin"] not in {"reported", "executed"}:
            raise ValueError(
                "Unknown values, hypotheses and static checks cannot confirm a result claim."
            )
        source = store.record(db, data["source_id"])
        if data["origin"] == "reported":
            validate_support(store, db, source["id"], seen)
        elif source["kind"] != "run" or source["data"]["status"] != "succeeded":
            raise ValueError("An executed result needs its successful run record.")
    elif row["kind"] == "artifact":
        inputs = data.get("input_snapshots", [])
        if not inputs:
            raise ValueError("An artifact needs reviewed input observations.")
        for item in inputs:
            current = store.record(db, item["id"], "observation")
            if current["revision"] != item["revision"]:
                raise ValueError(
                    "Artifact inputs changed. Regenerate or review the original snapshot."
                )
            validate_support(store, db, item["id"], seen)
    else:
        raise ValueError("Claims can reference evidence, observations and research artifacts.")


def create_claim(store, command: ClaimInput, identifier=None, expected=None):
    if command.status == "confirmed" and not (command.evidence_ids or command.edges):
        raise ValueError("A confirmed claim needs reviewed evidence.")
    with store.db(write=True) as db:
        ids = [edge.source_id for edge in command.edges] if command.edges else command.evidence_ids
        if len(ids) != len(set(ids)):
            raise ValueError("Each source can have one explicit relation to this claim.")
        sources = [store.record(db, i) for i in ids]
        if any(r["kind"] not in {"evidence", "observation", "artifact"} for r in sources):
            raise ValueError("Claims can reference evidence, observations and research artifacts.")
        edges = (
            [edge.model_dump() for edge in command.edges]
            if command.edges
            else [
                {
                    "source_id": source["id"],
                    "source_revision": source["revision"],
                    "role": source["data"].get("stance", "context"),
                    "qualifiers": "",
                    "review": "unreviewed",
                }
                for source in sources
            ]
        )
        for edge, source in zip(edges, sources):
            if edge["source_revision"] != source["revision"]:
                raise ValueError("A claim source changed; review the exact revision again.")
        if command.status == "confirmed":
            if not any(e["role"] == "supports" and e["review"] == "confirmed" for e in edges):
                raise ValueError("Confirm an explicit supporting relation to this specific claim.")
            if any(e["role"] == "contradicts" for e in edges):
                raise ValueError(
                    "Record the claim as disputed while contradictory evidence is unresolved."
                )
            for edge in edges:
                if edge["role"] in {"supports", "qualifies"}:
                    if edge["review"] != "confirmed":
                        raise ValueError(
                            "Review every supporting or qualifying relation before confirming."
                        )
                    validate_support(store, db, edge["source_id"])
        row = store.put(
            db,
            "claim",
            {**command.model_dump(), "evidence_ids": ids, "edges": edges},
            identifier=identifier,
            expected=expected,
        )
        if identifier:
            db.execute("DELETE FROM dependencies WHERE target=?", (identifier,))
        for edge in edges:
            store.link(db, edge["source_id"], row["id"], edge["role"])
        return row


def screen(store, command: ScreeningInput):
    with store.db(write=True) as db:
        edition = store.record(db, command.edition_id, "edition")
        if (
            command.stage == "full_text"
            and command.decision in {"include", "exclude"}
            and edition["data"]["full_text"] == "missing"
        ):
            raise ValueError("Full-text decisions require the full text. Use awaiting_full_text.")
        # Append-only decisions preserve the actual screening history.
        row = store.put(db, "screening", command.model_dump())
        store.link(db, command.edition_id, row["id"], "screened")
        return row
