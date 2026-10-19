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

docker build --quiet --platform linux/amd64 -t "$image" -f "$root/tests/visual/Dockerfile" "$root/tests/visual" >/dev/null
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
    -e UPDATE_SNAPSHOTS="$update" -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e PYTHONPYCACHEPREFIX=/tmp/pycache \
    -v "$root:/repo" "$image" \
    bash -c 'uv sync --quiet --frozen --extra dev --extra visual && uv run --frozen pytest -m visual tests/visual "$@"' bash "$@"
