"""Native-chat paper review records and author follow-up actions."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from deeptutor.api.routers.studio import operation
from deeptutor.studio import paper_reviews
from deeptutor.studio.database import Conflict, Missing, Studio

router = APIRouter()


@router.post("/paper-reviews")
async def create_review(payload: paper_reviews.PaperReviewCreate):
    return await operation(paper_reviews.create, Studio(), payload)


@router.get("/paper-reviews")
async def list_reviews(
    edition_id: str = Query("", max_length=100), manuscript_id: str = Query("", max_length=100)
):
    return await operation(
        paper_reviews.list_reviews, Studio(), edition_id=edition_id, manuscript_id=manuscript_id
    )


@router.get("/paper-reviews/options")
async def review_options():
    return await operation(paper_reviews.options)


@router.get("/paper-reviews/{identifier}")
async def get_review(identifier: str):
    return await operation(paper_reviews.get, Studio(), identifier)


@router.post("/paper-reviews/{identifier}/session")
async def bind_session(identifier: str, payload: paper_reviews.PaperReviewSessionBinding):
    try:
        return await paper_reviews.bind_session(Studio(), identifier, payload.session_id)
    except Missing as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/paper-reviews/{identifier}/issues/{issue_id}")
async def update_issue(
    identifier: str, issue_id: str, payload: paper_reviews.PaperReviewIssueUpdate
):
    return await operation(paper_reviews.update_issue, Studio(), identifier, issue_id, payload)


@router.get("/paper-reviews/{identifier}/export")
async def export_review(identifier: str, format: Literal["json", "markdown"] = "json"):
    result = await operation(paper_reviews.export, Studio(), identifier, format)
    return Response(
        content=result["content"],
        media_type=result["mime"],
        headers={"Content-Disposition": f'attachment; filename="{result["filename"]}"'},
    )
