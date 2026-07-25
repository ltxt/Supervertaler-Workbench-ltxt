"""Tests for the grid-cell protected-tag seam in Supervertaler.py.

Phase 1 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md.

``apply_grid_cell_text`` / ``read_grid_cell_text`` are the only two places in
the grid that know whether tags are protected. The contract they must honour is
that ``read_grid_cell_text`` is a **strict drop-in** for ``toPlainText()``:
with protection off — or on a cell with no tags — it returns exactly the same
string. Everything downstream (compact-tag expansion, invisible-marker
stripping, outer-tag re-attachment) depends on that.

Importing Supervertaler.py itself would build the whole application, so the two
helpers are loaded in isolation: their source is extracted and executed against
stub editor classes. That keeps the test honest — it runs the real code — while
staying headless.
"""

import ast
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 required for the atom layer")

from PyQt6.QtGui import QTextDocument  # noqa: E402
from PyQt6.QtWidgets import QApplication, QTextEdit  # noqa: E402

from modules import tag_atoms as _tag_atoms  # noqa: E402
from modules import tag_protection as _tp  # noqa: E402

REQUEST_EXAMPLE = (
    'Specify <cf color="#227acb" font="tahoma"><b>Wall thickness </b></cf>'
    '<b>range </b><g3>②</g3>'
)

SEAM_FUNCTIONS = (
    "_protected_tags_active",
    "_tag_detail_level",
    "apply_grid_cell_text",
    "read_grid_cell_text",
    "sync_tag_atom_color",
)


@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def seam(qapp):
    """The real seam functions from Supervertaler.py, executed in isolation.

    Supervertaler.py cannot be imported in a test (it constructs the app), so
    the relevant top-level functions are located in its AST and exec'd against
    a namespace holding a stand-in for the editor class that carries the flags.
    """
    source = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    class _FlagHolder:
        tag_protection_enabled = False
        tag_detail_level = _tag_atoms.DETAIL_MEDIUM
        # What atoms actually render with; derived from the display mode.
        tag_effective_detail = _tag_atoms.DETAIL_MEDIUM

    ns = {
        "_tag_atoms": _tag_atoms,
        "_tag_protection": _tp,
        "EditableGridTextEditor": _FlagHolder,
        "print": lambda *a, **k: None,
    }

    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in SEAM_FUNCTIONS:
            segment = "".join(lines[node.lineno - 1:node.end_lineno])
            exec(compile(segment, "Supervertaler.py", "exec"), ns)
            found.add(node.name)

    missing = set(SEAM_FUNCTIONS) - found
    assert not missing, f"seam functions not found in Supervertaler.py: {missing}"
    ns["_FlagHolder"] = _FlagHolder
    return ns


@pytest.fixture
def protection(seam):
    """Turn protection on for a test and always restore it afterwards."""
    holder = seam["_FlagHolder"]

    class _Switch:
        def on(self, detail=_tag_atoms.DETAIL_MEDIUM):
            holder.tag_protection_enabled = True
            holder.tag_detail_level = detail
            holder.tag_effective_detail = detail

        def off(self):
            holder.tag_protection_enabled = False

    holder.tag_protection_enabled = False
    holder.tag_detail_level = _tag_atoms.DETAIL_MEDIUM
    holder.tag_effective_detail = _tag_atoms.DETAIL_MEDIUM
    yield _Switch()
    holder.tag_protection_enabled = False
    holder.tag_detail_level = _tag_atoms.DETAIL_MEDIUM
    holder.tag_effective_detail = _tag_atoms.DETAIL_MEDIUM


@pytest.fixture
def edit(qapp):
    return QTextEdit()


# ---------------------------------------------------------------------------
# The drop-in contract
# ---------------------------------------------------------------------------

SAMPLES = [
    REQUEST_EXAMPLE,
    "plain text with no tags",
    "",
    "<b>bold</b>",
    "Trados <1>x</1> and <2/>",
    "[1}memoQ{1]",
    "multi\nline <b>text</b>",
    "prose with if x < y and y > z",
]


@pytest.mark.parametrize("raw", SAMPLES)
def test_readback_matches_plaintext_when_protection_is_off(
        seam, protection, edit, raw):
    protection.off()
    seam["apply_grid_cell_text"](edit, raw)
    assert edit.toPlainText() == raw
    assert seam["read_grid_cell_text"](edit) == raw


@pytest.mark.parametrize("raw", SAMPLES)
def test_readback_is_lossless_when_protection_is_on(
        seam, protection, edit, raw):
    protection.on()
    seam["apply_grid_cell_text"](edit, raw)
    assert seam["read_grid_cell_text"](edit) == raw


@pytest.mark.parametrize("raw", SAMPLES)
@pytest.mark.parametrize("detail", _tag_atoms.DETAIL_LEVELS)
def test_readback_is_lossless_at_every_detail_level(
        seam, protection, edit, raw, detail):
    protection.on(detail)
    seam["apply_grid_cell_text"](edit, raw)
    assert seam["read_grid_cell_text"](edit) == raw


def test_protection_off_is_byte_identical_to_the_old_behaviour(
        seam, protection, edit):
    """The regression guard: with the flag off nothing may change at all."""
    protection.off()
    for raw in SAMPLES:
        reference = QTextEdit()
        reference.setPlainText(raw)          # exactly what the code did before
        seam["apply_grid_cell_text"](edit, raw)
        assert edit.toPlainText() == reference.toPlainText()
        assert seam["read_grid_cell_text"](edit) == reference.toPlainText()


# ---------------------------------------------------------------------------
# The invariant that matters most: U+FFFC must never reach a segment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", SAMPLES)
def test_object_replacement_char_never_escapes_into_segment_text(
        seam, protection, edit, raw):
    protection.on()
    seam["apply_grid_cell_text"](edit, raw)
    assert _tag_atoms.OBJECT_REPLACEMENT_CHARACTER not in seam[
        "read_grid_cell_text"](edit)


def test_readback_after_editing_a_protected_cell(seam, protection, edit):
    protection.on()
    seam["apply_grid_cell_text"](edit, "<b>Wall</b>")
    cursor = edit.textCursor()
    cursor.setPosition(1)
    cursor.insertText("Muur en ")
    assert seam["read_grid_cell_text"](edit) == "<b>Muur en Wall</b>"
    assert _tag_atoms.OBJECT_REPLACEMENT_CHARACTER not in seam[
        "read_grid_cell_text"](edit)


def test_readback_after_deleting_a_tag_in_a_protected_cell(
        seam, protection, edit):
    protection.on()
    seam["apply_grid_cell_text"](edit, "AB<b>CD")
    cursor = edit.textCursor()
    cursor.setPosition(3)
    cursor.deletePreviousChar()
    assert seam["read_grid_cell_text"](edit) == "ABCD"


# ---------------------------------------------------------------------------
# Composition with the rest of the write-back chain
# ---------------------------------------------------------------------------

def test_composes_with_compact_placeholders(seam, protection, edit):
    """A cell in compact view holds {1}-style placeholders. Those are a tag
    family in their own right, so they are protected too, and the read-back
    still yields the placeholders that expand_compact_tags() expects."""
    protection.on()
    display = "Specify {1}Wall{/1}"
    seam["apply_grid_cell_text"](edit, display)
    assert seam["read_grid_cell_text"](edit) == display


def test_composes_with_invisible_markers(seam, protection, edit):
    """Invisible-character markers are ordinary text and must survive."""
    protection.on()
    display = "Wall·thickness <b>x</b>"
    seam["apply_grid_cell_text"](edit, display)
    assert seam["read_grid_cell_text"](edit) == display


# ---------------------------------------------------------------------------
# Detail level and colour plumbing
# ---------------------------------------------------------------------------

def test_detail_level_reaches_the_atoms(seam, protection, edit):
    protection.on(_tag_atoms.DETAIL_LONG)
    seam["apply_grid_cell_text"](edit, '<cf color="#227acb">x</cf>')
    cursor = edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(1, cursor.MoveMode.KeepAnchor)
    label = cursor.charFormat().property(_tag_atoms.PROP_LABEL)
    assert label == '<cf color="#227acb">'


def test_detail_level_defaults_to_medium(seam, protection, edit):
    """A bare number says nothing about what a tag does, so the default Partial
    level shows the tag name instead."""
    protection.on()
    assert seam["_tag_detail_level"]() == _tag_atoms.DETAIL_MEDIUM


def test_sync_tag_atom_color_updates_the_renderer(seam):
    seam["sync_tag_atom_color"]("#00aa00")
    assert _tag_atoms.renderer().text_color.name() == "#00aa00"
    seam["sync_tag_atom_color"]("#7f0001")


def test_sync_tag_atom_color_survives_nonsense(seam):
    seam["sync_tag_atom_color"]("#7f0001")
    seam["sync_tag_atom_color"](None)
    assert _tag_atoms.renderer().text_color.name() == "#7f0001"


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

def test_falls_back_to_plain_text_if_the_atom_layer_fails(
        seam, protection, edit, monkeypatch):
    """A failure in the atom layer must degrade to today's behaviour, never
    lose the segment."""
    protection.on()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated atom failure")

    monkeypatch.setattr(_tag_atoms, "install_atoms", boom)
    seam["apply_grid_cell_text"](edit, REQUEST_EXAMPLE)
    assert edit.toPlainText() == REQUEST_EXAMPLE


def test_readback_falls_back_when_serialisation_fails(
        seam, protection, edit, monkeypatch):
    protection.on()
    seam["apply_grid_cell_text"](edit, "<b>x</b>")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated serialisation failure")

    monkeypatch.setattr(_tag_atoms, "document_to_raw", boom)
    # Must not raise; returns the plain text as a last resort.
    assert isinstance(seam["read_grid_cell_text"](edit), str)


# ---------------------------------------------------------------------------
# The seam is actually wired in
# ---------------------------------------------------------------------------

def test_write_back_paths_use_the_seam_not_toplaintext():
    """Guard against a future edit reintroducing a raw toPlainText() read on
    the two segment write-back paths, which would store U+FFFC."""
    source = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()

    # Target-cell handler.
    assert "new_text = read_grid_cell_text(editor_widget)" in source
    # Source-cell handler (v1.10.229 F2 / allow-replace-in-source path).
    assert source.count("read_grid_cell_text(editor_widget)") >= 2


def test_editors_apply_text_through_the_seam():
    source = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()
    assert "apply_grid_cell_text(self, text)" in source
    # Both editors' update_display_mode plus both constructors.
    assert source.count("apply_grid_cell_text(self, text)") >= 3


def test_protection_flag_defaults_to_on():
    """Protection is the default now that the pills have been reviewed in the
    running application. Turning it off restores plain-text tag editing."""
    source = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()
    assert re.search(r"^\s+tag_protection_enabled = True\s*$", source,
                     re.MULTILINE)


def test_no_grid_cell_bypasses_the_seam():
    """The audit, locked in.

    Every read of a grid cell must go through read_grid_cell_text() or it would
    return U+FFFC for each protected tag, and every write must go through
    apply_grid_cell_text() or it would silently strip the protection. The two
    exempt call sites are the word-selection helpers, which deliberately work
    in *display* coordinates where one atom is correctly one character.
    """
    source = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()
    offenders = re.findall(
        r"(?:target_widget|source_widget)\.(?:toPlainText|setPlainText)\(",
        source)
    assert offenders == [], (
        f"{len(offenders)} grid-cell call(s) bypass the protected-tag seam; "
        "use apply_grid_cell_text()/read_grid_cell_text() instead")


def test_display_coordinate_helpers_still_use_plain_text():
    """Counterpart to the guard above: select_word_under_cursor and the
    select-all helper must NOT be converted — they index into the displayed
    text, where a tag atom is one character."""
    source = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()
    assert "text = widget.toPlainText()" in source
    assert "if not widget.toPlainText():" in source
