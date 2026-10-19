# Image for the visual tests: Chromium and fonts from Playwright, Python 3.14 through uv.
# Screenshots differ between systems, so baselines are made and checked only in this image.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.14 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=UTC
RUN uv python install 3.14
# The tests use the Python above. Downloading another one would need a writable /opt/python.
ENV UV_PYTHON_DOWNLOADS=never
# pwuser of the Playwright image. The script runs the container as the caller's uid anyway.
USER 1001:1001
WORKDIR /repo
