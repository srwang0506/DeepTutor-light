"""Validated commands. Suggestions never silently become verified facts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WorkInput(Command):
    title: str = Field(min_length=1, max_length=1000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    year: int | None = Field(default=None, ge=1000, le=2200)
    kind: Literal["paper", "book", "lecture", "web", "code", "media"] = "paper"
    tags: list[str] = Field(default_factory=list, max_length=100)
    abstract: str = Field(default="", max_length=30000)


class EditionInput(Command):
    work_id: str
    label: str = Field(default="Original", min_length=1, max_length=200)
    doi: str = Field(default="", max_length=500)
    arxiv: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=4000)
    citekey: str = Field(default="", max_length=200)
    venue: str = Field(default="", max_length=500)


class EvidenceInput(Command):
    edition_id: str
    blob_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1, le=100000)
    page_label: str = Field(default="", max_length=100)
    kind: Literal["quote", "table", "figure", "formula", "code"] = "quote"
    region: tuple[float, float, float, float] | None = None
    quote: str = Field(default="", max_length=20000)
    prefix: str = Field(default="", max_length=1000)
    suffix: str = Field(default="", max_length=1000)
    label: str = Field(min_length=1, max_length=500)
    interpretation: str = Field(default="", max_length=10000)
    stance: Literal["supports", "contradicts", "qualifies", "context"] = "context"
    verification: Literal["unreviewed", "confirmed", "disputed"] = "unreviewed"
    parser_signature: str = Field(default="original", max_length=200)
    visual_review: Literal["not_reviewed", "confirmed", "disputed"] = "not_reviewed"
    review_note: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def valid_region(self):
        if self.region:
            x0, y0, x1, y1 = self.region
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("Select a nonempty region inside the source page.")
        if not self.quote.strip() and not self.region:
            raise ValueError("Evidence requires an exact quote or page region.")
        return self


class Protocol(Command):
    profile: Literal[
        "general", "hoi_detection", "hoi_reconstruction", "trajectory", "mathematics"
    ] = "general"
    dataset: str = Field(default="", max_length=500)
    dataset_version: str = Field(default="", max_length=500)
    split: str = Field(default="", max_length=500)
    population: str = Field(default="", max_length=500)
    input: str = Field(default="", max_length=500)
    supervision: str = Field(default="", max_length=500)
    alignment: str = Field(default="", max_length=500)
    sampling: str = Field(default="", max_length=500)
    metric: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=500)
    threshold: str = Field(default="", max_length=500)
    aggregation: str = Field(default="", max_length=500)
    direction: Literal["higher", "lower", "unknown"] = "unknown"
    not_applicable: list[str] = Field(default_factory=list, max_length=12)
    extensions: dict[str, str] = Field(default_factory=dict, max_length=30)
    field_states: dict[str, Literal["known", "unknown", "not_applicable", "conflict"]] = Field(
        default_factory=dict, max_length=12
    )

    @model_validator(mode="after")
    def applicable_conditions(self):
        if any(len(k) > 100 or len(v) > 1000 for k, v in self.extensions.items()):
            raise ValueError("Protocol extension names or values are too long.")
        fields = set(type(self).model_fields) - {
            "direction",
            "not_applicable",
            "profile",
            "extensions",
            "field_states",
        }
        if not set(self.field_states) <= fields:
            raise ValueError("Unsupported protocol state field.")
        for field in fields:
            value = getattr(self, field).strip()
            state = self.field_states.get(field)
            missing = not value or value.casefold() in {"unknown", "n/a", "?", "未知"}
            if state == "known" and missing:
                raise ValueError("A known protocol field needs a concrete value.")
            if state == "not_applicable" and value:
                raise ValueError("Not-applicable fields cannot contain a value.")
            if field in self.not_applicable and state not in {None, "not_applicable"}:
                raise ValueError("Protocol field state conflicts with not-applicable selection.")
            self.field_states.setdefault(
                field,
                "not_applicable"
                if field in self.not_applicable
                else "unknown"
                if missing
                else "known",
            )
        if (
            len(set(self.not_applicable)) != len(self.not_applicable)
            or not set(self.not_applicable) <= fields
        ):
            raise ValueError("Choose distinct, supported protocol fields as not applicable.")
        if any(getattr(self, field).strip() for field in self.not_applicable):
            raise ValueError(
                "A condition cannot have a value and be not applicable at the same time."
            )
        return self


class ObservationInput(Command):
    title: str = Field(min_length=1, max_length=500)
    origin: Literal["reported", "executed", "static_check", "hypothesis"]
    source_id: str
    protocol: Protocol
    value: float | None = None
    state: Literal[
        "reported", "no_full_text", "not_found", "not_reported", "not_applicable", "conflict"
    ] = "reported"
    note: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def value_status(self):
        if self.state != "reported" and self.value is not None:
            raise ValueError(
                "Unknown or conflicting observations cannot carry an authoritative value."
            )
        if self.state == "reported" and self.value is None:
            raise ValueError("A reported observation needs a numeric value.")
        return self


class RunInput(Command):
    title: str = Field(min_length=1, max_length=500)
    status: Literal["planned", "running", "succeeded", "failed", "cancelled", "static_only"]
    repository: str = Field(default="", max_length=4000)
    commit: str = Field(default="", max_length=200)
    patch: str = Field(default="", max_length=1000000)
    command: str = Field(default="", max_length=10000)
    configuration: dict = Field(default_factory=dict)
    dataset: str = Field(default="", max_length=500)
    split: str = Field(default="", max_length=500)
    environment: str = Field(default="", max_length=10000)
    hardware: str = Field(default="", max_length=2000)
    seed: int | None = None
    checkpoint: str = Field(default="", max_length=4000)
    log: str = Field(default="", max_length=2000000)
    artifacts: list[dict[str, str]] = Field(default_factory=list, max_length=200)


class ClaimEvidenceEdge(Command):
    source_id: str
    source_revision: int = Field(ge=1)
    role: Literal["supports", "contradicts", "qualifies", "context"]
    qualifiers: str = Field(default="", max_length=5000)
    review: Literal["unreviewed", "confirmed", "disputed"] = "unreviewed"


class ClaimInput(Command):
    title: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["draft", "confirmed", "disputed"] = "draft"
    note: str = Field(default="", max_length=10000)
    edges: list[ClaimEvidenceEdge] = Field(default_factory=list, max_length=100)


class ScreeningInput(Command):
    edition_id: str
    stage: Literal["title_abstract", "full_text", "extraction"]
    decision: Literal["pending", "include", "exclude", "awaiting_full_text"]
    reason: str = Field(default="", max_length=5000)
    criterion: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def reason_required(self):
        if self.decision in {"include", "exclude"} and not self.reason.strip():
            raise ValueError("Record a reason for inclusion or exclusion.")
        return self


class RevisionCommand(Command):
    expected_revision: int = Field(ge=1)


class FileEdit(Command):
    path: str = Field(min_length=1, max_length=500)
    base_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str = Field(max_length=2000000)


class ProposalInput(FileEdit):
    source_revisions: dict[str, int] = Field(default_factory=dict, max_length=100)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    rationale: str = Field(default="", max_length=5000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class ManuscriptInput(Command):
    title: str = Field(min_length=1, max_length=500)
    main_file: str = Field(default="main.tex", max_length=500)
    engine: Literal["pdflatex", "xelatex", "lualatex"] = "pdflatex"


class ReviewInput(Command):
    submission_id: str
    comment: str = Field(min_length=1, max_length=20000)
    response: str = Field(default="", max_length=20000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    changes: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    status: Literal["open", "working", "addressed"] = "open"
