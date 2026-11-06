"""Recover Mastery → Question Bank projections from the owning learning DB.

The outbox is committed in the same transaction as the interaction. A worker
crash after grading but before projection cannot lose the repair request.
"""

import asyncio
import json

from deeptutor.learning.assessment import AssessmentRecord, record_assessment
from deeptutor.learning.models import MasteryInteraction
from deeptutor.learning.storage import LearningStore


def projection_records(payload):
    interaction = MasteryInteraction.model_validate(payload["interaction"])
    if not interaction.session_id:
        return []
    leaves = interaction.question.questions or [interaction.question]
    results = {r["question_id"]: r for r in interaction.result.get("results", [])}
    records = []
    for question in leaves:
        verdict = results.get(question.question_id, interaction.result)
        result = verdict.get("result")
        if not result:
            result = (
                "correct"
                if verdict.get("is_correct") is True
                else "incorrect"
                if verdict.get("is_correct") is False
                else "ungraded"
            )
        if result not in {"correct", "partial", "incorrect"} or verdict.get("final") is False:
            continue
        evidence = payload.get("evidence", {}).get(question.question_id, {})
        records.append(
            AssessmentRecord(
                session_id=interaction.session_id,
                turn_id=interaction.turn_id,
                question_id=question.question_id,
                question=question.prompt,
                question_type={"choice": "choice", "open": "written"}.get(
                    question.question_type, "short_answer"
                ),
                options=question.choice_map,
                correct_answer=question.expected_answer,
                explanation=question.explanation,
                difficulty=question.difficulty,
                user_answer=interaction.answers.get(question.question_id, interaction.user_answer),
                result=result,
                is_correct=verdict.get("is_correct"),
                source="mastery_path",
                assessment_type="quiz",
                attempt_count=evidence.get("attempt_count", 1),
                hints_used=evidence.get("hints_used", 0),
                confidence=evidence.get("confidence"),
                response_time=evidence.get("response_time"),
                quality=evidence.get("quality"),
                material_id=interaction.path_id,
                material_title=payload.get("title", ""),
                section_id=question.knowledge_point_id,
                mastery_path_id=interaction.path_id,
                knowledge_point_id=question.knowledge_point_id,
                visual_context=question.visual_context,
                created_at=interaction.updated_at,
            )
        )
    return records


async def reconcile_mastery_projections(store=None):
    store = store or LearningStore()
    recovered = failed = 0
    for row in await asyncio.to_thread(store.pending_projections):
        try:
            for record in projection_records(json.loads(row["payload_json"])):
                await record_assessment(record)
            await asyncio.to_thread(
                store.acknowledge_projection, row["interaction_id"], row["digest"]
            )
            recovered += 1
        except Exception:
            failed += 1  # Keep durable work pending; a grade is never rolled back.
    return recovered, failed
