"""Stable card identity, explicit content revisions, and conservative edit tracking."""

import copy
import hashlib
import json
import re

from deeptutor.notes.blocks import walk_blocks

META = "_study"


def body(card):
    return {k: v for k, v in card.items() if k != META}


def legacy_id(block_id, card):
    # Byte-for-byte compatible with the pre-migration review key and Anki GUID.
    return (
        block_id
        + ":"
        + hashlib.sha256(json.dumps(body(card), sort_keys=True).encode()).hexdigest()[:16]
    )


def content_hash(card):
    return hashlib.sha256(
        json.dumps(body(card), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def substantial(before, after):
    # Case matters in formulas and units (mW != MW). Only whitespace is minor.
    def normalized(value):
        return re.sub(r"\s+", " ", str(value)).strip()

    return any(
        normalized(before.get(k, "")) != normalized(after.get(k, ""))
        for k in set(before) | set(after)
        if k != META
    )


def card_blocks(blocks):
    for block in walk_blocks(blocks or []):
        if block.get("type") != "learning" or block.get("props", {}).get("kind") != "flashcards":
            continue
        try:
            data = json.loads(block["props"].get("data", "{}"))
            cards = data.get("cards", [])
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(cards, list):
            yield block, data, cards


def metadata(card):
    return card.get(META) if isinstance(card.get(META), dict) else {}


def normalize_cards(blocks, previous=None, progress=None):
    if blocks is None:
        return None
    result = copy.deepcopy(blocks)
    old_by_id = {}
    for block, _, cards in card_blocks(previous):
        for card in cards:
            if isinstance(card, dict) and card.get("front"):
                identifier = metadata(card).get("id") or legacy_id(block["id"], card)
                if isinstance(identifier, str):
                    old_by_id[identifier] = card
    seen = set()
    for block, data, cards in card_blocks(result):
        for card in cards:
            if not isinstance(card, dict) or not card.get("front"):
                continue
            supplied = metadata(card)
            identifier = supplied.get("id") or legacy_id(block["id"], card)
            if not isinstance(identifier, str) or not re.fullmatch(r"[\w:-]{1,180}", identifier):
                identifier = legacy_id(block["id"], card)
            duplicate = identifier in seen
            if duplicate:
                base = legacy_id(block["id"], card)
                counter = 1
                while (identifier := f"{base}:copy{counter}") in seen:
                    counter += 1
            seen.add(identifier)
            old = old_by_id.get(identifier)
            prior = metadata(old) if old else ({} if duplicate else supplied)
            fingerprint = content_hash(card)
            old_hash = content_hash(old) if old else prior.get("content_hash", fingerprint)
            try:
                revision = min(1000000000, max(1, int(prior.get("revision", 1))))
            except (TypeError, ValueError, OverflowError):
                revision = 1
            changed = old_hash != fingerprint
            record = (progress or {}).get(identifier, {})
            confirmed = record.get("confirmed_content_hash")
            pending = bool(prior.get("needs_schedule_review")) and confirmed != old_hash
            major = changed and (substantial(old, card) if old else True)
            previous_content = body(old) if old and major else prior.get("previous_content")
            card[META] = {
                "id": identifier,
                "legacy_guid": prior.get("legacy_guid") or identifier,
                "revision": revision + int(changed),
                "content_hash": fingerprint,
                "needs_schedule_review": (pending or major) and confirmed != fingerprint,
                # One prior version is enough for an actionable schedule decision;
                # later edits never reuse the content hash as scheduling identity.
                "previous_content": previous_content if pending or major else None,
            }
        block["props"]["data"] = json.dumps(data, ensure_ascii=False)
    return result
