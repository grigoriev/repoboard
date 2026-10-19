# Contributing

Issues and pull requests are welcome.

## Build and test

```sh
uv sync --extra dev --group visual            # install the dependencies
uv run ruff check .                            # lint
uv run ruff format --check .                   # format check
uv run mypy repoboard tests                    # type check
uv run pytest --cov --cov-report=term-missing  # tests with coverage
scripts/visual-tests.sh                        # screenshot tests in the Playwright image (Docker)
scripts/visual-tests.sh --update               # accept new screenshots after a UI change
```

The visual tests compare every view with `tests/visual/snapshots`. They run only in the pinned
Playwright image, because fonts render differently on each system. A failure leaves the actual and
diff images in `tests/visual/output`. Review the new screenshots before you commit them.

CI runs lint, type check, tests and SonarCloud for every pull request.

## Pull requests

1. Branch from the default branch as `type/description`, for example `fix/empty-title`.
2. Keep one change per pull request. New behavior comes with tests; a bug fix adds a test that
   fails without it.
3. Write commit messages as [Conventional Commits](https://www.conventionalcommits.org/) without a
   scope: `feat: ...`, `fix: ...`, `docs: ...`, `refactor: ...`, `test: ...`, `build: ...`,
   `ci: ...`, `chore: ...`.
4. Sign your commits. The default branch accepts verified signatures only.
5. Add an entry under `## [Unreleased]` in `CHANGELOG.md`, written for users: the release notes
   quote it. Update the README when behavior or configuration changes.

Pull requests are squash-merged once all required checks are green.

## Releases

A maintainer runs the Bump Version workflow. It moves the Unreleased entries into a versioned
section, tags the release, and the Release workflow publishes it with signed build provenance.
