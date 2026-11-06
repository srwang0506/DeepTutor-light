import hashlib
import json

import fitz
import pytest

from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.studio import actions, catalog
from deeptutor.studio.contracts import ActionRequest, Locator, ObjectRef, connector_manifests
from deeptutor.studio.database import Conflict, Studio
from deeptutor.studio.models import EditionInput, WorkInput


@pytest.mark.asyncio
@pytest.mark.parametrize("attach_during_generation", [False, True])
async def test_selection_creates_durable_versioned_quiz_and_replay_has_no_model_call(
    tmp_path, attach_during_generation
):
    store = Studio(tmp_path / "studio", "project")
    sessions = SQLiteSessionStore(db_path=tmp_path / "session.db")
    work = catalog.create_work(store, WorkInput(title="Queues"))
    edition = catalog.create_edition(store, EditionInput(work_id=work["id"]))
    text = "A queue removes items in first-in first-out order."
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        content = pdf.tobytes()
    edition = catalog.attach_pdf(store, edition["id"], content, "queue.pdf", 1)
    ref = ObjectRef(
        scope="@library",
        kind="edition",
        id=edition["id"],
        revision=2,
        locator=Locator(blob_hash=hashlib.sha256(content).hexdigest(), page=1, quote=text),
    )
    request = ActionRequest(
        action="practice_from_selection", target=ref, idempotency_key="practice-action-123456"
    )
    calls = []

    async def complete(*args, **kwargs):
        calls.append(True)
        if attach_during_generation:
            with fitz.open() as added:
                added.new_page().insert_text((72, 72), "Another source attachment")
                catalog.attach_pdf(store, edition["id"], added.tobytes(), "new.pdf", 2)
        return json.dumps(
            {
                "questions": [
                    {
                        "prompt": f"Question {i}: Which rule does a queue use?",
                        "choices": ["FIFO order", "LIFO order", "Random order", "Sorted order"],
                        "correct_choice_index": 0,
                        "evidence": text,
                    }
                    for i in range(3)
                ]
            }
        )

    receipt = await actions.practice(store, sessions, request, complete)
    assert receipt["committed"], receipt
    activity = store.get(receipt["operation_id"])
    assert len(activity["data"]["questions"]) == 3
    assert all(q["question_version"] for q in activity["data"]["questions"])
    with store.db() as db:
        assert (
            db.execute(
                "SELECT source_revision FROM dependencies WHERE target=?", (activity["id"],)
            ).fetchone()[0]
            == 2
        )
    assert activity["data"]["read_set"]["records"][edition["id"]]["revision"] == 2
    assert (await sessions.get_turn(activity["data"]["turn_id"]))["status"] == "completed"
    assert await actions.practice(store, sessions, request, complete) == receipt
    assert calls == [True]
    changed = request.model_copy(update={"max_output_tokens": 2600})
    with pytest.raises(Conflict):
        await actions.practice(store, sessions, changed, complete)


def test_connector_contracts_do_not_advertise_unimplemented_live_tracking():
    rows = {r["id"]: r for r in connector_manifests()}
    assert rows["tracking"]["mode"] == "file_exchange"
    assert "execute" not in rows["ssh"]["capabilities"]
