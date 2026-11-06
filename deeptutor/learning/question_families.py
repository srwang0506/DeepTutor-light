"""Reviewable semantic question families, separate from paragraph/source identity.

Candidate ranking is advisory. Only explicit concept/method/conditions reviews
can change the exposure identity, and every revision has an audit record.
"""

import json
import re
import time
import unicodedata

from pydantic import Field

from deeptutor.services.session.quiz_contracts import fingerprint
from deeptutor.studio.models import Command


class FamilyReview(Command):
    session_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    question_id: str = Field(min_length=1, max_length=200)
    question_version: str = Field(min_length=64, max_length=64)
    objective: str = Field(min_length=2, max_length=300)
    method: str = Field(min_length=2, max_length=500)
    conditions: str = Field(min_length=2, max_length=1000)
    rationale: str = Field(min_length=5, max_length=2000)
    expected_revision: int = Field(default=0, ge=0)


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


# A versioned bilingual vocabulary helps discover candidates. It does not issue a verdict.
CONCEPTS = {
    "queue_fifo": ("queue", "fifo", "队列", "先进先出"),
    "stack_lifo": ("stack", "lifo", "栈", "后进先出"),
    "binary_search": ("binary search", "二分查找", "二分搜索"),
    "bfs": ("breadth first", "breadth-first", "广度优先", "宽度优先"),
    "dfs": ("depth first", "depth-first", "深度优先"),
    "dynamic_programming": ("dynamic programming", "动态规划"),
    "conditional_probability": ("conditional probability", "条件概率", "bayes", "贝叶斯"),
    "hoi_detection": ("hoi detection", "interaction detection", "交互检测", "hico-det"),
    "hoi_reconstruction": ("3d reconstruction", "三维重建", "chamfer", "mesh"),
}


def concepts(text):
    text = normalized(text)
    return {key for key, terms in CONCEPTS.items() if any(term in text for term in terms)}


def lexical(text):
    text = normalized(text)
    words = set(re.findall(r"[a-z0-9]+", text))
    for chunk in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(chunk[n : n + 2] for n in range(len(chunk) - 1))
    return words


def rank(question, candidates):
    qconcepts, qwords = concepts(question), lexical(question)
    ranked = []
    for candidate in candidates:
        descriptor = (
            candidate["objective"] + " " + candidate["method"] + " " + candidate["conditions"]
        )
        cconcepts, cwords = concepts(descriptor), lexical(descriptor)
        semantic = len(qconcepts & cconcepts) / max(1, len(qconcepts | cconcepts))
        overlap = len(qwords & cwords) / max(1, len(qwords | cwords))
        score = 0.7 * semantic + 0.3 * overlap
        if score > 0:
            ranked.append(
                {
                    **candidate,
                    "score": round(score, 4),
                    "reason": "concept_vocabulary_and_lexical_overlap",
                    "automatic_assignment": False,
                }
            )
    return sorted(ranked, key=lambda x: (-x["score"], x["family"]))[:10]


def workspace(conn, session_id):
    row = conn.execute(
        "SELECT COALESCE(json_extract(preferences_json,'$.workspace_id'),'') FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if not row:
        raise ValueError("Session not found.")
    return row[0]


async def suggest(store, contract, session_id):
    def read():
        with store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            scope = workspace(conn, session_id)
            rows = conn.execute(
                "SELECT family,descriptor,revision FROM quiz_families WHERE workspace=? ORDER BY reviewed DESC LIMIT 1000",
                (scope,),
            ).fetchall()
            unique = {}
            for row in rows:
                unique.setdefault(
                    row["family"], {**json.loads(row["descriptor"]), "family": row["family"]}
                )
            current = conn.execute(
                "SELECT descriptor,revision FROM quiz_families WHERE workspace=? AND version=?",
                (scope, contract["question_version"]),
            ).fetchone()
            return {
                "candidates": rank(contract["question"], list(unique.values())),
                "current": {**json.loads(current["descriptor"]), "revision": current["revision"]}
                if current
                else None,
                "algorithm": "reviewed-concept-method-v1",
            }

    return await store._run(read)


async def review(store, contract, command):
    descriptor = command.model_dump(include={"objective", "method", "conditions", "rationale"})
    if any(not str(v).strip() for v in descriptor.values()):
        raise ValueError("Complete the concept, method, conditions and review rationale.")
    objective = fingerprint({"concept": normalized(command.objective)})
    family = fingerprint(
        {
            "concept": objective,
            "method": normalized(command.method),
            "conditions": normalized(command.conditions),
        }
    )

    def write():
        with store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            scope = workspace(conn, command.session_id)
            prior = conn.execute(
                "SELECT revision FROM quiz_families WHERE workspace=? AND version=?",
                (scope, command.question_version),
            ).fetchone()
            revision = prior[0] if prior else 0
            if revision != command.expected_revision:
                raise ValueError("This family review changed; reload before saving.")
            if not conn.execute(
                "SELECT 1 FROM quiz_exposures WHERE session_id=? AND version=? AND level='revealed'",
                (command.session_id, command.question_version),
            ).fetchone():
                raise ValueError(
                    "Open the reference in review mode before confirming its solution family."
                )
            now = time.time()
            values = (
                scope,
                command.question_version,
                family,
                objective,
                json.dumps(descriptor, ensure_ascii=False),
                revision + 1,
                now,
            )
            conn.execute("INSERT OR REPLACE INTO quiz_families VALUES (?,?,?,?,?,?,?)", values)
            conn.execute("INSERT INTO quiz_family_reviews VALUES (?,?,?,?,?,?,?)", values)
            return {
                "family": family,
                "objective_id": objective,
                "revision": revision + 1,
                "reviewed": True,
                **descriptor,
            }

    return await store._run(write)
