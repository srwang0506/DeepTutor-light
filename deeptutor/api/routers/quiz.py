"""Thin HTTP adapter for shared authoritative Quiz operations."""

from fastapi import APIRouter, HTTPException

from deeptutor.learning.quiz import QuizReceipt, QuizSubmission, question_contract, submit_quiz
from deeptutor.services.session import get_sqlite_session_store

router = APIRouter()


@router.post("/submit", response_model=QuizReceipt)
async def submit(request: QuizSubmission):
    try:
        return await submit_quiz(get_sqlite_session_store(), request)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/attempts/{session_id}/{attempt_id}", response_model=QuizReceipt)
async def status(session_id: str, attempt_id: str):
    store = get_sqlite_session_store()
    await store.reconcile_quiz_projections(attempt_id=attempt_id)
    result = await store.quiz_submission(attempt_id, session_id)
    if result is None:
        raise HTTPException(404, "Submission not found")
    return result


@router.post("/attempts/{session_id}/{attempt_id}/cancel", response_model=QuizReceipt)
async def cancel(session_id: str, attempt_id: str):
    result = await get_sqlite_session_store().cancel_quiz_submission(attempt_id, session_id)
    if result is None:
        raise HTTPException(404, "Submission not found")
    return result


from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExposureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    question_id: str = Field(min_length=1, max_length=200)
    question_version: str = Field(default="", max_length=64)
    level: Literal["hint", "revealed"]
    event_id: str = Field(min_length=16, max_length=100)


@router.post("/exposures")
async def exposure(payload: ExposureInput):
    store = get_sqlite_session_store()
    try:
        contract = await question_contract(
            store,
            payload.session_id,
            payload.turn_id,
            payload.question_id,
            payload.question_version,
        )
        result = await store.record_quiz_exposure(
            payload.session_id, contract, payload.level, payload.event_id
        )
        if payload.level == "revealed":
            result.update(
                correct_answer=contract["correct_answer"],
                explanation=contract.get("explanation", ""),
            )
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


from deeptutor.learning import question_families


@router.post("/families/suggest")
async def family_suggestions(payload: ExposureInput):
    store = get_sqlite_session_store()
    try:
        contract = await question_contract(
            store,
            payload.session_id,
            payload.turn_id,
            payload.question_id,
            payload.question_version,
        )
        # Reviewing the solution family is itself answer disclosure.
        await store.record_quiz_exposure(payload.session_id, contract, "revealed", payload.event_id)
        return await question_families.suggest(store, contract, payload.session_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/families/review")
async def family_review(payload: question_families.FamilyReview):
    store = get_sqlite_session_store()
    try:
        contract = await question_contract(
            store,
            payload.session_id,
            payload.turn_id,
            payload.question_id,
            payload.question_version,
        )
        return await question_families.review(store, contract, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{session_id}/{turn_id}/{question_id}")
async def read_question(session_id: str, turn_id: str, question_id: str, version: str = ""):
    try:
        from deeptutor.learning.quiz_views import learning_question

        return learning_question(
            await question_contract(
                get_sqlite_session_store(), session_id, turn_id, question_id, version
            )
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
