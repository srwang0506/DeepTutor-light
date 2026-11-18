"""Set the hosted workspace password in an owner's interactive terminal."""

import argparse
from getpass import GetPassWarning, getpass
import os
from pathlib import Path
import sys
import warnings

import bcrypt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hash_file", type=Path, help="New private file for the bcrypt hash")
    target = parser.parse_args().hash_file
    if not sys.stdin.isatty():
        parser.exit(1, "Open an interactive terminal to set the workspace password.\n")
    if target.exists():
        parser.exit(1, "Choose a new filename for the password hash.\n")

    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            password = getpass("Workspace password: ").encode()
            if not 1 <= len(password) <= 72:
                parser.exit(1, "Use a password between 1 and 72 UTF-8 bytes.\n")
            if getpass("Confirm workspace password: ").encode() != password:
                parser.exit(1, "The passwords did not match.\n")
        except (GetPassWarning, EOFError):
            parser.exit(1, "A terminal with hidden password input is required.\n")

    password_hash = bcrypt.hashpw(password, bcrypt.gensalt())
    with open(target, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
        handle.write(password_hash + b"\n")
    print("Workspace password hash saved.")


if __name__ == "__main__":
    main()
