import json
from types import SimpleNamespace

import pytest

from deeptutor.studio import assist, evidence, manuscripts
from deeptutor.studio.database import Conflict, Missing, Studio
from deeptutor.studio.models import FileEdit, ManuscriptInput
from tests.studio.test_domains import original_evidence


@pytest.fixture
def setup(tmp_path):
    store = Studio(tmp_path / "studio", "a")
    mid = manuscripts.create_manuscript(store, ManuscriptInput(title="Paper"))["id"]
    file = manuscripts.read_file(store, mid, "main.tex")
    text = "Only seen objects improved by 2 mm.\nSecret unselected paragraph."
    manuscripts.save_file(store, mid, FileEdit(path="main.tex", base_hash=file["hash"], text=text))
    file = manuscripts.read_file(store, mid, "main.tex")
    command = assist.AssistInput(
        path="main.tex", base_hash=file["hash"], start=0, end=text.index("\n")
    )
    config = SimpleNamespace(
        provider_mode="oauth",
        provider_name="codex",
        model="fixture",
        effective_url=None,
        base_url=None,
    )
    return store, mid, file, command, config


@pytest.mark.asyncio
async def test_cloud_policy_scope_and_no_model_call_until_allowed(setup):
    store, mid, file, command, config = setup

    async def complete(*args, **kwargs):
        raise AssertionError("must not transmit")

    with pytest.raises(PermissionError):
        await assist.propose(store, mid, command, config, complete)
    assist.save_preferences(store, assist.WritingPreferences(allow_cloud=True), 0)
    assert assist.preferences(Studio(store.root, "b"))["data"]["allow_cloud"] is False
    with pytest.raises(Conflict):
        assist.save_preferences(store, assist.WritingPreferences(), 0)
    with pytest.raises(Missing):
        assist.context(Studio(store.root, "b"), mid, command)


@pytest.mark.asyncio
async def test_ai_patch_is_selection_only_fact_flagged_and_hash_guarded(setup):
    store, mid, file, command, config = setup
    assist.save_preferences(store, assist.WritingPreferences(allow_cloud=True), 0)

    async def complete(prompt, **kwargs):
        payload = json.loads(prompt)
        assert "Secret unselected" not in prompt
        assert payload["selected_tex"] == "Only seen objects improved by 2 mm."
        return "All objects improved by 3 mm."

    proposal = await assist.propose(store, mid, command, config, complete)
    assert {x["kind"] for x in proposal["data"]["fact_changes"]} >= {"scope", "numbers_units"}
    assert manuscripts.read_file(store, mid, "main.tex") == file
    with pytest.raises(ValueError, match="factual"):
        manuscripts.accept_proposal(store, proposal["id"], proposal["revision"])
    manuscripts.save_file(
        store, mid, FileEdit(path="main.tex", base_hash=file["hash"], text="External edit")
    )
    with pytest.raises(Conflict):
        manuscripts.accept_proposal(store, proposal["id"], proposal["revision"], True)


@pytest.mark.asyncio
async def test_changed_source_during_model_call_cannot_be_pinned_as_new_evidence(setup):
    store, mid, file, command, config = setup
    assist.save_preferences(store, assist.WritingPreferences(allow_cloud=True), 0)
    source = original_evidence(store)
    command = command.model_copy(update={"source_ids": [source["id"]]})

    async def complete(*args, **kwargs):
        evidence.review_evidence(store, source["id"], source["revision"], "disputed")
        return "Improvement was observed only for seen objects."

    with pytest.raises(Conflict, match="source changed"):
        await assist.propose(store, mid, command, config, complete)
    assert store.list("proposal") == []


def test_only_loopback_standard_model_is_local():
    for endpoint, expected in [
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.2:11434", False),
        ("https://localhost.example.com", False),
    ]:
        config = SimpleNamespace(
            provider_mode="standard", base_url=endpoint, effective_url=endpoint
        )
        assert assist.is_local(config) is expected


@pytest.mark.asyncio
async def test_selection_boundary_newlines_survive_model_whitespace_cleanup(setup):
    store, mid, file, command, config = setup
    assist.save_preferences(store, assist.WritingPreferences(allow_cloud=True), 0)
    text = "\nOnly seen objects improved by 2 mm.\n"
    manuscripts.save_file(store, mid, FileEdit(path="main.tex", base_hash=file["hash"], text=text))
    file = manuscripts.read_file(store, mid, "main.tex")
    command = command.model_copy(update={"base_hash": file["hash"], "end": len(text)})

    async def complete(*args, **kwargs):
        return "Only seen objects improved by 2 mm."

    proposal = await assist.propose(store, mid, command, config, complete)
    assert proposal["data"]["text"] == text
