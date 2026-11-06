import json

import pytest

from deeptutor.api.routers.studio import PassageInput
from deeptutor.studio import catalog, evidence, experiments, manuscripts, writing
from deeptutor.studio.database import Conflict, Missing, Studio, sha
from deeptutor.studio.models import (
    ClaimInput,
    FileEdit,
    ManuscriptInput,
    ObservationInput,
    Protocol,
)
from tests.studio.test_domains import complete_protocol, edition, original_evidence


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", "a")


def test_manuscript_citekey_namespace_preserves_old_macros_and_rejects_duplicate(store):
    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))["id"]
    old = '@string{conf = "Old Conference"}\n@article{existing, title={An {Old} Title}, journal=conf, author={A}}'
    manuscripts.save_file(store, m, FileEdit(path="original.bib", base_hash=sha(b""), text=old))
    e = edition(store)
    with pytest.raises(Conflict):
        writing.add_citation(store, m, e["id"], "existing")
    result = writing.add_citation(store, m, e["id"], "samePaper2026")
    assert result["citation"]["data"]["citekey"] == "samePaper2026"
    assert {r["citekey"] for r in writing.citations(store, m)["entries"]} == {
        "existing",
        "samePaper2026",
    }
    assert manuscripts.read_file(store, m, "original.bib")["text"] == old
    assert catalog.library(store)[0]["editions"][0]["data"]["citekey"] == ""
    other = manuscripts.create_manuscript(store, ManuscriptInput(title="Other paper"))["id"]
    writing.add_citation(store, other, e["id"], "differentKey")
    assert writing.citations(store, other)["entries"][0]["citekey"] == "differentKey"


def test_passage_binding_exact_relocation_ambiguity_and_stale_transitive_impact(store):
    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))["id"]
    e = original_evidence(store)
    current = manuscripts.read_file(store, m, "main.tex")
    manuscripts.save_file(
        store,
        m,
        FileEdit(path="main.tex", base_hash=current["hash"], text="A claim.\nSecond paragraph."),
    )
    current = manuscripts.read_file(store, m, "main.tex")
    command = PassageInput(
        path="main.tex", base_hash=current["hash"], start=0, end=8, source_ids=[e["id"]]
    )
    binding = writing.bind_passage(store, m, command)
    assert writing.passages(store, m)[0]["location"] == {"status": "current", "line": 1}
    manuscripts.save_file(
        store,
        m,
        FileEdit(
            path="main.tex",
            base_hash=current["hash"],
            text="New heading\nA claim.\nSecond paragraph.",
        ),
    )
    assert writing.passages(store, m)[0]["location"] == {"status": "exact_relocated", "line": 2}
    with pytest.raises(Conflict):
        writing.bind_passage(store, m, command)
    current = manuscripts.read_file(store, m, "main.tex")
    manuscripts.save_file(
        store, m, FileEdit(path="main.tex", base_hash=current["hash"], text="A claim.\nA claim.")
    )
    assert writing.passages(store, m)[0]["location"]["status"] == "ambiguous"
    evidence.review_evidence(store, e["id"], e["revision"], "disputed")
    assert writing.passages(store, m)[0]["stale"]
    assert {r["id"] for r in store.impact(e["id"])} >= {binding["id"], m}
    with pytest.raises(Missing):
        writing.passages(Studio(store.root, "b"), m)


def test_confirmed_claim_cannot_launder_unknown_values_through_table(store):
    e = original_evidence(store)
    o = evidence.create_observation(
        store,
        ObservationInput(
            title="Unknown",
            origin="reported",
            source_id=e["id"],
            protocol=Protocol(),
            state="not_reported",
        ),
    )
    table = experiments.make_table(store, "Unknown values", [o["id"]])
    with pytest.raises(ValueError, match="Unknown"):
        evidence.create_claim(
            store,
            ClaimInput(
                title="This result is confirmed",
                status="confirmed",
                edges=[
                    dict(
                        source_id=table["id"],
                        source_revision=table["revision"],
                        role="supports",
                        review="confirmed",
                    )
                ],
            ),
        )
    draft = evidence.create_claim(
        store, ClaimInput(title="Needs review", evidence_ids=[table["id"]])
    )
    assert draft["data"]["status"] == "draft"


def test_metric_units_are_case_sensitive():
    a = complete_protocol()
    b = a.model_copy(update={"unit": "MW"})
    a.unit = "mW"
    result = evidence.compare_protocols(a, b)
    assert result["status"] == "incomparable"
    assert result["differences"][0]["field"] == "unit"
