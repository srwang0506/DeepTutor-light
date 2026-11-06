import asyncio
import copy
import json

import pytest

from deeptutor.studio import evaluations as ev
from deeptutor.studio.database import Conflict, Studio


def suite(**overrides):
    source = dict(source_id="paper", revision="v2", locator="page:3/table:1")
    case = dict(
        id="a",
        domain="reading",
        family="f1",
        papers=["p1"],
        prompt="Find the value.",
        sources=[dict(**source, text="The value is 42.")],
        expected=dict(answerable=True, accepted_answers=["42"], citations=[source]),
    )
    return ev.Suite.model_validate(
        dict(
            title="Frozen test",
            curator="Reviewer",
            source_url="https://example.test/source",
            license="test fixture",
            cases=[case],
            **overrides,
        )
    )


def test_freeze_rejects_leakage_and_unpaired_variants(tmp_path):
    store = Studio(tmp_path, project="test")
    original = suite().model_dump()
    for change in ({"family": "f1", "papers": []}, {"family": "f2", "papers": ["p1"]}):
        value = copy.deepcopy(original)
        value["cases"].append({**value["cases"][0], **change, "id": "b", "split": "development"})
        with pytest.raises(ValueError, match="boundary"):
            ev.freeze(store, ev.Suite.model_validate(value))
    original["cases"][0]["pair"] = "pair"
    with pytest.raises(ValueError, match="one clean"):
        ev.freeze(store, ev.Suite.model_validate(original))


def test_model_never_receives_reference_and_digest_is_checked(tmp_path):
    store = Studio(tmp_path, project="test")
    frozen = ev.freeze(store, suite())
    run = ev.start(store, frozen["id"], {"model": "fixture"})

    async def complete(prompt, **kwargs):
        actual = json.loads(prompt)
        assert set(actual) == {"prompt", "sources"}
        assert "expected" not in prompt and "accepted_answers" not in prompt
        return json.dumps(
            {
                "answer": "42",
                "abstained": False,
                "citations": [suite().cases[0].sources[0].model_dump(exclude={"text"})],
            }
        )

    result = asyncio.run(ev.advance(store, run["id"], {"model": "fixture"}, complete))
    assert result["metrics"]["all"]["correct"] == 1
    assert result["metrics"]["all"]["citation_hits"] == 1
    assert result["metrics"]["all"]["cost_usd"] is None
    assert result["metrics"]["all"]["reported_tokens"] is None
    assert result["independent_certification"] is False
    with store.db(write=True) as db:
        data = copy.deepcopy(frozen["data"])
        data["cases"][0]["prompt"] = "Changed"
        store.put(
            db, "evaluation_suite", data, identifier=frozen["id"], expected=frozen["revision"]
        )
    with pytest.raises(Conflict, match="sealed"):
        ev.report(store, run["id"])


def test_failed_and_interrupted_calls_remain_in_denominator(tmp_path):
    store = Studio(tmp_path, project="test")
    frozen = ev.freeze(store, suite())
    model = {"model": "fixture"}
    run = ev.start(store, frozen["id"], model)

    async def invalid(*args, **kwargs):
        return "not JSON"

    result = asyncio.run(ev.advance(store, run["id"], model, invalid))
    assert result["metrics"]["all"]["cases"] == 1
    assert result["metrics"]["all"]["scored"] == 0
    assert result["metrics"]["all"]["accuracy_over_all_cases"] == 0
    run = ev.start(store, frozen["id"], model)
    with store.db(write=True) as db:
        store.put(
            db,
            "evaluation_run",
            {**run["data"], "active_case": "a"},
            identifier=run["id"],
            expected=run["revision"],
        )

    async def forbidden(*args, **kwargs):
        pytest.fail("An interrupted request must never silently be repeated")

    result = asyncio.run(ev.advance(store, run["id"], model, forbidden))
    assert result["rows"][0]["state"] == "interrupted"
    assert result["metrics"]["all"]["accuracy_over_all_cases"] == 0


def test_refusal_does_not_disguise_missing_coverage():
    case = suite().cases[0].model_dump()
    result = ev.score(case, {"answer": "", "abstained": True, "citations": []})
    assert result["correct"] is False and result["unnecessary_refusal"]
    case["expected"]["answerable"] = False
    case["expected"]["citations"] = []
    assert ev.score(case, {"answer": "", "abstained": True, "citations": []})["correct"] is True
    result = ev.score(
        suite().cases[0].model_dump(),
        {
            "answer": "42",
            "abstained": False,
            "citations": [{"source_id": "paper", "revision": "v1", "locator": "page:3/table:1"}],
        },
    )
    assert result["invalid_citations"] == 1 and not result["citation_exact"]
    assert result["answer_correct"] and not result["correct"]
