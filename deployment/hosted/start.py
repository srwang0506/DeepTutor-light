"""Run the hosted workbench behind its platform's HTTPS ingress."""

import os
from pathlib import Path
import re
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PAGES_URL = "https://srwang0506.github.io/tools/deeptutor/"


def public_host(origin: str) -> str:
    url = urlsplit(origin)
    host = url.hostname or ""
    if (
        origin != "https://" + host
        or "." not in host
        or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", s) for s in host.split(".")
        )
        or len(host) > 253
    ):
        raise ValueError(
            "DEEPTUTOR_PUBLIC_ORIGIN must be the canonical HTTPS origin of the service."
        )
    return host


def main():
    # Some container hosts mount a new volume as root:root. Only the mount
    # itself needs initialization; every application process runs as tutor.
    if os.geteuid() == 0:
        import pwd

        tutor = pwd.getpwnam("tutor")
        volume = os.open("/runtime", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchown(volume, tutor.pw_uid, tutor.pw_gid)
            os.fchmod(volume, 0o700)
        finally:
            os.close(volume)
        os.setgroups([])
        os.setgid(tutor.pw_gid)
        os.setuid(tutor.pw_uid)
        os.environ.update(HOME=tutor.pw_dir, USER=tutor.pw_name, LOGNAME=tutor.pw_name)

    host = public_host(os.environ.get("DEEPTUTOR_PUBLIC_ORIGIN", ""))
    port = int(os.environ.get("PORT", "8080"))
    if port in {3782, 8001} or not 1024 <= port <= 65535:
        raise ValueError("Use an unprivileged PORT other than the two internal service ports.")

    secret_file = os.environ.get("DEEPTUTOR_PASSWORD_HASH_FILE")
    password_hash = (
        Path(secret_file).read_text().strip()
        if secret_file
        else os.environ.get("AUTH_PASSWORD_HASH", "")
    )
    if not re.fullmatch(r"\$2[ab]\$(?:1[0-6])\$[./A-Za-z0-9]{53}", password_hash):
        raise ValueError("A bcrypt workspace password hash is required before opening the service.")

    os.umask(0o077)
    os.environ.update(
        AUTH_ENABLED="true",
        DEEPTUTOR_AUTH_ENABLED="true",
        NEXT_PUBLIC_AUTH_ENABLED="true",
        AUTH_USERNAME=os.environ.get("AUTH_USERNAME") or "owner",
        AUTH_PASSWORD_HASH=password_hash,
        AUTH_COOKIE_SECURE="true",
        DEEPTUTOR_IGNORE_PROCESS_ENV_OVERRIDES="0",
        DEEPTUTOR_API_BASE_URL="http://127.0.0.1:8001",
        DEEPTUTOR_STUDY_RUNTIME="1",
        DEEPTUTOR_LOCAL_ORIGINS="https://" + host,
        DEEPTUTOR_NEXT_DIST_DIR=".next-study",
        TZ=os.environ.get("TZ", "UTC"),
    )
    from deeptutor.services.setup import init_user_directories

    init_user_directories()
    # Substitute only the deployment values. Nginx request variables are
    # deliberately left untouched.
    config = (HERE / "nginx.conf.in").read_text()
    config = (
        config.replace("@PORT@", str(port)).replace("@HOST@", host).replace("@PAGES@", PAGES_URL)
    )
    Path("/tmp/deeptutor-nginx.conf").write_text(config)
    os.execvp("supervisord", ["supervisord", "-n", "-c", str(HERE / "supervisord.conf")])


if __name__ == "__main__":
    main()
