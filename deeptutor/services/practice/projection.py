"""Project checked practice attempts into the owning workspace's learning path."""

from __future__ import annotations

import logging

from deeptutor.core.errors import AccessError

from .storage import PracticeStore

logger = logging.getLogger(__name__)


def reconcile_practice_evidence(practice: PracticeStore, request_id: str | None = None) -> None:
    from deeptutor.modules.catalog import module_enabled
    from deeptutor.services.workspace.context import get_workspace_scope

    scope = get_workspace_scope()
    if (scope and scope.archived) or not module_enabled("mastery"):
        return
    from deeptutor.learning.assessment import (
        PracticeObjectiveUnavailable,
        apply_practice_retention,
        practice_evidence_exists,
    )

    for identifier, payload in practice.pending_evidence(request_id=request_id):
        try:
            try:
                practice.validate_pending_source(payload)
            except (AccessError, PermissionError, ValueError, LookupError, FileNotFoundError):
                # If the learning commit succeeded but its acknowledgement was
                # lost, retain that receipt. Never project new evidence after
                # the source changed or the learner's access was withdrawn.
                status = (
                    "applied"
                    if practice_evidence_exists(identifier, payload["path_id"])
                    else "unavailable"
                )
                practice.finish_evidence(identifier, status)
                continue
            apply_practice_retention(identifier, payload)
            practice.finish_evidence(identifier, "applied")
        except PracticeObjectiveUnavailable:
            practice.finish_evidence(identifier, "unavailable")
        except Exception:
            logger.warning("Could not project practice evidence for %s", identifier, exc_info=True)


def practice_objectives() -> list[dict]:
    from deeptutor.modules.catalog import module_enabled

    if not module_enabled("mastery"):
        return []
    from deeptutor.learning.policy import path_display_name
    from deeptutor.learning.storage import LearningStore

    store = LearningStore()
    return [
        {
            "path_id": progress.book_id,
            "name": path_display_name(progress),
            "objectives": [
                {"id": point.id, "name": point.name}
                for module in progress.modules
                for point in module.knowledge_points
            ],
        }
        for progress, _topic, _, _ in store.list_topic_snapshots(status="active")
        if any(module.knowledge_points for module in progress.modules)
    ]


def validate_practice_objective(path_id: str, knowledge_point_id: str) -> None:
    from deeptutor.learning.policy import find_knowledge_point
    from deeptutor.learning.storage import LearningStore
    from deeptutor.modules.catalog import module_enabled

    if module_enabled("mastery"):
        store = LearningStore()
        progress = store.load(path_id)
        if progress is not None and progress.book_id == path_id:
            topic = store.get_topic(path_id, progress=progress)
            point, _, _ = find_knowledge_point(progress, knowledge_point_id)
            if topic is not None and topic.metadata.status == "active" and point is not None:
                return
    raise ValueError("This learning objective is not available in this workspace.")
