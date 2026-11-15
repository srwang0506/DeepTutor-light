"""Practice must preserve source evidence while imports/reviews are retry safe."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest

from deeptutor.api.routers import practice, question_notebook
from deeptutor.core.assessment import ASSESSMENT_SOURCES
from deeptutor.services.practice.importing import normalize_question, preview
from deeptutor.services.practice.scheduler import DAY, day_bounds, schedule
from deeptutor.services.practice.storage import PracticeStore, ReviewConflict
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.fixture
def bank(tmp_path, monkeypatch):
    store = SQLiteSessionStore(tmp_path / "practice.db")
    for module in (practice, question_notebook):
        monkeypatch.setattr(module, "get_sqlite_session_store", lambda: store)
    app = FastAPI()
    app.include_router(question_notebook.router, prefix="/bank")
    app.include_router(practice.router, prefix="/practice")
    with TestClient(app) as client:
        yield store, PracticeStore(store.db_path), client


def seed(store, **changes):
    async def run():
        await store.ensure_notebook_session("lesson", "Lesson")
        item = {
            "question_id": "q1",
            "question": "2 + 2?",
            "question_type": "single_choice",
            "options": {"A": "3", "B": "4"},
            "correct_answer": "B",
            "user_answer": "A",
            "is_correct": False,
            **changes,
        }
        await store.upsert_notebook_entries("lesson", [item])
        return await store.find_notebook_entry(
            "lesson", item["question_id"], item.get("turn_id", "")
        )

    return asyncio.run(run())


@pytest.mark.parametrize("source", sorted(ASSESSMENT_SOURCES))
def test_every_producer_enrolls_mistakes_once(bank, source):
    store, repo, _ = bank
    entry = seed(store, source=source)
    first = repo.state(entry["id"])
    assert first["due_at"] == entry["created_at"] + DAY
    repo.review(entry["id"], "first_review", first["version"], "good", "B")
    reviewed = repo.state(entry["id"])
    seed(store, source=source)  # idempotent source re-save cannot reset the schedule
    assert repo.state(entry["id"]) == reviewed


def test_ungraded_imports_are_not_mistakes_and_review_preserves_original(bank):
    store, repo, client = bank
    entry = seed(store, result="ungraded")
    assert repo.overview("UTC")["mistakes"] == 0
    payload = {"request_id": "submission_00000001", "version": 0, "rating": "again", "answer": "A"}
    assert client.post(f"/practice/questions/{entry['id']}/review", json=payload).status_code == 200
    assert repo.overview("UTC")["mistakes"] == 1
    original = asyncio.run(store.get_notebook_entry(entry["id"]))
    for field in ("user_answer", "result", "is_correct", "correct_answer", "created_at"):
        assert original[field] == entry[field]
    assert client.get("/bank/entries?mistakes_only=true").json()["total"] == 1


def test_duplicate_review_and_two_tabs_cannot_advance_twice(bank):
    store, repo, _ = bank
    entry = seed(store)
    args = (entry["id"], "same_request", 0, "good", "B")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: repo.review(*args), range(2)))
    assert results[0] == results[1]
    assert repo.state(entry["id"])["review_count"] == 1
    with pytest.raises(ReviewConflict):
        repo.review(entry["id"], "other_request", 0, "easy", "B")
    with pytest.raises(ReviewConflict):
        repo.review(entry["id"], "same_request", 0, "again", "A")


def test_server_rejects_incorrect_answer_with_success_rating(bank):
    store, repo, client = bank
    entry = seed(store)
    response = client.post(
        f"/practice/questions/{entry['id']}/review",
        json={"request_id": "submission_00000001", "version": 0, "rating": "good", "answer": "A"},
    )
    assert response.status_code == 422
    assert repo.state(entry["id"])["review_count"] == 0


def test_source_key_change_invalidates_open_question(bank):
    store, repo, _ = bank
    entry = seed(store)
    seed(store, correct_answer="A")
    with pytest.raises(ReviewConflict):
        repo.review(entry["id"], "stale", 0, "good", "A")


def test_timezone_due_order_and_resolved_entries(bank):
    store, repo, client = bank
    old = seed(store)
    new = seed(store, question_id="q2")
    with repo.connect() as conn:
        conn.execute("UPDATE practice_review_state SET due_at=100 WHERE entry_id=?", (old["id"],))
        conn.execute("UPDATE practice_review_state SET due_at=200 WHERE entry_id=?", (new["id"],))
    assert repo.queue("Asia/Shanghai", now=DAY) == [old["id"], new["id"]]
    asyncio.run(store.update_notebook_entry(old["id"], {"resolved": True}))
    assert repo.queue("UTC", now=DAY) == [new["id"]]
    assert repo.overview("UTC", now=DAY)["mistakes"] == 2
    with repo.connect() as conn:
        conn.execute("UPDATE sessions SET deleted_at=1 WHERE id='lesson'")
    assert repo.overview("UTC", now=DAY)["total"] == 0
    assert repo.queue("UTC", now=DAY) == []
    assert client.get(f"/practice/questions/{old['id']}").status_code == 404
    assert (
        client.post(f"/practice/questions/{old['id']}/check", json={"answer": "B"}).status_code
        == 404
    )


def test_first_mistake_timestamp_follows_first_failed_practice(bank):
    store, repo, _ = bank
    entry = seed(store, result="ungraded")
    repo.review(entry["id"], "correct_first", 0, "good", "B", now=100)
    repo.review(entry["id"], "wrong_later", 1, "again", "A", now=200)
    assert repo.state(entry["id"])["first_wrong_at"] == 200


def test_source_mistake_after_success_uses_the_actual_wrong_date(bank):
    store, repo, _ = bank
    entry = seed(store, result="ungraded")
    repo.review(entry["id"], "initial-success", 0, "good", "B", now=100)
    with repo.connect() as conn:
        conn.execute(
            "UPDATE notebook_entries SET result='incorrect', is_correct=0, updated_at=200 WHERE id=?",
            (entry["id"],),
        )
    assert repo.state(entry["id"])["first_wrong_at"] == 200


def test_oversized_spreadsheet_dimensions_and_csv_cells_are_rejected(bank):
    _, _, client = bank
    book = Workbook()
    book.active.cell(row=1, column=1000, value="question")
    output = io.BytesIO()
    book.save(output)
    book.close()
    for name, data in [
        ("wide.xlsx", output.getvalue()),
        ("large.csv", b"question,answer\n" + b"a" * 200000 + b",yes\n"),
    ]:
        response = client.post("/practice/import/preview", files={"file": (name, data)})
        assert response.status_code == 422


def test_import_preview_commit_retries_dedup_tags_and_no_source_overwrite(bank):
    store, repo, client = bank
    data = "题目,题型,A,B,答案,标签\n2+2=?,单选题,3,4,B,数学\n".encode()
    result = client.post(
        "/practice/import/preview", files={"file": ("questions.csv", data)}, data={"target": "bank"}
    ).json()
    assert result["valid"] == 1 and result["token"]
    assert repo.overview("UTC")["total"] == 0  # preview does not alter the bank
    commit = client.post("/practice/import/commit", json={"token": result["token"]})
    assert commit.json() == {"created": 1, "duplicates": 0, "total": 1}
    assert (
        client.post("/practice/import/commit", json={"token": result["token"]}).json()
        == commit.json()
    )
    questions = [
        normalize_question({"question": "2+2=?", "A": "3", "B": "4", "answer": "B", "tags": "复习"})
    ]
    again = repo.commit_import(repo.stage_import("again.json", "mistakes", questions))
    assert again["duplicates"] == 1
    listing = asyncio.run(store.list_notebook_entries())
    assert listing["total"] == 1
    entry = listing["items"][0]
    assert entry["result"] == "ungraded"
    assert {category["name"] for category in entry["categories"]} == {"数学", "复习"}
    assert repo.overview("UTC")["mistakes"] == 1


def test_invalid_row_blocks_entire_import_and_reports_line(bank):
    _, repo, client = bank
    result = client.post(
        "/practice/import/preview",
        files={"file": ("file.csv", b"question,answer\ngood,yes\nbad,\n")},
    ).json()
    assert result["token"] is None
    assert result["errors"][0]["row"] == 3
    assert result["valid"] == 1
    assert repo.overview("UTC")["total"] == 0


def test_template_round_trips_and_formulas_are_rejected(bank):
    _, _, client = bank
    for format in ("csv", "xlsx"):
        data = client.get(f"/practice/import/template?format={format}").content
        assert len(preview(data, f"template.{format}")["questions"]) == 4
    book = Workbook()
    book.active.append(["question", "answer"])
    book.active.append(["=1+1", "2"])
    file = io.BytesIO()
    book.save(file)
    with pytest.raises(ValueError, match="formulas"):
        preview(file.getvalue(), "formulas.xlsx")


def test_multi_select_text_and_json_validation():
    row = normalize_question(
        {"题干": "Primes?", "题型": "多选题", "options": ["2", "3", "4"], "答案": "AB"}
    )
    assert row["correct_answer"] == "A,B"
    assert preview(json.dumps([row]).encode(), "q.json")["questions"][0]["question"] == "Primes?"
    with pytest.raises(ValueError, match="500"):
        preview(json.dumps([row] * 501).encode(), "many.json")


def test_additive_migration_is_idempotent(bank):
    store, repo, _ = bank
    entry = seed(store)
    with repo.connect() as conn:
        for trigger in (
            "practice_capture_insert",
            "practice_capture_update",
            "practice_invalidate_question",
        ):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DROP TABLE practice_review_state")
    upgraded = SQLiteSessionStore(store.db_path)
    state = repo.state(entry["id"])
    assert state["first_wrong_at"] == entry["created_at"]
    restored = asyncio.run(upgraded.get_notebook_entry(entry["id"]))
    assert {k: v for k, v in restored.items() if k not in {"practice", "categories"}} == entry
    SQLiteSessionStore(store.db_path)
    assert repo.state(entry["id"]) == state
    asyncio.run(upgraded.delete_notebook_entry(entry["id"]))
    with repo.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM practice_review_state").fetchone()[0] == 0


def test_import_transaction_rolls_back(bank):
    _, repo, _ = bank
    token = repo.stage_import(
        "q.json", "bank", [normalize_question({"question": "Q", "answer": "A"})]
    )
    with repo.connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_import BEFORE INSERT ON notebook_entries BEGIN SELECT RAISE(ABORT, 'unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        repo.commit_import(token)
    assert repo.overview("UTC")["total"] == 0
    with repo.connect() as conn:
        conn.execute("DROP TRIGGER reject_import")
    assert repo.commit_import(token)["created"] == 1


def test_daily_scheduler_and_dst():
    now = datetime(2026, 3, 8, 12, tzinfo=timezone.utc).timestamp()
    start, end = day_bounds("America/New_York", now)
    assert end - start == 23 * 3600
    with pytest.raises(ValueError):
        day_bounds("not/a-zone", now)
    good = schedule({}, "good", now)
    better = schedule(good, "good", good["due_at"])
    again = schedule(better, "again", better["due_at"])
    assert good["interval_days"] < better["interval_days"]
    assert 0 < again["interval_days"] < 1
    assert again["lapses"] == 1 and again["review_count"] == 3
    assert schedule({}, "easy", now)["due_at"] > good["due_at"]
    hard = schedule(better, "hard", now)
    assert (
        schedule(hard, "good", now)["interval_days"] >= schedule(hard, "hard", now)["interval_days"]
    )


def test_malformed_files_are_validation_errors(bank):
    _, _, client = bank
    for filename, data in [
        ("broken.xlsx", b"not a workbook"),
        ("broken.json", b"{"),
        ("old.xls", b"old format"),
    ]:
        response = client.post("/practice/import/preview", files={"file": (filename, data)})
        assert response.status_code == 422


def test_import_receipts_and_questions_are_isolated_between_stores(bank, tmp_path):
    _, repo, _ = bank
    token = repo.stage_import(
        "q.json", "bank", [normalize_question({"question": "Q", "answer": "A"})]
    )
    other = SQLiteSessionStore(tmp_path / "other-user.db")
    other_repo = PracticeStore(other.db_path)
    with pytest.raises(LookupError):
        other_repo.commit_import(token)
    repo.commit_import(token)
    assert other_repo.overview("UTC")["total"] == 0
    with pytest.raises(LookupError):
        other_repo.review(1, "other-user-attempt", 0, "again", "")


def test_imports_keep_file_provenance_without_creating_sessions(bank):
    store, repo, client = bank
    question = normalize_question({"question": "Q", "answer": "A"})
    repo.commit_import(repo.stage_import("lesson.csv", "mistakes", [question]))
    listing = asyncio.run(store.list_notebook_entries())
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["session_id"] == ""
    assert item["origin_type"] == "external_import"
    assert item["origin_ref"] == "practice-import"
    assert item["material_id"].startswith("import:")
    assert repo.overview("UTC")["mistakes"] == 1
    assert asyncio.run(store.list_sessions()) == []
    assert client.get("/bank/entries").json()["total"] == 1
    assert client.get("/practice/summary").json()["mistakes"] == 1


def test_analytics_separates_creation_first_mistake_and_repeat_reviews(bank):
    store, repo, client = bank
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc).timestamp()
    entry = seed(store, source="mastery_path", result="ungraded")
    with repo.connect() as conn:
        conn.execute(
            "UPDATE notebook_entries SET created_at=? WHERE id=?", (now - 3 * DAY, entry["id"])
        )
    repo.review(entry["id"], "first-wrong", 0, "again", "A", now=now - DAY)
    repo.review(entry["id"], "second-wrong", 1, "again", "A", now=now)
    repo.review(entry["id"], "second-wrong", 1, "again", "A", now=now)
    report = repo.analytics("Asia/Shanghai", 7, now=now)
    assert report["totals"] == {"questions": 1, "mistakes": 1, "reviews": 2}
    assert len(report["daily"]) == 7
    assert report["daily"][-4] == {
        "date": "2026-09-16",
        "questions": 1,
        "mistakes": 0,
        "reviews": 0,
    }
    assert report["daily"][-2]["mistakes"] == 1
    assert report["daily"][-1]["mistakes"] == 0
    assert report["sources"] == [{"source": "mastery_path", **report["totals"]}]
    asyncio.run(store.update_notebook_entry(entry["id"], {"resolved": True}))
    assert repo.analytics("Asia/Shanghai", 7, now=now)["totals"] == report["totals"]
    assert client.get("/practice/analytics?days=8").status_code == 422
    assert client.get("/practice/analytics?timezone=not-a-zone").status_code == 422


def test_analytics_local_midnight_dst_zero_days_and_deleted_sources(bank):
    store, repo, _ = bank
    now = datetime(2026, 3, 10, 12, tzinfo=timezone.utc).timestamp()
    # These straddle local midnight during the daylight-saving transition.
    entries = [seed(store, question_id=f"dst-{i}", source="book") for i in range(2)]
    timestamps = [
        datetime(2026, 3, 8, 4, 59, tzinfo=timezone.utc).timestamp(),
        datetime(2026, 3, 8, 5, 1, tzinfo=timezone.utc).timestamp(),
    ]
    with repo.connect() as conn:
        for entry, ts in zip(entries, timestamps, strict=True):
            conn.execute("UPDATE notebook_entries SET created_at=? WHERE id=?", (ts, entry["id"]))
            conn.execute(
                "UPDATE practice_review_state SET first_wrong_at=? WHERE entry_id=?",
                (ts, entry["id"]),
            )
    data = repo.analytics("America/New_York", 7, now=now)
    by_day = {row["date"]: row for row in data["daily"]}
    assert by_day["2026-03-07"]["questions"] == by_day["2026-03-08"]["questions"] == 1
    assert by_day["2026-03-09"]["questions"] == 0
    with repo.connect() as conn:
        conn.execute("UPDATE sessions SET deleted_at=? WHERE id='lesson'", (now,))
    assert repo.analytics("America/New_York", 7, now=now)["sources"] == []


@pytest.fixture
def learning_targets(tmp_path, monkeypatch):
    from deeptutor.learning.models import (
        KnowledgePoint,
        KnowledgeType,
        LearningModule,
        LearningProgress,
        TopicMetadata,
    )
    from deeptutor.learning.storage import LearningStore

    learning = LearningStore(root=tmp_path / "learning")
    learning.save(
        LearningProgress(
            book_id="dsa",
            name="Data Structures and Algorithms",
            modules=[
                LearningModule(
                    id="graphs",
                    name="Graphs",
                    order=0,
                    knowledge_points=[
                        KnowledgePoint(
                            id="bfs",
                            module_id="graphs",
                            name="Why BFS uses a queue",
                            type=KnowledgeType.CONCEPT,
                        ),
                    ],
                )
            ],
            knowledge_types={"bfs": KnowledgeType.CONCEPT},
            mastery_levels={"bfs": 0.35},
        )
    )
    learning.put_topic(TopicMetadata(path_id="dsa", goal="Study Data Structures"), [])
    monkeypatch.setattr("deeptutor.learning.storage.LearningStore", lambda: learning)
    monkeypatch.setattr("deeptutor.modules.catalog.module_enabled", lambda name: name == "mastery")
    return learning


def test_binding_and_first_checked_answer_update_retention_without_unlocking_mastery(
    bank, learning_targets
):
    store, repo, client = bank
    entry = seed(
        store,
        source="immersive_reading",
        question="Which structure does BFS use?",
        options={
            "A": "Stack",
            "B": "Queue",
        },
    )
    url = f"/practice/questions/{entry['id']}"
    body = client.get(url).json()
    assert body["entry"]["correct_answer"] == body["entry"]["explanation"] == ""
    assert client.get("/practice/objectives").json()["paths"][0]["objectives"] == [
        {"id": "bfs", "name": "Why BFS uses a queue"},
    ]
    bad = {"version": 0, "objective": {"path_id": "other-workspace", "knowledge_point_id": "bfs"}}
    assert client.put(url + "/objective", json=bad).status_code == 422
    binding = {"version": 0, "objective": {"path_id": "dsa", "knowledge_point_id": "bfs"}}
    linked = client.put(url + "/objective", json=binding).json()["state"]
    assert linked["version"] == 1
    assert linked["objective"] == binding["objective"]
    assert linked["due_at"] == entry["created_at"] + DAY

    attempt = {"request_id": "practice-first-answer", "answer": "B", "version": 1}
    checked = client.post(url + "/check", json=attempt).json()
    assert checked["correct"] is True and checked["correct_answer"] == "B"
    assert client.post(url + "/check", json={**attempt, "answer": "A"}).status_code == 409
    assert (
        client.post(url + "/review", json={**attempt, "answer": "A", "rating": "again"}).status_code
        == 409
    )
    receipt = client.post(url + "/review", json={**attempt, "rating": "good"}).json()
    assert receipt["independent"] is True and receipt["objective_evidence"] == "applied"
    assert client.post(url + "/review", json={**attempt, "rating": "good"}).json() == receipt

    progress = learning_targets.load("dsa")
    assert progress.mastery_levels == {"bfs": 0.35}
    assert progress.qualitative_mastery == {}
    assert progress.repetition_states["bfs"].review_count == 1
    (evidence,) = progress.learning_evidence
    assert evidence.result == "correct" and evidence.learner_answer == "B"
    assert evidence.evidence_id == "practice:practice-first-answer"
    saved = asyncio.run(store.get_notebook_entry(entry["id"]))
    assert saved["user_answer"] == "A"  # The source attempt remains history.
    assert saved["practice"]["last_answer"] == "B"

    from deeptutor.services.session._turn_runtime_shared import _format_question_bank_entry

    context = _format_question_bank_entry(saved)
    assert "**Latest saved practice answer:** B" in context
    assert "**User's Answer:** A" not in context


def test_same_round_correction_and_legacy_or_self_report_do_not_project(bank, learning_targets):
    from deeptutor.services.practice.projection import reconcile_practice_evidence

    store, repo, _ = bank
    entry = seed(store, source="mastery_path", mastery_path_id="dsa", knowledge_point_id="bfs")
    key = entry["id"]
    repo.check(key, "A", "first", 0, now=100)
    assert repo.review(key, "first", 0, "again", "A", now=101)["independent"]
    repo.check(key, "B", "same_round", 1, now=102)
    assert not repo.review(key, "same_round", 1, "good", "B", now=103)["independent"]
    assert not repo.review(key, "self", 2, "good", "", now=104, self_report=True)["independent"]
    assert not repo.review(key, "legacy_no_check", 3, "good", "B", now=105)["independent"]
    repo.check(key, "B", "delayed", 4, now=DAY + 103)
    assert repo.review(key, "delayed", 4, "good", "B", now=DAY + 104)["independent"]
    reconcile_practice_evidence(repo)
    assert [e.result for e in learning_targets.load("dsa").learning_evidence] == [
        "incorrect",
        "correct",
    ]
    assert repo.state(key)["review_count"] == 5


def test_practice_projection_replays_after_acknowledgement_failure(
    bank, learning_targets, monkeypatch
):
    from deeptutor.services.practice.projection import reconcile_practice_evidence

    store, repo, client = bank
    entry = seed(store, source="mastery_path", mastery_path_id="dsa", knowledge_point_id="bfs")
    key = entry["id"]
    original = PracticeStore.finish_evidence

    def interrupted(*args):
        raise RuntimeError("interrupted after projection")

    monkeypatch.setattr(PracticeStore, "finish_evidence", interrupted)
    repo.check(key, "B", "restart_test", 0, now=100)
    repo.review(key, "restart_test", 0, "good", "B", now=101)
    reconcile_practice_evidence(repo)
    assert repo.evidence_status("restart_test") == "pending"
    assert len(learning_targets.load("dsa").learning_evidence) == 1

    monkeypatch.setattr(PracticeStore, "finish_evidence", original)
    restarted = PracticeStore(repo.db_path)
    assert client.get(f"/practice/questions/{key}").status_code == 200
    assert restarted.evidence_status("restart_test") == "applied"
    assert len(learning_targets.load("dsa").learning_evidence) == 1


def test_original_quiz_disclosure_remains_ineligible_even_after_one_day(bank, learning_targets):
    store, repo, _ = bank
    entry = seed(
        store,
        turn_id="turn-id",
        source="mastery_path",
        mastery_path_id="dsa",
        knowledge_point_id="bfs",
    )

    async def expose():
        await store.begin_turn("lesson", capability="deep_question", turn_id="turn-id")
        contract = await store.register_quiz_question(
            "lesson",
            "turn-id",
            {
                "question": entry["question"],
                "options": entry["options"],
                "correct_answer": entry["correct_answer"],
                "question_id": entry["question_id"],
            },
        )
        await store.record_quiz_exposure("lesson", contract, "revealed", "reference-view")

    asyncio.run(expose())
    from time import time

    moment = time() + 2 * DAY
    repo.check(entry["id"], "B", "after_formal_reveal", 0, now=moment)
    receipt = repo.review(entry["id"], "after_formal_reveal", 0, "good", "B", now=moment + 1)
    assert not receipt["independent"]
    assert repo.pending_evidence() == []


def test_source_revocation_blocks_new_review_and_pending_projection(
    bank, learning_targets, monkeypatch
):
    from deeptutor.services.practice.projection import reconcile_practice_evidence

    store, repo, client = bank
    entry = seed(store, source="immersive_reading", material_id="reading-material")
    url = f"/practice/questions/{entry['id']}"
    binding = {"version": 0, "objective": {"path_id": "dsa", "knowledge_point_id": "bfs"}}
    assert client.put(url + "/objective", json=binding).status_code == 200
    revoked = False

    def check_access(material):
        assert material == "reading-material"
        if revoked:
            raise PermissionError("revoked")

    monkeypatch.setattr(
        "deeptutor.multi_user.learning_access.assert_learning_material", check_access
    )
    body = {"request_id": "after-reading-grant", "answer": "B", "version": 1}
    assert client.post(url + "/check", json=body).status_code == 200
    revoked = True
    assert client.post(url + "/check", json=body).status_code == 403
    assert client.post(url + "/review", json={**body, "rating": "good"}).status_code == 403
    assert repo.state(entry["id"])["review_count"] == 0

    revoked = False
    # Save the transaction, then simulate stopping before the path projection.
    receipt = repo.review(entry["id"], body["request_id"], 1, "good", "B")
    assert receipt["independent"]
    revoked = True
    reconcile_practice_evidence(repo)
    assert learning_targets.load("dsa").learning_evidence == []
    replay = client.post(url + "/review", json={**body, "rating": "good"})
    assert replay.status_code == 200
    assert replay.json() == {**receipt, "objective_evidence": "unavailable"}
    assert repo.state(entry["id"])["review_count"] == 1
