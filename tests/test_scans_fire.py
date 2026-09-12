"""Every source-scanning helper fires on a planted violation.

A scan that has never seen a violation is indistinguishable from a scan that
cannot see one. So: every helper under tests/ or scripts/ that inspects Python
source through the ast module must have a test here that plants a violation in
a temp file and watches the helper catch it.

Discovery is structural — the AST of the suite itself, not a hand-kept list —
so a new scanner without a plant fails this file instead of slipping in
quietly. The plant for a helper `mod._fn` is a test function here named
`test_<mod minus its test_ prefix>_<fn minus underscores>_fires_on_plant`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (ROOT / "tests", ROOT / "scripts")
SELF = Path(__file__).resolve()


def _calls_ast(fn: ast.AST) -> bool:
    """Does this function body parse or walk Python source via the ast module?"""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "ast"
            and f.attr in {"parse", "walk"}
        ):
            return True
    return False


def scanning_helpers(roots: tuple[Path, ...] = SCAN_ROOTS, skip: tuple[Path, ...] = (SELF,)) -> dict[str, Path]:
    """{'module.function': file} for every module-level, non-test function under
    `roots` whose body parses or walks Python source with the ast module."""
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.py")):
            if path.resolve() in skip:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("test_"):
                    continue
                if _calls_ast(node):
                    found[f"{path.stem}.{node.name}"] = path
    return found


def plant_test_name(helper: str) -> str:
    module, fn = helper.split(".", 1)
    return f"test_{module.removeprefix('test_')}_{fn.strip('_')}_fires_on_plant"


HELPERS = scanning_helpers()


# --- the structural rule -----------------------------------------------------

def test_discovery_finds_the_no_egress_scanner():
    assert "test_no_egress._imports" in HELPERS, sorted(HELPERS)


@pytest.mark.parametrize("helper", sorted(HELPERS) or ["<none discovered>"])
def test_every_scanner_has_a_plant(helper):
    if helper == "<none discovered>":
        pytest.fail("no scanning helper discovered at all; the discovery itself is broken")
    name = plant_test_name(helper)
    assert name in globals(), f"{helper} scans source but has no plant here — add {name}()"


def test_discovery_itself_fires_on_plant(tmp_path):
    planted = tmp_path / "test_planted.py"
    planted.write_text(
        "import ast\n"
        "from pathlib import Path\n"
        "\n"
        "def _scan(path: Path):\n"
        "    return ast.parse(path.read_text(encoding='utf-8'))\n"
        "\n"
        "def _plain(x):\n"
        "    return x\n"
        "\n"
        "def test_something():\n"
        "    return ast.walk(ast.parse(''))\n",
        encoding="utf-8",
    )
    found = scanning_helpers(roots=(tmp_path,), skip=())
    assert found == {"test_planted._scan": planted}
    assert plant_test_name("test_planted._scan") == "test_planted_scan_fires_on_plant"


# --- the plants ----------------------------------------------------------------

def test_no_egress_imports_fires_on_plant(tmp_path):
    from test_no_egress import FORBIDDEN, _imports

    leaky = tmp_path / "leaky.py"
    leaky.write_text(
        "import socket\n"
        "from urllib.request import urlopen\n"
        "import ctypes.util\n"
        "import json  # innocent\n",
        encoding="utf-8",
    )
    assert _imports(leaky) & FORBIDDEN == {"socket", "urllib", "ctypes"}

    clean = tmp_path / "clean.py"
    clean.write_text("import json\nfrom pathlib import Path\n", encoding="utf-8")
    assert _imports(clean) & FORBIDDEN == set()
