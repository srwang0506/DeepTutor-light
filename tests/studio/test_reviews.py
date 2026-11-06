import csv
import io

import pytest

from deeptutor.studio import catalog, evidence
from deeptutor.studio import reviews as r
from deeptutor.studio.database import Conflict, Studio
from deeptutor.studio.models import EvidenceInput
from tests.studio.test_domains import edition


@pytest.fixture
def case(tmp_path):
    store = Studio(tmp_path / "studio", project="a")
    source = edition(store)
    protocol = r.create(
        store,
        r.ProtocolInput(
            title="Review",
            question="What result is reported?",
            search_query="reported results",
            inclusion=["primary study"],
            exclusion=["no original"],
            extraction_fields=["metric"],
            edition_ids=[source["id"]],
        ),
    )
    return store, source, protocol


def decide(store, source, protocol, stage, decision="include", **kw):
    return r.decide(
        store,
        protocol["id"],
        r.DecisionInput(
            edition_id=source["id"],
            expected_edition_revision=source["revision"],
            stage=stage,
            decision=decision,
            reason="Read the original study",
            source_reviewed=True,
            **kw,
        ),
    )


def test_screening_order_conflicts_and_stale_versions(case):
    store, source, protocol = case
    with pytest.raises(ValueError, match="first"):
        decide(store, source, protocol, "full_text")
    first = decide(store, source, protocol, "abstract")
    with pytest.raises(Conflict, match="Another"):
        decide(store, source, protocol, "abstract")
    decide(store, source, protocol, "full_text")
    assert r.report(store, protocol["id"])["counts"]["included"] == 1
    with store.db(write=True) as db:
        store.put(
            db,
            "edition",
            {**source["data"], "label": "corrected metadata"},
            identifier=source["id"],
            expected=source["revision"],
        )
    report = r.report(store, protocol["id"])
    assert report["counts"]["abstract_pending"] == 1 and report["items"][0]["stale"]
    assert store.get(first["id"])["data"]["decision"] == "include"


def test_extraction_requires_current_matching_anchor_and_exports_receipts(case):
    store, source, protocol = case
    decide(store, source, protocol, "abstract")
    decide(store, source, protocol, "full_text")
    anchor = evidence.create_evidence(
        store,
        EvidenceInput(
            edition_id=source["id"],
            blob_hash=source["data"]["files"][0]["hash"],
            page=1,
            label="result",
            quote="A measured result of 42.0.",
            verification="confirmed",
        ),
    )
    command = r.ExtractionInput(
        edition_id=source["id"],
        expected_edition_revision=source["revision"],
        field="metric",
        value="=42",
        evidence_ids=[anchor["id"]],
    )
    result = r.extract(store, protocol["id"], command)
    assert result["data"]["read_set"]
    rows = list(csv.reader(io.StringIO(r.export_csv(store, protocol["id"]))))
    assert "'=42" in rows[1]
    with pytest.raises(Conflict, match="changed"):
        r.extract(store, protocol["id"], command)
    other = edition(store)
    with pytest.raises(ValueError, match="outside"):
        decide(store, other, protocol, "abstract")
