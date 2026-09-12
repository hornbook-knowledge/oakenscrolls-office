# Contributing

## The one test command

    python3 -m pytest tests/ -q

Two legs. On a base install (`pip install -e ".[dev]"`) the Nestor-backed
fuzzy-citation tests in `tests/test_almanac_seam_nestor.py` skip, because
Nestor is an unpublished git dependency behind the `citations` extra. With
`pip install -e ".[dev,citations]"` they run too. Everything else runs in
both legs: the math, the ledger rules, the web routes (no socket), the bridge
seams, the no-egress AST scan, and the release-wiring checks below.

CI runs that same command on Linux for every Python the classifiers in
`pyproject.toml` name (3.10 through 3.13) and on Windows for the floor and
the ceiling of that list. Read source files with an explicit
`encoding="utf-8"`: Windows Pythons before 3.15 default to the locale codec,
and the tree carries non-ASCII characters on purpose.

## Lint

    python3 -m pip install ruff==0.16.7
    ruff check .

ruff is pinned to that exact version in both the `dev` extra and the lint job;
`tests/test_release_wiring.py` fails if the two drift. The rule set is ruff's
default at that version. When a rule fires, fix the tree; do not loosen the
config.

## The `test` check

Branch protection points at one check, `test`. It depends on every other job
in `.github/workflows/tests.yml`, runs even when one failed, and
`scripts/ci_gate.py` turns any non-success result — failure, cancelled, or
skipped — into a red check. A skipped leg is never green.

## Pull-request titles

`.github/workflows/pr-title.yml` requires a conventional-commit title:

    type(scope)!: summary

with `type` one of `build`, `chore`, `ci`, `deps`, `docs`, `feat`, `fix`,
`perf`, `refactor`, `security`, `test`; the scope and the `!` are optional.
Commit messages follow the same shape: this repo merges with merge commits,
so release-please reads the commits, not the title.

## Releases

release-please (`release-please-config.json`) watches `main`. `chore`, `ci`,
`docs` and `test` are hidden from the changelog and do not cut a release.
`build`, `deps`, `feat`, `fix`, `perf`, `refactor` and `security` do: `feat`
bumps minor, a `!` or a `BREAKING CHANGE:` footer bumps major, the rest bump
patch. Merging the release PR tags `vX.Y.Z`, writes release notes, and
rewrites the version in `safe-app-manifest.json`. Nothing is uploaded
anywhere; the package version itself is derived from the tag by
setuptools-scm at build time.

Never push a tag by hand except the first one (see the pull request that
introduced this file for the exact command).
