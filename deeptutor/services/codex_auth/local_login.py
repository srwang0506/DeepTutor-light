"""Opt-in bridge to an existing local Codex login.

Codex owns refresh and logout. DeepTutor reads the access credential on demand;
it never stores, refreshes, revokes or copies Codex's refresh token. This bridge
is available only to the deployment administrator, never other app accounts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil

from .contracts import CodexAuthError, CodexCredentials, decode_codex_jwt
from .storage import CodexCredentialStore, _atomic_write_json


def local_login_path() -> Path | None:
    if os.environ.get("DEEPTUTOR_USE_CODEX_LOGIN") != "1":
        return None
    from deeptutor.multi_user.context import get_current_user

    user = get_current_user()
    if not user.is_admin or user.scope.kind != "admin":
        return None
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"


class LocalCodexCredentialStore(CodexCredentialStore):
    external_login = True

    def __init__(self, user_root: Path, auth_path: Path):
        super().__init__(user_root)
        self.auth_path = auth_path

    def load_credentials(self) -> CodexCredentials | None:
        try:
            # Codex may atomically rotate this file; read it afresh each time.
            if self.auth_path.stat().st_size > 1024 * 1024:
                raise ValueError("oversized credential file")
            payload = json.loads(self.auth_path.read_text())
            tokens = payload.get("tokens") or {}
            access = tokens.get("access_token")
            account = tokens.get("account_id")
            if (
                not isinstance(access, str)
                or not access
                or not isinstance(account, str)
                or not account
            ):
                return None
            claims = decode_codex_jwt(access)
            if not claims.expires_at or (claims.account_id and claims.account_id != account):
                raise ValueError("invalid account")
            return CodexCredentials(
                schema_version=1,
                access_token=access,
                refresh_token="managed-by-local-codex",
                id_token="managed-by-local-codex",
                account_id=account,
                expires_at=claims.expires_at,
                generation=int(hashlib.sha256(access.encode()).hexdigest()[:15], 16),
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise CodexAuthError(
                "local_login_unavailable",
                "The local Codex login could not be read. Sign in through Codex.",
                401,
            ) from exc

    def current_generation(self):
        credentials = self.load_credentials()
        return credentials.generation if credentials else 0

    def authentication_rejected(self, generation):
        return False  # Codex, not this application, owns the refresh lifecycle.

    def commit_credentials(self, *args, **kwargs):
        raise managed_login_error()

    def clear_credentials(self, *args, **kwargs):
        raise managed_login_error()

    def commit_catalog_cache(self, snapshot):
        with self._locked():
            credentials = self.load_credentials()
            if (
                not credentials
                or credentials.generation != snapshot.generation
                or hashlib.sha256(credentials.account_id.encode()).hexdigest()
                != snapshot.account_hash
            ):
                raise self._generation_changed()
            _atomic_write_json(self.catalog_cache_path, snapshot.to_dict())

    async def refresh_external(self):
        executable = shutil.which("codex")
        if not executable:
            raise CodexAuthError("codex_missing", "Install Codex to renew the local login.", 503)
        # Stdio RPC runs no thread or model task. Only the account refresh API
        # is called. Never forward server stderr or credentials into app logs.
        process = await asyncio.create_subprocess_exec(
            executable,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=4 * 1024 * 1024,
        )

        async def call(request):
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise CodexAuthError(
                        "local_refresh_failed",
                        "Codex could not renew the login. Open Codex and sign in again.",
                        401,
                    )
                response = json.loads(line)
                if response.get("id") == request["id"]:
                    if "error" in response:
                        raise CodexAuthError(
                            "local_refresh_failed",
                            "Codex could not renew the login. Open Codex and sign in again.",
                            401,
                        )
                    return response

        try:
            async with asyncio.timeout(30):
                await call(
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {"clientInfo": {"name": "deeptutor-light", "version": "1.0.0"}},
                    }
                )
                process.stdin.write(b'{"method":"initialized"}\n')
                await process.stdin.drain()
                await call({"id": 2, "method": "account/read", "params": {"refreshToken": True}})
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        credentials = self.load_credentials()
        if not credentials:
            raise CodexAuthError("authentication_required", "Sign in through Codex first.", 401)
        return credentials


def managed_login_error():
    return CodexAuthError(
        "local_login_managed",
        "This login is managed by your local Codex app. Sign in or out there, or disable DEEPTUTOR_USE_CODEX_LOGIN to use a separate login.",
        409,
    )
