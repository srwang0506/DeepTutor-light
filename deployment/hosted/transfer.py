"""Import a verified offline workspace or restore a cloud revision without taking ownership."""

import argparse
import json
import os
from pathlib import Path

from deeptutor.runtime.backup import verify
from deployment.hosted.neon_store import CloudStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["import", "restore"])
    parser.add_argument("config", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    store = CloudStore(json.loads(args.config.read_text()))
    directory = args.directory.resolve()
    if args.operation == "import":
        original = verify(directory)
        store.claim(directory, seed=True)
        store.save(directory, acknowledge=True, source_home=original["source_home"])
        print(f"Imported {len(original['files'])} files into revision {store.revision}.")
        return
    if directory.exists():
        raise ValueError("Restore requires a new destination")
    with store.connection() as db:
        row = db.execute(
            "SELECT revision,manifest FROM dt_cloud_workspaces WHERE name=%s FOR SHARE",
            (store.name,),
        ).fetchone()
        if not row or not row[1]:
            raise ValueError("No committed workspace")
        manifest = store.decode_manifest(*row)
        store.restore(db, directory, manifest)
        print(f"Verified and restored {len(manifest['files'])} files from revision {row[0]}.")


if __name__ == "__main__":
    main()
