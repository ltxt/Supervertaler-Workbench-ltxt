"""Tests for the tag-protection settings plumbing.

Phase 4 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md.

Two settings: `tag_protection_enabled` (whether each inline tag behaves as one
unbreakable unit) and `tag_detail_level` (how much of a tag the "Partial tags"
view shows). Full tags always means every attribute, so the configured level
applies only to Partial — which is what keeps a toolbar click from overwriting
the user's Settings choice.
"""

import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from modules import tag_atoms as ta  # noqa: E402

SOURCE = open(os.path.join(REPO, "Supervertaler.py"), encoding="utf-8").read()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_protection_defaults_to_on():
    """Protection is the default. Turning the setting off restores plain-text
    tag editing for anyone who prefers it."""
    assert re.search(r"^\s+tag_protection_enabled = True\s*$", SOURCE, re.MULTILINE)
    assert "general_settings.get('tag_protection_enabled', True)" in SOURCE


def test_partial_detail_defaults_to_medium():
    """memoQ defaults to its Filtered level. Supervertaler has no per-format
    attribute source, so Medium (tag name, no attributes) is the default — a
    bare number says nothing about what the tag does."""
    assert re.search(r"^\s+tag_detail_level = _tag_atoms\.DETAIL_MEDIUM\s*$",
                     SOURCE, re.MULTILINE)


# ---------------------------------------------------------------------------
# The settings widgets exist and are wired end to end
# ---------------------------------------------------------------------------

def test_protection_checkbox_is_created_and_saved():
    assert "tag_protection_check = CheckmarkCheckBox(" in SOURCE
    assert "general_settings['tag_protection_enabled'] = protection_on" in SOURCE
    assert "EditableGridTextEditor.tag_protection_enabled = protection_on" in SOURCE


def test_detail_combo_is_created_and_saved():
    assert "tag_detail_combo = QComboBox()" in SOURCE
    assert "general_settings['tag_detail_level'] = detail" in SOURCE
    assert "EditableGridTextEditor.tag_detail_level = detail" in SOURCE


def test_detail_combo_offers_only_the_levels_partial_view_can_render():
    """Long is reached with the Full tags button, and Filtered needs a
    per-format definition of which attributes matter that Supervertaler does
    not have — a hardcoded allowlist was rejected as a stand-in."""
    combo = SOURCE[SOURCE.index("tag_detail_combo = QComboBox()"):]
    combo = combo[:combo.index("tag_detail_combo.setToolTip")]
    assert "_tag_atoms.DETAIL_SHORT" in combo
    assert "_tag_atoms.DETAIL_MEDIUM" in combo
    assert "_tag_atoms.DETAIL_FILTERED" not in combo
    assert "_tag_atoms.DETAIL_LONG" not in combo


def test_saved_detail_value_is_validated_before_use():
    """A hand-edited settings file must not be able to set a bogus level."""
    assert "if detail in _tag_atoms.DETAIL_LEVELS:" in SOURCE
    assert "if saved_detail in _tag_atoms.DETAIL_LEVELS:" in SOURCE


def test_widgets_are_threaded_through_both_save_signatures():
    assert SOURCE.count(
        "tag_protection_check=None, tag_detail_combo=None):") == 2
    assert SOURCE.count(
        "tag_protection_check=tag_protection_check, tag_detail_combo=tag_detail_combo") == 2


def test_both_settings_are_restored_at_startup():
    assert "general_settings.get('tag_protection_enabled', True)" in SOURCE
    assert "general_settings.get('tag_detail_level')" in SOURCE


# ---------------------------------------------------------------------------
# Live refresh: protection needs a rebuild, detail alone does not
# ---------------------------------------------------------------------------

def test_toggling_protection_rebuilds_the_grid():
    """Turning protection on or off changes what each cell's document
    *contains* — atoms rather than characters — so a re-label is not enough."""
    assert "if _new_protection != _old_tag_protection:" in SOURCE
    block = SOURCE[SOURCE.index("if _new_protection != _old_tag_protection:"):]
    assert "self.load_segments_to_grid()" in block[:600]


def test_changing_only_the_detail_level_just_relabels():
    assert "elif _new_detail != _old_tag_detail and self.current_project:" in SOURCE
    block = SOURCE[SOURCE.index("elif _new_detail != _old_tag_detail"):]
    assert "self._refresh_grid_display_mode()" in block[:300]


def test_refresh_only_runs_when_a_value_actually_changed():
    """The existing settings code snapshots old values so expensive grid loops
    are skipped on an unrelated save; the new settings follow that."""
    assert "_old_tag_protection = getattr(EditableGridTextEditor" in SOURCE
    assert "_old_tag_detail = getattr(EditableGridTextEditor" in SOURCE


# ---------------------------------------------------------------------------
# Toolbar (2 positions) vs Settings (4 levels)
# ---------------------------------------------------------------------------

def test_a_toolbar_click_cannot_overwrite_the_configured_level():
    """Earlier revisions mapped Partial onto Short, so a toolbar click — or a
    grid reload — discarded a Medium choice. The level the user configured is
    now separate from the level currently being rendered: only the derived
    value moves."""
    assert "tag_effective_detail = \\" in SOURCE
    assert "self._effective_tag_detail(mode)" in SOURCE
    # The old flag-based workaround is gone.
    assert "set_detail" not in SOURCE


def test_full_view_always_renders_every_attribute():
    block = SOURCE[SOURCE.index("def _effective_tag_detail"):]
    block = block[:400]
    assert "if mode == 'full':" in block
    assert "DETAIL_LONG" in block
    assert "tag_detail_level" in block   # anything else uses the configured level


# ---------------------------------------------------------------------------
# The under-grid editor panel — the Phase 4 blocker
# ---------------------------------------------------------------------------

def test_under_grid_panel_reads_through_the_seam():
    """on_tab_target_change writes straight to segment.target. Left on
    toPlainText(), it would have stored U+FFFC once the panel showed atoms."""
    assert ("new_text = read_grid_cell_text(panel.editor_widget.target_editor)"
            in SOURCE)


def test_under_grid_panel_is_populated_through_the_seam():
    assert "apply_grid_cell_text(editor.source_editor, source_text)" in SOURCE
    assert "apply_grid_cell_text(editor.target_editor, target_text)" in SOURCE


def test_no_panel_editor_bypasses_the_seam():
    offenders = re.findall(
        r"\.(?:source_editor|target_editor)\.(?:toPlainText|setPlainText)\(",
        SOURCE)
    assert offenders == [], (
        f"{len(offenders)} under-grid editor call(s) bypass the protected-tag "
        "seam; use apply_grid_cell_text()/read_grid_cell_text()")


# ---------------------------------------------------------------------------
# Detail levels behave as documented
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("detail,expected", [
    (ta.DETAIL_SHORT, "1"),
    (ta.DETAIL_MEDIUM, "cf"),
    # FILTERED is defined for parity with memoQ but has no per-format attribute
    # source yet, so it renders as MEDIUM rather than guessing.
    (ta.DETAIL_FILTERED, "cf"),
    (ta.DETAIL_LONG, '<cf color="#227acb" font="tahoma">'),
])
def test_each_settings_level_renders_differently(detail, expected):
    from modules.tag_protection import parse_tags

    token = parse_tags('<cf color="#227acb" font="tahoma">x</cf>',
                       number=True)[0]
    assert ta.atom_label(token, detail) == expected


def test_only_full_has_a_fixed_level():
    """Partial has no entry on purpose: it renders at the configured level."""
    mapping_block = SOURCE[SOURCE.index("TAG_VIEW_MODE_DETAIL = {"):]
    mapping_block = mapping_block[:mapping_block.index("}")]
    assert "'full'" in mapping_block
    assert "DETAIL_LONG" in mapping_block
    assert "'partial'" not in mapping_block
    assert "DETAIL_FILTERED" not in mapping_block


def test_grid_reload_does_not_override_the_detail_level():
    """Found by screenshotting the real grid: load_segments_to_grid() ends by
    calling _refresh_grid_display_mode(), which re-derived the detail level from
    the display mode — so a Medium or Filtered choice from Settings was silently
    replaced by Short/Long on every grid load. The refresh must use the current
    level instead."""
    block = SOURCE[SOURCE.index("if _protected_tags_active() and mode in ('partial', 'full'):"):]
    block = block[:1200]
    assert "detail = _tag_detail_level()" in block
    assert "detail = self.TAG_VIEW_MODE_DETAIL.get(mode" not in block


# ---------------------------------------------------------------------------
# The two renderings must not both run
# ---------------------------------------------------------------------------

def test_placeholder_substitution_is_skipped_when_protected():
    """Found by reading atom labels out of the running grid.

    Partial view has two renderings: compact_tags() placeholders when protection
    is off, and atom labels when it is on. Both were running, so atoms were built
    out of {1} placeholders instead of the real tags. Consequences: the Medium
    level could only ever show numbers, the hover tooltip said "{1}" instead of
    the real markup, and the tag identity carried by each atom was the
    placeholder. Every compact_tags() call on a display path must be gated.
    """
    for marker in ("if not _protected_tags_active() and self._canonical_tag_view_mode(",
                   "if mode == 'partial' and not _protected_tags_active():"):
        assert marker in SOURCE, marker
    # No display-path call to compact_tags() may sit outside such a gate.
    # Matches real call sites only — prose mentions in docstrings are not calls.
    call = re.compile(r"^\s*(?:[\w.]+\s*=\s*)?compact_tags\(")
    lines = SOURCE.splitlines()
    checked = 0
    for line_no, line in enumerate(lines, 1):
        if not call.match(line):
            continue
        checked += 1
        window = "\n".join(lines[max(0, line_no - 14):line_no])
        # Either an explicit protection gate, or a guard on the placeholder map,
        # which is only ever populated on the unprotected path.
        gated = ("_protected_tags_active()" in window
                 or "_compact_tag_map is not None" in window
                 or "if compact_map:" in window)
        assert gated, (
            f"compact_tags() at line {line_no} is not gated on protection")
    assert checked >= 5, f"expected several call sites, found {checked}"


def test_word_joiner_is_not_inserted_into_protected_tags():
    """protect_tags_from_linebreak() exists so word-wrap cannot split "</i>"
    after the slash. A protected tag is a single object and cannot split, and the
    joiner would otherwise be baked into the atom's stored markup and tooltip."""
    idx = SOURCE.index("text = protect_tags_from_linebreak(text)")
    preceding = SOURCE[:idx].splitlines()[-4:]
    assert any("_protected_tags_active()" in line for line in preceding)
