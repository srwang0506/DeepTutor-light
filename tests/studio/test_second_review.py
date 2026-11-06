"""Executable regressions for the 1274880 review; no model or real SSH service."""

import base64
import io
import json
from pathlib import Path
import threading
import zipfile

import bibtexparser
import fitz
import pytest

from deeptutor.studio import (
    catalog,
    connections,
    evidence,
    experiments,
    manuscripts,
    remote_reader,
    resume,
    tex_sandbox,
)
from deeptutor.studio.contracts import Locator, ObjectRef
from deeptutor.studio.database import Conflict, Studio, encoded, sha
from deeptutor.studio.models import (
    EditionInput,
    EvidenceInput,
    FileEdit,
    ManuscriptInput,
    ObservationInput,
    Protocol,
    RunInput,
    WorkInput,
)
from deeptutor.studio.read_set import ReadSet


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", "review-two")


def source(store, rotation=0):
    work = catalog.create_work(store, WorkInput(title="Measured source"))
    edition = catalog.create_edition(store, EditionInput(work_id=work["id"]))
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=700)
        page.insert_text((60, 80), "Equal distances: one metre and 100 centimetres.")
        page.insert_text((60, 350), "Unrelated paragraph outside the selected region.")
        rect = page.search_for("Equal distances")[0] + (-2, -2, 320, 2)
        page.set_rotation(rotation)
        visible = rect * page.rotation_matrix
        region = (
            visible.x0 / page.rect.width,
            visible.y0 / page.rect.height,
            visible.x1 / page.rect.width,
            visible.y1 / page.rect.height,
        )
        content = doc.tobytes()
    edition = catalog.attach_pdf(store, edition["id"], content, "paper.pdf", 1)
    return edition, sha(content), region


def observations(store):
    edition, digest, _ = source(store)
    anchor = evidence.create_evidence(
        store,
        EvidenceInput(
            edition_id=edition["id"],
            blob_hash=digest,
            page=1,
            label="distance",
            quote="Equal distances: one metre and 100 centimetres.",
            verification="confirmed",
        ),
    )
    protocol = dict(
        dataset="fixture",
        split="test",
        metric="distance",
        aggregation="single run",
        direction="lower",
        not_applicable=[
            "dataset_version",
            "population",
            "input",
            "supervision",
            "alignment",
            "sampling",
            "threshold",
        ],
    )
    return [
        evidence.create_observation(
            store,
            ObservationInput(
                title=unit,
                source_id=anchor["id"],
                origin="reported",
                value=value,
                protocol=Protocol(**protocol, unit=unit),
            ),
        )
        for unit, value in [("m", 1), ("cm", 100)]
    ]


@pytest.mark.parametrize("renderer", [experiments.make_figure, experiments.make_table])
def test_edit_between_comparison_and_rendering_never_publishes_mixed_versions(
    store, monkeypatch, renderer
):
    rows = observations(store)
    compare = experiments.compare_observations
    captured = []

    def concurrent_edit(snapshot, left, right):
        assert isinstance(snapshot, ReadSet)
        result = compare(snapshot, left, right)
        with store.db(write=True) as db:
            old = store.record(db, right)
            data = {**old["data"], "value": 1, "protocol": {**old["data"]["protocol"], "unit": "m"}}
            store.put(db, "observation", data, identifier=right, expected=old["revision"])
        captured.append(snapshot.get(right)["data"])
        return result

    monkeypatch.setattr(experiments, "compare_observations", concurrent_edit)
    with pytest.raises(Conflict, match="changed"):
        renderer(store, "race", [r["id"] for r in rows])
    assert captured[0]["value"] == 100 and captured[0]["protocol"]["unit"] == "cm"
    assert store.list("artifact") == []


def test_read_set_is_immutable_and_link_keeps_consumed_revision(store):
    edition, _, _ = source(store)
    snapshot = ReadSet.capture(store, [edition["id"]])
    snapshot.get(edition["id"])["data"]["files"].clear()
    assert snapshot.get(edition["id"])["data"]["files"]
    with store.db(write=True) as db:
        store.put(
            db, "edition", {**edition["data"], "venue": "new"}, identifier=edition["id"], expected=2
        )
        target = store.put(db, "activity", {"title": "from old edition"})
        snapshot.attach(store, db, target["id"], [edition["id"]], "practice_source")
        assert (
            db.execute(
                "SELECT source_revision FROM dependencies WHERE target=?", (target["id"],)
            ).fetchone()[0]
            == 2
        )
    assert store.stale(target["id"])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_selection_uses_display_coordinates_and_reads_unrotated_text(store, rotation):
    edition, digest, region = source(store, rotation)
    selected = catalog.page_source(store, edition["id"], digest, 1, region)
    assert "Equal distances" in selected["text"]
    assert "Unrelated paragraph" not in selected["text"]


@pytest.mark.parametrize("bad", ["hash", "page", "quote"])
def test_resume_rejects_fabricated_locator(store, bad):
    edition, digest, _ = source(store)
    loc = dict(blob_hash=digest, page=1)
    loc.update(
        {
            "hash": {"blob_hash": sha(b"foreign")},
            "page": {"page": 20},
            "quote": {"quote": "invented words"},
        }[bad]
    )
    with pytest.raises(ValueError):
        resume.save(
            store,
            ObjectRef(
                scope="@library",
                kind="edition",
                id=edition["id"],
                revision=2,
                locator=Locator(**loc),
            ),
        )
    assert store.list("resume") == []


def test_sparse_bibliography_enriches_without_losing_originals_aliases_or_user_title(store):
    sparse = json.dumps([{"id": "initial", "title": "A method", "DOI": "10.1234/hoi"}])
    identifier = catalog.import_bibliography(store, sparse, "csl")["entries"][0]["edition_id"]
    edition = store.get(identifier)
    with store.db(write=True) as db:
        work = store.record(db, edition["data"]["work_id"])
        store.put(
            db,
            "work",
            {**work["data"], "title": "My curated title"},
            identifier=work["id"],
            expected=1,
        )
    rich = "@inproceedings{conference,title={A method},doi={10.1234/hoi},booktitle={CVPR},pages={1--8},publisher={IEEE},customfield={keep}}"
    imported = catalog.import_bibliography(store, rich, "bibtex")
    assert imported["entries"][0]["edition_id"] == identifier
    row = store.get(identifier)
    data = row["data"]
    assert len(data["bibliographic_sources"]) == 2
    assert data["citekey_aliases"] == ["conference", "initial"]
    assert data["bibliographic_provenance"]["pages"]
    entry = bibtexparser.loads(catalog.bibliography(store, [identifier])).entries[0]
    assert entry["ENTRYTYPE"] == "inproceedings" and entry["booktitle"] == "CVPR"
    assert entry["pages"] == "1--8" and entry["customfield"] == "keep"
    assert entry["title"] == "My curated title"
    catalog.import_bibliography(store, rich, "bibtex")
    assert store.get(identifier)["revision"] == row["revision"]


def test_run_reimport_after_edit_reuses_immutable_receipt_and_cannot_claim_execution(store):
    payload = json.dumps({"title": "Original", "status": "succeeded", "seed": 42})
    row = store.get(experiments.import_run_rows(store, payload, "json")[0]["id"])
    data = {key: value for key, value in row["data"].items() if key in RunInput.model_fields}
    data["title"] = "Edited by user"
    updated = experiments.create_run(store, RunInput(**data), row["id"], row["revision"])
    again = store.get(experiments.import_run_rows(store, payload, "json")[0]["id"])
    assert again["id"] == updated["id"] and again["data"]["title"] == "Edited by user"
    assert not again["data"]["execution_verified"]
    with pytest.raises(ValueError, match="executed|successful"):
        evidence.create_observation(
            store,
            ObservationInput(
                title="Not witnessed",
                source_id=row["id"],
                origin="executed",
                value=1,
                protocol=Protocol(),
            ),
        )
    assert len(store.list("run")) == 1


def test_submission_plan_resolves_extensionless_files_and_excludes_private_extras(
    store, monkeypatch
):
    mid = manuscripts.create_manuscript(store, ManuscriptInput(title="Draft"))["id"]
    sources = {
        "main.tex": r"\documentclass{article}\usepackage{graphicx}\begin{document}\input{section}\includegraphics{plot}\bibliography{refs}\end{document}",
        "section.tex": "Public method",
        "refs.bib": "",
        "plot.pdf": "%PDF-public",
        "private-notes.txt": "private",
        "old-unblinded.pdf": "old-private",
    }
    with store.db(write=True) as db:
        build = store.put(
            db,
            "build",
            {
                "manuscript_id": mid,
                "status": "succeeded",
                "main_file": "main.tex",
                "engine": "pdflatex",
                "input_files": {k: store.save_blob(v.encode()) for k, v in sources.items()},
                "outputs": {"preview.pdf": store.save_blob(b"old PDF")},
            },
        )
    plan = manuscripts.submission_plan(store, mid, build["id"])
    assert set(plan["required"]) == {"main.tex", "section.tex", "refs.bib", "plot.pdf"}
    assert plan["reasons"]["plot.pdf"] and not plan["unresolved"]

    def compile_subset(store, row, cancelled=None):
        assert set(row["data"]["input_files"]) == set(plan["required"])
        with store.db(write=True) as db:
            return store.put(
                db,
                "build",
                {
                    **row["data"],
                    "status": "succeeded",
                    "outputs": {"preview.pdf": store.save_blob(b"clean PDF")},
                },
                identifier=row["id"],
                expected=row["revision"],
            )

    monkeypatch.setattr(manuscripts, "_execute_build", compile_subset)
    result = manuscripts.submission(store, mid, plan["required"], build["id"])
    with zipfile.ZipFile(
        io.BytesIO(store.blob_path(result["data"]["archive_hash"]).read_bytes())
    ) as package:
        assert set(package.namelist()) == set(plan["required"]) | {"submission.pdf"}
        assert package.read("submission.pdf") == b"clean PDF"
    archive = manuscripts.submission(store, mid, list(sources), build["id"], "archive")
    assert set(archive["data"]["files"]) == set(sources)


def test_dynamic_dependency_is_explicitly_unresolved(store):
    files = {"main.tex": store.save_blob(rb"\input{\chosenSection}")}
    result = manuscripts.dependency_manifest(store, files, "main.tex")
    assert result["required"] == ["main.tex"]
    assert result["unresolved"] == [
        {"path": "main.tex", "command": "input", "expression": r"\chosenSection"}
    ]


def test_recovering_prepared_edit_finishes_original_proposal_receipt(store):
    from deeptutor.studio.models import ProposalInput

    mid = manuscripts.create_manuscript(store, ManuscriptInput(title="Recovery"))["id"]
    file = manuscripts.read_file(store, mid, "main.tex")
    proposal = manuscripts.propose(
        store,
        mid,
        ProposalInput(
            path="main.tex",
            base_hash=file["hash"],
            start=0,
            end=len(file["text"]),
            text="Prepared paper",
        ),
    )
    root = manuscripts.tree(store, mid)
    store.save_blob(file["text"].encode())
    prepared = store.save_blob(b"Prepared paper")
    journal = root / ".studio-save-abcdef098765.json"
    journal.write_text(
        encoded(
            {
                "path": "main.tex",
                "before": file["hash"],
                "after": prepared,
                "proposal": {"id": proposal["id"], "revision": 1},
            }
        )
    )
    (root / "main.tex").write_text("External edit after crash")
    disk_hash = sha(b"External edit after crash")
    manuscripts.resolve_recovery(store, mid, journal.name, disk_hash, "prepared")
    row = store.get(proposal["id"])
    assert row["data"]["status"] == "accepted" and row["data"]["receipt"]["hash"] == prepared
    assert manuscripts.accept_proposal(store, proposal["id"], 1)["data"]["status"] == "accepted"
    assert (root / "main.tex").read_text() == "Prepared paper"


def test_protocol_states_are_separate_from_values_and_units_keep_case():
    known = Protocol(metric="power", unit="mW")
    conflicted = Protocol(metric="power", unit="mW", field_states={"unit": "conflict"})
    assert known.field_states["unit"] == "known"
    assert evidence.compare_protocols(known, conflicted)["status"] == "conflict"
    unknown = Protocol(metric="power", unit="mW", field_states={"unit": "unknown"})
    assert "unit" in evidence.compare_protocols(known, unknown)["unknown"]
    result = evidence.compare_protocols(known, Protocol(metric="power", unit="MW"))
    assert not (result["status"] == "comparable" and not result["conversions"])
    with pytest.raises(ValueError, match="concrete"):
        Protocol(unit="UNKNOWN", field_states={"unit": "known"})


def test_sync_and_background_build_share_one_execution_lease(store, monkeypatch):
    mid = manuscripts.create_manuscript(store, ManuscriptInput(title="Race"))["id"]
    build = manuscripts.prepare_build(store, mid)
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def compile_once(store, row, cancelled=None):
        calls.append(row["id"])
        entered.set()
        assert finish.wait(4)
        with store.db(write=True) as db:
            return store.put(
                db,
                "build",
                {**row["data"], "status": "failed"},
                identifier=row["id"],
                expected=row["revision"],
            )

    monkeypatch.setattr(manuscripts, "_execute_build", compile_once)
    worker = threading.Thread(target=manuscripts.execute_build, args=(store, build))
    worker.start()
    try:
        assert entered.wait(3)
        assert manuscripts.execute_build(store, build)["data"]["status"] == "running"
    finally:
        finish.set()
        worker.join(4)
    assert calls == [build["id"]]
    assert manuscripts.execute_build(store, build)["data"]["status"] == "failed"
    assert len(calls) == 1


@pytest.mark.parametrize("root", ["/", str(Path.home()), str(Path.home().parent)])
def test_tex_never_accepts_root_or_home_as_distribution(root, tmp_path):
    with pytest.raises(ValueError, match="allowlist"):
        tex_sandbox.command(tmp_path, "/usr/bin/latexmk", "-pdf", "main.tex", Path(root))


def test_linux_standard_binary_does_not_bind_filesystem_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tex_sandbox.sys, "platform", "linux")
    monkeypatch.setattr(tex_sandbox.shutil, "which", lambda _: "/usr/bin/bwrap")
    args = tex_sandbox.command(
        tmp_path,
        "/usr/bin/latexmk",
        "-pdf",
        "main.tex",
        tex_sandbox.distribution_roots("/usr/bin/latexmk"),
    )
    mounted = [args[i + 1] for i, value in enumerate(args) if value == "--ro-bind"]
    assert "/" not in mounted and str(Path.home()) not in mounted
    assert "--unshare-all" in args


def test_remote_intermediate_symlink_swap_is_rejected(tmp_path):
    root, outside = tmp_path / "approved", tmp_path / "outside"
    (root / "logs").mkdir(parents=True)
    outside.mkdir()
    (root / "logs/run.txt").write_text("approved")
    (outside / "run.txt").write_text("private")
    validated = remote_reader.checked(root / "logs/run.txt", [root])
    (root / "logs").rename(root / "previous-logs")
    (root / "logs").symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        remote_reader.read_file(validated)


def test_revoked_remote_read_does_not_leave_raw_blob(store, tmp_path):
    row = connections.create_connection(
        store,
        connections.ConnectionInput(
            title="Local fixture", provider="ssh", alias="fixture", roots=[str(tmp_path)]
        ),
    )
    payload = b"private fixture must not be persisted"

    def revoke(config, request):
        connections.create_connection(
            store,
            connections.ConnectionInput(**{**config, "enabled": False}),
            row["id"],
            row["revision"],
        )
        return {"content": base64.b64encode(payload).decode(), "sha256": sha(payload)}

    with pytest.raises(ValueError, match="scope changed"):
        connections.read_connection(
            store,
            row["id"],
            connections.ReadRequest(operation="read", path=str(tmp_path / "run.log")),
            transport=revoke,
        )
    assert not any(p.name == sha(payload) for p in store.root.rglob("*"))
    assert store.list("remote_snapshot") == []


@pytest.mark.parametrize(
    "query,expected",
    [
        ("图3b中的误差", ("figure", "3b")),
        ("表3b中的数值", ("table", "3b")),
        ("定义2中的条件", ("definition", "2")),
        ("解释第7题", ("exercise", "7")),
    ],
)
def test_chinese_locators_preserve_adjacent_words(query, expected):
    from deeptutor.services.rag.pipelines.llamaindex.study_locator import locator_intent

    assert locator_intent(query) == expected


def test_structural_mentions_are_not_definitions():
    from deeptutor.services.rag.pipelines.llamaindex.study_locator import structural_labels

    assert ("table", "3b") not in structural_labels("We refer to Table 3b for details.")
    assert ("table", "3b") in structural_labels("Table 3b: Results\nAccuracy is shown below.")


@pytest.mark.parametrize(
    "term,other", [("α", "β"), ("C++", "C"), ("F@0.01", "F@0.1"), ("图算法", "树结构")]
)
def test_scientific_tokens_retrieve_exact_identifier_after_upgrade(store, term, other):
    from deeptutor.services.workspace.project_recall import tokens

    hit = catalog.create_work(store, WorkInput(title=term + " research"))
    catalog.create_work(store, WorkInput(title=other + " research"))
    with store.db(write=True) as db:
        db.execute("DELETE FROM metadata WHERE key='scientific_tokens_v2'")
        db.execute("UPDATE search SET content='legacy tokenization'")
    assert tokens(term)
    assert [r["id"] for r in store.list("work", term)] == [hit["id"]]


@pytest.mark.parametrize("sentinel", ["UNKNOWN", "Unknown", "unknown", "?", "n/a"])
def test_unknown_protocol_value_never_becomes_known_through_case(sentinel):
    protocol = Protocol(metric=sentinel, unit=sentinel)
    result = evidence.compare_protocols(protocol, protocol)
    assert result["status"] == "unknown"
    assert "metric" in result["unknown"] and "unit" in result["unknown"]
