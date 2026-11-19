"""Defer unrelated API schemas in profiles that opt in to indexed routing.

The generated index contains only routing patterns. Authentication, dependency
injection and handlers always come from the original FastAPI router. A stale
index disables deferral; OpenAPI expands every router before schema generation.
"""

from __future__ import annotations

from copy import copy
import hashlib
from importlib import import_module
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from starlette.routing import BaseRoute, Match, Route, WebSocketRoute

from deeptutor.services.file_io import read_text

INDEX_PATH = Path(__file__).with_name("router_index.json")


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).with_name("routers")
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def _unreachable(*_args: Any) -> None:
    raise RuntimeError("Deferred routing pattern cannot execute a request")


class DeferredRouter(BaseRoute):
    def __init__(
        self, app: FastAPI, reference: str, options: dict[str, Any], patterns: list[dict]
    ) -> None:
        self.app = app
        self.reference = reference
        self.options = options
        self.loaded: APIRouter | None = None
        prefix = options.get("prefix", "")
        self.patterns: list[BaseRoute] = []
        for pattern in patterns:
            path = prefix + pattern["path"]
            if pattern["kind"] == "websocket":
                route = WebSocketRoute(path, _unreachable, name=pattern["name"])
            else:
                route = Route(path, _unreachable, methods=pattern["methods"], name=pattern["name"])
                # Starlette implicitly adds HEAD; FastAPI APIRoute does not.
                route.methods = set(pattern["methods"])
            self.patterns.append(route)

    def load(self) -> APIRouter:
        if self.loaded is None:
            module, attribute = self.reference.split(":", 1)
            router = APIRouter(redirect_slashes=False, dependency_overrides_provider=self.app)
            router.include_router(getattr(import_module(module), attribute), **self.options)
            # Publish only a fully constructed router. There is no await during
            # initialization, so concurrent requests cannot observe half a load.
            self.loaded = router
        return self.loaded

    def matches(self, scope: dict) -> tuple[Match, dict]:
        partial: tuple[Match, dict] = (Match.NONE, {})
        for route in self.patterns:
            match, child = route.matches(scope)
            if match == Match.FULL:
                return match, child
            if match == Match.PARTIAL and partial[0] == Match.NONE:
                partial = (match, child)
        return partial

    async def handle(self, scope: dict, receive: Any, send: Any) -> None:
        await self.load()(scope, receive, send)

    def url_path_for(self, name: str, /, **path_params: Any) -> Any:
        from starlette.routing import NoMatchFound

        for route in self.patterns:
            try:
                return route.url_path_for(name, **path_params)
            except NoMatchFound:
                pass
        raise NoMatchFound(name, path_params)


class RouterLoader:
    def __init__(
        self, app: FastAPI, *, lazy: bool = False, warm_paths: tuple[str, ...] = ()
    ) -> None:
        self.app = app
        self.warm_paths = frozenset(warm_paths)
        self.records: dict[str, APIRouter] = {}
        self.index: dict[str, list[dict]] = {}
        if lazy:
            try:
                index = json.loads(read_text(INDEX_PATH))
                if index["fingerprint"] == source_fingerprint():
                    self.index = index["routers"]
            except (OSError, ValueError, KeyError):
                pass
        original_openapi = app.openapi

        def openapi() -> dict:
            self.expand()
            return original_openapi()

        app.openapi = openapi

    def include(
        self, reference: str, *, exclude_paths: tuple[str, ...] = (), **options: Any
    ) -> None:
        prefix = options.get("prefix", "")

        def excluded(path: str) -> bool:
            full = prefix + path
            return any(full == root or full.startswith(root + "/") for root in exclude_paths)

        patterns = self.index.get(reference)
        if patterns is not None and not (
            exclude_paths and any(excluded(p["path"]) for p in patterns)
        ):
            deferred = DeferredRouter(self.app, reference, options, patterns)
            if self.warm_paths and any(
                route.path in self.warm_paths for route in deferred.patterns
            ):
                deferred.load()
            self.app.router.routes.append(deferred)
        else:
            module, attribute = reference.split(":", 1)
            router = getattr(import_module(module), attribute)
            if exclude_paths:
                router = copy(router)
                router.routes = [
                    route for route in router.routes if not excluded(getattr(route, "path", ""))
                ]
            self.records[reference] = router
            self.app.include_router(router, **options)

    def expand(self) -> None:
        routes: list[BaseRoute] = []
        for route in self.app.router.routes:
            routes.extend(route.load().routes if isinstance(route, DeferredRouter) else [route])
        self.app.router.routes[:] = routes
