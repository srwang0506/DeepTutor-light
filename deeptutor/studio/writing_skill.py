"""Use the bundled writing skill one section at a time and keep its notes out of the manuscript."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

from deeptutor.services.skill.execution import _source
from deeptutor.services.skill.service import BUILTIN_SKILLS_ROOT, SkillNotFoundError

NAME = "research-paper-writing"
Section = Literal[
    "abstract",
    "introduction",
    "related-work",
    "method",
    "experiments",
    "conclusion",
    "paragraph-flow",
    "figures-tables",
    "self-review",
]
SECTION_FILES = {
    "paragraph-flow": "does-my-writing-flow-source",
    "figures-tables": "paper-review",
    "self-review": "paper-review",
}
EXAMPLES = {
    "abstract": "examples/abstract/template-a.md",
    "introduction": "examples/introduction/version-3-general-to-specific-setting.md",
    "method": "examples/method/section-skeleton.md",
}
ShortText = Annotated[str, Field(min_length=1, max_length=2000)]


class Claim(BaseModel):
    claim: ShortText
    source_ids: list[str] = Field(max_length=20)


class Draft(BaseModel):
    replacement: str = Field(min_length=1, max_length=40000)
    outline: list[ShortText] = Field(min_length=1, max_length=7)
    paragraph_roles: list[ShortText] = Field(min_length=1, max_length=24)
    checks: list[ShortText] = Field(min_length=1, max_length=10)
    claims: list[Claim] = Field(max_length=24)


def guide(section: Section):
    try:
        root = _source(NAME)
    except SkillNotFoundError as exc:
        raise ValueError("Enable the research-paper-writing skill in this workspace.") from exc
    if root.resolve() != (BUILTIN_SKILLS_ROOT / NAME).resolve():
        raise ValueError("The writing desk requires the bundled research-paper-writing skill.")
    paths = ["SKILL.md", f"references/{SECTION_FILES.get(section, section)}.md"]
    if section in EXAMPLES:
        paths.append("references/" + EXAMPLES[section])
    return {
        "name": NAME,
        "commit": json.loads((root / "PROVENANCE.json").read_text())["commit"],
        "section": section,
        "files": paths,
        "instructions": "\n\n".join((root / path).read_text(encoding="utf-8") for path in paths),
    }


def parse_draft(raw: str, sources):
    raw = raw.strip()
    if raw.startswith(("\x60\x60\x60json\n", "\x60\x60\x60\n")) and raw.endswith("\x60\x60\x60"):
        raw = raw.partition("\n")[2][:-3].strip()
    try:
        draft = Draft.model_validate_json(raw)
    except ValidationError:
        raise ValueError("The writing guide returned an incomplete report. Please retry.") from None
    if not draft.replacement.strip():
        raise ValueError("The writing guide returned an incomplete report. Please retry.")
    eligible = {item["id"] for item in sources if not item["stale"]}
    report = draft.model_dump(exclude={"replacement"})
    for claim in report["claims"]:
        claim["source_ids"] = list(
            dict.fromkeys(key for key in claim["source_ids"] if key in eligible)
        )
    return draft.replacement, report
