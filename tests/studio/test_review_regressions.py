"""Adversarial examples from the f458871 product review, with positive controls."""

import hashlib
import json
import re
import subprocess

import bibtexparser
import fitz
import pytest

from deeptutor.runtime.backup import backup, restore
from deeptutor.studio import catalog, evidence, experiments, manuscripts, remote_reader
from deeptutor.studio.database import Studio
from deeptutor.studio.models import (
    ClaimInput,
    EditionInput,
    EvidenceInput,
    ObservationInput,
    Protocol,
    WorkInput,
)


@pytest.fixture
def store(tmp_path):
    return Studio(tmp_path / "studio", "review-project")


def source(store):
    work = catalog.create_work(store, WorkInput(title="Measured source"))
    edition = catalog.create_edition(store, EditionInput(work_id=work["id"]))
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "The measured gain was 4 points.")
        content = doc.tobytes()
    edition = catalog.attach_pdf(store, edition["id"], content, "paper.pdf", 1)
    return dict(
        edition_id=edition["id"],
        blob_hash=hashlib.sha256(content).hexdigest(),
        page=1,
        label="Gain",
    )


def test_generated_bibliography_is_not_advertised_as_a_lossless_original(store):
    fields = source(store)
    result = catalog.bibliography_export(store, [fields["edition_id"]])
    assert result["content"] and not result["lossless"]
    assert result["losses"][0]["from"] == "catalog_metadata"
    assert result["losses"][0]["original_preserved"] is False


def test_blank_rectangle_never_proves_a_fabricated_quote(store):
    fields = source(store)
    with pytest.raises(ValueError):
        evidence.create_evidence(
            store,
            EvidenceInput(
                **fields,
                quote="The measured gain was 99 points.",
                region=(0.8, 0.8, 0.9, 0.9),
                verification="confirmed",
            ),
        )
    valid = evidence.create_evidence(
        store,
        EvidenceInput(**fields, quote="The measured gain was 4 points.", verification="confirmed"),
    )
    assert valid["data"]["verification"] == "confirmed"


def test_contradiction_is_not_silently_changed_into_support(store):
    row = evidence.create_evidence(
        store,
        EvidenceInput(
            **source(store),
            quote="The measured gain was 4 points.",
            stance="contradicts",
            verification="confirmed",
        ),
    )
    with pytest.raises(ValueError):
        evidence.create_claim(
            store, ClaimInput(title="The gain is 99", evidence_ids=[row["id"]], status="confirmed")
        )
    claim = evidence.create_claim(store, ClaimInput(title="Hypothesis", evidence_ids=[row["id"]]))
    with store.db() as db:
        assert (
            db.execute(
                "SELECT relation FROM dependencies WHERE source=? AND target=?",
                (row["id"], claim["id"]),
            ).fetchone()[0]
            == "contradicts"
        )


def test_claim_review_keeps_immutable_history_and_rejects_stale_edits(store):
    row = evidence.create_evidence(
        store,
        EvidenceInput(
            **source(store), quote="The measured gain was 4 points.", verification="confirmed"
        ),
    )
    draft = evidence.create_claim(store, ClaimInput(title="The gain was 4 points"))
    command = ClaimInput(
        title=draft["data"]["title"],
        status="confirmed",
        edges=[
            {
                "source_id": row["id"],
                "source_revision": row["revision"],
                "role": "supports",
                "review": "confirmed",
            }
        ],
    )
    confirmed = evidence.create_claim(store, command, draft["id"], 1)
    assert confirmed["revision"] == 2
    assert store.revision(draft["id"], 1)["data"]["status"] == "draft"
    with pytest.raises(ValueError, match="changed"):
        evidence.create_claim(store, command, draft["id"], 1)


def test_all_not_applicable_does_not_define_a_comparison():
    fields = [
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
    ]
    p = Protocol(not_applicable=fields, direction="higher")
    result = evidence.compare_protocols(p, p)
    assert result["status"] != "comparable"


def test_plot_normalizes_convertible_units_and_keeps_original_inputs(store):
    row = evidence.create_evidence(
        store,
        EvidenceInput(
            **source(store), quote="The measured gain was 4 points.", verification="confirmed"
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
    items = [
        evidence.create_observation(
            store,
            ObservationInput(
                title=unit,
                source_id=row["id"],
                origin="reported",
                value=value,
                protocol=Protocol(**protocol, unit=unit),
            ),
        )
        for unit, value in [("m", 1), ("cm", 100)]
    ]
    figure = experiments.make_figure(store, "Equal distances", [item["id"] for item in items])
    files = figure["data"]["files"]
    original = json.loads(store.blob_path(files["inputs.json"]).read_text())
    normalization = json.loads(store.blob_path(files["normalization.json"]).read_text())
    assert [item["data"]["value"] for item in original] == [1, 100]
    assert [
        item["data"]["value"] * factor for item, factor in zip(original, normalization["factors"])
    ] == [1, 1]
    assert store.blob_path(files["figure.svg"]).stat().st_size > 100


def test_join_precedes_library_pagination_and_old_identifiers_are_searchable(store):
    with store.db(write=True) as db:
        for n in range(510):
            work = store.put(db, "work", WorkInput(title=f"Unique source {n}").model_dump())
            edition = catalog._create_edition(
                store, db, EditionInput(work_id=work["id"], doi=f"10.1234/source-{n}").model_dump()
            )
            if n == 0:
                oldest = edition["id"]
    catalog.link_project(store, oldest)
    rows = catalog.library(store, project_only=True)
    assert rows[0]["editions"][0]["id"] == oldest
    assert catalog.library(store, "10.1234/source-0")[0]["editions"][0]["id"] == oldest


def test_nonempty_scientific_symbol_query_is_never_an_unfiltered_list(store):
    for title in ["α coefficient", "β coefficient", "Unrelated source"]:
        catalog.create_work(store, WorkInput(title=title))
    assert [r["data"]["title"] for r in catalog.library(store, "α")] == ["α coefficient"]
    assert store.list("work", "∞") == []


def test_bibliography_conference_round_trip_retains_type_and_fields(store):
    text = "@inproceedings{HOI,title={An {HOI} Method},author={A and B},year={2026},booktitle={CVPR},pages={1--8},publisher={IEEE},volume={2},note={Supplement},customfield={Keep me}}"
    imported = catalog.import_bibliography(store, text, "bibtex")
    actual = bibtexparser.loads(
        catalog.bibliography(store, [imported["entries"][0]["edition_id"]])
    ).entries[0]
    expected = bibtexparser.loads(text).entries[0]
    assert actual == expected


@pytest.mark.parametrize(
    "before,after",
    [
        ("-0.12", "+0.12"),
        ("−0.12", "+0.12"),
        ("A outperforms B", "B outperforms A"),
        ("the error decreases", "the error increases"),
        ("power 1 mW", "power 1 MW"),
    ],
)
def test_scientific_meaning_reversal_is_flagged(before, after):
    assert manuscripts.factual_changes(before, after)


def test_plain_language_polish_is_not_a_fact_warning():
    assert (
        manuscripts.factual_changes("We use the proposed method.", "We apply the proposed method.")
        == []
    )


def test_restore_rejects_unlisted_directory_symlinks(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data/source.txt").write_text("owned")
    snapshot = tmp_path / "snapshot"
    backup(home, snapshot)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "extra.txt").write_text("must not be imported")
    (snapshot / "data/surprise").symlink_to(outside, target_is_directory=True)
    target = tmp_path / "restored"
    with pytest.raises(ValueError):
        restore(snapshot, target)
    assert not target.exists()


def test_read_only_repository_probe_never_executes_clean_filter(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    (root / "tracked.txt").write_text("initial")
    git("add", "tracked.txt")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    marker = tmp_path / "unapproved-write"
    git("config", "filter.review.clean", f"touch '{marker}'; cat")
    (root / ".gitattributes").write_text("tracked.txt filter=review\n")
    (root / "tracked.txt").write_text("modified")
    result = remote_reader.repository(root, [root])
    assert not marker.exists()
    assert result["dirty"] is None


def test_same_import_retry_has_one_run_identity(store):
    payload = json.dumps({"title": "Imported trial", "status": "static_only", "seed": 42})
    first = experiments.import_run_rows(store, payload, "json")
    again = experiments.import_run_rows(store, payload, "json")
    assert first[0]["id"] == again[0]["id"]
    assert len(store.list("run")) == 1


def test_comparative_polish_preserves_relations():
    assert manuscripts.factual_changes("A outperforms B.", "We find that A outperforms B.") == []


def test_structural_locator_does_not_confuse_list_question_and_table():
    from deeptutor.services.rag.pipelines.llamaindex.study_locator import locator_pattern

    question = locator_pattern("解释第7题")
    assert not question.search("7. ordinary list item")
    assert not question.search("(7) x = y")
    assert question.search("Exercise 7: Calculate the sum")
    table = locator_pattern("表 3b")
    assert table.search("Table 3b: Results")
    assert not table.search("Table 3b2: Other results")


def test_library_cursor_filters_before_pages(store):
    for n in range(7):
        catalog.create_work(store, WorkInput(title=f"Paged {n}"))
    ids, cursor = [], ""
    while True:
        page = catalog.library_page(store, "Paged", limit=2, cursor=cursor)
        ids += [r["id"] for r in page["works"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 7


def test_ten_thousand_work_library_can_find_old_editions_without_truncation(store):
    # Bulk fixture isolates the read path from ingestion throughput.
    with store.db(write=True) as db:
        rows = []
        for i in range(10000):
            work = WorkInput(title=f"Paper {i}" + (" ∞" if i == 0 else "")).model_dump()
            edition = EditionInput(work_id=f"work-{i}", doi=f"10.1234/paper-{i}").model_dump()
            edition.update(files=[], full_text="missing")
            rows.extend(
                [
                    (
                        f"work-{i}",
                        "work",
                        "@library",
                        1,
                        json.dumps(work, ensure_ascii=False),
                        i + 1,
                        i + 1,
                    ),
                    (f"edition-{i}", "edition", "@library", 1, json.dumps(edition), i + 1, i + 1),
                ]
            )
        db.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?)", rows)
        db.executemany(
            "INSERT INTO revisions VALUES (?,?,?,?)", [(r[0], 1, r[4], r[5]) for r in rows]
        )
        db.execute(
            "INSERT INTO project_library VALUES (?,?,?,?)",
            (store.project, "edition-0", "reading", 1),
        )
        plan = str(
            [
                tuple(r)
                for r in db.execute(
                    "EXPLAIN QUERY PLAN SELECT w.id FROM records w WHERE w.id=? AND EXISTS (SELECT 1 FROM records e WHERE e.kind='edition' AND json_extract(e.data,'$.work_id')=+w.id)",
                    ("work-0",),
                )
            ]
        )
        assert re.search(r"SEARCH e USING INDEX editions_by_work .*<expr>=\?", plan)
    assert catalog.library_page(store, "∞")["works"][0]["id"] == "work-0"
    assert (
        catalog.library_page(store, "10.1234/paper-0")["works"][0]["editions"][0]["id"]
        == "edition-0"
    )
    assert catalog.library_page(store, project_only=True)["works"][0]["id"] == "work-0"
    seen, cursor = set(), ""
    while True:
        page = catalog.library_page(store, limit=200, cursor=cursor)
        ids = {row["id"] for row in page["works"]}
        assert not seen & ids
        seen |= ids
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 10000


def test_legacy_verification_is_visible_but_never_promoted(store):
    with store.db(write=True) as db:
        record = store.put(db, "evidence", {"verification": "confirmed", "resolution": "resolved"})
    assert record["data"]["verification"] == "unreviewed"
    assert record["data"]["legacy_verification"] == "confirmed"
    with store.db() as db:
        assert (
            json.loads(
                db.execute("SELECT data FROM revisions WHERE id=?", (record["id"],)).fetchone()[0]
            )["verification"]
            == "confirmed"
        )


def test_proposal_file_write_failure_recovers_one_receipt(store, monkeypatch):
    from deeptutor.studio.models import ManuscriptInput, ProposalInput

    manuscript = manuscripts.create_manuscript(store, ManuscriptInput(title="Crash receipt"))
    current = manuscripts.read_file(store, manuscript["id"], "main.tex")
    proposal = manuscripts.propose(
        store,
        manuscript["id"],
        ProposalInput(
            path="main.tex", base_hash=current["hash"], text="% reviewed\n", start=0, end=0
        ),
    )
    real = store.put

    def fail(db, kind, *args, **kwargs):
        if kind == "proposal":
            raise OSError("simulated database failure after file replacement")
        return real(db, kind, *args, **kwargs)

    monkeypatch.setattr(store, "put", fail)
    with pytest.raises(OSError):
        manuscripts.accept_proposal(store, proposal["id"], 1)
    monkeypatch.setattr(store, "put", real)
    accepted = manuscripts.accept_proposal(store, proposal["id"], 1)
    retried = manuscripts.accept_proposal(store, proposal["id"], 1)
    assert accepted == retried
    assert accepted["data"]["receipt"]["state"] == "applied"
    assert len(store.list("file_change")) == 1
    assert (
        manuscripts.read_file(store, manuscript["id"], "main.tex")["text"].count("% reviewed") == 1
    )


def test_import_key_cannot_be_reused_for_different_measurement(store):
    row = {
        "title": "Trial",
        "status": "static_only",
        "import_identity": {"idempotency_key": "operation-one"},
    }
    first = experiments.import_run_rows(store, json.dumps(row), "json")
    second = experiments.import_run_rows(store, json.dumps({**row, "seed": 2}), "json")
    assert first[0]["status"] == "imported"
    assert second[0]["status"] == "error"
    assert len(store.list("run")) == 1
