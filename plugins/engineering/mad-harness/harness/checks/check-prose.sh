#!/usr/bin/env bash
# Catch prose an edit broke: a sentence whose subject was deleted, a rule stated twice,
# a bullet ending on a comma. Three reviews found this class; no other check sees it.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m models.check_prose "$@"
