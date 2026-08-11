"""almanac_seam with Nestor ABSENT — the degraded (soft-dependency) path.

Nestor is an unpublished git dependency and only backs the OPTIONAL fuzzy
citation fallback. On a clean install without it, the seam must still import,
report itself degraded, and serve exact-match citations; only fuzzy matching
falls away (to an empty result, never a crash).

These tests stub Nestor out even when it IS installed, by evicting it from
sys.modules and blocking re-import with a meta-path finder — so the degraded
path is exercised in every environment, not only on a Nestor-less machine.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_vertical(root: Path, name: str, entries: list[dict], sha: str = "a" * 40) -> None:
    """A fake local almanac clone: catalog.json + just enough .git for HEAD."""
    repo = root / name
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / "catalog.json").write_text(json.dumps({"name": name, "entries": entries}))
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "refs" / "heads" / "main").write_text(sha + "\n")


ENTRY = {
    "id": "berkeley-earth-temperature",
    "title": "Berkeley Earth Surface Temperature",
    "description": "Independent global land and ocean surface temperature analysis",
    "publisher": "Berkeley Earth",
    "topics": ["temperature", "global"],
    "source": {"canonical_url": "https://berkeleyearth.org/data/"},
    "status": "live",
    "license": "CC-BY-NC-SA-4.0",
}


class _BlockNestor:
    """A meta-path finder that makes `import nestor[...]` raise, simulating a
    machine where the unpublished git dependency was never installed."""

    def find_spec(self, name, path=None, target=None):
        if name == "nestor" or name.startswith("nestor."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return None


@pytest.fixture()
def seam_without_nestor(tmp_path, monkeypatch):
    monkeypatch.setenv("ALMANAC_DATA_ROOT", str(tmp_path / "almanac-data"))
    # Evict any already-imported Nestor and block re-import for this test.
    for mod in [m for m in list(sys.modules) if m == "nestor" or m.startswith("nestor.")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockNestor(), *sys.meta_path])

    import almanac_seam
    # Reset the one-shot lazy probe so _matcher() re-runs under the block above
    # (a prior Nestor-backed test may have cached a real matcher on the module).
    monkeypatch.setattr(almanac_seam, "_MATCHER", None, raising=False)
    monkeypatch.setattr(almanac_seam, "_MATCHER_TRIED", False, raising=False)
    return almanac_seam


def test_seam_imports_and_reports_degraded(seam_without_nestor):
    # The whole point: the module is usable and honestly reports fuzzy is off.
    assert seam_without_nestor.nestor_available() is False


def test_exact_match_still_works_without_nestor(seam_without_nestor, tmp_path):
    make_vertical(tmp_path / "almanac-data", "climate-almanac", [ENTRY], sha="b" * 40)
    hits = seam_without_nestor.search("temperature global")
    assert [h["entry_id"] for h in hits] == ["berkeley-earth-temperature"]
    assert hits[0]["catalog_commit"] == "b" * 40
    assert hits[0]["canonical_url"] == "https://berkeleyearth.org/data/"


def test_exact_citation_carries_provenance_without_nestor(seam_without_nestor, tmp_path):
    make_vertical(tmp_path / "almanac-data", "climate-almanac", [ENTRY])
    cite = seam_without_nestor.citation(seam_without_nestor.search("berkeley")[0])
    assert cite["kind"] == "almanac-data"
    assert cite["publisher"] == "Berkeley Earth"
    assert cite["catalog_commit"] == "a" * 40


def test_fuzzy_degrades_to_empty_not_a_crash(seam_without_nestor, tmp_path):
    make_vertical(tmp_path / "almanac-data", "climate-almanac", [ENTRY])
    # Every token misspelled: exact token-AND matches nothing, and with Nestor
    # blocked the fuzzy fallback is gated off — so this is empty, not an error.
    assert seam_without_nestor.search("berkely erth temprature") == []


def test_no_clones_without_nestor_is_empty(seam_without_nestor):
    assert seam_without_nestor.verticals() == []
    assert seam_without_nestor.search("anything at all") == []
