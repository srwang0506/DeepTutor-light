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

In the homepage repository set `workspaceOrigin` in `data/deeptutor.js` to
the verified HTTPS origin, build and publish `docs/`. Test sign-in with a
fresh browser session. The application opens on the workspace origin and its
native logout closes the session. Configure the hosted model account in
DeepTutor Settings. Codex OAuth supports pasting the callback address into
the corresponding pending sign-in on the hosted instance.
