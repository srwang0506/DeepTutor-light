# GitHub Pages sign-in

The sign-in form lives at `https://srwang0506.github.io/tools/deeptutor/`.
Its native HTML form posts directly to the hosted workspace over HTTPS. The
workspace checks the password and issues its own secure, HttpOnly session
cookie. Pages receives neither the password nor the cookie. The application,
API, downloads and WebSocket upgrades require a valid server session.

## Deploy on a Linux server

Point a DNS hostname to the server. Set `DEEPTUTOR_DOMAIN` to that hostname
and `DEEPTUTOR_PASSWORD_HASH_PATH` to a local file holding a bcrypt hash. The
workspace process runs as UID 1000 and must be able to read the mounted secret.
Use the project's Python environment to set your password. Enter it twice
at the hidden terminal prompt. The command saves only a bcrypt hash in a new
owner-readable file.

```sh
python -m deployment.hosted.password /path/to/workspace-password.hash
docker compose -f compose.hosted.yml up --build -d
```

Caddy provisions HTTPS, and the named `workspace_data` volume holds the
application databases and uploaded files across container restarts. Back it
up with the same recovery procedure as any DeepTutor runtime. A managed
container service can instead build the `hosted` target in `Dockerfile.study`,
mount a persistent POSIX disk at `/runtime` writable by UID 1000, terminate
HTTPS at its public ingress, set `DEEPTUTOR_PUBLIC_ORIGIN` to its canonical
HTTPS origin and provide `AUTH_PASSWORD_HASH` as a secret.

For platforms that supply a root-owned volume, selecting UID 0 for container
startup lets the entry point set ownership on `/runtime`. It immediately
switches to UID 1000 before reading the password hash or starting services.

On a small x86-64 server, use the Linux image published by the **Hosted
workspace** workflow after its recovery test passes. Its run summary gives a
content-addressed GHCR reference. Set `DEEPTUTOR_HOSTED_IMAGE` to that
reference and use the same Compose file and password hash:

```sh
docker compose -f compose.hosted.yml pull workspace
docker compose -f compose.hosted.yml up -d --no-build
```

Pulling a private package requires registry read access. The image contains
the application; each deployment initializes its own data volume, password
and model account.

### Recovery after losing the cloud disk

The active workspace uses a POSIX volume. For an additional encrypted copy,
initialize a private restic repository, then set `DEEPTUTOR_BACKUP_REPOSITORY`
and the secret `DEEPTUTOR_BACKUP_PASSWORD`. For S3 set
`DEEPTUTOR_BACKUP_ACCESS_KEY_ID`, `DEEPTUTOR_BACKUP_SECRET_ACCESS_KEY`
and `DEEPTUTOR_BACKUP_REGION`. This configuration uses virtual-hosted S3
addressing. Give each workspace instance its own repository prefix and keep a
copy of the recovery password outside the cloud account.

On graceful termination the supervisor stops all writers before taking a
verified SQLite and file snapshot. The next start on a fresh disk restores
the latest completed snapshot before opening the application. A forced stop
can leave the backup at its previous checkpoint. Allow enough termination
grace for your data size. The active disk remains the source for newer writes.

In the homepage repository set `workspaceOrigin` in `data/deeptutor.js` to
the verified HTTPS origin, build and publish `docs/`. Test sign-in with a
fresh browser session. The application opens on the workspace origin and its
native logout closes the session. Configure the hosted model account in
DeepTutor Settings. Codex OAuth supports pasting the callback address into
the corresponding pending sign-in on the hosted instance.

### Render Free with Neon

Use a single Render Web Service with the verified hosted image, the Free
instance type and health check path `/health/ready`. Render supplies its
canonical hostname. Provide the workspace bcrypt hash as above.

Set `DEEPTUTOR_NEON_CONFIG_FILE` to the path of a Render secret file containing
a JSON object. Its fields are `database_url`, `namespace`, `bucket`,
`s3_endpoint`, `s3_region`, `s3_access_key`, `s3_secret_key` and
`encryption_key`. Use a private Neon bucket, a database role with its own
schema, and a base64-encoded random 32-byte encryption key. Keep a private
copy of the key. Give test and production workspaces distinct namespaces and
keys. The runtime can also read the JSON from the secret `DEEPTUTOR_NEON_CONFIG`.

The published Render image compiles papers with the offline Tectonic engine.
The default self-hosted Docker build also includes native TeX Live.

PostgreSQL holds the encrypted workspace manifest and database blocks. The
private bucket holds encrypted file blocks. A successful edit and a completed
chat turn wait for a committed revision. In-progress background work also
checkpoints. After an instance disappears, its replacement verifies and
hydrates the current revision before accepting traffic. Each replacement
fences the previous writer. A storage outage returns an error for an
unconfirmed edit.

For migration, stop writes on the source and take a verified native backup.
Import it into an empty namespace. An independent restore can run while the
new workspace is online.

```sh
python -m deployment.hosted.transfer import /private/cloud.json /private/native-backup
python -m deployment.hosted.transfer restore /private/cloud.json /private/new-restore
```
