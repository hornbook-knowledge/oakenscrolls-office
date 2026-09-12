"""almanac_seam, backed by Nestor's verified-match engine.

Two things the old exact-token-AND matcher could never do:
  1. a misspelled / reworded claim still finds its source (fuzzy fallback);
  2. a graded citation can be SEALED as a Nestor pair with provenance, so
     'this prediction was resolved against this public source' becomes an
     auditable, verified match — the same engine that resolves entity aliases
     and reconciles figures, now grading predictions against public records.

Offline: fake almanac clones under a temp ALMANAC_DATA_ROOT; Nestor over its
reference in-repo SqliteStore. No network, no almanac-data checkout required.

Nestor is an unpublished git dependency behind the `citations` extra (see
pyproject.toml), so it is absent on a base `pip install .`. The tests below
that actually exercise Nestor's fuzzy fallback or its resolver skip in that
environment (pytest.importorskip("nestor")) rather than failing; the two
exact-match tests need no matcher and keep running regardless — the mirror
image of test_almanac_seam_degraded.py, which stubs Nestor OUT so its
degraded path is exercised even when Nestor IS installed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_vertical(root: Path, name: str, entries: list[dict], sha: str = "a" * 40) -> None:
    repo = root / name
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / "catalog.json").write_text(json.dumps({"name": name, "entries": entries}))
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "refs" / "heads" / "main").write_text(sha + "\n")


BERKELEY = {
    "id": "berkeley-earth-temperature",
    "title": "Berkeley Earth Surface Temperature",
    "description": "Independent global land and ocean surface temperature analysis",
    "publisher": "Berkeley Earth",
    "topics": ["temperature", "global"],
    "source": {"canonical_url": "https://berkeleyearth.org/data/"},
    "status": "live",
    "license": "CC-BY-NC-SA-4.0",
}
DISASTERS = {
    "id": "billion-dollar-disasters",
    "title": "Billion-Dollar Disasters",
    "description": "US weather and climate disasters exceeding one billion dollars",
    "publisher": "NOAA",
    "topics": ["disasters"],
    "source": {"canonical_url": "https://example.gov/disasters"},
    "status": "frozen",
    "license": "public-domain",
}


@pytest.fixture()
def seam(tmp_path, monkeypatch):
    monkeypatch.setenv("ALMANAC_DATA_ROOT", str(tmp_path / "almanac-data"))
    make_vertical(tmp_path / "almanac-data", "climate-almanac", [BERKELEY, DISASTERS], sha="b" * 40)
    import almanac_seam
    return almanac_seam


def test_misspelled_claim_still_finds_its_source(seam):
    pytest.importorskip("nestor")
    # Every token is misspelled, so exact token-AND matches nothing — but the
    # Nestor fuzzy fallback recovers the right source WITH provenance intact.
    hits = seam.search("berkely erth temprature")
    assert hits, "fuzzy fallback should recover the source for a misspelled claim"
    assert hits[0]["entry_id"] == "berkeley-earth-temperature"
    assert hits[0]["canonical_url"] == "https://berkeleyearth.org/data/"
    assert hits[0]["catalog_commit"] == "b" * 40


def test_reworded_claim_finds_source(seam):
    pytest.importorskip("nestor")
    hits = seam.search("berkeley earth temp anomaly")
    assert hits and hits[0]["entry_id"] == "berkeley-earth-temperature"


def test_unrelated_claim_returns_nothing(seam):
    assert seam.search("quarterly estimated tax deadlines") == []


def test_exact_match_behavior_unchanged(seam):
    # the original contract still holds: exact token-AND is authoritative
    assert [h["entry_id"] for h in seam.search("temperature global")] == [
        "berkeley-earth-temperature"
    ]


def test_graded_citation_seals_as_a_nestor_pair(seam, tmp_path):
    """A resolution graded against a source becomes a SEALED Nestor pair with
    provenance — the verified-match engine applied to public-record grading."""
    pytest.importorskip("nestor")
    from nestor.entity import EntityResolver
    from nestor.sqlite_store import SqliteStore
    from nestor import cascade

    cascade.set_ledger_path(tmp_path / "ledger.jsonl")  # keep the ledger in tmp
    resolver = EntityResolver(SqliteStore(str(tmp_path / "nestor.db")), domain="almanac-citation")

    # Resolve the claim to a citation candidate, then seal claim -> canonical source.
    candidate = seam.search("berkely erth temprature")[0]
    resolver.seal(
        surface="berkely erth temprature",
        canonical=candidate["entry_id"],
        verifier="oakenscroll",
        origin=f'{candidate["vertical"]}@{candidate["catalog_commit"]}',
    )
    resolved = resolver.resolve("berkeley earth temperature")  # a *different* wording
    assert resolved["canonical"] == "berkeley-earth-temperature"
    assert resolved["sealed"] is True
    assert resolved["confidence"] >= 0.55
