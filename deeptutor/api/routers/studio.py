"""Personal Studio commands use the same domain methods as non-HTTP clients."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field

from deeptutor.api.routers.auth import require_admin
from deeptutor.api.routers.co_writer import LLMSelectionPayload, _validated_llm_selection
from deeptutor.studio import (
    assist,
    builds,
    catalog,
    connections,
    discovery,
    evidence,
    experiments,
    manuscripts,
    subscriptions,
    writing,
)
from deeptutor.studio.database import Conflict, Missing, Studio
from deeptutor.studio.models import (
    ClaimInput,
    Command,
    EditionInput,
    EvidenceInput,
    FileEdit,
    ManuscriptInput,
    ObservationInput,
    ProposalInput,
    ReviewInput,
    RunInput,
    ScreeningInput,
    WorkInput,
)

router = APIRouter()


async def operation(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except Missing as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc


async def file_bytes(file: UploadFile, maximum=100 * 1024 * 1024):
    data = bytearray()
    try:
        while part := await file.read(1024 * 1024):
            data.extend(part)
            if len(data) > maximum:
                raise HTTPException(413, "Upload exceeds the supported size.")
        return bytes(data)
    finally:
        await file.close()


@router.get("/library")
async def library(q: str = Query("", max_length=2000), project_only: bool = False):
    return {"works": await operation(catalog.library, Studio(), q, project_only)}


@router.post("/works")
async def work(payload: WorkInput):
    return await operation(catalog.create_work, Studio(), payload)


@router.post("/editions")
async def edition(payload: EditionInput):
    return await operation(catalog.create_edition, Studio(), payload)


@router.post("/editions/{identifier}/pdf")
async def attach(identifier: str, file: UploadFile = File(...), expected_revision: int = Form(...)):
    return await operation(
        catalog.attach_pdf,
        Studio(),
        identifier,
        await file_bytes(file),
        file.filename or "original.pdf",
        expected_revision,
    )


@router.get("/editions/{identifier}/files/{digest}")
async def original(identifier: str, digest: str):
    path, metadata = await operation(catalog.edition_file, Studio(), identifier, digest)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"},
    )


@router.get("/editions/{identifier}/files/{digest}/pages/{page}")
async def original_page(identifier: str, digest: str, page: int):
    return await operation(catalog.page_source, Studio(), identifier, digest, page)


class ProjectUse(Command):
    role: Literal["reading", "background", "method", "baseline", "excluded"] = "reading"


@router.post("/editions/{identifier}/project")
async def project_use(identifier: str, payload: ProjectUse):
    return await operation(catalog.link_project, Studio(), identifier, payload.role)


class BibliographyImport(Command):
    text: str = Field(max_length=5_000_000)
    format: Literal["bibtex", "ris", "csl"]


@router.post("/bibliography/import")
async def import_bibliography(payload: BibliographyImport):
    return await operation(catalog.import_bibliography, Studio(), payload.text, payload.format)


class BibliographyExport(Command):
    edition_ids: list[str] = Field(min_length=1, max_length=500)
    keys: dict[str, str] = Field(default_factory=dict)


@router.post("/bibliography/export")
async def export_bibliography(payload: BibliographyExport):
    text = await operation(catalog.bibliography, Studio(), payload.edition_ids, payload.keys)
    return Response(
        text,
        media_type="application/x-bibtex",
        headers={"Content-Disposition": "attachment; filename=references.bib"},
    )


@router.get("/records/{kind}")
async def records(
    kind: Literal[
        "edition",
        "evidence",
        "observation",
        "run",
        "artifact",
        "claim",
        "screening",
        "discovery",
        "manuscript",
        "proposal",
        "build",
        "submission",
        "review",
        "connection",
        "remote_snapshot",
        "connection_event",
        "passage",
        "citation",
        "collection",
        "subscription",
    ],
    q: str = Query("", max_length=2000),
):
    store = Studio()
    if kind == "build":
        await operation(builds.resume, store)
    return {"records": await operation(store.list, kind, q)}


@router.get("/record/{identifier}")
async def record(identifier: str):
    store = Studio()
    row = await operation(store.get, identifier)
    return {**row, "stale": await operation(store.stale, identifier)}


@router.get("/record/{identifier}/impact")
async def impact(identifier: str):
    return {"records": await operation(Studio().impact, identifier)}


@router.post("/evidence")
async def add_evidence(payload: EvidenceInput):
    return await operation(evidence.create_evidence, Studio(), payload)


@router.post("/observations")
async def add_observation(payload: ObservationInput):
    return await operation(evidence.create_observation, Studio(), payload)


class ObservationUpdate(ObservationInput):
    expected_revision: int = Field(ge=1)


@router.patch("/observations/{identifier}")
async def update_observation(identifier: str, payload: ObservationUpdate):
    return await operation(
        evidence.create_observation,
        Studio(),
        ObservationInput(**payload.model_dump(exclude={"expected_revision"})),
        identifier,
        payload.expected_revision,
    )


@router.get("/compare")
async def compare(left: str, right: str):
    return await operation(evidence.compare_observations, Studio(), left, right)


@router.post("/claims")
async def add_claim(payload: ClaimInput):
    return await operation(evidence.create_claim, Studio(), payload)


@router.post("/screening")
async def screening(payload: ScreeningInput):
    return await operation(evidence.screen, Studio(), payload)


@router.post("/runs")
async def add_run(payload: RunInput):
    return await operation(experiments.create_run, Studio(), payload)


class RunUpdate(RunInput):
    expected_revision: int = Field(ge=1)


@router.patch("/runs/{identifier}")
async def update_run(identifier: str, payload: RunUpdate):
    return await operation(
        experiments.create_run,
        Studio(),
        RunInput(**payload.model_dump(exclude={"expected_revision"})),
        identifier,
        payload.expected_revision,
    )


class RunsImport(Command):
    text: str = Field(max_length=5_000_000)
    format: Literal["csv", "json"]


@router.post("/runs/import")
async def import_runs(payload: RunsImport):
    return await operation(experiments.import_run_rows, Studio(), payload.text, payload.format)


class ArtifactInput(Command):
    title: str = Field(min_length=1, max_length=500)
    observation_ids: list[str] = Field(min_length=1, max_length=100)
    kind: Literal["table", "figure"] = "table"


@router.post("/artifacts")
async def artifact(payload: ArtifactInput):
    return await operation(
        experiments.make_table if payload.kind == "table" else experiments.make_figure,
        Studio(),
        payload.title,
        payload.observation_ids,
    )


@router.get("/artifacts/{identifier}/files/{name}")
async def artifact_file(identifier: str, name: str):
    store = Studio()
    row = await operation(store.get, identifier, "artifact")
    digest = row["data"]["files"].get(name)
    if not digest:
        raise HTTPException(404, "Artifact file not found.")
    return FileResponse(
        await operation(store.blob_path, digest),
        filename=name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


class DiscoveryInput(Command):
    query: str = Field(min_length=1, max_length=2000)
    providers: list[Literal["openalex", "crossref", "arxiv"]] = Field(
        default_factory=lambda: ["openalex", "crossref"]
    )
    limit: int = Field(default=20, ge=1, le=50)
    order: Literal["relevance", "newest"] = "relevance"
    year_from: int | None = Field(default=None, ge=1000, le=2200)
    year_to: int | None = Field(default=None, ge=1000, le=2200)


@router.post("/discover")
async def discover(payload: DiscoveryInput):
    try:
        return await discovery.search(Studio(), **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/discover/{identifier}/graph")
async def graph(identifier: str):
    return await operation(discovery.local_graph, Studio(), identifier)


@router.post("/manuscripts")
async def manuscript(payload: ManuscriptInput):
    return await operation(manuscripts.create_manuscript, Studio(), payload)


@router.post("/manuscripts/import")
async def import_manuscript(
    file: UploadFile = File(...),
    title: str = Form(...),
    main_file: str = Form("main.tex"),
    engine: Literal["pdflatex", "xelatex", "lualatex"] = Form("pdflatex"),
):
    return await operation(
        manuscripts.create_manuscript,
        Studio(),
        ManuscriptInput(title=title, main_file=main_file, engine=engine),
        await file_bytes(file),
    )


@router.get("/manuscripts/{identifier}/files")
async def manuscript_files(identifier: str):
    return {"files": await operation(manuscripts.file_list, Studio(), identifier)}


@router.get("/manuscripts/{identifier}/file")
async def manuscript_file(identifier: str, path: str):
    return await operation(manuscripts.read_file, Studio(), identifier, path)


@router.put("/manuscripts/{identifier}/file")
async def edit_manuscript_file(identifier: str, payload: FileEdit):
    return await operation(manuscripts.save_file, Studio(), identifier, payload)


@router.post("/manuscripts/{identifier}/proposals")
async def proposal(identifier: str, payload: ProposalInput):
    return await operation(manuscripts.propose, Studio(), identifier, payload)


class AcceptProposal(Command):
    expected_revision: int = Field(ge=1)
    confirm_facts: bool = False


@router.post("/proposals/{identifier}/accept")
async def accept(identifier: str, payload: AcceptProposal):
    return await operation(
        manuscripts.accept_proposal,
        Studio(),
        identifier,
        payload.expected_revision,
        payload.confirm_facts,
    )


@router.get("/manuscripts/{identifier}/audit")
async def audit(identifier: str):
    return await operation(manuscripts.audit, Studio(), identifier)


@router.post("/manuscripts/{identifier}/compile")
async def compile_manuscript(identifier: str):
    return await operation(builds.enqueue, Studio(), identifier)


@router.get("/builds/{identifier}/{name}")
async def build_file(
    identifier: str, name: Literal["preview.pdf", "build.log", "mapping.synctex.gz"]
):
    store = Studio()
    row = await operation(store.get, identifier, "build")
    digest = row["data"].get("outputs", {}).get(name)
    if not digest:
        raise HTTPException(404, "Build output unavailable.")
    return FileResponse(
        await operation(store.blob_path, digest),
        media_type="application/pdf" if name.endswith(".pdf") else "text/plain",
        headers={"X-Content-Type-Options": "nosniff"},
    )


class SubmissionInput(Command):
    paths: list[str] = Field(min_length=1, max_length=2000)
    build_id: str


@router.post("/manuscripts/{identifier}/submissions")
async def submit(identifier: str, payload: SubmissionInput):
    return await operation(
        manuscripts.submission, Studio(), identifier, payload.paths, payload.build_id
    )


@router.get("/submissions/{identifier}/download")
async def submission_download(identifier: str):
    store = Studio()
    row = await operation(store.get, identifier, "submission")
    return FileResponse(
        await operation(store.blob_path, row["data"]["archive_hash"]),
        media_type="application/zip",
        filename="submission.zip",
    )


@router.post("/reviews")
async def review(payload: ReviewInput):
    return await operation(manuscripts.review_comment, Studio(), payload)


@router.post("/connections", dependencies=[Depends(require_admin)])
async def add_connection(payload: connections.ConnectionInput):
    return await operation(connections.create_connection, Studio(), payload)


class ConnectionUpdate(connections.ConnectionInput):
    expected_revision: int = Field(ge=1)


@router.patch("/connections/{identifier}", dependencies=[Depends(require_admin)])
async def update_connection(identifier: str, payload: ConnectionUpdate):
    return await operation(
        connections.create_connection,
        Studio(),
        connections.ConnectionInput(**payload.model_dump(exclude={"expected_revision"})),
        identifier,
        payload.expected_revision,
    )


@router.post("/connections/{identifier}/read", dependencies=[Depends(require_admin)])
async def read_connection(identifier: str, payload: connections.ReadRequest):
    return await operation(connections.read_connection, Studio(), identifier, payload)


@router.get("/snapshots/{identifier}/text", dependencies=[Depends(require_admin)])
async def snapshot_text(identifier: str):
    return await operation(connections.snapshot_text, Studio(), identifier)


class SnapshotRun(Command):
    run: RunInput
    repository_snapshot_id: str = ""


@router.post("/snapshots/{identifier}/run", dependencies=[Depends(require_admin)])
async def import_remote_run(identifier: str, payload: SnapshotRun):
    return await operation(
        connections.import_snapshot_run,
        Studio(),
        identifier,
        payload.run,
        payload.repository_snapshot_id,
    )


class EvidenceReview(Command):
    expected_revision: int = Field(ge=1)
    verification: Literal["unreviewed", "confirmed", "disputed"]


@router.patch("/evidence/{identifier}/review")
async def review_evidence(identifier: str, payload: EvidenceReview):
    return await operation(
        evidence.review_evidence,
        Studio(),
        identifier,
        payload.expected_revision,
        payload.verification,
    )


@router.get("/manuscripts/{identifier}/recovery")
async def manuscript_recovery(identifier: str):
    return {"conflicts": await operation(manuscripts.recover_saves, Studio(), identifier)}


class RecoveryResolution(Command):
    journal: str = Field(min_length=1, max_length=1000)
    disk_hash: str = Field(default="", max_length=64)
    choice: Literal["disk", "before", "prepared"]


@router.post("/manuscripts/{identifier}/recovery")
async def resolve_manuscript_recovery(identifier: str, payload: RecoveryResolution):
    return await operation(
        manuscripts.resolve_recovery,
        Studio(),
        identifier,
        payload.journal,
        payload.disk_hash,
        payload.choice,
    )


@router.post("/builds/{identifier}/cancel")
async def cancel_build(identifier: str):
    return await operation(builds.cancel, Studio(), identifier)


@router.post("/builds/{identifier}/retry")
async def retry_build(identifier: str):
    return await operation(builds.retry, Studio(), identifier)


@router.get("/manuscripts/{identifier}/citations")
async def manuscript_citations(identifier: str, q: str = Query("", max_length=2000)):
    return await operation(writing.citations, Studio(), identifier, q)


class CitationInput(Command):
    edition_id: str
    citekey: str = Field(min_length=1, max_length=200)


@router.post("/manuscripts/{identifier}/citations")
async def add_manuscript_citation(identifier: str, payload: CitationInput):
    return await operation(
        writing.add_citation, Studio(), identifier, payload.edition_id, payload.citekey
    )


class PassageInput(Command):
    path: str = Field(min_length=1, max_length=500)
    base_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    source_ids: list[str] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=10000)


@router.get("/manuscripts/{identifier}/passages")
async def manuscript_passages(identifier: str):
    return {"passages": await operation(writing.passages, Studio(), identifier)}


@router.post("/manuscripts/{identifier}/passages")
async def bind_manuscript_passage(identifier: str, payload: PassageInput):
    return await operation(writing.bind_passage, Studio(), identifier, payload)


@router.get("/manuscripts/{identifier}/outline")
async def manuscript_outline(identifier: str):
    return {"outline": await operation(writing.outline, Studio(), identifier)}


class ReviewUpdate(ReviewInput):
    expected_revision: int = Field(ge=1)


@router.patch("/reviews/{identifier}")
async def update_review(identifier: str, payload: ReviewUpdate):
    return await operation(
        manuscripts.review_comment,
        Studio(),
        ReviewInput(**payload.model_dump(exclude={"expected_revision"})),
        identifier,
        payload.expected_revision,
    )


class DiscoveryPage(Command):
    provider: Literal["openalex", "crossref", "arxiv"]
    expected_revision: int = Field(ge=1)


@router.post("/discover/{identifier}/next")
async def next_discovery_page(identifier: str, payload: DiscoveryPage):
    try:
        return await discovery.next_page(
            Studio(), identifier, payload.provider, payload.expected_revision
        )
    except Missing as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class DiscoveryCollect(Command):
    provider: Literal["openalex", "crossref", "arxiv", "rss"]
    item_id: str = Field(min_length=1, max_length=2000)


@router.post("/discover/{identifier}/collect")
async def collect_discovery(identifier: str, payload: DiscoveryCollect):
    return await operation(
        discovery.collect, Studio(), identifier, payload.provider, payload.item_id
    )


@router.post("/subscriptions")
async def create_subscription(payload: subscriptions.SubscriptionInput):
    return await operation(subscriptions.save, Studio(), payload)


class SubscriptionUpdate(subscriptions.SubscriptionInput):
    expected_revision: int = Field(ge=1)


@router.patch("/subscriptions/{identifier}")
async def update_subscription(identifier: str, payload: SubscriptionUpdate):
    return await operation(
        subscriptions.save,
        Studio(),
        subscriptions.SubscriptionInput(**payload.model_dump(exclude={"expected_revision"})),
        identifier,
        payload.expected_revision,
    )


@router.post("/subscriptions/{identifier}/refresh")
async def refresh_subscription(identifier: str):
    try:
        return await subscriptions.refresh(Studio(), identifier)
    except Missing as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/snapshots/{identifier}/run-draft", dependencies=[Depends(require_admin)])
async def remote_run_draft(identifier: str):
    return await operation(connections.snapshot_run_draft, Studio(), identifier)




class PreferencesUpdate(assist.WritingPreferences):
    expected_revision: int = Field(ge=0)


@router.get("/writing/preferences")
async def writing_preferences():
    return await operation(assist.preferences, Studio())


@router.put("/writing/preferences")
async def update_writing_preferences(payload: PreferencesUpdate):
    return await operation(
        assist.save_preferences,
        Studio(),
        assist.WritingPreferences(**payload.model_dump(exclude={"expected_revision"})),
        payload.expected_revision,
    )


class WritingAssistInput(assist.AssistInput):
    llm_selection: LLMSelectionPayload | None = None


@router.post("/manuscripts/{identifier}/assist")
async def writing_assist(identifier: str, payload: WritingAssistInput):
    from deeptutor.services.llm import factory
    from deeptutor.services.llm.exceptions import LLMError
    from deeptutor.services.model_selection.runtime import (
        activate_llm_selection,
        reset_llm_selection,
    )

    config, token = activate_llm_selection(_validated_llm_selection(payload.llm_selection))
    try:
        return await assist.propose(
            Studio(),
            identifier,
            assist.AssistInput(**payload.model_dump(exclude={"llm_selection"})),
            config,
            factory.complete,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Missing as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Writing model timed out. Your source was not changed.") from exc
    except LLMError as exc:
        raise HTTPException(
            502, "Writing model is unavailable. Check model settings; your source was not changed."
        ) from exc
    finally:
        reset_llm_selection(token)
