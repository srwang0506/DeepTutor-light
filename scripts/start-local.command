#!/bin/zsh
# Double-click on macOS; keep this terminal open while using the workbench.
set -eu
project_root="${0:A:h:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$project_root"
exec .venv/bin/python scripts/study.py run --codex-login --local-embedding --home "$project_root/study-data"
