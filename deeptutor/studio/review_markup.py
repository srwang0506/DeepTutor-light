"""Project LaTeXCoder's portable review markers onto printable TeX without shifting SyncTeX lines."""

import re

START = re.compile(r"\\(cmtbg|revbg|addbg|delbg)\{")
CLOSE = {"cmtbg": "cmted", "revbg": "reved", "addbg": "added", "delbg": "deled"}


def group_end(source, cursor):
    if source[cursor : cursor + 1] != "{":
        return None
    level = 1
    cursor += 1
    while cursor < len(source):
        ch = source[cursor]
        cursor += 1
        if ch == "\\":
            cursor += 1
        elif ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
            if not level:
                return cursor
    return None


def note_definitions(data):
    declarations = re.finditer(
        rb"\\(?:newcommand|renewcommand|providecommand|def)\s*\{?\\(sectiontldr|tldr)\b", data
    )
    return {m[1].decode("ascii") for m in declarations}


def printable(path, data, declared=()):
    if not path.lower().endswith(".tex"):
        return data
    if not any(
        key in data
        for key in (
            b"\\cmtbg",
            b"\\revbg",
            b"\\addbg",
            b"\\delbg",
            b"% latexcoder:thread:",
            b"\\sectiontldr",
            b"\\tldr",
        )
    ):
        return data
    source = data.decode("utf-8")
    parts, last, scan = [], 0, 0
    while match := START.search(source, scan):
        scan = match.end()
        ident = group_end(source, match.end() - 1)
        body = group_end(source, ident) if ident is not None else None
        marker = "\\" + CLOSE[match[1]] + "{"
        end = source.find(marker, body) if body is not None else -1
        tail = group_end(source, end + len(marker) - 1) if end >= 0 else None
        if tail is None:
            continue
        visible = source[body:end] if match[1] != "delbg" else "\n" * source.count("\n", body, end)
        parts.extend(
            (
                source[last : match.start()],
                "\n" * source.count("\n", match.start(), body),
                visible,
                "\n" * source.count("\n", end, tail),
            )
        )
        last = scan = tail
    parts.append(source[last:])
    source = re.sub(r"(?m)^% latexcoder:thread:[^\r\n]*", "", "".join(parts))
    # TreeWriter notes are private drafting text unless the author defines their own typesetting.
    for macro in ("sectiontldr", "tldr"):
        if macro in declared or macro in note_definitions(data):
            continue
        parts, last = [], 0
        for match in re.finditer(r"\\" + macro + r"\s*\{", source):
            if match.start() < last:
                continue
            end = group_end(source, match.end() - 1)
            if end is not None:
                parts.extend(
                    (source[last : match.start()], "\n" * source.count("\n", match.start(), end))
                )
                last = end
        source = "".join(parts) + source[last:]
    return source.encode()
