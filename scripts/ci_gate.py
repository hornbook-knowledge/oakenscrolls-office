"""The aggregate `test` gate for .github/workflows/tests.yml.

Branch protection points at one check, `test`. It must go red when ANY leg
did not succeed. GitHub's plain `needs:` semantics would *skip* the aggregate
when a leg fails, and a skipped check reads as "nothing to see" rather than
"failed"; the workflow therefore runs this job with `if: always()` and this
script turns every non-success result — failure, cancelled, skipped, or a leg
that vanished from `needs` — into an explicit failure.

The workflow runs it with NEEDS=${{ toJSON(needs) }}. tests/test_release_wiring.py
imports it, plants a skipped leg, and expects a refusal.
"""

from __future__ import annotations

import json
import os
import sys

# Every job the `test` job depends on. tests/test_release_wiring.py checks this
# tuple against the workflow's `needs:` list so neither can drift alone.
EXPECTED_LEGS = ("linux", "windows", "lint")


def failures(needs: dict, expected: tuple[str, ...] = EXPECTED_LEGS) -> list[str]:
    """Everything wrong with a `needs` context; empty means every leg succeeded."""
    problems = []
    for leg in expected:
        if leg not in needs:
            problems.append(f"{leg}: missing from needs — was the job removed from the workflow?")
            continue
        result = needs[leg].get("result")
        if result != "success":
            problems.append(f"{leg}: result is {result!r}, not 'success'")
    for leg in needs:
        if leg not in expected:
            problems.append(f"{leg}: in needs but not in EXPECTED_LEGS — add it there")
    return problems


def main() -> int:
    raw = os.environ.get("NEEDS")
    if not raw:
        print("::error::NEEDS is unset; run with NEEDS=${{ toJSON(needs) }}")
        return 2
    problems = failures(json.loads(raw))
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print("every leg succeeded: " + ", ".join(EXPECTED_LEGS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
