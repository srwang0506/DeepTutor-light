"""Persistent review scheduling for flashcards embedded in notebook pages."""

import json
import time

from deeptutor.co_writer.storage import _atomic_write_json, _read_json
from deeptutor.notes.blocks import walk_blocks
from deeptutor.notes.card_identity import content_hash, legacy_id, metadata


def page_cards(doc):
    for block in walk_blocks(doc.blocks):
        props = block.get("props", {})
        if block.get("type") != "learning" or props.get("kind") != "flashcards":
            continue
        try:
            cards = json.loads(props.get("data", "{}")).get("cards", [])
        except (ValueError, AttributeError, TypeError):
            continue
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict) or not card.get("front"):
                continue
            meta = metadata(card)
            key = meta.get("id") or legacy_id(block["id"], card)
            yield dict(
                page_id=doc.id,
                page_title=doc.title,
                block_id=block["id"],
                card_id=key,
                front=str(card.get("front", "")),
                back=str(card.get("back", "")),
                content_revision=meta.get("revision", 1),
                content_hash=content_hash(card),
                needs_schedule_review=meta.get("needs_schedule_review", False),
                legacy_guid=meta.get("legacy_guid", key),
                previous_content=meta.get("previous_content"),
            )


def due_cards(storage, now=None):
    now = time.time() if now is None else now
    result = []
    for summary in storage.list_documents():
        doc = storage.load_document(summary.id)
        if doc is None:
            continue
        progress = _read_json(storage.doc_root(doc.id) / "review.json") or {}
        for card in page_cards(doc):
            record = progress.get(card["card_id"], {})
            due = record.get("due_at", 0)
            review_change = (
                card["needs_schedule_review"]
                and record.get("confirmed_content_hash") != card["content_hash"]
            )
            if due <= now or review_change:
                result.append({**card, "due_at": due, "needs_schedule_review": review_change})
    return sorted(result, key=lambda card: card["due_at"])[:200]


def grade_card(storage, page_id, card_id, remembered, expected_content_hash=None):
    with storage.locked():
        doc = storage.load_document(page_id)
        if doc is None:
            raise ValueError("Page not found")
        cards = {card["card_id"]: card for card in page_cards(doc)}
        valid = set(cards)
        if card_id not in valid:
            raise ValueError("This flashcard has changed or no longer exists")
        card = cards[card_id]
        if expected_content_hash and expected_content_hash != card["content_hash"]:
            raise ValueError("This card changed while reviewing. Reload before grading.")
        path = storage.doc_root(page_id) / "review.json"
        progress = {key: value for key, value in (_read_json(path) or {}).items() if key in valid}
        previous = progress.get(card_id, {})
        if (
            card["needs_schedule_review"]
            and previous.get("confirmed_content_hash") != card["content_hash"]
        ):
            raise ValueError("Review this card's content change before continuing its schedule.")
        interval = min(180, max(1, previous.get("interval_days", 0) * 2 + 1)) if remembered else 0
        result = {
            **previous,
            "interval_days": interval,
            "content_hash": card["content_hash"],
            "due_at": time.time() + (interval * 86400 if remembered else 600),
            "reviewed_at": time.time(),
        }
        progress[card_id] = result
        _atomic_write_json(path, progress)
        return result


def resolve_card_change(storage, page_id, card_id, expected_content_hash, action):
    if action not in {"keep", "reset"}:
        raise ValueError("Choose whether to keep or reset the review schedule.")
    with storage.locked():
        doc = storage.load_document(page_id)
        if doc is None:
            raise ValueError("Page not found")
        card = next((c for c in page_cards(doc) if c["card_id"] == card_id), None)
        if not card or card["content_hash"] != expected_content_hash:
            raise ValueError("This card changed. Reload before choosing its schedule.")
        path = storage.doc_root(page_id) / "review.json"
        progress = _read_json(path) or {}
        previous = progress.get(card_id, {})
        now = time.time()
        record = {
            **previous,
            "confirmed_content_hash": expected_content_hash,
            "schedule_decisions": [
                *previous.get("schedule_decisions", []),
                {"action": action, "at": now, "content_hash": expected_content_hash},
            ][-100:],
        }
        if action == "reset":
            record.update(interval_days=0, due_at=now)
        progress[card_id] = record
        _atomic_write_json(path, progress)
        return record
