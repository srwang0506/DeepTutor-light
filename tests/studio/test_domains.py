import io
import zipfile

import fitz
import pytest

from deeptutor.studio import catalog, evidence, experiments, manuscripts
from deeptutor.studio.database import Conflict, Missing, Studio, sha
from deeptutor.studio.models import (
    EditionInput,
    EvidenceInput,
    FileEdit,
    ManuscriptInput,
    ObservationInput,
    ProposalInput,
    Protocol,
    RunInput,
    ScreeningInput,
    WorkInput,
)


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "account", project="a")


def pdf(text="A measured result of 42.0."):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def edition(store):
    work = catalog.create_work(store, WorkInput(title="A source paper", authors=["A. Author"]))
    e = catalog.create_edition(store, EditionInput(work_id=work["id"], label="v1"))
    return catalog.attach_pdf(store, e["id"], pdf(), "source.pdf", e["revision"])


def original_evidence(store):
    e = edition(store)
    return evidence.create_evidence(
        store,
        EvidenceInput(
            edition_id=e["id"],
            blob_hash=e["data"]["files"][0]["hash"],
            page=1,
            quote="A measured result of 42.0.",
            label="Table 1 result",
            verification="confirmed",
        ),
    )


def complete_protocol():
    return Protocol(
        dataset="BEHAVE",
        dataset_version="v1",
        split="official-test",
        population="seen",
        input="RGB",
        supervision="same-training",
        alignment="none",
        sampling="surface-10k",
        metric="F-score",
        unit="percent",
        threshold="0.01m",
        aggregation="macro",
        direction="higher",
    )


def observation(store, value=42):
    source = original_evidence(store)
    return evidence.create_observation(
        store,
        ObservationInput(
            title="Method",
            origin="reported",
            source_id=source["id"],
            protocol=complete_protocol(),
            value=value,
        ),
    )


def test_library_shared_only_inside_account_and_project_records_isolated(store, tmp_path):
    source = original_evidence(store)
    other = Studio(store.root, project="b")
    assert len(other.list("work")) == 1
    assert other.list("evidence") == []
    with pytest.raises(Missing):
        other.get(source["id"])
    assert Studio(tmp_path / "different-account", project="a").list("work") == []


def test_exact_identifiers_and_versions_never_merge_by_title(store):
    a = catalog.create_work(store, WorkInput(title="Same title"))
    b = catalog.create_work(store, WorkInput(title="Same title"))
    first = catalog.create_edition(
        store,
        EditionInput(work_id=a["id"], doi="https://doi.org/10.1234/ABC", arxiv="2103.00001v1"),
    )
    with pytest.raises(Conflict):
        catalog.create_edition(store, EditionInput(work_id=b["id"], doi="10.1234/abc"))
    second = catalog.create_edition(store, EditionInput(work_id=a["id"], arxiv="2103.00001v2"))
    assert first["id"] != second["id"] and len(store.list("work")) == 2


def test_blob_byte_identity_and_pdf_page_bounds(store):
    e = edition(store)
    digest = e["data"]["files"][0]["hash"]
    again = catalog.attach_pdf(
        store, e["id"], store.blob_path(digest).read_bytes(), "renamed.pdf", e["revision"]
    )
    assert again["revision"] == e["revision"] and len(again["data"]["files"]) == 1
    with pytest.raises(ValueError):
        catalog.page_source(store, e["id"], digest, 2)
    with pytest.raises(ValueError):
        catalog.edition_file(store, e["id"], "0" * 64)


def test_unresolved_and_ambiguous_quotes_cannot_be_confirmed(store):
    e = edition(store)
    command = dict(
        edition_id=e["id"],
        blob_hash=e["data"]["files"][0]["hash"],
        page=1,
        quote="Not in the source",
        label="Claim",
    )
    row = evidence.create_evidence(store, EvidenceInput(**command))
    assert row["data"]["resolution"] == "unresolved"
    with pytest.raises(ValueError):
        evidence.create_evidence(store, EvidenceInput(**command, verification="confirmed"))


def test_original_evidence_not_rebound_when_an_edition_gains_another_file(store):
    original = original_evidence(store)
    e = store.get(original["data"]["edition_id"])
    catalog.attach_pdf(store, e["id"], pdf("Different contents"), "supplement.pdf", e["revision"])
    assert store.get(original["id"])["data"]["blob_hash"] == original["data"]["blob_hash"]
    assert store.stale(original["id"]) == []


def test_two_missing_protocols_are_unknown_not_comparable():
    result = evidence.compare_protocols(Protocol(metric="F@0.01"), Protocol(metric="F@0.01"))
    assert result["status"] == "unknown" and "unit" in result["unknown"]


def test_different_units_or_splits_prevent_delta(store):
    a = observation(store)
    b = observation(store, 50)
    assert evidence.compare_observations(store, a["id"], b["id"])["delta"] == 8
    data = b["data"]
    data["protocol"]["unit"] = "fraction"
    evidence.create_observation(store, ObservationInput(**data), b["id"], b["revision"])
    assert evidence.compare_observations(store, a["id"], b["id"])["delta"] is None


def test_failed_or_static_run_cannot_become_executed_evidence(store):
    for status in ["failed", "planned", "static_only"]:
        run = experiments.create_run(store, RunInput(title="Trial", status=status))
        assert run["data"]["missing"]
        with pytest.raises(ValueError):
            evidence.create_observation(
                store,
                ObservationInput(
                    title="Result",
                    origin="executed",
                    source_id=run["id"],
                    protocol=complete_protocol(),
                    value=1,
                ),
            )


def test_changed_result_marks_dependent_artifacts_and_manuscript_stale(store):
    row = observation(store)
    table = experiments.make_table(store, "Results", [row["id"]])
    manuscript = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))
    with store.db(write=True) as db:
        store.link(db, table["id"], manuscript["id"])
    evidence.create_observation(
        store, ObservationInput(**{**row["data"], "value": 41}), row["id"], row["revision"]
    )
    assert store.stale(table["id"]) and store.stale(manuscript["id"])
    assert {r["id"] for r in store.impact(row["id"])} == {table["id"], manuscript["id"]}
    assert store.get(table["id"])["data"]["input_snapshots"][0]["data"]["value"] == 42


def test_dependency_cycles_and_cross_project_references_rejected(store):
    a = observation(store)
    b = observation(store)
    with store.db(write=True) as db:
        store.link(db, a["id"], b["id"])
        with pytest.raises(ValueError):
            store.link(db, b["id"], a["id"])
    other = Studio(store.root, project="b")
    with other.db(write=True) as db:
        row = other.put(db, "claim", {"title": "private"})
        with pytest.raises(Missing):
            other.link(db, a["id"], row["id"])


def test_screening_requires_reason_and_full_text(store):
    w = catalog.create_work(store, WorkInput(title="Metadata only"))
    e = catalog.create_edition(store, EditionInput(work_id=w["id"]))
    with pytest.raises(ValueError):
        ScreeningInput(edition_id=e["id"], stage="title_abstract", decision="exclude")
    with pytest.raises(ValueError):
        evidence.screen(
            store,
            ScreeningInput(
                edition_id=e["id"], stage="full_text", decision="exclude", reason="Not found"
            ),
        )
    row = evidence.screen(
        store, ScreeningInput(edition_id=e["id"], stage="full_text", decision="awaiting_full_text")
    )
    assert row["data"]["decision"] == "awaiting_full_text"


def test_bibtex_preserves_existing_keys_and_exact_doi_dedup(store):
    source = "@article{CustomKey, title={A {Nested} Title}, author={Smith, A and Wang, B}, year={2025}, doi={10.1234/test}}"
    first = catalog.import_bibliography(store, source, "bibtex")
    second = catalog.import_bibliography(store, source, "bibtex")
    assert (
        first["entries"][0]["status"] == "imported" and second["entries"][0]["status"] == "existing"
    )
    output = catalog.bibliography(store, [first["entries"][0]["edition_id"]])
    assert "CustomKey" in output and "Nested" in output


def test_manuscript_rejects_archive_traversal(store):
    for name in ["../main.tex", "/main.tex", ".git/config"]:
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr(name, "bad")
        with pytest.raises(ValueError):
            manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"), data.getvalue())
    assert store.list("manuscript") == []


def test_external_edit_never_overwritten(store):
    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))
    before = manuscripts.read_file(store, m["id"], "main.tex")
    file = manuscripts.project_file(store, m["id"], "main.tex")
    file.write_text("external edit")
    with pytest.raises(Conflict):
        manuscripts.save_file(
            store, m["id"], FileEdit(path="main.tex", base_hash=before["hash"], text="new")
        )
    assert file.read_text() == "external edit"


def test_proposal_flags_scope_and_requires_explicit_acceptance(store):
    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))
    f = manuscripts.read_file(store, m["id"], "main.tex")
    manuscripts.save_file(
        store,
        m["id"],
        FileEdit(path="main.tex", base_hash=f["hash"], text="仅在已见物体上观察到提升"),
    )
    f = manuscripts.read_file(store, m["id"], "main.tex")
    p = manuscripts.propose(
        store,
        m["id"],
        ProposalInput(
            path="main.tex",
            base_hash=f["hash"],
            start=0,
            end=len(f["text"]),
            text="对所有物体均有提升",
        ),
    )
    assert any(c["kind"] == "scope" for c in p["data"]["fact_changes"])
    with pytest.raises(ValueError):
        manuscripts.accept_proposal(store, p["id"], p["revision"])
    manuscripts.accept_proposal(store, p["id"], p["revision"], True)
    assert manuscripts.read_file(store, m["id"], "main.tex")["text"] == "对所有物体均有提升"


def test_stale_proposal_and_scope_hidden_manuscript(store):
    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))
    f = manuscripts.read_file(store, m["id"], "main.tex")
    p = manuscripts.propose(
        store,
        m["id"],
        ProposalInput(path="main.tex", base_hash=f["hash"], start=0, end=0, text="hello"),
    )
    manuscripts.save_file(
        store, m["id"], FileEdit(path="main.tex", base_hash=f["hash"], text="external")
    )
    with pytest.raises(Conflict):
        manuscripts.accept_proposal(store, p["id"], 1, True)
    with pytest.raises(Missing):
        manuscripts.file_list(Studio(store.root, project="b"), m["id"])


def test_submission_uses_immutable_build_allowlist(store):
    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))
    snapshot = store.save_blob(b"old manuscript")
    with store.db(write=True) as db:
        build = store.put(
            db,
            "build",
            {
                "manuscript_id": m["id"],
                "status": "succeeded",
                "input_files": {"main.tex": snapshot},
                "outputs": {"preview.pdf": store.save_blob(pdf())},
            },
        )
    file = manuscripts.project_file(store, m["id"], "main.tex")
    file.write_text("new manuscript")
    with pytest.raises(ValueError):
        manuscripts.submission(store, m["id"], ["private-notes.md"], build["id"])
    submission = manuscripts.submission(store, m["id"], ["main.tex"], build["id"])
    with zipfile.ZipFile(store.blob_path(submission["data"]["archive_hash"])) as z:
        assert set(z.namelist()) == {"main.tex", "submission.pdf"}
        assert z.read("main.tex") == b"old manuscript"


def test_optimistic_record_revision_rejects_lost_update(store):
    row = observation(store)
    command = ObservationInput(**{**row["data"], "value": 45})
    evidence.create_observation(store, command, row["id"], 1)
    with pytest.raises(Conflict):
        evidence.create_observation(store, command, row["id"], 1)


def test_fts_chinese_and_identifiers(store):
    catalog.create_work(store, WorkInput(title="人体物体交互重建 QPIC"))
    assert len(catalog.library(store, "交互")) == 1
    assert len(catalog.library(store, "QPIC")) == 1
    assert catalog.library(store, "不存在") == []


def test_prepared_save_recovers_after_crash_without_rewriting(store):
    import json

    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Recovery"))
    root = manuscripts.tree(store, m["id"])
    before = (root / "main.tex").read_bytes()
    after = b"new source saved before process stopped"
    journal = root / ".studio-save-abcdef.json"
    journal.write_text(
        json.dumps(
            {"path": "main.tex", "before": store.save_blob(before), "after": store.save_blob(after)}
        )
    )
    (root / "main.tex").write_bytes(after)
    assert manuscripts.recover_saves(store, m["id"]) == []
    assert not journal.exists()
    assert manuscripts.read_file(store, m["id"], "main.tex")["text"] == after.decode()
    changes = store.list("file_change")
    assert changes[0]["data"]["recovered"] is True
    manuscripts.recover_saves(store, m["id"])
    assert len(store.list("file_change")) == len(changes)


def test_crash_recovery_does_not_overwrite_external_edits(store):
    import json

    m = manuscripts.create_manuscript(store, ManuscriptInput(title="Recovery conflict"))
    root = manuscripts.tree(store, m["id"])
    journal = root / ".studio-save-abcdef.json"
    journal.write_text(
        json.dumps(
            {
                "path": "main.tex",
                "before": store.save_blob(b"before"),
                "after": store.save_blob(b"after"),
            }
        )
    )
    (root / "main.tex").write_text("external source")
    conflicts = manuscripts.recover_saves(store, m["id"])
    assert len(conflicts) == 1 and journal.exists()
    with pytest.raises(Conflict, match="recovered save"):
        manuscripts.save_file(
            store,
            m["id"],
            FileEdit(path="main.tex", base_hash=sha(b"external source"), text="overwrite"),
        )
    assert (root / "main.tex").read_text() == "external source"


def test_protocol_cannot_hide_populated_conditions_as_not_applicable():
    with pytest.raises(ValueError):
        Protocol(unit="mm", not_applicable=["unit"])
    with pytest.raises(ValueError):
        Protocol(not_applicable=["invented"])


def test_failed_bibliography_entry_leaves_no_orphan_work(store):
    result = catalog.import_bibliography(
        store, "@article{bad,title={Bad source},url={file:///private/test}}", "bibtex"
    )
    assert result["entries"][0]["status"] == "error"
    assert not store.list("work")


def test_repeat_import_links_existing_edition_to_current_project(store):
    source = "@article{ref,title={Exact source},doi={10.1234/example}}"
    first = catalog.import_bibliography(store, source, "bibtex")
    other = Studio(store.root, "b")
    second = catalog.import_bibliography(other, source, "bibtex")
    assert second["entries"][0]["status"] == "existing"
    assert (
        catalog.library(other, project_only=True)[0]["editions"][0]["id"]
        == first["entries"][0]["edition_id"]
    )
