"""Prefer explicit exercise/equation/section labels inside the selected corpus."""

import re
import unicodedata


def locator_pattern(query):
    query = unicodedata.normalize("NFC", query)
    match = re.search(
        r"第\s*(\d+(?:\.\d+)*)\s*([题章节])|(?:公式|方程|equation|eq\.?|exercise|question|section)\s*\(?\s*(\d+(?:\.\d+)*)",
        query,
        re.I,
    )
    if not match:
        return None
    label = re.escape(match[1] or match[3])
    if match[1]:
        return re.compile(rf"第\s*{label}\s*{match[2]}|(?<![\d.]){label}[、.)．](?!\d)", re.I)
    return re.compile(
        rf"(?:公式|方程|equation|eq\.?|exercise|question|section)\s*\(?\s*{label}(?![\d.])|\({label}\)",
        re.I,
    )


def prioritize_located_nodes(index, query, candidates, *, top_k):
    pattern = locator_pattern(query)
    if pattern is None:
        return candidates
    from llama_index.core.schema import NodeWithScore

    nodes = list(getattr(index.docstore, "docs", {}).values())
    exact = [node for node in nodes if pattern.search(node.get_content())]
    if not exact:
        return candidates
    found = [NodeWithScore(node=node, score=1.0) for node in exact[:top_k]]
    identities = {node.node_id for node in exact}
    # Preserve surrounding assumptions and figure captions on the same source
    # page. The caller has already narrowed the index to authorized documents.
    pages = {(node.metadata.get("document_key"), node.metadata.get("page_index")) for node in exact}
    for node in nodes:
        page = (node.metadata.get("document_key"), node.metadata.get("page_index"))
        if None not in page and page in pages and node.node_id not in identities:
            found.append(NodeWithScore(node=node, score=0.5))
            identities.add(node.node_id)
    return (found + [hit for hit in candidates if hit.node.node_id not in identities])[:top_k]
