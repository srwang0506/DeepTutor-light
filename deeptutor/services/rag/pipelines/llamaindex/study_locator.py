"""Typed structural lookup in an already authorized corpus, followed by rank fusion.

An exercise number is not a list item or an equation number. Scope filtering
precedes fusion; a high score can never restore an excluded edition/version.
"""

from functools import lru_cache
import hashlib
import re
import unicodedata

LABELS = {
    "exercise": r"题|习题|练习|exercise|question|problem",
    "equation": r"公式|方程|equation|eq\.?",
    "table": r"表|table",
    "figure": r"图|figure|fig\.?",
    "section": r"节|章|section|chapter",
    "definition": r"定义|definition",
}
NUMBER = r"\d+(?:\.\d+)*[a-z]?"


def locator_intent(query):
    text = unicodedata.normalize("NFC", query)
    for kind, label in LABELS.items():
        match = re.search(
            rf"(?:第\s*(?P<zh>{NUMBER})\s*(?:{label}))|(?:(?:{label})\s*\(?\s*(?P<en>{NUMBER})(?![\w.]))",
            text,
            re.I,
        )
        if match:
            return kind, (match["zh"] or match["en"]).lower()
    return None


def locator_pattern(query):
    intent = locator_intent(query)
    if not intent:
        return None
    kind, number = intent
    number = re.escape(number)
    label = LABELS[kind]
    explicit = rf"(?:第\s*{number}\s*(?:{label})|(?:{label})\s*\(?\s*{number}(?![\w.]))"
    if kind == "equation":
        # Parenthesized numbering alone is insufficient; require a math relation on that line.
        explicit += rf"|(?m:^\s*\({number}\)[^\n]*(?:=|≤|≥|\\(?:frac|sum|int)))"
    return re.compile(explicit, re.I)


@lru_cache(maxsize=1024)
def structural_labels(text):
    labels = set()
    for kind, label in LABELS.items():
        for match in re.finditer(
            rf"(?:第\s*({NUMBER})\s*(?:{label}))|(?:(?:{label})\s*\(?\s*({NUMBER})(?![\w.]))",
            text,
            re.I,
        ):
            labels.add((kind, (match[1] or match[2]).lower()))
    return frozenset(labels)


def prioritize_located_nodes(index, query, candidates, *, top_k, scope=None):
    intent = locator_intent(query)
    if intent is None:
        return candidates
    from llama_index.core.schema import NodeWithScore

    def permitted(node):
        return all(node.metadata.get(k) == v for k, v in (scope or {}).items())

    nodes = [n for n in getattr(index.docstore, "docs", {}).values() if permitted(n)]
    exact = [
        n
        for n in nodes
        if intent in structural_labels(n.get_content()[:100000])
        or (intent[0] == "equation" and locator_pattern(query).search(n.get_content()[:100000]))
    ]
    if not exact:
        return [c for c in candidates if permitted(c.node)][:top_k]
    pages = {
        (n.metadata.get("document_key"), n.metadata.get("revision"), n.metadata.get("page_index"))
        for n in exact
        if n.metadata.get("document_key") is not None and n.metadata.get("page_index") is not None
    }
    neighbors = [
        n
        for n in nodes
        if (
            n.metadata.get("document_key"),
            n.metadata.get("revision"),
            n.metadata.get("page_index"),
        )
        in pages
        and n not in exact
    ]
    ranks, records = {}, {}
    # Structural hits receive an explicit tie-break. RRF preserves lexical/dense corroboration.
    for channel in [
        exact[:40],
        [hit.node for hit in candidates if permitted(hit.node)][:40],
        neighbors[:8],
    ]:
        for rank, node in enumerate(channel):
            identity = (
                node.metadata.get("document_key"),
                node.metadata.get("revision"),
                node.metadata.get("page_index"),
                hashlib.sha256(node.get_content().encode()).hexdigest(),
            )
            ranks[identity] = ranks.get(identity, 0) + 1 / (60 + rank + 1)
            records[identity] = node
    exact_ids = {n.node_id for n in exact}
    ordered = sorted(ranks, key=lambda k: (records[k].node_id in exact_ids, ranks[k]), reverse=True)
    return [NodeWithScore(node=records[key], score=ranks[key]) for key in ordered[:top_k]]
