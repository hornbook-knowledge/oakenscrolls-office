"""
almanac_seam.py — resolution evidence from local almanac-data clones. b17: OAKOF

The almanac-data org ("catalog, don't host") publishes versioned catalogs of
pointers to authoritative public datasets — climate, economy, justice, health,
and friends. This seam lets a world-facing prediction be graded WITH A CITATION:
"resolved FALSE, per berkeley-earth-temperature in climate-almanac @ a1b2c3d".

Sovereign by construction: this module reads LOCAL CLONES only. No network,
no subprocess — it lives inside the no-egress zone (tests/test_no_egress.py).
The human syncs the catalogs with git on their own terms; the seam only ever
opens files under ALMANAC_DATA_ROOT (default ~/github/almanac-data). The
clone's git commit is read straight from .git files and pinned into the
citation, so evidence records *which version of the catalog* vouched.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

# Nestor's string matcher backs the OPTIONAL fuzzy fallback in search(). Nestor
# is an unpublished git dependency (pyproject pins it from GitHub), so it may be
# absent on a clean install — this seam imports it LAZILY: the ledger, the TUI,
# and exact-match citations all run without it, and only the fuzzy fallback is
# gated. It is stdlib-only (difflib) — importing it pulls no network, so this
# module stays inside the no-egress zone (tests/test_no_egress.py; 'nestor' is
# not forbidden).
_MATCHER = None          # resolved StringMatcher, or None if Nestor is absent
_MATCHER_TRIED = False   # probe the import once, not on every call
# Minimum title/id/publisher similarity for a fuzzy citation to surface.
_FUZZY_THRESHOLD = 0.55


def _matcher():
    """Nestor's StringMatcher, or None if Nestor isn't installed. The import is
    attempted once and cached, so a clean install without the git dependency
    degrades to exact-match citations instead of failing to import."""
    global _MATCHER, _MATCHER_TRIED
    if not _MATCHER_TRIED:
        _MATCHER_TRIED = True
        try:
            from nestor.matcher import StringMatcher
            _MATCHER = StringMatcher()
        except ImportError:
            _MATCHER = None
    return _MATCHER


def nestor_available() -> bool:
    """Whether the fuzzy citation fallback is active (i.e. Nestor is installed).
    Exact-match citations work regardless."""
    return _matcher() is not None


def almanac_root() -> Path:
    return Path(
        os.environ.get("ALMANAC_DATA_ROOT", str(Path.home() / "github" / "almanac-data"))
    ).expanduser()


def _head_commit(repo: Path) -> Optional[str]:
    """The clone's HEAD sha via plain file reads — no git binary, no subprocess."""
    head = repo / ".git" / "HEAD"
    try:
        text = head.read_text().strip()
    except OSError:
        return None
    if not text.startswith("ref:"):
        return text or None
    ref = text.split(None, 1)[1].strip()
    loose = repo / ".git" / ref
    try:
        return loose.read_text().strip()
    except OSError:
        pass
    packed = repo / ".git" / "packed-refs"
    try:
        for line in packed.read_text().splitlines():
            if line.endswith(ref) and not line.startswith(("#", "^")):
                return line.split()[0]
    except OSError:
        pass
    return None


def verticals() -> list[dict]:
    """Local almanac clones: any direct child of the root with a catalog.json."""
    root = almanac_root()
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        catalog = child / "catalog.json"
        if child.is_dir() and catalog.is_file():
            out.append({"name": child.name, "path": child, "commit": _head_commit(child)})
    return out


def _entry_text(entry: dict) -> str:
    parts = [
        entry.get("id", ""),
        entry.get("title", ""),
        entry.get("description", ""),
        entry.get("publisher", ""),
        " ".join(entry.get("topics") or []),
    ]
    return " ".join(parts).lower()


def _candidate(v: dict, entry: dict) -> dict:
    """The citation candidate a matched entry yields — provenance included."""
    source = entry.get("source") or {}
    return {
        "vertical": v["name"],
        "entry_id": entry.get("id"),
        "title": entry.get("title"),
        "publisher": entry.get("publisher"),
        "canonical_url": source.get("canonical_url"),
        "status": entry.get("status"),
        "license": entry.get("license"),
        "catalog_commit": v["commit"],
    }


def _fuzzy_score(matcher, query_norm: str, entry: dict) -> float:
    """Best Nestor similarity of the query against the entry's SHORT fields
    (title / id / publisher). Short-vs-short is where difflib is meaningful —
    unlike the concatenated blob — so a reworded or misspelled claim can still
    find its source. Only called when a matcher is present."""
    best = 0.0
    for field in (entry.get("title"), entry.get("id"), entry.get("publisher")):
        if field:
            best = max(best, matcher.similarity(query_norm, matcher.normalize(field)))
    return best


def search(query: str, limit: int = 8) -> list[dict]:
    """Match a claim against local almanac-data catalogs, returning citation
    candidates with provenance.

    Exact token-AND (id/title/description/publisher/topics) is AUTHORITATIVE and
    ranks live sources first — behavior unchanged. Only when NO entry matches
    exactly does a Nestor StringMatcher fuzzy fallback surface the closest
    sources by title/id/publisher similarity, so a reworded or misspelled claim
    ('berkely erth temprature') still finds its evidence. That fallback is gated
    on Nestor being installed (the unpublished git dep); without it, exact
    token-AND still works and search simply returns no fuzzy matches. Empty list
    when no clones exist."""
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return []
    matcher = _matcher()  # None on a clean install without the Nestor git dep
    exact: list[dict] = []
    fuzzy: list[tuple[float, dict]] = []
    query_norm = matcher.normalize(query) if matcher else ""
    for v in verticals():
        try:
            catalog = json.loads((v["path"] / "catalog.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for entry in catalog.get("entries", []):
            text = _entry_text(entry)
            if all(t in text for t in tokens):
                exact.append(_candidate(v, entry))
            elif matcher:  # fuzzy fallback is gated on Nestor being installed
                score = _fuzzy_score(matcher, query_norm, entry)
                if score >= _FUZZY_THRESHOLD:
                    fuzzy.append((score, _candidate(v, entry)))
    if exact:
        exact.sort(key=lambda h: (h["status"] != "live", h["vertical"], h["entry_id"] or ""))
        return exact[:limit]
    # Fuzzy fallback — fires only when nothing matched exactly. Live sources
    # first, then by descending similarity.
    fuzzy.sort(key=lambda sc: (sc[1]["status"] != "live", -sc[0], sc[1]["vertical"]))
    return [cand for _, cand in fuzzy][:limit]


def citation(candidate: dict, note: Optional[str] = None) -> dict:
    """The evidence record a resolution carries: which source vouched, in which
    vertical, at which catalog version, observed when. Facts only."""
    return {
        "kind": "almanac-data",
        "vertical": candidate["vertical"],
        "entry_id": candidate["entry_id"],
        "title": candidate["title"],
        "publisher": candidate["publisher"],
        "canonical_url": candidate["canonical_url"],
        "catalog_commit": candidate["catalog_commit"],
        "cited_at": int(time.time()),
        "note": note,
    }
