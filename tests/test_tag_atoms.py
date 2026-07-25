"""Tests for atomic (protected) inline tags — modules/tag_atoms.py.

Phase 1 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md.

These exercise real Qt text documents headlessly (offscreen platform). The
central property under test is that a protected tag behaves as ONE indivisible
character while the segment text round-trips byte-for-byte.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 required for the atom layer")

from PyQt6.QtGui import QTextCursor, QTextDocument  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from modules import tag_protection as tp  # noqa: E402
from modules.tag_atoms import (  # noqa: E402
    DETAIL_FILTERED,
    DETAIL_LEVELS,
    DETAIL_LONG,
    DETAIL_MEDIUM,
    DETAIL_SHORT,
    OBJECT_REPLACEMENT_CHARACTER,
    TAG_OBJECT_TYPE,
    atom_label,
    atom_tokens,
    document_to_raw,
    has_atoms,
    insert_tag_atom,
    install_atoms,
    register_document,
    relabel_atoms,
    renderer,
)

OBJ = OBJECT_REPLACEMENT_CHARACTER

REQUEST_EXAMPLE = (
    'Specify <cf color="#227acb" font="tahoma"><b>Wall thickness </b></cf>'
    '<b>range </b><g3>②</g3>'
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def doc(qapp):
    d = QTextDocument()
    register_document(d)
    return d


# ---------------------------------------------------------------------------
# Round-trip: the invariant everything else depends on
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    REQUEST_EXAMPLE,
    "no tags at all",
    "",
    "<b>bold</b>",
    "leading <b>tag",
    "<b>trailing tag</b>",
    "<b></b>",                                   # adjacent pair, nothing between
    "<b><b>",                                    # identical adjacent tags
    "a<br/>b<br/>c",
    "[1}memoQ{1] and [3]",
    "Trados <1>x</1> plus <2/>",
    "Vind {00108}jouw CS{00109} hier",
    '<cf color="#227acb" font="tahoma">attrs</cf>',
    "<li-o>hyphenated</li-o>",
    "multi\nline <b>text</b>\nhere",
    "unicode ② ünïcödé <b>ok</b>",
])
def test_round_trip_is_lossless(doc, raw):
    install_atoms(doc, raw)
    assert document_to_raw(doc) == raw


def test_round_trip_at_every_detail_level(doc):
    for detail in DETAIL_LEVELS:
        install_atoms(doc, REQUEST_EXAMPLE, detail=detail)
        assert document_to_raw(doc) == REQUEST_EXAMPLE, detail


def test_identical_adjacent_tags_both_survive(doc):
    """Qt can merge two adjacent runs sharing a format into ONE fragment, so a
    naive per-fragment serialiser would emit the markup only once."""
    install_atoms(doc, "<b><b>x")
    assert document_to_raw(doc) == "<b><b>x"
    assert len(atom_tokens(doc)) == 2


# ---------------------------------------------------------------------------
# Atomicity — the point of the exercise
# ---------------------------------------------------------------------------

def test_tag_occupies_exactly_one_document_position(doc):
    install_atoms(doc, "AB<cf color='#227acb'>CD")
    assert doc.toPlainText() == f"AB{OBJ}CD"


def test_one_backspace_removes_the_whole_tag(doc):
    install_atoms(doc, "AB<cf color='#227acb'>CD")
    cursor = QTextCursor(doc)
    cursor.setPosition(3)          # just past the atom
    cursor.deletePreviousChar()
    assert document_to_raw(doc) == "ABCD"


def test_one_delete_removes_the_whole_tag(doc):
    install_atoms(doc, "AB<b>CD")
    cursor = QTextCursor(doc)
    cursor.setPosition(2)          # just before the atom
    cursor.deleteChar()
    assert document_to_raw(doc) == "ABCD"


def test_arrow_key_steps_over_the_tag_as_one_unit(doc):
    install_atoms(doc, "AB<b>CD")
    cursor = QTextCursor(doc)
    cursor.setPosition(2)
    cursor.movePosition(QTextCursor.MoveOperation.Right)
    assert cursor.position() == 3   # 2 -> 3, the atom consumed one step


def test_caret_cannot_be_placed_inside_a_tag(doc):
    """There is no position 'inside' the atom: the tag spans [2, 3) only."""
    install_atoms(doc, "AB<cf color='#227acb' font='tahoma'>CD")
    positions = {a["position"] for a in atom_tokens(doc)}
    assert positions == {2}
    assert doc.characterCount() - 1 == len("AB") + 1 + len("CD")


def test_typing_next_to_a_tag_cannot_corrupt_it(doc):
    install_atoms(doc, "<b>x</b>")
    cursor = QTextCursor(doc)
    cursor.setPosition(1)          # between the opening atom and 'x'
    cursor.insertText("TYPED")
    assert document_to_raw(doc) == "<b>TYPEDx</b>"


def test_selecting_across_a_tag_and_deleting_removes_it_whole(doc):
    install_atoms(doc, "AB<b>CD</b>EF")
    cursor = QTextCursor(doc)
    cursor.setPosition(1)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    assert "<" not in document_to_raw(doc).replace("<b>", "").replace("</b>", "")


def test_partial_tag_text_can_never_appear_in_readback(doc):
    """The failure mode protection exists to prevent: a tag mangled into '<>'.
    Deleting any single position must never leave a fragment of markup."""
    install_atoms(doc, "x<cf color='#227acb'>y")
    for pos in range(1, doc.characterCount() - 1):
        d = QTextDocument()
        register_document(d)
        install_atoms(d, "x<cf color='#227acb'>y")
        cursor = QTextCursor(d)
        cursor.setPosition(pos)
        cursor.deletePreviousChar()
        raw = document_to_raw(d)
        assert raw in ("<cf color='#227acb'>y", "xy",
                       "x<cf color='#227acb'>"), raw


# ---------------------------------------------------------------------------
# Display detail levels (Partial / Full and the two in between)
# ---------------------------------------------------------------------------

def _labels(raw, detail):
    toks = tp.parse_tags(raw, number=True)
    return [atom_label(t, detail) for t in toks]


def test_short_labels_are_numbers_with_role():
    """Open = "1", close = "/1", empty = "2/" — the pair shares a number and the
    standalone tag gets its own, as memoQ documents."""
    assert _labels("<b>x</b><br/>", DETAIL_SHORT) == ["1", "/1", "2/"]


def test_short_label_marks_empty_tags_distinctly():
    assert _labels("<br/>", DETAIL_SHORT) == ["1/"]


def test_medium_labels_are_type_and_name_without_attributes():
    assert _labels('<cf color="#227acb">x</cf>', DETAIL_MEDIUM) == ["cf", "/cf"]


def test_long_labels_are_the_complete_markup():
    assert _labels('<cf color="#227acb">x</cf>', DETAIL_LONG) == [
        '<cf color="#227acb">', "</cf>"]


def test_filtered_labels_keep_selected_attributes_only():
    labels = _labels('<cf color="#227acb" font="tahoma">x</cf>', DETAIL_FILTERED)
    assert labels[0] == 'cf color="#227acb"'      # font is not in the allowlist
    assert labels[1] == "/cf"


def test_pair_shares_its_number_in_short_view():
    labels = _labels(REQUEST_EXAMPLE, DETAIL_SHORT)
    # <cf>=1 <b>=2 </b>=/2 </cf>=/1 <b>=3 </b>=/3 <g3>=4 </g3>=/4
    assert labels == ["1", "2", "/2", "/1", "3", "/3", "4", "/4"]


def test_relabel_switches_detail_without_touching_text(doc):
    install_atoms(doc, REQUEST_EXAMPLE, detail=DETAIL_SHORT)
    before = doc.toPlainText()
    changed = relabel_atoms(doc, DETAIL_LONG)
    assert changed == 8
    assert doc.toPlainText() == before          # same characters
    assert document_to_raw(doc) == REQUEST_EXAMPLE


def test_relabel_preserves_load_time_numbers(doc):
    """memoQ renumbers only on reopen; switching view must not renumber."""
    install_atoms(doc, REQUEST_EXAMPLE, detail=DETAIL_SHORT)
    before = [a["number"] for a in atom_tokens(doc)]
    relabel_atoms(doc, DETAIL_MEDIUM)
    assert [a["number"] for a in atom_tokens(doc)] == before


# ---------------------------------------------------------------------------
# Atom metadata
# ---------------------------------------------------------------------------

def test_atom_tokens_reports_markup_kind_and_number(doc):
    install_atoms(doc, "<b>x</b>")
    atoms = atom_tokens(doc)
    assert [(a["raw"], a["kind"], a["number"]) for a in atoms] == [
        ("<b>", tp.KIND_OPEN, 1),
        ("</b>", tp.KIND_CLOSE, 1),
    ]


def test_atom_format_carries_a_tooltip(doc):
    """Hover detail for Partial view rides on the atom's char format."""
    install_atoms(doc, '<cf color="#227acb">x</cf>', detail=DETAIL_SHORT)
    cursor = QTextCursor(doc)
    cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    tip = cursor.charFormat().toolTip()
    assert '<cf color="#227acb">' in tip
    assert "opening" in tip


def test_has_atoms(doc):
    install_atoms(doc, "<b>x</b>")
    assert has_atoms(doc)
    install_atoms(doc, "plain")
    assert not has_atoms(doc)


def test_properties_do_not_leak_onto_following_text(doc):
    """Qt carries a char format forward, so text typed after an atom would
    inherit its properties unless the format is reset."""
    install_atoms(doc, "<b>tail")
    it = doc.begin().begin()
    frags = []
    while not it.atEnd():
        frags.append(it.fragment())
        it += 1
    text_frag = [f for f in frags if f.text() == "tail"][0]
    assert text_frag.charFormat().objectType() != TAG_OBJECT_TYPE


def test_atom_count_returned_by_install(doc):
    assert install_atoms(doc, REQUEST_EXAMPLE) == 8
    assert install_atoms(doc, "no tags") == 0


# ---------------------------------------------------------------------------
# Insertion (used by the Ctrl+, shortcut once protection is on)
# ---------------------------------------------------------------------------

def test_insert_tag_atom_inserts_a_protected_tag(doc):
    install_atoms(doc, "AB")
    cursor = QTextCursor(doc)
    cursor.setPosition(2)
    assert insert_tag_atom(cursor, "<b>") is True
    assert document_to_raw(doc) == "AB<b>"
    assert len(atom_tokens(doc)) == 1


def test_insert_tag_atom_supports_standalone_tags(doc):
    """The Phase 0 fix, end to end: a standalone tag can now be inserted."""
    install_atoms(doc, "AB")
    cursor = QTextCursor(doc)
    cursor.setPosition(2)
    assert insert_tag_atom(cursor, "<2/>") is True
    assert document_to_raw(doc) == "AB<2/>"


def test_insert_tag_atom_rejects_non_tags(doc):
    install_atoms(doc, "AB")
    cursor = QTextCursor(doc)
    assert insert_tag_atom(cursor, "not a tag") is False
    assert insert_tag_atom(cursor, "a < b") is False


def test_inserted_atom_is_also_indivisible(doc):
    install_atoms(doc, "AB")
    cursor = QTextCursor(doc)
    cursor.setPosition(2)
    insert_tag_atom(cursor, '<cf color="#227acb">')
    cursor.setPosition(3)
    cursor.deletePreviousChar()
    assert document_to_raw(doc) == "AB"


# ---------------------------------------------------------------------------
# Structured origin: caller supplies the tokens
# ---------------------------------------------------------------------------

def test_caller_supplied_tokens_are_used_verbatim(doc):
    """A structured import knows its own tags; parsing must not be re-imposed."""
    raw = "<1>Wall</1>"
    tokens = tp.parse_tags(raw, origin=tp.ORIGIN_STRUCTURED, number=True)
    install_atoms(doc, raw, tokens=tokens)
    assert document_to_raw(doc) == raw
    assert len(atom_tokens(doc)) == 2


def test_text_that_only_looks_like_markup_is_left_alone(doc):
    """Requirement 2, at the rendering layer: prose is not protected."""
    raw = "if x < y and y > z"
    install_atoms(doc, raw)
    assert not has_atoms(doc)
    assert document_to_raw(doc) == raw
    assert doc.toPlainText() == raw


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def test_renderer_is_a_shared_singleton(qapp):
    assert renderer() is renderer()


def test_renderer_colour_can_be_themed(qapp):
    r = renderer()
    r.set_color("#0000ff")
    assert r.text_color.name() == "#0000ff"
    r.set_color("not a colour")     # invalid input must not wipe the theme
    assert r.text_color.name() == "#0000ff"
    r.set_color("#7f0001")


def test_intrinsic_size_grows_with_label_length(doc):
    install_atoms(doc, '<cf color="#227acb">x</cf>', detail=DETAIL_SHORT)
    cursor = QTextCursor(doc)
    cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    short_fmt = cursor.charFormat()
    small = renderer().intrinsicSize(doc, 0, short_fmt)

    install_atoms(doc, '<cf color="#227acb">x</cf>', detail=DETAIL_LONG)
    cursor = QTextCursor(doc)
    cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    big = renderer().intrinsicSize(doc, 0, cursor.charFormat())

    assert big.width() > small.width()
    assert small.height() > 0
