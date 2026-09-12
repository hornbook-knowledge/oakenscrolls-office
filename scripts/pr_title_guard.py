"""PR-title guard for .github/workflows/pr-title.yml.

The title is the one line reviewers, the merge commit, and the changelog see
first, so it must be a conventional-commit line: the type says what the change
does to a release (see release-please-config.json for which types cut one).

Run by the workflow with PR_TITLE and PR_BODY in the environment; imported by
tests/test_release_wiring.py, which plants bad titles.
"""

from __future__ import annotations

import os
import re
import sys

# True once this repo publishes an installable artifact (PyPI, a wheel attached
# to a release). It does not today: Oakenscroll's Office is installed from
# source (dev.sh, dev.ps1, the safe-app-store manifest) and release-please cuts
# tags and GitHub release notes, nothing is uploaded. While unpackaged, a
# breaking-change title (`type!:`) needs no further explanation; once packaged,
# the PR body must carry a `BREAKING CHANGE:` line so consumers of the
# artifact are told what broke.
PACKAGED = False

TYPES = ("build", "chore", "ci", "deps", "docs", "feat", "fix", "perf", "refactor", "security", "test")
TITLE_RE = re.compile(r"^(" + "|".join(TYPES) + r")(\([a-z0-9._-]+\))?!?: .+")
BREAKING_FOOTER = "BREAKING CHANGE:"


def is_breaking(title: str) -> bool:
    head = title.split(": ", 1)[0]
    return head.endswith("!")


def problems(title: str, body: str = "", packaged: bool = PACKAGED) -> list[str]:
    """Everything wrong with a PR title (and, when packaged, its body)."""
    out = []
    if not TITLE_RE.match(title):
        out.append(
            f"title {title!r} is not a conventional-commit line: "
            f"expected `type(scope)!: summary` with type one of {', '.join(TYPES)}"
        )
    if packaged and is_breaking(title) and BREAKING_FOOTER not in (body or ""):
        out.append(
            f"title {title!r} marks a breaking change but the PR body has no "
            f"`{BREAKING_FOOTER}` line; consumers of the published artifact must be told"
        )
    return out


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    body = os.environ.get("PR_BODY", "")
    found = problems(title, body)
    for problem in found:
        print(f"::error::{problem}")
    if found:
        return 1
    print(f"ok: {title!r} (packaged={PACKAGED})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
