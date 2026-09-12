"""The release wiring is code, so it is tested like code.

Each check below is a pure function over parsed files, run twice: once on the
real tree (expect no problems) and once on a planted violation (expect the
specific problem). A check that has never seen a violation is not known to
work.

What is pinned to what:
  - the linux matrix in tests.yml  == the Python classifiers in pyproject.toml,
    and the windows matrix is that list's floor and ceiling;
  - ruff is pinned exactly, to the same version, in the lint job and the dev
    extra;
  - the aggregate `test` job needs every other job, runs with if: always(),
    and scripts/ci_gate.py refuses a skipped, cancelled, failed or missing leg;
  - pr-title.yml runs on the four PR events and scripts/pr_title_guard.py
    enforces the conventional-commit line;
  - release-please is present with the hidden / release-cutting split the
    config's $comment keys describe;
  - CodeQL runs one way or the other: an advanced workflow analysing python
    and actions, OR GitHub's default setup recorded as configured for python
    in .github/codeql-default-setup.json (the two cannot coexist — GitHub
    refuses advanced uploads while default setup is on — and the API that
    reports default setup needs a token CI does not have, so the tree carries
    the record).
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import ci_gate
import pr_title_guard

PR_EVENTS = ["opened", "edited", "synchronize", "reopened"]
HIDDEN_TYPES = {"chore", "ci", "docs", "test"}
RELEASE_CUTTING_TYPES = {"build", "deps", "feat", "fix", "perf", "refactor", "security"}
COMMENT_KEYS = ("$comment-hidden-rule", "$comment-what-cuts-a-release")
CODEQL_LANGUAGES = {"python", "actions"}
CODEQL_RECORD = ROOT / ".github" / "codeql-default-setup.json"
CLASSIFIER_RE = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")
RUFF_EXACT_RE = re.compile(r"ruff==(\d+\.\d+\.\d+)$")
RUFF_ANY_RE = re.compile(r"ruff\s*([=<>!~]+)\s*([\d.]+)")


# --- readers -----------------------------------------------------------------

def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _yaml(path: Path) -> dict:
    return yaml.safe_load(_text(path))


def triggers(workflow: dict) -> dict:
    """The `on:` block. PyYAML (YAML 1.1) reads a bare `on` key as True."""
    return workflow.get("on", workflow.get(True))


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


# --- checks (pure) -----------------------------------------------------------

def classifier_pythons(pyproject: dict) -> list[str]:
    found = []
    for c in pyproject["project"].get("classifiers", []):
        m = CLASSIFIER_RE.match(c)
        if m:
            found.append(m.group(1))
    return sorted(found, key=_version_key)


def matrix_pythons(workflow: dict, job: str) -> list[str]:
    raw = workflow["jobs"][job]["strategy"]["matrix"]["python"]
    return sorted((str(v) for v in raw), key=_version_key)


def check_matrix_is_the_classifiers(pyproject: dict, workflow: dict) -> list[str]:
    problems = []
    classifiers = classifier_pythons(pyproject)
    linux = matrix_pythons(workflow, "linux")
    if not classifiers:
        problems.append("pyproject.toml declares no 'Programming Language :: Python :: 3.X' classifier")
    if linux != classifiers:
        problems.append(f"linux matrix {linux} != classifiers {classifiers}")
    windows = matrix_pythons(workflow, "windows")
    floor_ceiling = [classifiers[0], classifiers[-1]] if classifiers else []
    if windows != floor_ceiling:
        problems.append(f"windows matrix {windows} != floor and ceiling {floor_ceiling}")
    return problems


def _run_lines(workflow: dict, job: str) -> list[str]:
    lines = []
    for step in workflow["jobs"][job]["steps"]:
        lines.extend(str(step.get("run", "")).splitlines())
    return lines


def ruff_pin_in_workflow(workflow: dict) -> str | None:
    """The exact `ruff==X.Y.Z` the lint job installs; None if it is not exact."""
    for line in _run_lines(workflow, "lint"):
        for token in line.split():
            m = RUFF_EXACT_RE.match(token)
            if m:
                return m.group(1)
    return None


def ruff_pin_in_pyproject(pyproject: dict) -> str | None:
    for spec in pyproject["project"]["optional-dependencies"]["dev"]:
        m = RUFF_EXACT_RE.match(spec.replace(" ", ""))
        if m:
            return m.group(1)
    return None


def check_ruff_pinned(pyproject: dict, workflow: dict) -> list[str]:
    problems = []
    wf = ruff_pin_in_workflow(workflow)
    pp = ruff_pin_in_pyproject(pyproject)
    if wf is None:
        problems.append("lint job does not install ruff with an exact `ruff==X.Y.Z` pin")
    if pp is None:
        problems.append("dev extra does not pin ruff exactly (`ruff==X.Y.Z`)")
    if wf and pp and wf != pp:
        problems.append(f"ruff pin drift: lint job {wf} vs dev extra {pp}")
    return problems


def check_gate(workflow: dict) -> list[str]:
    problems = []
    jobs = workflow["jobs"]
    gate = jobs.get("test")
    if gate is None:
        return ["no job named `test` (the check branch protection points at)"]
    if str(gate.get("if", "")).strip() != "always()":
        problems.append("`test` must run with `if: always()` or a failed leg skips it")
    needs = sorted(gate.get("needs", []))
    legs = sorted(j for j in jobs if j != "test")
    if needs != legs:
        problems.append(f"`test` needs {needs} but the workflow's other jobs are {legs}")
    expected = sorted(ci_gate.EXPECTED_LEGS)
    if expected != legs:
        problems.append(f"scripts/ci_gate.py EXPECTED_LEGS {expected} != workflow jobs {legs}")
    steps = gate.get("steps", [])
    runs_gate = any("scripts/ci_gate.py" in str(s.get("run", "")) for s in steps)
    if not runs_gate:
        problems.append("`test` does not run scripts/ci_gate.py")
    feeds_needs = any(
        str(s.get("env", {}).get("NEEDS", "")).replace(" ", "") == "${{toJSON(needs)}}" for s in steps
    )
    if not feeds_needs:
        problems.append("`test` does not pass NEEDS=${{ toJSON(needs) }} to the gate")
    return problems


def check_pr_title_workflow(workflow: dict) -> list[str]:
    problems = []
    pr = (triggers(workflow) or {}).get("pull_request") or {}
    if sorted(pr.get("types", [])) != sorted(PR_EVENTS):
        problems.append(f"pr-title.yml pull_request.types {pr.get('types')} != {PR_EVENTS}")
    steps = [s for job in workflow["jobs"].values() for s in job["steps"]]
    guard = [s for s in steps if "scripts/pr_title_guard.py" in str(s.get("run", ""))]
    if not guard:
        problems.append("pr-title.yml does not run scripts/pr_title_guard.py")
    elif "PR_TITLE" not in guard[0].get("env", {}):
        problems.append("the guard step is not given PR_TITLE")
    return problems


def check_release_please(config: dict, manifest: dict, workflow: dict) -> list[str]:
    problems = []
    for key in COMMENT_KEYS:
        if not str(config.get(key, "")).strip():
            problems.append(f"release-please-config.json lacks {key}")
    sections = config.get("changelog-sections", [])
    hidden = {s["type"] for s in sections if s.get("hidden")}
    shown = {s["type"] for s in sections if not s.get("hidden")}
    if hidden != HIDDEN_TYPES:
        problems.append(f"hidden types {sorted(hidden)} != {sorted(HIDDEN_TYPES)}")
    if shown != RELEASE_CUTTING_TYPES:
        problems.append(f"release-cutting types {sorted(shown)} != {sorted(RELEASE_CUTTING_TYPES)}")
    if set(hidden | shown) != set(pr_title_guard.TYPES):
        problems.append("changelog-sections and pr_title_guard.TYPES disagree on the type list")
    if "." not in config.get("packages", {}):
        problems.append("release-please-config.json has no package at '.'")
    if "." not in manifest:
        problems.append(".release-please-manifest.json has no entry for '.'")
    on = triggers(workflow) or {}
    if "main" not in (on.get("push") or {}).get("branches", []):
        problems.append("release-please.yml does not run on push to main")
    steps = [s for job in workflow["jobs"].values() for s in job["steps"]]
    with_ = next((s.get("with", {}) for s in steps if "release-please-action" in str(s.get("uses", ""))), None)
    if with_ is None:
        problems.append("release-please.yml does not use googleapis/release-please-action")
    elif with_.get("config-file") != "release-please-config.json" or with_.get("manifest-file") != ".release-please-manifest.json":
        problems.append("release-please-action is not pointed at the config and manifest files")
    return problems


def codeql_workflow_languages(workflow: dict) -> set[str]:
    """Languages an advanced CodeQL workflow analyses (matrix or init `with`)."""
    found: set[str] = set()
    for job in workflow.get("jobs", {}).values():
        found.update(str(v) for v in job.get("strategy", {}).get("matrix", {}).get("language", []))
        for step in job.get("steps", []):
            if "codeql-action/init" in str(step.get("uses", "")):
                langs = str(step.get("with", {}).get("languages", ""))
                if "${{" not in langs:
                    found.update(x.strip() for x in langs.split(",") if x.strip())
    return found


def check_codeql(codeql_workflow: dict | None, default_setup: dict | None) -> list[str]:
    """One of the two ways, never neither, never both."""
    if codeql_workflow is None and default_setup is None:
        return [
            (
                "no CodeQL: neither .github/workflows/codeql.yml nor a "
                ".github/codeql-default-setup.json record of default setup"
            )
        ]
    if codeql_workflow is not None and default_setup is not None and default_setup.get("state") == "configured":
        return ["both an advanced codeql.yml and default setup recorded as configured: GitHub refuses the workflow's uploads while default setup is on"]
    problems = []
    if codeql_workflow is not None:
        missing = CODEQL_LANGUAGES - codeql_workflow_languages(codeql_workflow)
        if missing:
            problems.append(f"codeql.yml does not analyse {sorted(missing)}")
        return problems
    if default_setup.get("state") != "configured":
        problems.append(f"default setup record says state={default_setup.get('state')!r}, not 'configured'")
    if "python" not in default_setup.get("languages", []):
        problems.append(f"default setup record languages {default_setup.get('languages')} do not cover python")
    return problems


# --- fixtures ----------------------------------------------------------------

@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(_text(ROOT / "pyproject.toml"))


@pytest.fixture(scope="module")
def tests_wf() -> dict:
    return _yaml(WORKFLOWS / "tests.yml")


@pytest.fixture(scope="module")
def pr_title_wf() -> dict:
    return _yaml(WORKFLOWS / "pr-title.yml")


@pytest.fixture(scope="module")
def rp_config() -> dict:
    return json.loads(_text(ROOT / "release-please-config.json"))


@pytest.fixture(scope="module")
def rp_manifest() -> dict:
    return json.loads(_text(ROOT / ".release-please-manifest.json"))


@pytest.fixture(scope="module")
def rp_wf() -> dict:
    return _yaml(WORKFLOWS / "release-please.yml")


# --- matrix == classifiers ---------------------------------------------------

def test_matrix_is_the_classifiers(pyproject, tests_wf):
    assert check_matrix_is_the_classifiers(pyproject, tests_wf) == []


def test_matrix_plant_a_dropped_python_is_caught(pyproject, tests_wf):
    wf = copy.deepcopy(tests_wf)
    wf["jobs"]["linux"]["strategy"]["matrix"]["python"].pop()
    problems = check_matrix_is_the_classifiers(pyproject, wf)
    assert any("linux matrix" in p for p in problems), problems


def test_matrix_plant_a_classifier_without_a_leg_is_caught(pyproject, tests_wf):
    pp = copy.deepcopy(pyproject)
    pp["project"]["classifiers"].append("Programming Language :: Python :: 3.99")
    problems = check_matrix_is_the_classifiers(pp, tests_wf)
    assert any("linux matrix" in p for p in problems), problems
    assert any("windows matrix" in p for p in problems), problems


def test_matrix_plant_windows_off_the_floor_is_caught(pyproject, tests_wf):
    wf = copy.deepcopy(tests_wf)
    wf["jobs"]["windows"]["strategy"]["matrix"]["python"] = ["3.11", "3.13"]
    problems = check_matrix_is_the_classifiers(pyproject, wf)
    assert any("windows matrix" in p for p in problems), problems


# --- ruff pinned ---------------------------------------------------------------

def test_ruff_pinned_exactly_and_identically(pyproject, tests_wf):
    assert check_ruff_pinned(pyproject, tests_wf) == []


def test_ruff_plant_a_range_in_the_workflow_is_caught(pyproject, tests_wf):
    wf = copy.deepcopy(tests_wf)
    for step in wf["jobs"]["lint"]["steps"]:
        if "ruff==" in str(step.get("run", "")):
            step["run"] = re.sub(r"ruff==[\d.]+", "ruff>=0.16", step["run"])
    problems = check_ruff_pinned(pyproject, wf)
    assert any("exact" in p for p in problems), problems


def test_ruff_plant_a_drifted_dev_extra_is_caught(pyproject, tests_wf):
    pp = copy.deepcopy(pyproject)
    dev = pp["project"]["optional-dependencies"]["dev"]
    pp["project"]["optional-dependencies"]["dev"] = [
        "ruff==0.0.1" if s.startswith("ruff") else s for s in dev
    ]
    problems = check_ruff_pinned(pp, tests_wf)
    assert any("drift" in p for p in problems), problems


# --- the aggregate gate ----------------------------------------------------------

def test_gate_names_every_job_and_runs_the_script(tests_wf):
    assert check_gate(tests_wf) == []


def test_gate_plant_a_leg_dropped_from_needs_is_caught(tests_wf):
    wf = copy.deepcopy(tests_wf)
    wf["jobs"]["test"]["needs"].remove("windows")
    problems = check_gate(wf)
    assert any("needs" in p for p in problems), problems


def test_gate_plant_a_new_job_outside_needs_is_caught(tests_wf):
    wf = copy.deepcopy(tests_wf)
    wf["jobs"]["docs"] = {"runs-on": "ubuntu-latest", "steps": []}
    problems = check_gate(wf)
    assert any("needs" in p for p in problems), problems


def test_gate_plant_without_always_is_caught(tests_wf):
    wf = copy.deepcopy(tests_wf)
    del wf["jobs"]["test"]["if"]
    problems = check_gate(wf)
    assert any("always()" in p for p in problems), problems


def _needs(**results: str) -> dict:
    return {leg: {"result": results.get(leg, "success"), "outputs": {}} for leg in ci_gate.EXPECTED_LEGS}


def test_gate_script_passes_when_every_leg_succeeded():
    assert ci_gate.failures(_needs()) == []


@pytest.mark.parametrize("result", ["skipped", "cancelled", "failure"])
def test_gate_script_plant_rejects_any_non_success(result):
    problems = ci_gate.failures(_needs(windows=result))
    assert problems and "windows" in problems[0] and result in problems[0], problems


def test_gate_script_plant_rejects_a_missing_leg():
    needs = _needs()
    del needs["lint"]
    problems = ci_gate.failures(needs)
    assert problems and "lint" in problems[0] and "missing" in problems[0], problems


def test_gate_script_plant_rejects_a_leg_it_does_not_know():
    needs = _needs()
    needs["mystery"] = {"result": "success", "outputs": {}}
    problems = ci_gate.failures(needs)
    assert problems and "mystery" in problems[0], problems


def test_gate_script_exit_code_is_the_verdict(monkeypatch, capsys):
    monkeypatch.setenv("NEEDS", json.dumps(_needs(linux="skipped")))
    assert ci_gate.main() == 1
    assert "::error::linux: result is 'skipped'" in capsys.readouterr().out
    monkeypatch.setenv("NEEDS", json.dumps(_needs()))
    assert ci_gate.main() == 0
    monkeypatch.delenv("NEEDS")
    assert ci_gate.main() == 2


# --- pr-title ------------------------------------------------------------------------

def test_pr_title_workflow_present(pr_title_wf):
    assert check_pr_title_workflow(pr_title_wf) == []


def test_pr_title_plant_a_missing_event_is_caught(pr_title_wf):
    wf = copy.deepcopy(pr_title_wf)
    triggers(wf)["pull_request"]["types"].remove("edited")
    problems = check_pr_title_workflow(wf)
    assert any("types" in p for p in problems), problems


def test_pr_title_guard_regex_is_the_fleet_one():
    assert pr_title_guard.TITLE_RE.pattern == (
        r"^(build|chore|ci|deps|docs|feat|fix|perf|refactor|security|test)(\([a-z0-9._-]+\))?!?: .+"
    )


@pytest.mark.parametrize(
    "title",
    [
        "ci: the fleet CI floor",
        "feat(web): a reliability diagram",
        "fix!: drop the legacy path",
        "deps: bump textual from 0.47.0 to 0.48.0",
        "chore(main): release 0.3.0",
        "ci(deps): bump actions/checkout from 5 to 6",
    ],
)
def test_pr_title_guard_accepts_conventional_titles(title):
    assert pr_title_guard.problems(title) == []


@pytest.mark.parametrize(
    "title",
    [
        "Update README",
        "feature: no such type",
        "fix:missing space",
        "Fix: capitalised type",
        "fix(Scope): upper-case scope",
        "ci: ",
    ],
)
def test_pr_title_guard_plant_rejects_non_conventional_titles(title):
    problems = pr_title_guard.problems(title)
    assert problems and "not a conventional-commit line" in problems[0], problems


def test_pr_title_guard_packaged_switch_governs_breaking_footer():
    title = "feat!: the ledger schema changes"
    assert pr_title_guard.problems(title, "", packaged=False) == []
    assert any("BREAKING CHANGE" in p for p in pr_title_guard.problems(title, "", packaged=True))
    assert pr_title_guard.problems(title, "BREAKING CHANGE: ledger v2", packaged=True) == []


def test_pr_title_guard_packaged_is_declared_and_false_here():
    # This repo installs from source and uploads nothing; flip when it does.
    assert pr_title_guard.PACKAGED is False


# --- release-please ----------------------------------------------------------------------

def test_release_please_present(rp_config, rp_manifest, rp_wf):
    assert check_release_please(rp_config, rp_manifest, rp_wf) == []


def test_release_please_plant_a_hidden_feat_is_caught(rp_config, rp_manifest, rp_wf):
    cfg = copy.deepcopy(rp_config)
    for s in cfg["changelog-sections"]:
        if s["type"] == "feat":
            s["hidden"] = True
    problems = check_release_please(cfg, rp_manifest, rp_wf)
    assert any("hidden types" in p for p in problems), problems


def test_release_please_plant_a_missing_comment_is_caught(rp_config, rp_manifest, rp_wf):
    cfg = copy.deepcopy(rp_config)
    del cfg["$comment-what-cuts-a-release"]
    problems = check_release_please(cfg, rp_manifest, rp_wf)
    assert any("$comment-what-cuts-a-release" in p for p in problems), problems


def test_release_please_plant_an_empty_manifest_is_caught(rp_config, rp_wf):
    problems = check_release_please(rp_config, {}, rp_wf)
    assert any("manifest" in p for p in problems), problems


def test_release_please_manifest_is_the_version_before_the_first_tag(rp_manifest):
    assert re.fullmatch(r"\d+\.\d+\.\d+", rp_manifest["."])


# --- CodeQL: advanced workflow OR recorded default setup -------------------------------

def _codeql_workflow() -> dict | None:
    path = WORKFLOWS / "codeql.yml"
    return _yaml(path) if path.exists() else None


def _codeql_record() -> dict | None:
    return json.loads(_text(CODEQL_RECORD)) if CODEQL_RECORD.exists() else None


ADVANCED_WF = {
    "jobs": {
        "analyze": {
            "strategy": {"matrix": {"language": ["python", "actions"]}},
            "steps": [{"uses": "github/codeql-action/init@v4", "with": {"languages": "${{ matrix.language }}"}}],
        }
    }
}


def test_codeql_runs_one_way_or_the_other():
    assert check_codeql(_codeql_workflow(), _codeql_record()) == []


def test_codeql_plant_neither_is_caught():
    problems = check_codeql(None, None)
    assert problems and "no CodeQL" in problems[0], problems


def test_codeql_plant_both_is_caught():
    problems = check_codeql(ADVANCED_WF, {"state": "configured", "languages": ["python"]})
    assert problems and "both" in problems[0], problems


def test_codeql_plant_a_workflow_missing_actions_is_caught():
    wf = copy.deepcopy(ADVANCED_WF)
    wf["jobs"]["analyze"]["strategy"]["matrix"]["language"] = ["python"]
    problems = check_codeql(wf, None)
    assert problems and "actions" in problems[0], problems
    assert check_codeql(ADVANCED_WF, None) == []


def test_codeql_plant_default_setup_off_or_without_python_is_caught():
    off = check_codeql(None, {"state": "not-configured", "languages": []})
    assert any("not 'configured'" in p for p in off), off
    no_python = check_codeql(None, {"state": "configured", "languages": ["javascript"]})
    assert any("cover python" in p for p in no_python), no_python
    assert check_codeql(None, {"state": "configured", "languages": ["python"]}) == []


def test_codeql_record_is_dated_and_says_how_it_was_observed():
    record = _codeql_record()
    if record is None:
        pytest.skip("advanced codeql.yml in use; no default-setup record needed")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["observed"])
    assert record["evidence"].strip()
