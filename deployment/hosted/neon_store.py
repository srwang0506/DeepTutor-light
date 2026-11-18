"""Encrypted, fenced workspace revisions in PostgreSQL and a private S3 bucket."""

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4
import zlib

import certifi
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import psycopg
from psycopg.conninfo import conninfo_to_dict

from deployment.hosted.cloud_tree import capture, inspect_tree, pieces, restore_tree

CREATE_WORKSPACES = """
    CREATE TABLE IF NOT EXISTS dt_cloud_workspaces (
        name text PRIMARY KEY, writer uuid NOT NULL, revision bigint NOT NULL,
        manifest bytea
    )
"""
CREATE_BLOCKS = """
    CREATE TABLE IF NOT EXISTS dt_cloud_blocks (
        workspace text NOT NULL REFERENCES dt_cloud_workspaces(name),
        digest text NOT NULL, payload bytea NOT NULL, PRIMARY KEY (workspace, digest)
    )
"""


class FencedWriter(RuntimeError):
    """A newer instance is serving this workspace."""


class CloudStore:
    def __init__(self, settings, objects=None):
        self.url = settings["database_url"]
        self.bucket = settings["bucket"]
        self.name = settings["namespace"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", self.name):
            raise ValueError("Invalid cloud workspace namespace")
        key = base64.b64decode(settings["encryption_key"], validate=True)
        if len(key) != 32:
            raise ValueError("The workspace encryption key must contain 32 bytes")
        self.cipher = AESGCM(key)
        if objects is None:
            import boto3
            from botocore.config import Config

            self.objects = boto3.client(
                "s3",
                endpoint_url=settings["s3_endpoint"],
                aws_access_key_id=settings["s3_access_key"],
                aws_secret_access_key=settings["s3_secret_key"],
                region_name=settings["s3_region"],
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=5,
                    read_timeout=25,
                    retries={"total_max_attempts": 3},
                ),
            )
        else:
            self.objects = objects
        self.writer = uuid4()
        self.revision = 0
        self.manifest = {}
        self.signatures = {}
        self.fenced = False
        self.last_owner_check = 0.0

    def connection(self):
        options = conninfo_to_dict(self.url)
        if options.get("host", "").endswith(".neon.tech"):
            options.update(sslmode="verify-full", sslrootcert=certifi.where())
        options.update(connect_timeout=10, application_name="deeptutor-hosted")
        return psycopg.connect(**options)

    def seal(self, raw, purpose):
        nonce = os.urandom(12)
        aad = (self.name + "/" + purpose).encode()
        return nonce + self.cipher.encrypt(nonce, zlib.compress(raw, 3), aad)

    def open(self, sealed, purpose):
        aad = (self.name + "/" + purpose).encode()
        return zlib.decompress(self.cipher.decrypt(sealed[:12], sealed[12:], aad))

    def decode_manifest(self, revision, sealed):
        return json.loads(self.open(bytes(sealed), f"manifest/{revision}")) if sealed else {}

    def key(self, digest):
        return f"workspaces/{self.name}/{digest[:2]}/{digest}"

    def read_block(self, db, kind, digest):
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Invalid cloud block digest")
        if kind == "state":
            row = db.execute(
                "SELECT payload FROM dt_cloud_blocks WHERE workspace=%s AND digest=%s",
                (self.name, digest),
            ).fetchone()
            if row is None:
                raise ValueError("Cloud database block missing")
            sealed = bytes(row[0])
        elif kind == "file":
            stream = self.objects.get_object(Bucket=self.bucket, Key=self.key(digest))["Body"]
            try:
                sealed = stream.read()
            finally:
                stream.close()
        else:
            raise ValueError("Unknown cloud block type")
        block = self.open(sealed, kind + "/" + digest)
        if sha256(block).hexdigest() != digest:
            raise ValueError("Cloud block failed verification")
        return block

    def claim(self, home, *, seed=False):
        # The predecessor can finish its last write before we read the manifest.
        # Rollback keeps it authoritative if hydration, decryption or verification fails.
        with self.connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(775521018)")
            db.execute(CREATE_WORKSPACES)
            db.execute(CREATE_BLOCKS)
            db.execute(
                "INSERT INTO dt_cloud_workspaces VALUES (%s,%s,0,NULL) ON CONFLICT DO NOTHING",
                (self.name, self.writer),
            )
            revision, sealed = db.execute(
                "SELECT revision, manifest FROM dt_cloud_workspaces WHERE name=%s FOR UPDATE",
                (self.name,),
            ).fetchone()
            manifest = self.decode_manifest(revision, sealed)
            if seed and manifest:
                raise ValueError("Import requires an empty cloud workspace")
            if manifest:
                restore_tree(home, manifest, lambda kind, digest: self.read_block(db, kind, digest))
            elif not seed and (home / "data").exists():
                raise ValueError("Existing local workspace needs an explicit import")
            db.execute(
                "UPDATE dt_cloud_workspaces SET writer=%s WHERE name=%s", (self.writer, self.name)
            )
        self.manifest, self.revision = manifest, revision
        if manifest and manifest["home"] == str(home):
            self.signatures = inspect_tree(home / "data")[1]

    def locked_row(self, db):
        writer, revision, sealed = db.execute(
            "SELECT writer,revision,manifest FROM dt_cloud_workspaces WHERE name=%s FOR UPDATE",
            (self.name,),
        ).fetchone()
        if writer != self.writer:
            self.fenced = True
            raise FencedWriter("A replacement instance owns this workspace")
        return revision, sealed

    def save(self, home, *, acknowledge=False, observe=False, source_home=None):
        if self.fenced:
            raise FencedWriter("A replacement instance owns this workspace")
        with tempfile.TemporaryDirectory(prefix="deeptutor-commit-") as stage:
            pending = Path(stage)
            result = capture(home, self.manifest, self.signatures, pending)
            if source_home is not None:
                result.manifest["home"] = str(source_home)
            verify_reader = observe and time.monotonic() - self.last_owner_check >= 5
            if result.manifest == self.manifest and not acknowledge and not verify_reader:
                self.signatures = result.signatures
                return
            with self.connection() as db:
                revision, sealed = self.locked_row(db)
                self.last_owner_check = time.monotonic()
                # A connection may have failed after COMMIT but before its reply.
                # In that case reconcile against the committed remote revision.
                previous = (
                    self.manifest
                    if revision == self.revision
                    else self.decode_manifest(revision, sealed)
                )
                if previous != result.manifest:
                    if revision != self.revision:
                        result = capture(home, {}, {}, pending)
                        if source_home is not None:
                            result.manifest["home"] = str(source_home)
                    revision = self.publish(db, previous, result.manifest, revision, pending)
            self.manifest, self.signatures, self.revision = (
                result.manifest,
                result.signatures,
                revision,
            )

    def publish(self, db, previous, manifest, revision, pending):
        for kind in ("state", "file"):
            for digest in pieces(manifest, kind) - pieces(previous, kind):
                raw = (pending / (kind + "-" + digest)).read_bytes()
                sealed = self.seal(raw, kind + "/" + digest)
                if kind == "state":
                    db.execute(
                        "INSERT INTO dt_cloud_blocks VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (self.name, digest, sealed),
                    )
                else:
                    self.objects.put_object(Bucket=self.bucket, Key=self.key(digest), Body=sealed)
        revision += 1
        encrypted = self.seal(
            json.dumps(manifest, separators=(",", ":")).encode(), f"manifest/{revision}"
        )
        db.execute(
            "UPDATE dt_cloud_workspaces SET revision=%s,manifest=%s WHERE name=%s",
            (revision, encrypted, self.name),
        )
        removed = pieces(previous, "state") - pieces(manifest, "state")
        if removed:
            db.execute(
                "DELETE FROM dt_cloud_blocks WHERE workspace=%s AND digest=ANY(%s)",
                (self.name, list(removed)),
            )
        return revision

    def prune_files(self):
        # Run only under the same row lock as uploads. This also removes orphan
        # uploads from interrupted transactions; never unlink a published block.
        with self.connection() as db:
            revision, sealed = self.locked_row(db)
            retained = pieces(self.decode_manifest(revision, sealed), "file")
            prefix = f"workspaces/{self.name}/"
            pages = self.objects.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            )
            for page in pages:
                for item in page.get("Contents", []):
                    digest = item["Key"].rsplit("/", 1)[-1]
                    if re.fullmatch("[a-f0-9]{64}", digest) and digest not in retained:
                        self.objects.delete_object(Bucket=self.bucket, Key=item["Key"])
