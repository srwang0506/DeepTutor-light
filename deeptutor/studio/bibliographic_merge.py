"""Additive enrichment with original arrivals, aliases and conflicting fields retained."""

from .database import encoded, sha


def fields(source):
    raw, format = source.get("record", {}), source.get("format")
    if format == "bibtex":
        return {k: v for k, v in raw.items() if k != "ID" and v}
    if format == "csl":
        result = {
            dest: raw[src]
            for src, dest in {
                "title": "title",
                "container-title": "booktitle",
                "page": "pages",
                "publisher": "publisher",
                "volume": "volume",
                "issue": "number",
                "DOI": "doi",
                "URL": "url",
            }.items()
            if raw.get(src)
        }
        kind = raw.get("type")
    elif format == "ris":
        result = {
            dest: raw[src][0]
            for src, dest in {
                "TI": "title",
                "JO": "booktitle",
                "SP": "pages",
                "PB": "publisher",
                "VL": "volume",
                "IS": "number",
                "DO": "doi",
                "UR": "url",
            }.items()
            if raw.get(src)
        }
        if raw.get("EP") and result.get("pages"):
            result["pages"] += "--" + raw["EP"][0]
        kind = (raw.get("TY") or [""])[0]
    else:
        return {}
    if kind:
        result["ENTRYTYPE"] = {
            "paper-conference": "inproceedings",
            "CONF": "inproceedings",
            "CPAPER": "inproceedings",
            "book": "book",
            "BOOK": "book",
            "chapter": "incollection",
            "CHAP": "incollection",
            "thesis": "phdthesis",
            "THES": "phdthesis",
        }.get(kind, "article")
    return result


def merge(data, source, citekey=""):
    originals = list(data.get("bibliographic_sources", []))
    old = data.get("bibliographic_source")
    if old and old not in originals:
        originals.append(old)
    if source not in originals:
        originals.append(source)
    result = dict(data.get("bibliographic_merged") or fields(old or {}))
    origins = dict(data.get("bibliographic_provenance", {}))
    if old:
        for key in fields(old):
            origins.setdefault(key, sha(encoded(old).encode()))
    conflicts = list(data.get("bibliographic_conflicts", []))
    digest = sha(encoded(source).encode())
    for key, value in fields(source).items():
        if not result.get(key):
            result[key], origins[key] = value, digest
        elif result[key] != value:
            conflict = {"field": key, "retained": result[key], "incoming": value, "source": digest}
            if conflict not in conflicts:
                conflicts.append(conflict)
    aliases = sorted(
        set(filter(None, [*data.get("citekey_aliases", []), data.get("citekey"), citekey]))
    )
    return {
        **data,
        "bibliographic_sources": originals,
        "bibliographic_merged": result,
        "bibliographic_provenance": origins,
        "bibliographic_conflicts": conflicts,
        "citekey_aliases": aliases,
    }
