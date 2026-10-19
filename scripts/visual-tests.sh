#!/usr/bin/env bash
# Run the visual tests in the Playwright image. Pass --update to accept new screenshots.
set -euo pipefail

image=repoboard-visual
root="$(cd "$(dirname "$0")/.." && pwd)"
update=0
if [[ "${1:-}" == "--update" ]]; then
    update=1
    shift
fi

# The Dockerfile copies nothing, so the build gets no context.
docker build --quiet --platform linux/amd64 -t "$image" - <"$root/tests/visual/Dockerfile" >/dev/null
# The uv cache lives in the repo (git ignores it), so a second run downloads nothing.
mkdir -p "$root/.cache/uv"
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
    -e UPDATE_SNAPSHOTS="$update" -e HOME=/tmp -e UV_CACHE_DIR=/repo/.cache/uv -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache -v "$root:/repo" "$image" \
    bash -c 'uv sync --quiet --frozen --extra dev --group visual && uv run --frozen pytest -m visual tests/visual "$@"' bash "$@"
