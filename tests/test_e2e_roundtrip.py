"""End-to-end round trips over the real CAT-tool corpus, with golden snapshots.

The suite's long-standing gap was that nothing took a document all the way
through import → translate → export → re-import. Every defect the tag work has
actually found came from doing that by hand; this makes it a test.

Two kinds of assertion, and the distinction matters:

**Invariants** must hold for every file, always. Tags reassemble; verification is
clean; nothing that was written comes back different. A failure here is a bug,
not a change — the message says which segment and what happened.

**Golden snapshots** (`tests/golden/*.json`) are a tripwire, not a specification.
They record what the handlers currently do — including two known defects, called
out in ``KNOWN_DEFECTS`` below. A golden diff means *something changed*; whether
that is a fix or a regression is for the reader to judge. Regenerate with::

    UPDATE_GOLDEN=1 pytest tests/test_e2e_roundtrip.py

and review the diff before committing, because that diff is the assertion.

The snapshots hold no document text — segment indices, tag strings, counts and a
digest — so they stay reviewable in a pull request.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

pytest.importorskip("docx", reason="python-docx is needed for the DOCX round trip")

import roundtrip_harness as rh  # noqa: E402

from modules import tag_protection as tp  # noqa: E402

pytestmark = pytest.mark.e2e

UPDATE = os.environ.get("UPDATE_GOLDEN", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Known defects
# ---------------------------------------------------------------------------
# Segments the export is currently expected to get wrong. Anything *outside*
# these sets fails the run; the sets themselves are documented so they cannot be
# mistaken for correct behaviour, and shrinking one is a fix worth noticing.

MEMOQ_WRITER_NOTE = """\
MQXLIFFHandler._place_translation_carefully() inserts a translation by running
`node_text.replace(whole_source_text, translation)` over each individual XML text
node. When inline tags split a segment across several nodes, no single node holds
the whole source text, every replace is a no-op, and the cloned source stays —
while update_target_segments() counts the segment as updated and marks it
Confirmed. Underneath sits the real gap: _extract_plain_text() discards memoQ's
bpt/ept markers, so bold/italic runs never appear in the segment text and the
writer has nothing to rebuild them from. Fixing that means teaching the MQXLIFF
handler to surface bpt/ept as real inline tags, the way the SDLXLIFF handler
already does."""

KNOWN_DEFECTS = {
    "mqxliff_CAT_test_DOCX_docx_lit": {
        "segments": {9, 57, 58, 62, 66, 68, 102},
        "note": MEMOQ_WRITER_NOTE,
    },
    "mqxliff_CAT_test_IDML_idml_lit": {
        "segments": {3},
        "note": MEMOQ_WRITER_NOTE,
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def results(tmp_path_factory):
    """Run every round trip once; the assertions below read the results.

    Session-scoped because exporting the corpus is the slow part (a few seconds)
    and none of the assertions mutate what they inspect.
    """
    out_dir = tmp_path_factory.mktemp("roundtrip")
    done = {}
    for name, run in rh.ROUND_TRIPS.items():
        result = run(str(out_dir))
        assert result.name == name, (
            f"harness key {name!r} does not match RoundTrip.name {result.name!r}; "
            f"golden files are keyed by name")
        done[name] = result
    return done


def _ids():
    return sorted(rh.ROUND_TRIPS)


@pytest.fixture(params=_ids())
def result(request, results):
    return results[request.param]


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_corpus_is_present_and_covers_every_format(results):
    formats = {r.fmt for r in results.values()}
    assert formats == {"DOCX", "SDLXLIFF", "MQXLIFF", "XLF"}, formats
    for result in results.values():
        assert result.sources, f"{result.name}: no segments extracted"


def test_tags_reassemble_into_the_original_segment(result):
    """Tokens must tile the source exactly. If they do not, every downstream
    assumption — atomic rendering, insertion, verification — is unsound."""
    for i, text in enumerate(result.sources):
        out, pos = [], 0
        for token in tp.parse_tags(text or ""):
            out.append((text or "")[pos:token.start])
            out.append(token.raw)
            pos = token.end
        out.append((text or "")[pos:])
        assert "".join(out) == (text or ""), f"{result.name} segment {i}"


def test_translating_never_disturbs_the_tags(result):
    """Every target must carry its source's tags, in order. This is the property
    a translator relies on and the one tag protection exists to guarantee."""
    issues = result.verification_issues
    assert issues == {}, (
        f"{result.name}: {len(issues)} segment(s) with tag problems — "
        + "; ".join(f"[{i}] {d}" for i, d in list(issues.items())[:5]))


def test_what_was_written_is_what_comes_back(result):
    """The round trip's core claim. Mismatches outside the documented known
    defects are failures."""
    if not result.read_back:
        pytest.skip(f"{result.fmt} has no export path — parser-only check")

    known = KNOWN_DEFECTS.get(result.name, {})
    expected = known.get("segments", set())
    unexpected = [i for i in result.mismatched if i not in expected]

    detail = ""
    if unexpected:
        i = unexpected[0]
        detail = (f"\n  first one, segment {i}:"
                  f"\n    wrote:     {result.written[i][:90]!r}"
                  f"\n    file holds:{result.read_back[i][:90]!r}")
    assert not unexpected, (
        f"{result.name}: {len(unexpected)} segment(s) did not survive the export "
        f"{unexpected[:10]}{detail}")


def test_known_defects_have_not_grown_or_silently_healed(result):
    """A known-defect list that drifts out of date is worse than none. If the
    memoQ writer is fixed, this fails and tells you to delete the entry."""
    known = KNOWN_DEFECTS.get(result.name)
    if not known:
        return
    still_broken = set(result.mismatched)
    expected = known["segments"]
    healed = expected - still_broken
    assert not healed, (
        f"{result.name}: segments {sorted(healed)} now round-trip correctly. "
        f"Remove them from KNOWN_DEFECTS in this file (and from "
        f"tests/golden/{result.name}.json via UPDATE_GOLDEN=1).")
    assert still_broken == expected, (
        f"{result.name}: unexpected mismatches {sorted(still_broken - expected)}")


# ---------------------------------------------------------------------------
# Format-specific invariants
# ---------------------------------------------------------------------------

def test_docx_keeps_every_run_colour_through_a_translation(results):
    """The reason `<cf color="…">` exists. Testing this with `target == source`
    proves nothing — the words have to change, which is why the harness
    pseudo-translates."""
    notes = results["docx_CAT_test_DOCX"].notes
    assert notes["run_colours_in_source"], "corpus file no longer has coloured runs"
    assert notes["run_colours_lost"] == {}, (
        f"colours lost on export: {notes['run_colours_lost']} "
        f"(source {notes['run_colours_in_source']}, "
        f"export {notes['run_colours_after_export']})")


def test_docx_translates_every_paragraph(results):
    """The paragraph-mapping regression: a paragraph that lost its translation
    kept the original *source*, so the export looked plausible rather than
    broken. Twelve of 62 paragraphs were affected. Accented pseudo-text differs
    from its source in every word, so equality with the source is the test."""
    result = results["docx_CAT_test_DOCX"]
    # Segments the transform legitimately leaves alone are not evidence of
    # anything: the corpus has one-character paragraphs (":", "“", "t") with no
    # accent mapping, whose 15% expansion rounds to zero added characters.
    untranslated = [i for i, text in enumerate(result.read_back)
                    if text == result.sources[i]
                    and result.written[i] != result.sources[i]]
    assert untranslated == [], (
        f"paragraphs still holding source text: {untranslated}")
    assert result.notes["paragraphs_in_export"] == len(result.sources)


def test_phrase_placeholders_are_recognised_and_kept(results):
    result = results["phrase_xlf"]
    assert result.tag_census().get(tp.FAMILY_PLACEHOLDER, 0) > 0, (
        "no {0}-style placeholders found; the fixture or the parser changed")


def test_trados_self_closing_tags_are_seen(results):
    """`<1/>` standalone tags were invisible to the old extractor, so tag
    insertion refused to place them."""
    result = results["sdlxliff_CAT_test_DOCX_docx"]
    empties = [t.raw for text in result.sources
               for t in tp.parse_tags(text or "")
               if t.kind == tp.KIND_EMPTY
               and t.family == tp.FAMILY_TRADOS_NUMERIC]
    assert empties, "no self-closing Trados tags in the corpus any more"


# ---------------------------------------------------------------------------
# Golden snapshots
# ---------------------------------------------------------------------------

def test_snapshot_matches_golden(result):
    path = rh.golden_path(result.name)
    snapshot = result.snapshot()

    if UPDATE:
        os.makedirs(rh.GOLDEN_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        pytest.skip(f"golden regenerated: {os.path.relpath(path, rh.REPO_ROOT)}")

    assert os.path.exists(path), (
        f"no golden for {result.name}. Create it with "
        f"UPDATE_GOLDEN=1 pytest tests/test_e2e_roundtrip.py")

    with open(path, encoding="utf-8") as fh:
        golden = json.load(fh)

    if golden == snapshot:
        return

    changed = sorted(set(golden) | set(snapshot))
    diff = [f"  {key}:\n    golden: {golden.get(key)!r}\n    now:    {snapshot.get(key)!r}"
            for key in changed if golden.get(key) != snapshot.get(key)]
    pytest.fail(
        f"{result.name} no longer matches its golden snapshot.\n"
        + "\n".join(diff)
        + "\n\nIf this change is intended, regenerate with "
          "UPDATE_GOLDEN=1 pytest tests/test_e2e_roundtrip.py and review the diff."
    )
