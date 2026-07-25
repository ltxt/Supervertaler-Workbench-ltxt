"""The tag model against segments from real CAT-tool exports.

The synthetic tests elsewhere cover the shapes we thought to write down. This
file runs the parser, the atom layer and verification over source text extracted
from genuine Trados, memoQ and Phrase files, which is where the assumptions
actually got tested.

Two defects were found this way and are pinned below:

* ``</cmt id="0" transform="close">`` — memoQ emits closing tags **with**
  attributes. The parser rejected those as prose, leaving the opener unpaired
  and unprotected.
* ``e^{x_i} / Σ_j e^{x_j}`` — a formula in a real IDML export was read as two
  memoQ content closing tags. Protecting those would have made the formula
  uneditable.

The fixture holds extracted *text only* (tests/fixtures/real_cat_tool_segments.json),
not the source documents, so it stays small and carries no document content
beyond the tagged segments the parser is being tested on.
"""

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from modules import tag_protection as tp  # noqa: E402

FIXTURE = os.path.join(REPO, "tests", "fixtures", "real_cat_tool_segments.json")

with open(FIXTURE, encoding="utf-8") as fh:
    CORPUS = json.load(fh)

ALL_SEGMENTS = [(origin, text)
                for origin, texts in CORPUS.items()
                for text in texts]


def test_fixture_covers_the_three_bilingual_formats():
    origins = " ".join(CORPUS)
    for expected in ("trados", "memoq", "phrase"):
        assert expected in origins
    assert len(ALL_SEGMENTS) >= 30


# ---------------------------------------------------------------------------
# The invariant: tokens must always reassemble into the original text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin,text", ALL_SEGMENTS,
                         ids=[f"{o.split('/')[0]}-{i}"
                              for i, (o, _) in enumerate(ALL_SEGMENTS)])
def test_tokens_reassemble_into_the_original_segment(origin, text):
    out, pos = [], 0
    for token in tp.parse_tags(text):
        out.append(text[pos:token.start])
        out.append(token.raw)
        pos = token.end
    out.append(text[pos:])
    assert "".join(out) == text


@pytest.mark.parametrize("origin,text", ALL_SEGMENTS,
                         ids=[f"{o.split('/')[0]}-{i}"
                              for i, (o, _) in enumerate(ALL_SEGMENTS)])
def test_every_real_segment_has_at_least_one_tag(origin, text):
    """The fixture is filtered to tagged segments, so a parser change that
    stops recognising a real tag family shows up here."""
    assert tp.parse_tags(text), f"no tags recognised in {origin}: {text[:70]!r}"


# ---------------------------------------------------------------------------
# The atom layer, on real text
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def atom_doc(qapp):
    """One document reused across the corpus.

    Creating and dropping a QTextDocument per test while a custom object handler
    is registered on its layout aborts the interpreter, so the document is built
    once and refilled.
    """
    from PyQt6.QtGui import QTextDocument

    from modules import tag_atoms as ta

    doc = QTextDocument()
    ta.register_document(doc)
    return doc


def test_atoms_round_trip_every_real_segment(atom_doc):
    """Every real segment, at every detail level, must come back byte-exact and
    must never leak the object-replacement character into segment text."""
    from modules import tag_atoms as ta

    checked = 0
    for origin, text in ALL_SEGMENTS:
        for detail in ta.DETAIL_LEVELS:
            ta.install_atoms(atom_doc, text, detail=detail)
            back = ta.document_to_raw(atom_doc)
            assert back == text, f"{origin} at {detail}: {back[:70]!r}"
            assert ta.OBJECT_REPLACEMENT_CHARACTER not in back
            checked += 1
    assert checked == len(ALL_SEGMENTS) * len(ta.DETAIL_LEVELS)


# ---------------------------------------------------------------------------
# Verification against a real target
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin,text", ALL_SEGMENTS,
                         ids=[f"{o.split('/')[0]}-{i}"
                              for i, (o, _) in enumerate(ALL_SEGMENTS)])
def test_copying_source_to_target_verifies_clean(origin, text):
    """Copy-source-to-target is a real workflow; it must never report a problem."""
    assert tp.verify_tags(text, text) == []


@pytest.mark.parametrize("origin,text", ALL_SEGMENTS,
                         ids=[f"{o.split('/')[0]}-{i}"
                              for i, (o, _) in enumerate(ALL_SEGMENTS)])
def test_inserting_every_sequence_satisfies_verification(origin, text):
    """Driving the Ctrl+, shortcut to exhaustion on a real segment must end with
    a target that verification accepts."""
    target = ""
    for _ in range(60):
        nxt = tp.next_tag_sequence(text, target)
        if not nxt:
            break
        target += nxt
    assert tp.next_tag_sequence(text, target) == ""
    kinds = {i.kind for i in tp.verify_tags(text, target)}
    assert kinds <= {tp.ISSUE_REORDERED}, kinds


@pytest.mark.parametrize("origin,text", ALL_SEGMENTS,
                         ids=[f"{o.split('/')[0]}-{i}"
                              for i, (o, _) in enumerate(ALL_SEGMENTS)])
def test_dropping_a_tag_is_always_detected(origin, text):
    """Remove the first tag from a copy of the source; verification must notice.

    A segment that is nothing but a tag (``<tbl linid="0" />`` appears in the
    IDML export) would leave an empty target, which verification skips on
    purpose — an untranslated segment must not be reported. Standing in a word
    keeps such a segment in scope as a real translation.
    """
    tokens = tp.parse_tags(text)
    first = tokens[0]
    damaged = text[:first.start] + text[first.end:]
    if not damaged.strip():
        damaged = "vertaald"
    issues = tp.verify_tags(text, damaged)
    assert issues, f"dropping {first.raw!r} went unnoticed in {origin}"


# ---------------------------------------------------------------------------
# The two defects real data exposed
# ---------------------------------------------------------------------------

def test_memoq_attributed_closing_tag_is_recognised():
    """From memoq/CAT_test_DOCX.docx_lit.mqxliff."""
    found = [t for texts in CORPUS.values() for t in texts if "<cmt " in t]
    assert found, "fixture no longer contains the memoQ comment segment"
    tokens = tp.parse_tags(found[0], number=True)
    kinds = [(t.kind, t.number) for t in tokens]
    assert (tp.KIND_OPEN, 1) in kinds
    assert (tp.KIND_CLOSE, 1) in kinds, "attributed closer not paired"


def test_maths_in_a_real_export_is_not_treated_as_tags():
    """From memoq/CAT_test_IDML.idml_lit.mqxliff."""
    formula = "Equation (text): softmax(x)_i = e^{x_i} / Σ_j e^{x_j}."
    assert tp.parse_tags(formula) == []


def test_real_standalone_tags_are_visible_to_the_insert_shortcut():
    """"Page <1/> of <3/>" is a real Trados segment. Before the Phase 0 fix,
    extract_all_tags could not see self-closing tags, so Ctrl+, refused to
    insert either one and reported that all tags were already placed."""
    segment = "Page <1/> of <3/>"
    assert tp.next_tag_sequence(segment, "") == "<1/>"
    assert tp.next_tag_sequence(segment, "Pagina <1/> van ") == "<3/>"


# ---------------------------------------------------------------------------
# Software placeholders (Phrase)
# ---------------------------------------------------------------------------

def test_phrase_placeholders_have_their_own_family():
    """{0}-style placeholders look identical to Supervertaler's internal compact
    display placeholders but are a different thing: they belong to the source
    document and must survive into the translation. Sharing a family meant they
    were classified as display artefacts, and as opening tags rather than
    standalone ones."""
    phrase = [t for k, v in CORPUS.items() if "phrase" in k for t in v]
    with_placeholders = [t for t in phrase if tp.parse_tags(t) and all(
        x.family == tp.FAMILY_PLACEHOLDER for x in tp.parse_tags(t))]
    assert with_placeholders, "fixture no longer holds a placeholder-only segment"
    for text in with_placeholders:
        for token in tp.parse_tags(text, number=True):
            assert token.family == tp.FAMILY_PLACEHOLDER
            assert token.kind == tp.KIND_EMPTY, "a placeholder never pairs"


def test_placeholders_render_verbatim_at_every_level():
    """Showing {0} as "1" because it is the first tag in the segment would be
    actively wrong — the number is part of the placeholder's meaning."""
    from modules import tag_atoms as ta

    token = tp.parse_tags("Betrag {0} und {7}", number=True)
    assert [t.raw for t in token] == ["{0}", "{7}"]
    for detail in ta.DETAIL_LEVELS:
        assert [ta.atom_label(t, detail) for t in token] == ["{0}", "{7}"]


def test_dropping_a_placeholder_is_reported():
    source = "Wir konnten {0} {1} aus Rechnung {2} nicht zuordnen"
    issues = tp.verify_tags(source, "Kon {1} uit factuur {2} niet toewijzen")
    assert [i.tag for i in issues if i.kind == tp.ISSUE_MISSING] == ["{0}"]
