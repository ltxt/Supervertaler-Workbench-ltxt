"""End-to-end round trips over the real CAT-tool corpus, with golden snapshots.

The suite's long-standing gap was that nothing took a document all the way
through import → translate → export → re-import. Every defect the tag work has
actually found came from doing that by hand; this makes it a test.

Two kinds of assertion, and the distinction matters:

**Invariants** must hold for every file, always. Tags reassemble; verification is
clean; nothing that was written comes back different. A failure here is a bug,
not a change — the message says which segment and what happened.

**Golden snapshots** (`tests/golden/*.json`) are a tripwire, not a specification.
They record what the handlers currently do, including any defect not yet fixed
(``KNOWN_DEFECTS`` below, currently empty). A golden diff means *something
changed*; whether that is a fix or a regression is for the reader to judge.
Regenerate with::

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

#: Empty, and worth keeping that way deliberately rather than deleting the
#: mechanism: it is what told us the memoQ writer had been fixed.
#:
#: It used to hold 7 DOCX and 1 IDML segment, from two defects that a user found
#: by opening the exports in memoQ 12.4.36 — the DOCX file imported with errors
#: and the IDML file would not open at all, silently. Both came from the writer
#: round-tripping the file through ElementTree and placing text by
#: whole-source-string replacement inside individual XML nodes; the writer now
#: rewrites only the target spans, as text. See the note above
#: MQXLIFFHandler.update_target_segments().
KNOWN_DEFECTS: dict = {}


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


def test_the_exported_file_carries_the_sources_tags(result):
    """Same claim as above, made against the bytes on disk rather than the string
    we handed the exporter. This is what a translator actually receives, and it
    stays meaningful even for segments the export gets wrong."""
    if not result.read_back:
        pytest.skip(f"{result.fmt} has no export path — parser-only check")
    issues = result.export_verification_issues
    assert issues == {}, (
        f"{result.name}: the export has tag problems in {len(issues)} segment(s) — "
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


MEMOQ_CORPUS = [
    "memoq/CAT_test_DOCX.docx_lit.mqxliff",
    "memoq/CAT_test_IDML.idml_lit.mqxliff",
]


@pytest.mark.parametrize("source_file", MEMOQ_CORPUS)
def test_memoq_load_save_is_byte_identical(source_file, tmp_path):
    """A load→save with nothing translated must reproduce the file exactly.

    memoQ 12.4.36 rejected the old output: the DOCX file imported with errors and
    the IDML file would not open at all, with no message. The writer had been
    re-serialising the whole document through ElementTree, which destroys things
    memoQ put there deliberately and cannot be talked out of — CDATA sections
    (6 and 3 of them), `&quot;` inside tag payloads (204 and 16), raw tabs in
    attribute values, the `xmlns="MQXliff"` on every `<mq:*>` element (210 and
    103), CRLF line endings (2683 and 1331 lines), the BOM, and attribute order.

    Writing by substring replacement makes this invariant hold by construction,
    which is why it is worth asserting: if it ever fails, someone has gone back to
    round-tripping the tree.
    """
    from modules.mqxliff_handler import MQXLIFFHandler

    src = os.path.join(rh.CORPUS, source_file)
    out = tmp_path / "identity.mqxliff"

    handler = MQXLIFFHandler()
    assert handler.load(src)
    assert handler.save(str(out))

    original = open(src, "rb").read()
    written = out.read_bytes()
    assert written == original, (
        f"{source_file}: load→save changed {abs(len(written) - len(original))} bytes")


@pytest.mark.parametrize("source_file", MEMOQ_CORPUS)
def test_memoq_translation_never_touches_a_tag(source_file, tmp_path):
    """Translating must not alter one inline tag — not its id, rid or payload.

    Two separate defects meet here. The translation used to be written *inside*
    tag payloads: a segment that was nothing but `<ph id="1">&lt;tbl linid="0"
    /&gt;</ph>` came out as `⟦<tbl linid="0" />⟧` inside the `ph`, destroying
    memoQ's stored markup, and that is the file memoQ refused to open.

    Separately, targets were built by cloning the *source's* elements — but `rid`
    is scoped to the document, not the trans-unit, and memoQ's own files prove it:
    DOCX unit 59 has `rid="1"` in the source against `rid="2"` in the target, and
    IDML unit 4 has `rid="1"` against `rid="3"`. Cloning invented duplicate rids.
    Reusing the target's own elements avoids both.
    """
    import xml.etree.ElementTree as ET

    from modules.mqxliff_handler import MQXLIFFHandler

    inline = {"bpt", "ept", "ph", "it", "x"}

    def tags(elem):
        return [(e.tag.split("}")[-1], e.get("id"), e.get("rid"), e.text or "")
                for e in elem.iter() if e.tag.split("}")[-1] in inline]

    src = os.path.join(rh.CORPUS, source_file)
    out = tmp_path / "translated.mqxliff"

    handler = MQXLIFFHandler()
    assert handler.load(src)
    segments = handler.extract_source_segments()
    handler.update_target_segments([rh.translate(s.plain_text or "") for s in segments])
    assert handler.save(str(out))

    ns = {"x": "urn:oasis:names:tc:xliff:document:1.2"}
    before = ET.parse(src).getroot().findall(".//x:trans-unit", ns)
    after = ET.parse(str(out)).getroot().findall(".//x:trans-unit", ns)
    assert len(before) == len(after)

    for old, new in zip(before, after):
        for child in ("source", "target"):
            a, b = old.find(f"x:{child}", ns), new.find(f"x:{child}", ns)
            if a is None or b is None:
                continue
            assert tags(a) == tags(b), (
                f"{source_file} unit {old.get('id')}: <{child}> inline tags changed\n"
                f"  before: {tags(a)}\n  after:  {tags(b)}")


@pytest.mark.parametrize("source_file", MEMOQ_CORPUS)
def test_memoq_changes_nothing_outside_the_targets(source_file, tmp_path):
    """Translating may rewrite `<target>` content and `mq:status`. Nothing else.

    Blank out those two and the file must be byte-identical to its input. This is
    the strongest statement available without memoQ itself: whatever else the
    handler gets wrong, it is not silently editing the rest of a customer's file.
    """
    import re as _re

    from modules.mqxliff_handler import MQXLIFFHandler

    src = os.path.join(rh.CORPUS, source_file)
    out = tmp_path / "translated.mqxliff"

    handler = MQXLIFFHandler()
    assert handler.load(src)
    segments = handler.extract_source_segments()
    handler.update_target_segments([rh.translate(s.plain_text or "") for s in segments])
    assert handler.save(str(out))

    def blank(text):
        text = _re.sub(r"(<target\b[^>]*>).*?(</target>)", r"\1\2", text, flags=_re.DOTALL)
        return _re.sub(r'mq:status="[^"]*"', 'mq:status=""', text)

    # Both sides read as bytes and decoded: text mode would apply universal
    # newlines and hide the very CRLF preservation this is here to protect.
    original = blank(open(src, "rb").read().decode("utf-8-sig"))
    written = blank(out.read_bytes().decode("utf-8-sig"))
    assert written == original, f"{source_file}: changed something outside <target>"


@pytest.mark.parametrize("source_file", MEMOQ_CORPUS)
def test_memoq_never_confirms_a_segment_with_no_edit_provenance(source_file, tmp_path):
    """A confirmed segment must carry the provenance memoQ pairs with one.

    Measured across both corpus files: all 51 + 105 ``PartiallyEdited`` units have
    ``mq:lastchanginguser="navi"`` and a real ``mq:lastchangedtimestamp``, while
    the single ``PreTranslated`` unit — IDML trans-unit 26 — has no user attribute
    and ``0001-01-01T00:00:00Z``, .NET's ``DateTime.MinValue``. Confirming that one
    anyway invented a combination memoQ never writes.

    It mattered because it was the *second* thing unique to the IDML file, sitting
    on the same trans-unit as the corrupted ``ph`` payload — the two were
    confounded, so fixing only the tag bug would have left a live candidate for
    the silent open failure. Every DOCX unit already had a user, which is
    consistent with that file importing while the IDML one did not.
    """
    import re as _re

    from modules.mqxliff_handler import MQXLIFFHandler

    src = os.path.join(rh.CORPUS, source_file)
    out = tmp_path / "confirmed.mqxliff"

    handler = MQXLIFFHandler()
    assert handler.load(src)
    segments = handler.extract_source_segments()
    handler.update_target_segments([rh.translate(s.plain_text or "") for s in segments])
    assert handler.save(str(out))

    text = out.read_bytes().decode("utf-8-sig")
    offenders = []
    for match in _re.finditer(r"<trans-unit\b[^>]*>", text):
        tag = match.group(0)
        if 'mq:status="Confirmed"' not in tag:
            continue
        if 'mq:lastchanginguser="' not in tag:
            offenders.append(_re.search(r'id="([^"]*)"', tag).group(1))
        if 'mq:lastchangedtimestamp="0001-01-01T00:00:00Z"' in tag:
            offenders.append(_re.search(r'id="([^"]*)"', tag).group(1) + " (min-value time)")
    assert offenders == [], (
        f"{source_file}: confirmed without edit provenance: {offenders}")


def test_memoq_fills_a_self_closing_empty_target(tmp_path):
    """`<target … />` is what a file that was never pretranslated looks like, and
    both corpus files are pretranslated, so nothing else covers it. Filling one
    means replacing the whole tag rather than its content."""
    from modules.mqxliff_handler import MQXLIFFHandler

    doc = (
        '﻿<?xml version="1.0" encoding="UTF-8"?>\r\n'
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2"'
        ' xmlns:mq="MQXliff">\r\n'
        '<file original="a.docx" source-language="en" target-language="nl">\r\n'
        '<body>\r\n'
        '<trans-unit id="1" mq:status="NotStarted">\r\n'
        '<source xml:space="preserve">Hello <bpt id="1">{}</bpt>world<ept id="1">{}'
        '</ept>.</source>\r\n'
        '<target xml:space="preserve" />\r\n'
        '</trans-unit>\r\n'
        '</body>\r\n</file>\r\n</xliff>\r\n'
    )
    src = tmp_path / "empty.mqxliff"
    src.write_bytes(doc.encode("utf-8"))
    out = tmp_path / "filled.mqxliff"

    handler = MQXLIFFHandler()
    assert handler.load(str(src))
    assert handler.update_target_segments(["Hallo wereld."]) == 1
    assert handler.skipped_segments == []
    assert handler.save(str(out))

    text = out.read_bytes().decode("utf-8-sig")
    assert '<target xml:space="preserve">' in text
    assert "</target>" in text
    assert "<target xml:space=\"preserve\" />" not in text
    # The bpt/ept pair survives, and the translation sits between them because
    # that is the slot which carried the text in the source.
    assert ('<target xml:space="preserve">Hallo wereld.<bpt id="1">{}</bpt>'
            in text) or ('<bpt id="1">{}</bpt>Hallo wereld.<ept id="1">{}</ept>'
                         in text), text
    # Still parseable, and still CRLF and BOM.
    import xml.etree.ElementTree as ET
    ET.parse(str(out))
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in out.read_bytes()


def test_memoq_reports_segments_it_could_not_write(tmp_path):
    """A segment the writer cannot place must be left alone, reported, and *not*
    marked confirmed.

    The old code counted every segment as updated and confirmed it regardless, so
    a file still holding source text in eight of 104 segments looked complete.
    Driving a target whose anchor tag has been deleted exercises the refusal.
    """
    from modules.mqxliff_handler import MQXLIFFHandler

    src = os.path.join(rh.CORPUS, "memoq/CAT_test_IDML.idml_lit.mqxliff")
    handler = MQXLIFFHandler()
    assert handler.load(src)
    segments = handler.extract_source_segments()

    # Strip every visible tag payload out of the translations. The writer can no
    # longer locate its anchors, so it must decline rather than guess.
    damaged = [(s.plain_text or "").replace("<", "").replace(">", "") for s in segments]
    written = handler.update_target_segments(damaged)

    assert handler.skipped_segments, "a translation with its tags removed was accepted"
    assert written == len(segments) - len(handler.skipped_segments)

    out = tmp_path / "damaged.mqxliff"
    assert handler.save(str(out))

    text = out.read_bytes().decode("utf-8-sig")
    original = open(src, "rb").read().decode("utf-8-sig")
    for index, unit_id, _reason in handler.skipped_segments:
        marker = f'<trans-unit id="{unit_id}"'
        assert marker in text
        # The skipped unit's target must be exactly what memoQ wrote.
        def target_of(doc):
            start = doc.index(marker)
            end = doc.index("</trans-unit>", start)
            block = doc[start:end]
            i = block.index("<target")
            return block[i:block.index("</target>", i)]
        assert target_of(text) == target_of(original), (
            f"skipped unit {unit_id} was modified anyway")


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
