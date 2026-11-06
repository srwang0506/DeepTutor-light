"""Shared Quiz operations for HTTP controls and model tools.

Question answers and rubrics always come from a server contract. Legacy quiz
events may be imported, but never a client's copy of the reference answer.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deeptutor.learning.assessment import AssessmentRecord, to_notebook_item
from deeptutor.learning.semantic_assessment import AssessmentUnavailable, Verdict, judge_answers


class QuizSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    question_id: str = Field(min_length=1, max_length=200)
    question_version: str = Field(default="", max_length=64)
    attempt_id: str = Field(min_length=16, max_length=100)
    answer: str = Field(default="", max_length=16000)
    answer_images: list[dict[str, str | None]] = Field(default_factory=list, max_length=4)
    assistance: Literal["independent", "hint", "revealed"] = "independent"
    retry: bool = False


class QuizReceipt(BaseModel):
    attempt_id: str
    state: Literal["grading", "committed", "ungraded", "cancelled"]
    assessment_committed: bool
    result: Literal["correct", "partial", "incorrect", "ungraded"] | None = None
    feedback: str | None = None
    error: str | None = None
    confidence: float | None = None
    projection_status: Literal["complete", "pending"] = "complete"
    entry_id: int | None = None
    independent: bool = False
    evidence_kind: Literal["independent", "assisted", "correction"] | None = None


async def question_contract(store, session_id, turn_id, question_id, version=""):
    session = await store.get_session(session_id)
    turn = await store.get_turn(turn_id)
    if not session or session.get("deleted_at") or not turn or turn["session_id"] != session_id:
        raise ValueError("Question or session is unavailable in this workspace")
    contract = await store.get_quiz_question(session_id, turn_id, question_id, version)
    if contract is None and not version:
        # Historical streams are server records. Preserve outer metadata from
        # old summaries; streaming payloads used to omit validation issues.
        events = await store.get_turn_events(turn_id)
        candidates = []
        for event in events:
            metadata = event.get("metadata") or {}
            summary = metadata.get("summary") or {}
            for row in summary.get("results", []):
                qa = row.get("qa_pair", row)
                if qa.get("question_id") == question_id:
                    candidates.append(
                        {**qa, "metadata": {**row.get("metadata", {}), **qa.get("metadata", {})}}
                    )
        if candidates:
            contract = await store.register_quiz_question(session_id, turn_id, candidates[-1])
    if contract is None:
        raise ValueError("The saved question version is unavailable. Generate a new question.")
    issues = (contract.get("metadata") or {}).get("issues")
    if (
        issues
        or contract.get("generation_status") == "failed"
        or contract.get("answerable") is False
    ):
        raise ValueError("This question failed validation and cannot be graded")
    if not contract.get("question") or not contract.get("correct_answer"):
        raise ValueError("The question has no authoritative answer")
    validate_source_scope(contract)
    return contract


def validate_source_scope(contract):
    scope = (contract.get("metadata") or {}).get("source_scope") or {}
    if scope.get("studio_object"):
        from deeptutor.studio.actions import resolve
        from deeptutor.studio.contracts import ObjectRef
        from deeptutor.studio.database import Studio

        resolve(Studio(), ObjectRef(**scope["studio_object"]))
    material_id = scope.get("reading_material_id")
    if material_id:
        from deeptutor.multi_user.learning_access import assert_learning_material
        from deeptutor.reading.store import ReadingStore

        assert_learning_material(material_id)
        store = ReadingStore()
        revision = scope.get("reading_material_revision")
        current = store.manifest(material_id)
        if (
            revision
            and current.revision != revision
            and not any(m.revision == revision for m in store.revisions(material_id))
        ):
            raise ValueError(
                "The original material version is unavailable; this answer was not graded"
            )
    if scope.get("knowledge_bases"):
        from deeptutor.services.workspace.knowledge import learning_source_access

        with learning_source_access(scope["knowledge_bases"]):
            pass


def rule_verdict(contract, answer):
    from deeptutor.learning.pending import resolve_choice_submission

    qtype = contract.get("question_type")
    key = str(contract.get("correct_answer") or "").strip()
    feedback = contract.get("explanation") or "Checked against the saved answer."
    if qtype == "choice":
        options = contract.get("options") or {}
        expected = resolve_choice_submission(key, options)
        actual = resolve_choice_submission(answer, options)
        if not expected or not actual:
            return Verdict(
                result="ungraded", confidence=0, feedback="Choose one unambiguous option."
            )
        return Verdict(
            result="correct" if expected == actual else "incorrect", confidence=1, feedback=feedback
        )
    if qtype == "concept":
        booleans = {
            "true": "true",
            "false": "false",
            "正确": "true",
            "错误": "false",
            "对": "true",
            "错": "false",
        }
        expected, actual = booleans.get(key.casefold()), booleans.get(answer.strip().casefold())
        if expected and actual:
            return Verdict(
                result="correct" if expected == actual else "incorrect",
                confidence=1,
                feedback=feedback,
            )
    # Exact equality can confirm an answer. Inequality cannot disprove an
    # equivalent formula, a unit conversion, or a valid paraphrase.
    if answer.strip() and answer.strip() == key:
        return Verdict(result="correct", confidence=1, feedback=feedback)
    from deeptutor.learning.verification import verify_equality

    if verify_equality(answer, key)["status"] == "passed":
        return Verdict(
            result="correct",
            confidence=1,
            feedback="Equivalent numerical value and units, checked deterministically.",
        )
    return None


async def submit_quiz(store, request: QuizSubmission):
    contract = await question_contract(
        store, request.session_id, request.turn_id, request.question_id, request.question_version
    )
    request = request.model_copy(update={"question_version": contract["question_version"]})
    payload = request.model_dump()
    lease = await store.begin_quiz_submission(payload, retry=request.retry)
    if lease is None:
        return await store.quiz_submission(request.attempt_id, request.session_id)
    if request.assistance != "independent":
        await store.record_quiz_exposure(
            request.session_id, contract, request.assistance, "attempt:" + request.attempt_id
        )
    verdict = None
    image_references = []
    try:
        if not request.answer.strip() and not request.answer_images:
            raise AssessmentUnavailable("Provide an answer before assessment.")
        if request.assistance == "revealed":
            verdict = Verdict(
                result="ungraded",
                confidence=0,
                feedback="Reference viewed. A later correction is valuable; use a new transfer question after a delay to check independent understanding.",
            )
        if verdict is None and not request.answer_images:
            verdict = rule_verdict(contract, request.answer)
        if verdict is None:
            native_images = []
            if request.answer_images:
                # Reuse the existing bounded local-attachment resolver. No remote
                # URL fetches or silent text-only grading of image answers.
                from deeptutor.learning.quiz_images import answer_image_parts, persist_answer_images

                native_images = await asyncio.to_thread(
                    answer_image_parts, request.answer_images, request.session_id
                )
                image_references = await persist_answer_images(native_images, request.session_id)
            verdicts = await asyncio.wait_for(
                judge_answers(
                    None,
                    [
                        {
                            "question_id": request.question_id,
                            "prompt": contract["question"],
                            "expected_answer": contract["correct_answer"],
                            "explanation": contract.get("explanation", ""),
                            "options": contract.get("options") or {},
                            "answer": request.answer,
                            "_visual_images": native_images,
                        }
                    ],
                ),
                timeout=150,
            )
            verdict = verdicts[request.question_id]
        validate_source_scope(contract)
        receipt = {
            "result": verdict.result,
            "feedback": verdict.feedback,
            "confidence": verdict.confidence,
            "projection_status": "complete",
        }
    except (Exception, asyncio.CancelledError) as exc:
        # No failed model call is an incorrect answer. A retry reuses its saved
        # submission; a new independent attempt gets a new identity.
        receipt = {
            "result": "ungraded",
            "feedback": "Assessment unavailable. Your answer is saved; retry grading.",
            "error": type(exc).__name__,
            "projection_status": "complete",
        }
    record = AssessmentRecord(
        session_id=request.session_id,
        turn_id=request.turn_id,
        question_id=request.question_id,
        attempt_id=request.attempt_id,
        question=contract["question"],
        question_type=contract.get("question_type", ""),
        options=contract.get("options") or {},
        user_answer=request.answer,
        correct_answer=contract["correct_answer"],
        explanation=receipt["feedback"],
        result=receipt["result"],
        hints_used=int(request.assistance != "independent"),
    )
    item = to_notebook_item(record, [])
    item["user_answer_images"] = image_references
    attempt = record.model_dump(mode="json") | {
        "occurred_at": record.created_at,
        "question_version": request.question_version,
        "assistance": request.assistance,
    }
    await store.finish_quiz_submission(request.attempt_id, lease, item, attempt, receipt)
    return await store.quiz_submission(request.attempt_id, request.session_id)
