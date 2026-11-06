"""Selection → durable practice → existing authoritative quiz/assessment service."""

import asyncio
import json
import time

from . import catalog
from .contracts import ActionReceipt, ActionRequest, ObjectRef
from .database import Conflict, encoded, sha


def resolve(store, ref):
    row = store.revision(ref.id, ref.revision)
    if row["kind"] != ref.kind or row["scope"] != ref.scope:
        raise ValueError("Object identity or scope does not match this source.")
    return row


def receipt(row):
    data = row["data"]
    return ActionReceipt(
        operation_id=row["id"],
        committed=data["state"] == "committed",
        state=data["state"],
        results=[
            ObjectRef(scope=row["scope"], kind="activity", id=row["id"], revision=row["revision"])
        ]
        if data["state"] == "committed"
        else [],
        failure_reason=data.get("failure_reason"),
        elapsed_seconds=data.get("elapsed_seconds"),
        continue_actions=["answer", "return_to_source"]
        if data["state"] == "committed"
        else ["retry"],
    ).model_dump()


async def practice(store, sessions, request: ActionRequest, complete):
    source = resolve(store, request.target)
    if source["kind"] != "edition":
        raise ValueError("Choose an original edition for source-grounded practice.")
    if set(request.permissions) != {"read_source", "create_practice"}:
        raise ValueError(
            "Practice requires reading the selected source and saving a learning activity."
        )
    if any(ref != request.target for ref in request.allowed_sources):
        raise ValueError("Selection practice uses only the explicitly selected original.")
    loc = request.target.locator
    if not loc.page or not loc.blob_hash or not loc.quote.strip():
        raise ValueError("Select source text on a physical page first.")
    if not any(f["hash"] == loc.blob_hash for f in source["data"]["files"]):
        raise ValueError("Original file does not belong to this frozen edition.")
    page = catalog.page_source(store, source["id"], loc.blob_hash, loc.page, loc.region)
    quote = " ".join(loc.quote.split())
    if quote not in " ".join(page["text"].split()):
        raise ValueError("The selected words cannot be matched to the original page.")
    payload = request.model_dump()
    digest = sha(encoded(payload).encode())
    with store.db(write=True) as db:
        old = db.execute(
            "SELECT id FROM records WHERE scope=? AND kind='activity' AND json_extract(data,'$.idempotency_key')=?",
            (store.project, request.idempotency_key),
        ).fetchone()
        row = store.record(db, old[0]) if old else None
        if row and row["data"]["request_hash"] != digest:
            raise Conflict("This operation key belongs to another request.")
        if row and row["data"]["state"] == "committed":
            return receipt(row)
        if (
            row
            and row["data"]["state"] == "running"
            and time.time() - row["updated"] < request.timeout_seconds + 15
        ):
            return receipt(row)
        data = {
            "title": f"原文理解练习 · 第 {loc.page} 页",
            "source": request.target.model_dump(),
            "request": payload,
            "request_hash": digest,
            "idempotency_key": request.idempotency_key,
            "state": "running",
        }
        if row and row["data"].get("questions"):
            data.update(questions=row["data"]["questions"], state="prepared")
        row = store.put(
            db,
            "activity",
            data,
            identifier=row["id"] if row else None,
            expected=row["revision"] if row else None,
        )
    started = time.monotonic()
    try:
        if not row["data"].get("questions"):
            if request.network_policy == "offline":
                from deeptutor.services.llm import get_llm_config

                from .assist import is_local

                if not is_local(get_llm_config()):
                    raise ValueError("Offline practice requires a local model.")
            from deeptutor.reading.extensions import ReadingContext
            from deeptutor.reading.quiz import _SYSTEM_ZH, _quiz

            context = ReadingContext(
                material_id=source["id"],
                locator=loc.page,
                selection=quote,
                visible_text=quote,
                source_anchor=loc.blob_hash,
                locale="zh",
            )
            raw = await asyncio.wait_for(
                complete(
                    json.dumps({"source": quote}, ensure_ascii=False),
                    system_prompt=_SYSTEM_ZH,
                    max_tokens=request.max_output_tokens,
                    max_retries=0,
                    response_format={"type": "json_object"},
                ),
                request.timeout_seconds,
            )
            generated = _quiz(json.loads(raw), context)
            questions = []
            for index, question in enumerate(generated.questions):
                questions.append(
                    {
                        "question_id": f"{row['id']}:{index}",
                        "question": question.prompt,
                        "question_type": "choice",
                        "options": dict(zip("ABCD", question.choices)),
                        "correct_answer": "ABCD"[question.correct_choice_index],
                        "explanation": "材料依据：" + question.evidence,
                        "metadata": {
                            "source_scope": {"studio_object": request.target.model_dump()},
                            "question_family": sha(quote.encode()),
                            "objective_id": "source:" + source["id"],
                            "answer_origin": "source_grounded_model",
                        },
                    }
                )
            with store.db(write=True) as db:
                row = store.put(
                    db,
                    "activity",
                    {**row["data"], "state": "prepared", "questions": questions},
                    identifier=row["id"],
                    expected=row["revision"],
                )
        session_id, turn_id = "studio-" + row["id"], "practice-" + row["id"]
        if not await sessions.get_session(session_id):
            await sessions.create_session(title=row["data"]["title"], session_id=session_id)
        if not await sessions.get_turn(turn_id):
            await sessions.begin_turn(session_id, capability="deep_question", turn_id=turn_id)
        saved = [
            await sessions.register_quiz_question(session_id, turn_id, q)
            for q in row["data"]["questions"]
        ]
        await sessions.update_turn_status(turn_id, "completed")
        resolve(store, request.target)  # Recheck current access after model/provider work.
        with store.db(write=True) as db:
            row = store.put(
                db,
                "activity",
                {
                    **row["data"],
                    "state": "committed",
                    "questions": saved,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "elapsed_seconds": time.monotonic() - started,
                },
                identifier=row["id"],
                expected=row["revision"],
            )
            store.link(db, source["id"], row["id"], "practice_source")
        return receipt(row)
    except Exception as exc:
        with store.db(write=True) as db:
            latest = store.record(db, row["id"])
            if latest["revision"] != row["revision"]:
                raise Conflict(
                    "This activity was resumed by another request; read its receipt."
                ) from exc
            row = store.put(
                db,
                "activity",
                {
                    **row["data"],
                    "state": "failed",
                    "failure_reason": str(exc)[:1000],
                    "elapsed_seconds": time.monotonic() - started,
                },
                identifier=row["id"],
                expected=row["revision"],
            )
        return receipt(row)
