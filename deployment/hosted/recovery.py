"""Restore a missing cloud disk and checkpoint a stopped workspace to restic."""

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from deeptutor.runtime.backup import backup, relocate_file_references, restore


def take_backup_settings():
    repository = os.environ.pop("DEEPTUTOR_BACKUP_REPOSITORY", None)
    if not repository:
        return None
    settings = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "RESTIC_REPOSITORY": repository,
        "RESTIC_PASSWORD": os.environ.pop("DEEPTUTOR_BACKUP_PASSWORD"),
        "GOMAXPROCS": "1",
        "GOGC": "40",
    }
    if repository.startswith("s3:"):
        for name in ["ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "REGION"]:
            settings["AWS_" + name] = os.environ.pop("DEEPTUTOR_BACKUP_" + name)
    return settings


def restic(settings, *args, cwd=None):
    command = ["restic", "--no-cache"]
    if settings["RESTIC_REPOSITORY"].startswith("s3:"):
        command += ["-o", "s3.bucket-lookup=dns", "-o", "s3.connections=2"]
    return subprocess.check_output([*command, *args], cwd=cwd, env=settings, text=True)


def restore_if_missing(home, settings):
    if (home / "data").is_dir():
        return
    snapshots = json.loads(restic(settings, "snapshots", "--json", "--host", "deeptutor"))
    if not snapshots:
        return
    latest = max(snapshots, key=lambda row: row["time"])
    with tempfile.TemporaryDirectory(prefix="deeptutor-cloud-restore-") as folder:
        restic(settings, "restore", latest["id"], "--target", folder)
        if not home.exists():
            restore(folder, home)
        else:
            # The runtime itself may be a mounted filesystem. Populate its
            # missing data tree atomically without replacing the mount.
            with tempfile.TemporaryDirectory(dir=home) as stage:
                recovered = Path(stage) / "runtime"
                restore(folder, recovered)
                relocate_file_references(recovered, str(recovered), home)
                (recovered / "data").rename(home / "data")
    print("Recovered the verified workspace from encrypted storage.", flush=True)


def checkpoint(home, settings):
    with tempfile.TemporaryDirectory(prefix="deeptutor-cloud-save-") as folder:
        snapshot = Path(folder) / "snapshot"
        backup(home, snapshot)
        restic(settings, "backup", "--host", "deeptutor", ".", cwd=snapshot)
    print("Saved the encrypted workspace checkpoint.", flush=True)


def supervise(command, home, settings):
    process = None
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        if process is not None and process.poll() is None:
            process.send_signal(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    process = subprocess.Popen(command)
    if stopping:
        process.terminate()
    code = process.wait()
    if stopping and code == 0:
        checkpoint(home, settings)
    raise SystemExit(code)
