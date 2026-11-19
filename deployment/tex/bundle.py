"""Assemble offline Tectonic inputs from the pinned format and installed TeX files."""

import errno
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tarfile


def distribution_files(roots):
    # Debian keeps some font packages in /usr/share/texmf, outside texmf-dist.
    for root in roots:
        for part in ("tex/latex/base", "tex/latex", "tex/generic", "fonts", "bibtex", "tex/xelatex", "tex/luatex"):
            yield from sorted((root / part).rglob("*"))


def assemble(archive, distribution, destination, *extra_roots):
    if (
        hashlib.sha256(archive.read_bytes()).hexdigest()
        != "6109880d8a31bbf1f692060ee388d70aac2e64b19031b50cbfce6c2949a4ae76"
    ):
        raise ValueError("Tectonic format archive checksum mismatch.")
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as package:
        for item in package:
            if not item.isfile() or Path(item.name).name != item.name:
                raise ValueError("Unexpected entry in Tectonic format archive.")
            (destination / item.name).write_bytes(package.extractfile(item).read())
    # The guest resolves bundle entries by filename. Use ordinary, read-only
    # distribution files and retain the format's matching base class and fonts.
    for item in distribution_files((distribution, *extra_roots)):
        target = destination / item.name
        if item.is_symlink() or not item.is_file() or target.exists():
            continue
        try:
            os.link(item, target)
        except OSError as exc:
            if exc.errno not in {errno.EXDEV, errno.EPERM}:
                raise
            shutil.copyfile(item, target)


if __name__ == "__main__":
    assemble(*(Path(arg) for arg in sys.argv[1:]))
