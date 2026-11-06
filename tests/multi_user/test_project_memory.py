"""Project memory is local by default; selected retrieval is read-only and one-hop."""

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from deeptutor.services.memory.snapshot.adapters import _iter_chat_dbs
from deeptutor.services.path_service import get_path_service
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.workspace import ContentWorkspaceService, WorkspaceError
from deeptutor.services.workspace.context import workspace_context
from deeptutor.services.workspace.project_recall import (
    _visible_messages,
    project_overview,
    recall_prompt,
    search_project_memory,
    tokens,
)


def note(wid, text):
    with workspace_context(wid):
        path = get_path_service().get_memory_dir() / "L3" / "preferences.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


def session(wid, sid, text):
    with workspace_context(wid):
        path = get_path_service().get_chat_history_db()
        SQLiteSessionStore(path)
        with sqlite3.connect(path) as db:
            db.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at) VALUES (?,?,1,2)", (sid, text)
            )
            db.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES (?,'user',?,2)",
                (sid, text),
            )
        return path


def select(service, target, sources, discoverable=True):
    return service.update_workspace(
        target, memory_policy={"sources": sources, "discoverable": discoverable}
    )


def test_project_scoped_memory_and_selected_one_hop(as_user):
    with as_user("alice"):
        svc = ContentWorkspaceService()
        a = svc.create_workspace("DSA")["workspace_id"]
        b = svc.create_workspace("HOI")["workspace_id"]
        c = svc.create_workspace("Other")["workspace_id"]
        pa = note(a, "动态规划先画状态转移图 ALPHA")
        pb = note(b, "动态规划用于 HOI 时序建模 BRAVO")
        pc = note(c, "动态规划未授权 CHARLIE")
        pg = note("", "动态规划通用记忆 GENERAL")
        assert len({pa, pb, pc, pg}) == 4
        before = pb.read_bytes()
        with workspace_context(a):
            assert _iter_chat_dbs() == [("", get_path_service().get_chat_history_db())]
            assert [h["workspace_id"] for h in search_project_memory("动态规划")["hits"]] == [a]
        select(svc, b, [c])
        select(svc, a, [b])
        result = search_project_memory("动态规划", workspace_id=a)
        assert {h["workspace_id"] for h in result["hits"]} == {a, b}
        assert "CHARLIE" not in recall_prompt(result) and "GENERAL" not in recall_prompt(result)
        assert pb.read_bytes() == before
        assert all("dt_workspace=" in h["url"] for h in result["hits"])
        select(svc, a, [])
        assert {
            h["workspace_id"] for h in search_project_memory("动态规划", workspace_id=a)["hits"]
        } == {a}


def test_revocation_archive_deletion_and_cache_refresh(as_user):
    with as_user("alice"):
        svc = ContentWorkspaceService()
        a = svc.create_workspace("Target")["workspace_id"]
        b = svc.create_workspace("Source")["workspace_id"]
        path = note(b, "HOI baseline HICO-DET VERSION_ONE")
        select(svc, a, [b])
        assert "VERSION_ONE" in recall_prompt(search_project_memory("HOI baseline", workspace_id=a))
        path.write_text("HOI baseline V-COCO VERSION_TWO")
        assert "VERSION_TWO" in recall_prompt(search_project_memory("HOI baseline", workspace_id=a))
        path.unlink()
        assert search_project_memory("HOI baseline", workspace_id=a)["hits"] == []
        note(b, "HOI baseline REDISCOVERED")
        select(svc, b, [], discoverable=False)
        assert search_project_memory("HOI baseline", workspace_id=a)["hits"] == []
        select(svc, b, [], discoverable=True)
        assert search_project_memory("HOI baseline", workspace_id=a)["hits"]
        svc.update_workspace(b, archived=True)
        assert search_project_memory("HOI baseline", workspace_id=a)["hits"] == []
        svc.update_workspace(b, archived=False)
        assert search_project_memory("HOI baseline", workspace_id=a)["hits"]


def test_session_identity_deleted_and_archived_are_not_recalled(as_user):
    with as_user("alice"):
        svc = ContentWorkspaceService()
        a = svc.create_workspace("A")["workspace_id"]
        b = svc.create_workspace("B")["workspace_id"]
        db_a = session(a, "same-id", "HICO-DET evaluation AP_A")
        db_b = session(b, "same-id", "HICO-DET evaluation AP_B")
        select(svc, a, [b])
        result = search_project_memory(
            "HICO-DET evaluation", workspace_id=a, exclude_session="same-id"
        )
        assert {h["workspace_id"] for h in result["hits"]} == {b}
        with sqlite3.connect(db_b) as db:
            db.execute("UPDATE sessions SET deleted_at=3")
        assert not search_project_memory(
            "HICO-DET evaluation", workspace_id=a, exclude_session="same-id"
        )["hits"]
        with sqlite3.connect(db_a) as db:
            db.execute("UPDATE sessions SET preferences_json=?", (json.dumps({"archived": True}),))
        assert not search_project_memory("HICO-DET evaluation", workspace_id=a)["hits"]


def test_owner_boundary_and_invalid_policy(as_user):
    with as_user("alice"):
        svc = ContentWorkspaceService()
        a = svc.create_workspace("Private")["workspace_id"]
        note(a, "HOI secret")
        for policy in (
            {"sources": [a]},
            {"sources": ["missing"]},
            {"sources": "all"},
            {"sources": [], "discoverable": "yes"},
            {"sources": [], "unexpected": True},
        ):
            with pytest.raises(WorkspaceError):
                svc.update_workspace(a, memory_policy=policy)
    with as_user("bob"):
        with pytest.raises(WorkspaceError):
            search_project_memory("HOI", workspace_id=a)
        with pytest.raises(WorkspaceError):
            ContentWorkspaceService().create_workspace("B", memory_policy={"sources": [a]})


def test_creation_can_select_general_and_metadata_roundtrip(as_user):
    with as_user("alice"):
        note("", "BFS queue complexity O(V+E)")
        svc = ContentWorkspaceService()
        a = svc.create_workspace(
            "算法",
            description="图与动态规划",
            color="#547b9c",
            instructions="先讲直觉",
            memory_policy={"sources": [""], "discoverable": False},
        )
        assert a["memory_policy"] == {"sources": [""], "discoverable": False}
        assert a["description"] == "图与动态规划"
        assert a["instructions"] == "先讲直觉"
        assert (
            search_project_memory("BFS queue", workspace_id=a["workspace_id"])["hits"][0][
                "workspace_id"
            ]
            == ""
        )
        assert project_overview(a["workspace_id"])["counts"]["sessions"] == 0


def test_branch_selection_and_context_bound(as_user):
    messages = [
        {"id": 1, "parent_message_id": None},
        {"id": 2, "parent_message_id": 1},
        {"id": 3, "parent_message_id": 2},
        {"id": 4, "parent_message_id": 1},
    ]
    assert [m["id"] for m in _visible_messages(messages, {})] == [1, 2, 3]
    assert [m["id"] for m in _visible_messages(messages, {"1": 4})] == [1, 4]
    assert _visible_messages([{"id": 1, "parent_message_id": 1}], {}) == []
    assert "动态" in tokens("动态规划") and "hoi" in tokens("ＨＯＩ")
    with as_user("alice"):
        wid = ContentWorkspaceService().create_workspace("Bounded")["workspace_id"]
        note(wid, "HOI paper protocol " * 4000)
        result = search_project_memory("HOI paper protocol", workspace_id=wid)
        assert len(result["hits"]) <= 6 and len(recall_prompt(result)) < 8500
        assert search_project_memory("irrelevantxyz", workspace_id=wid)["hits"] == []


@pytest.mark.asyncio
async def test_async_writes_keep_origin_scope(as_user):
    from deeptutor.services.memory import get_memory_store

    with as_user("alice"):
        svc = ContentWorkspaceService()
        a = svc.create_workspace("A")["workspace_id"]
        b = svc.create_workspace("B")["workspace_id"]
        with workspace_context(a):
            task = asyncio.create_task(
                get_memory_store().overwrite_doc("L3", "scope", "BFS origin A")
            )
        with workspace_context(b):
            await task
            assert get_memory_store().read_raw("L3", "scope") == ""
        with workspace_context(a):
            assert get_memory_store().read_raw("L3", "scope") == "BFS origin A"


@pytest.mark.asyncio
async def test_consolidator_runs_and_undo_are_project_local(as_user):
    from deeptutor.services.memory.consolidator.runs import get_run_manager

    with as_user("alice"):
        svc = ContentWorkspaceService()
        a = svc.create_workspace("A")["workspace_id"]
        b = svc.create_workspace("B")["workspace_id"]
        with workspace_context(a):
            manager_a = get_run_manager()
        with workspace_context(b):
            manager_b = get_run_manager()
            assert manager_a is not manager_b
        with workspace_context(a):
            assert get_run_manager() is manager_a
    with as_user("bob"):
        assert get_run_manager() not in (manager_a, manager_b)
