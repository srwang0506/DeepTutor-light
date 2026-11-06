"""Grounded evidence and explicit comparability; missing conditions never match."""

from .catalog import page_source
from .database import Studio
from .models import ClaimInput, EvidenceInput, ObservationInput, Protocol, ScreeningInput


def create_evidence(store: Studio, command: EvidenceInput):
    page = page_source(store, command.edition_id, command.blob_hash, command.page)
    quote = command.quote.strip()

    # Page coordinates remain pinned to original bytes even after re-parsing.
    def normalized(s):
        return " ".join(s.split())

    matches = normalized(page["text"]).count(normalized(quote)) if quote else 0
    resolution = (
        "resolved"
        if command.region or matches == 1
        else "ambiguous"
        if matches > 1
        else "unresolved"
    )
    data = {
        **command.model_dump(),
        "parser_signature": page["parser_signature"],
        "resolution": resolution,
        "source_excerpt": page["text"][:100000],
    }
    if command.verification == "confirmed" and resolution != "resolved":
        raise ValueError("Resolve the original page region before confirming this evidence.")
    with store.db(write=True) as db:
        row = store.put(db, "evidence", data)
        store.link(db, command.edition_id, row["id"], "original_edition")
        return row


def compare_protocols(a: Protocol, b: Protocol):
    fields = set(Protocol.model_fields) - {"not_applicable", "direction"}
    unknown = []
    differences = []
    matched = []
    for field in sorted(fields):
        x, y = getattr(a, field).strip(), getattr(b, field).strip()
        # Scientific symbols and units are case-sensitive (mW != MW).
        if field not in {"unit", "metric", "threshold"}:
            x, y = x.casefold(), y.casefold()
        na = field in a.not_applicable
        nb = field in b.not_applicable
        if na and nb:
            matched.append(field)
            continue
        if (
            not x
            or not y
            or x in {"unknown", "n/a", "?", "未知"}
            or y in {"unknown", "n/a", "?", "未知"}
        ):
            unknown.append(field)
        elif na != nb or x != y:
            differences.append(
                {"field": field, "left": getattr(a, field), "right": getattr(b, field)}
            )
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
            db, "observation", command.model_dump(), identifier=identifier, expected=expected
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
            if original["data"]["verification"] != "confirmed":
                result["status"] = "unknown"
                result["unknown"].append("unreviewed_evidence")
    result["delta"] = (
        b["data"]["value"] - a["data"]["value"] if result["status"] == "comparable" else None
    )
    return result


def review_evidence(store, identifier, expected, verification):
    with store.db(write=True) as db:
        original = store.record(db, identifier, "evidence")
        if verification == "confirmed" and original["data"]["resolution"] != "resolved":
            raise ValueError("Resolve the original page region before confirming evidence.")
        return store.put(
            db,
            "evidence",
            {**original["data"], "verification": verification},
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
        if data["verification"] != "confirmed" or data["resolution"] != "resolved":
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


def create_claim(store, command: ClaimInput):
    if command.status == "confirmed" and not command.evidence_ids:
        raise ValueError("A confirmed claim needs reviewed evidence.")
    with store.db(write=True) as db:
        sources = [store.record(db, i) for i in command.evidence_ids]
        if any(r["kind"] not in {"evidence", "observation", "artifact"} for r in sources):
            raise ValueError("Claims can reference evidence, observations and research artifacts.")
        if command.status == "confirmed":
            for source in sources:
                validate_support(store, db, source["id"])
        row = store.put(db, "claim", command.model_dump())
        for identifier in command.evidence_ids:
            store.link(db, identifier, row["id"], "supports")
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
