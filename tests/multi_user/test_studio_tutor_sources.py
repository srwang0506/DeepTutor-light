"""Tutor sources stay frozen, bounded, and within the active branch and grants."""

import json
from urllib.parse import parse_qs, urlsplit

import fitz
import pytest

from deeptutor.core.object_reference import Locator, ObjectRef
from deeptutor.core.turn_request import TurnRequest
from deeptutor.runtime.registry.tool_registry import get_tool_registry
from deeptutor.services.session.source_inventory import build_inventory
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from deeptutor.studio import catalog, experiments, tutor_sources
from deeptutor.studio.database import Missing, Studio
from deeptutor.studio.models import EditionInput, RunInput, WorkInput


def _ref(record, **locator):
    return ObjectRef(
        scope=record["scope"],
        kind=record["kind"],
        id=record["id"],
        revision=record["revision"],
        locator=Locator(**locator),
    )


def _pdf(*pages):
    with fitz.open() as document:
        for content in pages:
            document.new_page().insert_text((60, 75), content)
        return document.tobytes()


def _paper(store, title, pages):
    work = catalog.create_work(store, WorkInput(title=title))
    edition = catalog.create_edition(store, EditionInput(work_id=work["id"]))
    edition = catalog.attach_pdf(
        store, edition["id"], _pdf(*pages), "paper.pdf", edition["revision"]
    )
    return edition


@pytest.mark.asyncio
async def test_one_turn_reads_two_papers_and_frozen_experiment_with_exact_citations(as_user):
    with as_user("alice", role="admin"):
        workspace = ContentWorkspaceService().create_workspace("HOI")
        with workspace_context(workspace["workspace_id"]):
            store = Studio()
            first = _paper(
                store, "HOI detection", ["HOI detection overview", "HICO-DET test fixed split"]
            )
            second = _paper(store, "HOI reconstruction", ["F-score at 5 mm"])
            run = experiments.create_run(
                store,
                RunInput(
                    title="HOI local run",
                    status="succeeded",
                    dataset="HICO-DET",
                    split="test",
                    log="OLD log alpha\nrow 2 beta\nrow 3 gamma",
                ),
            )
            refs = [
                _ref(first, page=2, quote="HICO-DET test fixed split"),
                _ref(second, page=1),
                _ref(run),
            ]
            page = tutor_sources.list_sources("HOI", 2)
            assert len(page["items"]) == 2 and page["next_cursor"]
            remainder = tutor_sources.list_sources("HOI", 2, page["next_cursor"])
            assert len(remainder["items"]) == 1
            assert {x["ref"]["id"] for x in page["items"] + remainder["items"]} == {
                r.id for r in refs
            }
            # Revise both the experiment and the library record. A stale
            # attachment must continue to resolve only its selected revision.
            latest = catalog.attach_pdf(
                store, first["id"], _pdf("Newest original"), "new.pdf", first["revision"]
            )
            experiments.create_run(
                store,
                RunInput(title="HOI local run", status="failed", log="NEW log"),
                identifier=run["id"],
                expected=run["revision"],
            )
            payload = TurnRequest(content="Compare", studio_references=refs).to_payload()
            manifest, allowed, failed = tutor_sources.prepare_turn(
                tutor_sources.parse_references(payload["studio_references"])
            )
            assert failed == 0 and len(allowed) == 3
            assert "OLD log alpha" not in manifest and "HICO-DET test fixed split" not in manifest
            tool = get_tool_registry().get("read_studio_source")
            by_ref = {row["id"]: sid for sid, row in allowed.items()}
            responses = [
                await tool.execute(source_id=by_ref[ref.id], _studio_source_refs=allowed)
                for ref in refs
            ]
            first_body, second_body, run_body = (json.loads(res.content) for res in responses)
            assert first_body["selection_matched"] and first_body["page"] == 2
            assert "HICO-DET test fixed split" in first_body["text"]
            assert first_body["blob_hash"] == first["data"]["files"][0]["hash"]
            assert first_body["revision"] == first["revision"] != latest["revision"]
            assert "F-score at 5 mm" in second_body["text"]
            assert json.loads(run_body["text"])["execution_verified"] is False
            for result in responses:
                cite = result.metadata["studio_citation"]
                assert parse_qs(urlsplit(cite["href"]).query)["dt_workspace"] == [
                    workspace["workspace_id"]
                ]
                assert "/" in cite["href"] and result.public_result["content"]
            first_log = await tool.execute(
                source_id=by_ref[run["id"]], part="log", limit=14, _studio_source_refs=allowed
            )
            log = json.loads(first_log.content)
            assert log["text"] == "OLD log alpha\n" and log["next_offset"] == 14
            second_log = json.loads(
                (
                    await tool.execute(
                        source_id=by_ref[run["id"]],
                        part="log",
                        offset=log["next_offset"],
                        _studio_source_refs=allowed,
                    )
                ).content
            )
            assert second_log["line"] == 2 and second_log["end_line"] == 3
            assert "NEW" not in second_log["text"] and "row 2 beta" in second_log["text"]
            assert "OLD log" not in str(first_log.public_result)
            denied = await tool.execute(source_id="not-attached", _studio_source_refs=allowed)
            assert not denied.success


def test_project_grant_is_one_hop_and_live_even_after_manifest_resolution(as_user):
    with as_user("alice"):
        svc = ContentWorkspaceService()
        main, source, private = [
            svc.create_workspace(name)["workspace_id"] for name in ("HOI", "Related", "Private")
        ]
        with workspace_context(source):
            accessible = _ref(
                experiments.create_run(Studio(), RunInput(title="HOI shared", status="planned"))
            )
        with workspace_context(private):
            inaccessible = _ref(
                experiments.create_run(Studio(), RunInput(title="HOI private", status="planned"))
            )
        with workspace_context(main):
            assert not tutor_sources.list_sources("HOI")["items"]
            with pytest.raises(Missing):
                tutor_sources.read(accessible)
            svc.update_workspace(source, memory_policy={"sources": [private]})
            svc.update_workspace(main, memory_policy={"sources": [source]})
            assert [item["ref"]["id"] for item in tutor_sources.list_sources("HOI")["items"]] == [
                accessible.id
            ]
            _, allowed, unavailable = tutor_sources.prepare_turn([accessible, inaccessible])
            assert len(allowed) == 1 and unavailable == 1
            assert "HOI shared" in tutor_sources.read(accessible)["text"]
            svc.update_workspace(main, memory_policy={"sources": []})
            with pytest.raises(Missing):
                tutor_sources.read(accessible)
    with as_user("bob"), pytest.raises(Missing):
        tutor_sources.read(accessible)


def test_remote_reads_are_stored_admin_snapshots_and_never_start_transport(as_user, monkeypatch):
    from deeptutor.studio import connections

    def unexpected(*args, **kwargs):
        raise AssertionError("A stored snapshot must not start SSH/GitHub")

    monkeypatch.setattr(connections, "read_connection", unexpected)
    with as_user("alice", role="admin"):
        store = Studio()
        digest = store.save_blob(b"first line\nsecond line")
        with store.db(write=True) as db:
            snapshot = store.put(
                db,
                "remote_snapshot",
                {
                    "content_hash": digest,
                    "provider": "ssh",
                    "connection_title": "HOI test",
                    "result": {"path": "/results/hoi.csv"},
                },
            )
        ref = _ref(snapshot)
        assert tutor_sources.read(ref, offset=11)["line"] == 2
        assert tutor_sources.read(ref)["execution_verified"] is False
        # Simulate a scoped downgrade without requiring a particular account
        # root layout. The storage entry exists, but is intentionally unlisted.
        access = tutor_sources.access()
        monkeypatch.setattr(
            tutor_sources,
            "access",
            lambda: tutor_sources.SourcesAccess(access.project, access.names, False),
        )
        assert not tutor_sources.list_sources("HOI test")["items"]
        with pytest.raises(Missing):
            tutor_sources.read(ref)


@pytest.mark.asyncio
async def test_followup_inherits_only_active_branch_studio_references(as_user, tmp_path):
    with as_user("alice"):
        session = SQLiteSessionStore(tmp_path / "sessions.db")
        await session.create_session("Branches", session_id="branches")
        before = await session.add_message("branches", "user", "First")
        a_ref = {"id": "A", "revision": 1, "kind": "run", "scope": ""}
        b_ref = {"id": "B", "revision": 1, "kind": "run", "scope": ""}
        a, b = [
            await session.add_message(
                "branches",
                "user",
                ref["id"],
                parent_message_id=before,
                metadata={"request_snapshot": {"studioReferences": [ref]}},
            )
            for ref in (a_ref, b_ref)
        ]

        async def inventory(leaf, include=True):
            return await build_inventory(
                session,
                session_id="branches",
                leaf_message_id=leaf,
                current_turn_ordinal=3,
                fresh_attachment_records=[],
                fresh_notebook_records=[],
                fresh_book_context_text="",
                fresh_book_references=[],
                fresh_history_session_ids=[],
                fresh_question_entry_ids=[],
                include_studio_history=include,
            )

        assert (await inventory(a)).studio_references == [a_ref]
        assert (await inventory(b)).studio_references == [b_ref]
        assert (await inventory(None)).studio_references == [b_ref]
        assert not (await inventory(None, include=False)).studio_references
