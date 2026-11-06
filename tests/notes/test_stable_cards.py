"""Real storage round trips: legacy review identity, edits, clones and races."""

import copy
import json

import pytest

from deeptutor.co_writer.storage import CoWriterStorage, _atomic_write_json
from deeptutor.notes.anki_export import cards_from_document, render_anki_export
from deeptutor.notes.card_identity import META, legacy_id, normalize_cards
from deeptutor.notes.review import due_cards, grade_card, page_cards, resolve_card_change
from tests.notes.test_anki_export import _flash_block
from tests.notes.test_block_pages import Paths


@pytest.fixture
def store(tmp_path):
    return CoWriterStorage(Paths(tmp_path))


def make_page(store):
    return store.create_document(
        blocks=[_flash_block("flash", [{"front": "Power?", "back": "1 mW"}])]
    )


def edit(store, page, **fields):
    blocks = copy.deepcopy(page.blocks)
    data = json.loads(blocks[0]["props"]["data"])
    data["cards"][0].update(fields)
    blocks[0]["props"]["data"] = json.dumps(data)
    return store.update_document(page.id, blocks=blocks, expected_revision=page.revision)


def test_legacy_read_is_non_destructive_and_preserves_due_and_anki_guid(store):
    page = make_page(store)
    raw = {"front": "Power?", "back": "1 mW"}
    page.blocks = [_flash_block("flash", [raw])]
    _atomic_write_json(store.manifest_path(page.id), page.model_dump())
    before = store.manifest_path(page.id).read_bytes()
    identifier = legacy_id("flash", raw)
    _atomic_write_json(
        store.doc_root(page.id) / "review.json",
        {identifier: {"due_at": 10**12, "interval_days": 31}},
    )
    loaded = store.load_document(page.id)
    card = next(page_cards(loaded))
    assert card["card_id"] == identifier == card["legacy_guid"]
    assert not due_cards(store)
    assert store.manifest_path(page.id).read_bytes() == before
    assert render_anki_export(cards_from_document(loaded)).splitlines()[-1].endswith(identifier)


def test_minor_edits_preserve_schedule_major_edits_need_explicit_decision(store):
    page = make_page(store)
    card = next(page_cards(page))
    schedule = grade_card(store, page.id, card["card_id"], True, card["content_hash"])
    minor = edit(store, page, back="  1   mW ")
    current = next(page_cards(minor))
    assert current["card_id"] == card["card_id"]
    assert current["content_revision"] == 2
    assert not due_cards(store)
    major = edit(store, minor, back="1 MW")
    changed = due_cards(store)[0]
    assert changed["card_id"] == card["card_id"]
    assert changed["needs_schedule_review"]
    assert changed["previous_content"]["back"] == "  1   mW "
    with pytest.raises(ValueError, match="changed while reviewing"):
        grade_card(store, page.id, card["card_id"], True, card["content_hash"])
    with pytest.raises(ValueError, match="content change"):
        grade_card(store, page.id, card["card_id"], True, changed["content_hash"])
    decision = resolve_card_change(store, page.id, card["card_id"], changed["content_hash"], "keep")
    assert decision["due_at"] == schedule["due_at"]
    assert not due_cards(store)
    again = edit(store, major, back=" 1 MW ")
    assert not next(page_cards(again))["needs_schedule_review"]
    assert not due_cards(store)
    final = edit(store, again, back="2 MW")
    reset = resolve_card_change(
        store, page.id, card["card_id"], next(page_cards(final))["content_hash"], "reset"
    )
    assert reset["interval_days"] == 0
    assert len(reset["schedule_decisions"]) == 2
    assert len(due_cards(store)) == 1


def test_copying_cards_gets_distinct_identity_and_guid_and_survives_reload(store):
    page = make_page(store)
    blocks = copy.deepcopy(page.blocks)
    data = json.loads(blocks[0]["props"]["data"])
    data["cards"] *= 4
    blocks[0]["props"]["data"] = json.dumps(data)
    result = store.update_document(page.id, blocks=blocks, expected_revision=page.revision)
    cards = list(page_cards(result))
    assert len({c["card_id"] for c in cards}) == 4
    assert len({c["legacy_guid"] for c in cards}) == 4
    assert cards == list(page_cards(store.load_document(page.id)))


def test_bad_metadata_does_not_hide_whole_page_and_cannot_erase_pending_review(store):
    page = make_page(store)
    blocks = copy.deepcopy(page.blocks)
    data = json.loads(blocks[0]["props"]["data"])
    data["cards"][0][META]["revision"] = "oops"
    blocks[0]["props"]["data"] = json.dumps(data)
    assert normalize_cards(blocks)
    major = edit(store, page, back="10 MW")
    replacement = edit(
        store,
        major,
        **{
            META: {
                **json.loads(major.blocks[0]["props"]["data"])["cards"][0][META],
                "needs_schedule_review": False,
            }
        },
    )
    assert next(page_cards(replacement))["needs_schedule_review"]


def test_schedule_decision_rejects_stale_content(store):
    page = make_page(store)
    card = next(page_cards(page))
    edit(store, page, back="20 W")
    with pytest.raises(ValueError, match="changed"):
        resolve_card_change(store, page.id, card["card_id"], card["content_hash"], "reset")
