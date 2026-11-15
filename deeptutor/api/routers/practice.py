"""Practice workflow. Every operation resolves the current user's scoped store."""

from __future__ import annotations

import asyncio
import csv
import io
import time
from typing import Literal
import zipfile

from defusedxml.ElementTree import ParseError
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from deeptutor.core.errors import AccessError
from deeptutor.services.practice.importing import MAX_BYTES, preview
from deeptutor.services.practice.projection import (
    practice_objectives,
    reconcile_practice_evidence,
    validate_practice_objective,
)
from deeptutor.services.practice.scheduler import Rating, day_bounds
from deeptutor.services.practice.storage import PracticeStore, ReviewConflict
from deeptutor.services.session import get_sqlite_session_store

from .question_notebook import NotebookEntryItem

router = APIRouter()


class PracticeSummary(BaseModel):
    total: int
    mistakes: int
    due: int
    overdue: int
    reviewed_today: int
    next_due_at: float | None
    day_end: float
    timezone: str


class PracticeQuestion(BaseModel):
    entry: NotebookEntryItem
    state: dict
    content_workspace_id: str = ""
    content_workspace_name: str = ""


class PracticeCounts(BaseModel):
    questions: int
    mistakes: int
    reviews: int


class PracticeDailyCounts(PracticeCounts):
    date: str


class PracticeSourceCounts(PracticeCounts):
    source: str


class PracticeAnalytics(BaseModel):
    timezone: str
    days: int
    start_date: str
    end_date: str
    updated_at: float
    daily: list[PracticeDailyCounts]
    sources: list[PracticeSourceCounts]
    totals: PracticeCounts


class AnswerRequest(BaseModel):
    answer: str = Field(default="", max_length=20000)


class ReviewRequest(AnswerRequest):
    self_report: bool = False
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    version: int = Field(ge=0)
    rating: Rating


class CheckRequest(AnswerRequest):
    request_id: str | None = Field(
        default=None, min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    version: int | None = Field(default=None, ge=0)


class PracticeObjective(BaseModel):
    path_id: str = Field(min_length=1, max_length=255)
    knowledge_point_id: str = Field(min_length=1, max_length=255)


class ObjectiveRequest(BaseModel):
    version: int = Field(ge=0)
    objective: PracticeObjective | None


class CommitRequest(BaseModel):
    token: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]+$")


def _validate_timezone(timezone: str) -> tuple[float, float]:
    try:
        return day_bounds(timezone, time.time())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _entry(entry_id: int) -> dict:
    store = get_sqlite_session_store()
    if not await asyncio.to_thread(PracticeStore(store.db_path).is_visible, entry_id):
        raise HTTPException(404, "Question not found")
    entry = await store.get_notebook_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "Question not found")
    return entry


def _question_stem(entry: dict) -> dict:
    """The online recall screen does not receive the reference before checking."""
    hidden = ("correct_answer", "explanation", "user_answer", "user_answer_images", "practice")
    return {key: value for key, value in entry.items() if key not in hidden}


@router.get("/summary", response_model=PracticeSummary)
async def summary(timezone: str = "UTC"):
    store = get_sqlite_session_store()
    try:
        return await asyncio.to_thread(PracticeStore(store.db_path).overview, timezone)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/queue", response_model=list[PracticeQuestion])
async def queue(
    timezone: str = "UTC",
    category_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    store = get_sqlite_session_store()
    practice = PracticeStore(store.db_path)
    await asyncio.to_thread(reconcile_practice_evidence, practice)
    try:
        ids = await asyncio.to_thread(practice.queue, timezone, category_id, limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    from deeptutor.services.workspace.context import current_workspace_id

    questions = []
    for entry_id in ids:
        entry = await store.get_notebook_entry(entry_id)
        if entry:
            questions.append(
                {
                    "entry": _question_stem(entry),
                    "state": await asyncio.to_thread(practice.state, entry_id),
                    "content_workspace_id": current_workspace_id(),
                }
            )
    return questions


@router.get("/analytics", response_model=PracticeAnalytics)
async def practice_analytics(timezone: str = "UTC", days: int = 30):
    _validate_timezone(timezone)
    if days not in {7, 30, 90}:
        raise HTTPException(422, "Days must be 7, 30, or 90")
    store = get_sqlite_session_store()
    try:
        return await asyncio.to_thread(PracticeStore(store.db_path).analytics, timezone, days)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/questions/{entry_id}", response_model=PracticeQuestion)
async def practice_question(entry_id: int):
    entry = await _entry(entry_id)
    practice = PracticeStore(get_sqlite_session_store().db_path)
    await asyncio.to_thread(reconcile_practice_evidence, practice)
    state = await asyncio.to_thread(practice.state, entry_id)
    return {"entry": _question_stem(entry), "state": state}


@router.get("/objectives")
async def objectives():
    return {"paths": await asyncio.to_thread(practice_objectives)}


@router.put("/questions/{entry_id}/objective")
async def set_objective(entry_id: int, payload: ObjectiveRequest):
    await _entry(entry_id)
    practice = PracticeStore(get_sqlite_session_store().db_path)
    try:
        if payload.objective:
            await asyncio.to_thread(
                validate_practice_objective,
                payload.objective.path_id,
                payload.objective.knowledge_point_id,
            )
        await asyncio.to_thread(
            practice.bind_objective,
            entry_id,
            payload.version,
            payload.objective.model_dump() if payload.objective else None,
        )
        return {"state": await asyncio.to_thread(practice.state, entry_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/questions/{entry_id}/check")
async def check(entry_id: int, payload: CheckRequest):
    if (payload.request_id is None) != (payload.version is None):
        raise HTTPException(
            422, "An answer check requires both its submission ID and question version."
        )
    try:
        return await asyncio.to_thread(
            PracticeStore(get_sqlite_session_store().db_path).check,
            entry_id,
            payload.answer,
            payload.request_id,
            payload.version,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, "This practice source is no longer available.") from exc
    except AccessError as exc:
        raise HTTPException(
            exc.status_code, "This practice source is no longer available."
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, "This practice source is no longer available.") from exc
    except (ReviewConflict, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/questions/{entry_id}/review")
async def review(entry_id: int, payload: ReviewRequest):
    practice = PracticeStore(get_sqlite_session_store().db_path)
    try:
        outcome = await asyncio.to_thread(
            practice.review,
            entry_id,
            payload.request_id,
            payload.version,
            payload.rating,
            payload.answer,
            self_report=payload.self_report,
        )
        await asyncio.to_thread(reconcile_practice_evidence, practice, payload.request_id)
        return {
            **outcome,
            "objective_evidence": await asyncio.to_thread(
                practice.evidence_status, payload.request_id
            ),
        }
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, "This practice source is no longer available.") from exc
    except AccessError as exc:
        raise HTTPException(
            exc.status_code, "This practice source is no longer available."
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, "This practice source is no longer available.") from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/import/template")
async def import_template(format: Literal["csv", "xlsx"] = "csv"):
    rows = [
        [
            "question",
            "question_type",
            "A",
            "B",
            "C",
            "D",
            "correct_answer",
            "explanation",
            "tags",
            "user_answer",
        ],
        ["2 + 2 = ?", "single_choice", "3", "4", "5", "6", "B", "2 + 2 = 4", "Math,Addition", ""],
        [
            "Select the prime numbers",
            "multi_choice",
            "2",
            "3",
            "4",
            "6",
            "A,B",
            "2 and 3 are prime",
            "Math",
            "",
        ],
        ["The Earth orbits the Sun", "true_false", "", "", "", "", "true", "", "Science", ""],
        [
            "Explain photosynthesis",
            "short_answer",
            "",
            "",
            "",
            "",
            "Plants convert light into chemical energy.",
            "",
            "Biology",
            "",
        ],
    ]
    if format == "xlsx":
        from openpyxl import Workbook

        book = Workbook()
        for row in rows:
            book.active.append(row)
        output = io.BytesIO()
        book.save(output)
        book.close()
        data = output.getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows)
        data, media = output.getvalue().encode("utf-8-sig"), "text/csv"
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="practice-template.{format}"'},
    )


@router.post("/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    target: Literal["bank", "mistakes"] = Form("bank"),
):
    try:
        data = await file.read(MAX_BYTES + 1)
        parsed = await asyncio.to_thread(preview, data, file.filename or "")
    except (ValueError, UnicodeError, OSError, zipfile.BadZipFile, KeyError, ParseError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()
    token = None
    if not parsed["errors"]:
        token = await asyncio.to_thread(
            PracticeStore(get_sqlite_session_store().db_path).stage_import,
            file.filename or "",
            target,
            parsed["questions"],
        )
    return {
        "token": token,
        "total": len(parsed["questions"]) + len(parsed["errors"]),
        "valid": len(parsed["questions"]),
        "errors": parsed["errors"],
        "samples": parsed["questions"][:5],
    }


@router.post("/import/commit")
async def import_commit(payload: CommitRequest):
    try:
        return await asyncio.to_thread(
            PracticeStore(get_sqlite_session_store().db_path).commit_import, payload.token
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
