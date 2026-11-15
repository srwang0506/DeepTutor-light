"""Read-only Studio source selection for the Tutor composer."""

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from deeptutor.core.object_reference import ObjectRef
from deeptutor.services.workspace import WorkspaceError
from deeptutor.studio.tutor_sources import list_sources

router = APIRouter()


class TutorSourceItem(BaseModel):
    ref: ObjectRef
    title: str
    description: str
    href: str
    workspace_name: str
    external: bool
    pages: int | None = None


class TutorSourcePage(BaseModel):
    items: list[TutorSourceItem]
    next_cursor: str


@router.get("/tutor-sources", response_model=TutorSourcePage, response_model_exclude_none=True)
async def tutor_sources(
    q: str = Query("", max_length=500),
    limit: int = Query(30, ge=1, le=60),
    cursor: str = Query("", max_length=600),
):
    try:
        return await asyncio.to_thread(list_sources, q, limit, cursor)
    except (PermissionError, WorkspaceError) as exc:
        raise HTTPException(403, "Studio sources are unavailable in this project.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
