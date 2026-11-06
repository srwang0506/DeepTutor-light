"""Owner-scoped project metadata. Recall is direct and read-only, never transitive."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deeptutor.services.workspace.models import WorkspaceError


class MemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sources: list[str] = Field(default_factory=list, max_length=24)
    discoverable: bool = True


def project_rows(service) -> dict[str, dict]:
    return {
        "" if row["kind"] == "general" else row["workspace_id"]: row
        for row in service.list_workspaces()
        if row["kind"] in {"general", "workspace"}
    }


def validate_project_fields(
    service, *, description=None, color=None, memory_policy=None, workspace_id=""
) -> dict:
    result = {}
    if description is not None:
        if not isinstance(description, str) or len(description) > 600:
            raise WorkspaceError("Project description must be at most 600 characters.")
        result["description"] = description.strip()
    if color is not None:
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise WorkspaceError("Invalid project color.")
        result["color"] = color.lower()
    if memory_policy is not None:
        try:
            policy = MemoryPolicy.model_validate(memory_policy)
        except ValidationError as exc:
            raise WorkspaceError("Invalid project memory selection.") from exc
        rows = project_rows(service)
        general = service._builtin_id("general")
        current = "" if workspace_id == general else workspace_id
        sources = list(dict.fromkeys("" if s == general else s for s in policy.sources))
        previous = rows.get(current, {}).get("memory_policy", {}).get("sources", [])
        for source in sources:
            row = rows.get(source)
            if source in previous and source != current:
                continue  # Retain a paused selection; live retrieval still excludes it.
            if (
                source == current
                or not row
                or row["archived"]
                or row["status"] != "ready"
                or not row["memory_policy"].get("discoverable", True)
            ):
                raise WorkspaceError("A selected memory source is unavailable or is this project.")
        result["memory_policy"] = {"sources": sources, "discoverable": policy.discoverable}
    return result


def resolve_sources(service, workspace_id: str) -> tuple[dict, list[tuple[str, dict]], list[str]]:
    rows = project_rows(service)
    if workspace_id == service._builtin_id("general"):
        workspace_id = ""
    current = rows.get(workspace_id)
    if current is None or current["status"] != "ready":
        raise WorkspaceError("Project is unavailable.")
    sources = [(workspace_id, current)]
    unavailable = []
    for source in current["memory_policy"].get("sources", []):
        row = rows.get(source)
        if source == workspace_id:
            continue
        if (
            not row
            or row["archived"]
            or row["status"] != "ready"
            or not row["memory_policy"].get("discoverable", True)
        ):
            unavailable.append(source)
        else:
            sources.append((source, row))
    return current, sources, unavailable
