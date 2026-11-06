import asyncio

from pydantic import ValidationError
import pytest
import pytest_asyncio

from deeptutor.learning.quiz import QuizSubmission, question_contract, submit_quiz
from deeptutor.learning.semantic_assessment import AssessmentUnavailable, Verdict
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest_asyncio.fixture
async def quiz(tmp_path):
    store = SQLiteSessionStore(db_path=tmp_path / "session.db")
    await store.create_session(title="Test", session_id="s")
    await store.begin_turn("s", turn_id="t")
    q = await store.register_quiz_question(
        "s",
        "t",
        {
            "question_id": "q",
            "question": "2 + 2?",
            "question_type": "choice",
            "options": {"A": "4", "B": "5"},
            "correct_answer": "A",
            "explanation": "2 + 2 = 4",
            "metadata": {},
        },
    )
    return store, QuizSubmission(
        session_id="s",
        turn_id="t",
        question_id="q",
        question_version=q["question_version"],
        attempt_id="attempt-1234567890",
        answer="A",
    )


@pytest.mark.asyncio
async def test_duplicate_submissions_commit_one_attempt(quiz):
    store, request = quiz
    await asyncio.gather(*(submit_quiz(store, request) for _ in range(5)))
    receipt = await store.quiz_submission(request.attempt_id, "s")
    assert receipt["result"] == "correct"
    assert receipt["assessment_committed"]
    assert len(await store.list_assessment_attempts("s")) == 1
    with pytest.raises(ValueError, match="different answer"):
        await submit_quiz(store, request.model_copy(update={"answer": "B"}))
    assert await store.quiz_submission(request.attempt_id, "other") is None


@pytest.mark.asyncio
async def test_timeout_saved_ungraded_then_retry_same_submission(quiz, monkeypatch):
    store, request = quiz
    monkeypatch.setattr("deeptutor.learning.quiz.rule_verdict", lambda *_: None)

    async def unavailable(*_):
        raise AssessmentUnavailable("temporary")

    monkeypatch.setattr("deeptutor.learning.quiz.judge_answers", unavailable)
    receipt = await submit_quiz(store, request)
    assert receipt["state"] == "ungraded" and not receipt["assessment_committed"]
    assert await store.list_assessment_attempts("s") == []

    async def recovered(*_):
        return {"q": Verdict(result="partial", confidence=0.6, feedback="One step is missing")}

    monkeypatch.setattr("deeptutor.learning.quiz.judge_answers", recovered)
    receipt = await submit_quiz(store, request.model_copy(update={"retry": True}))
    assert receipt["assessment_committed"] and receipt["result"] == "partial"
    assert len(await store.list_assessment_attempts("s")) == 1


@pytest.mark.asyncio
async def test_cancellation_fences_late_verdict(quiz, monkeypatch):
    store, request = quiz
    started, finish = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr("deeptutor.learning.quiz.rule_verdict", lambda *_: None)

    async def delayed(*_):
        started.set()
        await finish.wait()
        return {"q": Verdict(result="correct", confidence=1, feedback="Correct")}

    monkeypatch.setattr("deeptutor.learning.quiz.judge_answers", delayed)
    task = asyncio.create_task(submit_quiz(store, request))
    await started.wait()
    await store.cancel_quiz_submission(request.attempt_id, "s")
    finish.set()
    assert (await task)["state"] == "cancelled"
    assert await store.list_assessment_attempts("s") == []


@pytest.mark.asyncio
async def test_cancel_after_commit_preserves_grade(quiz):
    store, request = quiz
    await submit_quiz(store, request)
    receipt = await store.cancel_quiz_submission(request.attempt_id, "s")
    assert receipt["assessment_committed"] and receipt["result"] == "correct"


@pytest.mark.asyncio
async def test_projection_failure_does_not_erase_grade_and_recovers_after_restart(
    quiz, monkeypatch
):
    store, request = quiz

    def failure(*args):
        raise RuntimeError("notebook temporarily unavailable")

    monkeypatch.setattr(store, "_upsert_notebook_entries_in_conn", failure)
    receipt = await submit_quiz(store, request)
    assert receipt["assessment_committed"]
    assert receipt["projection_status"] == "pending"
    restarted = SQLiteSessionStore(db_path=store.db_path)
    assert await restarted.reconcile_quiz_projections() == 1
    assert await restarted.reconcile_quiz_projections() == 0
    assert (await restarted.quiz_submission(request.attempt_id, "s"))[
        "projection_status"
    ] == "complete"
    assert len(await restarted.list_assessment_attempts("s")) == 1


@pytest.mark.asyncio
async def test_failed_contract_and_wrong_scope_are_not_answerable(quiz):
    store, request = quiz
    bad = await store.register_quiz_question(
        "s",
        "t",
        {
            "question_id": "bad",
            "question": "Incomplete",
            "correct_answer": "A",
            "metadata": {"issues": ["missing_options"]},
        },
    )
    with pytest.raises(ValueError, match="failed validation"):
        await question_contract(store, "s", "t", "bad", bad["question_version"])
    with pytest.raises(ValueError):
        await question_contract(store, "other", "t", "q")
    with pytest.raises(ValueError):
        await question_contract(store, "s", "t", "q", "not-the-version")
    with pytest.raises(ValidationError):
        QuizSubmission(**(request.model_dump() | {"correct_answer": "B"}))


@pytest.mark.asyncio
async def test_reference_viewed_does_not_count_as_mastery(quiz):
    store, request = quiz
    receipt = await submit_quiz(store, request.model_copy(update={"assistance": "revealed"}))
    assert receipt["state"] == "ungraded"
    assert await store.list_assessment_attempts("s") == []


def test_attempt_status_route_precedes_generic_question_route():
    from deeptutor.api.routers.quiz import router

    paths = [route.path for route in router.routes]
    assert paths.index("/attempts/{session_id}/{attempt_id}") < paths.index(
        "/{session_id}/{turn_id}/{question_id}"
    )


@pytest.mark.asyncio
async def test_image_answer_is_validated_graded_and_kept_in_notebook(quiz, monkeypatch, tmp_path):
    import base64
    from io import BytesIO

    from PIL import Image

    from deeptutor.services.storage.attachment_store import LocalDiskAttachmentStore

    store, request = quiz
    attachments = LocalDiskAttachmentStore(root=tmp_path / "images")
    monkeypatch.setattr("deeptutor.services.storage.get_attachment_store", lambda: attachments)
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")

    async def judge(_config, items):
        assert items[0]["_visual_images"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        return {
            "q": Verdict(result="correct", confidence=0.7, feedback="The image answer is correct")
        }

    monkeypatch.setattr("deeptutor.learning.quiz.judge_answers", judge)
    receipt = await submit_quiz(
        store,
        request.model_copy(
            update={
                "answer": "",
                "answer_images": [{"base64": base64.b64encode(buffer.getvalue()).decode()}],
            }
        ),
    )
    assert receipt["assessment_committed"]
    entry = await store.get_notebook_entry(receipt["entry_id"])
    assert len(entry["user_answer_images"]) == 1
    assert entry["user_answer_images"][0]["url"].startswith("/files/attachments/s/")
    assert not entry["user_answer_images"][0].get("base64")


@pytest.mark.asyncio
async def test_source_access_change_during_grading_never_commits(quiz, monkeypatch):
    store, request = quiz
    allowed = True

    def validate(_):
        if not allowed:
            raise PermissionError("source removed")

    async def judge(*_):
        nonlocal allowed
        allowed = False
        return {"q": Verdict(result="correct", confidence=1, feedback="yes")}

    monkeypatch.setattr("deeptutor.learning.quiz.validate_source_scope", validate)
    monkeypatch.setattr("deeptutor.learning.quiz.rule_verdict", lambda *_: None)
    monkeypatch.setattr("deeptutor.learning.quiz.judge_answers", judge)
    receipt = await submit_quiz(store, request)
    assert receipt["state"] == "ungraded"
    assert await store.list_assessment_attempts("s") == []


def test_source_scope_accepts_current_and_preserved_revisions(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from deeptutor.learning.quiz import validate_source_scope
    from deeptutor.reading.store import ReadingStore

    store = ReadingStore(tmp_path / "reading")
    source = tmp_path / "source.txt"
    source.write_text("A source for an exercise.")
    material = store.ingest(source)
    monkeypatch.setattr("deeptutor.reading.store.ReadingStore", lambda: store)
    monkeypatch.setattr(
        "deeptutor.multi_user.learning_access.assert_learning_material", lambda _: None
    )
    contract = {
        "metadata": {
            "source_scope": {
                "reading_material_id": material.material_id,
                "reading_material_revision": 1,
            }
        }
    }
    validate_source_scope(contract)
    monkeypatch.setattr(store, "manifest", lambda _: SimpleNamespace(revision=2))
    monkeypatch.setattr(store, "revisions", lambda _: [SimpleNamespace(revision=1)])
    validate_source_scope(contract)
    monkeypatch.setattr(store, "revisions", lambda _: [])
    with pytest.raises(ValueError, match="original material version"):
        validate_source_scope(contract)


@pytest.mark.asyncio
async def test_new_attempt_cannot_erase_reference_exposure(quiz):
    store, request = quiz
    await submit_quiz(store, request.model_copy(update={"assistance": "revealed"}))
    receipt = await submit_quiz(
        store, request.model_copy(update={"attempt_id": "new-attempt-1234567890"})
    )
    assert receipt["result"] == "correct"
    assert receipt["independent"] is False
    assert receipt["evidence_kind"] == "correction"
    attempts = await store.list_assessment_attempts("s")
    assert attempts[0]["hints_used"] > 0


@pytest.mark.asyncio
async def test_exposure_survives_restart_before_any_submission(quiz):
    store, request = quiz
    contract = await question_contract(store, "s", "t", "q")
    await store.record_quiz_exposure("s", contract, "hint", "reveal-event-123456")
    restarted = SQLiteSessionStore(db_path=store.db_path)
    receipt = await submit_quiz(restarted, request)
    assert receipt["evidence_kind"] == "assisted"
    assert receipt["independent"] is False


@pytest.mark.asyncio
async def test_old_assisted_attempt_is_backfilled_on_upgrade(quiz):
    store, request = quiz
    await submit_quiz(store, request.model_copy(update={"assistance": "revealed"}))
    # Simulate the schema immediately before the exposure ledger existed.
    with store._connect() as conn:
        conn.execute("DELETE FROM quiz_exposures")
        conn.execute("DELETE FROM quiz_migrations")
    restarted = SQLiteSessionStore(db_path=store.db_path)
    restarted = SQLiteSessionStore(db_path=store.db_path)
    with restarted._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM quiz_exposures").fetchone()[0] == 1
    receipt = await submit_quiz(
        restarted, request.model_copy(update={"attempt_id": "after-upgrade-123456789"})
    )
    assert receipt["evidence_kind"] == "correction"
    assert not receipt["independent"]


@pytest.mark.asyncio
async def test_exposure_replay_cannot_change_event_payload(quiz):
    store, _ = quiz
    contract = await question_contract(store, "s", "t", "q")
    await store.record_quiz_exposure("s", contract, "hint", "same-event")
    await store.record_quiz_exposure("s", contract, "hint", "same-event")
    with pytest.raises(ValueError, match="different event"):
        await store.record_quiz_exposure("s", contract, "revealed", "same-event")
