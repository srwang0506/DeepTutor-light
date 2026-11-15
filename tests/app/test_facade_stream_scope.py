"""SDK streams must not lend their project's ContextVar to their consumer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deeptutor.app.facade import DeepTutorApp
from deeptutor.services.workspace import context


@pytest.mark.asyncio
async def test_stream_restores_callers_scope_and_can_close_in_another_task(monkeypatch, tmp_path):
    scopes = {
        name: context.WorkspaceScope(name, tmp_path, tmp_path / name)
        for name in ("reader", "other")
    }
    monkeypatch.setattr(context, "resolve_workspace_scope", lambda name: scopes[name])
    observed = []

    async def subscribe(turn_id, after_seq):
        assert (turn_id, after_seq) == ("turn", 3)
        try:
            observed.append(context.current_workspace_id())
            yield {"type": "content"}
            observed.append(context.current_workspace_id())
            yield {"type": "done"}
        finally:
            observed.append(context.current_workspace_id())

    app = object.__new__(DeepTutorApp)
    app.workspace_id = "other"
    app._turn_workspaces = {"turn": "reader"}
    app.container = SimpleNamespace(start=AsyncMock())
    app.turns = SimpleNamespace(subscribe_turn=subscribe)
    with context.workspace_context(scopes["other"]):
        stream = app.stream_turn("turn", after_seq=3)
        assert (await anext(stream))["type"] == "content"
        assert context.current_workspace_id() == "other"

        # The admitted stream must still release its subscription if the
        # workspace registration is removed while the caller consumes it.
        def removed(_name):
            raise context.WorkspaceError("Workspace no longer registered")

        monkeypatch.setattr(context, "resolve_workspace_scope", removed)
        assert (await anext(stream))["type"] == "done"
        assert context.current_workspace_id() == "other"
        await asyncio.create_task(stream.aclose())
        assert context.current_workspace_id() == "other"
    assert observed == ["reader", "reader", "reader"]
    assert context.current_workspace_id() == ""
