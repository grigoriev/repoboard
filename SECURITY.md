# Security policy

## Reporting a vulnerability

Report a vulnerability privately through GitHub:
https://github.com/grigoriev/repoboard/security/advisories/new
(the **Security** tab, **Report a vulnerability**). Do not open a public issue for it.

We answer within a week. The fix goes into the next release, and its release notes name it.

## Supported versions

Only the latest release gets fixes.

## Scope

The Python code, the web UI templates and the workflows of this repository.

Vulnerabilities in upstream software (the GitHub CLI, FastAPI, uvicorn, Jinja2, HTMX, rumps,
pywebview and their dependencies) belong to the upstream project. Tell us as well if this project
is affected, so we can release a fix when the upstream fix is out.

## Local server

Repoboard serves its UI on `127.0.0.1` only. It never stores a GitHub token: every call goes
through the `gh` CLI and its own login.
