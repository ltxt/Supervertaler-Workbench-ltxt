"""Tests for the tag-protection settings plumbing.

Phase 4 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md.

Two settings are added: `tag_protection_enabled` (whether each inline tag
behaves as one unbreakable unit) and `tag_detail_level` (how much of each tag is
shown, memoQ's four levels). The toolbar reaches only Short and Long; the two
intermediate levels are Settings-only, which creates the one interaction worth
testing carefully — restoring settings must not overwrite a Medium/Filtered
choice with Short/Long.
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

def test_protection_defaults_to_off():
    """Until the pills have been reviewed in the running application, the
    feature must not turn itself on for existing users."""
    assert re.search(r"^\s+tag_protection_enabled = False\s*$", SOURCE, re.MULTILINE)
    assert "general_settings.get('tag_protection_enabled', False)" in SOURCE


def test_detail_defaults_to_short():
    assert re.search(r"^\s+tag_detail_level = _tag_atoms\.DETAIL_SHORT\s*$",
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


def test_detail_combo_offers_all_four_memoq_levels():
    for level in ta.DETAIL_LEVELS:
        assert f"_tag_atoms.DETAIL_{level.upper()}" in SOURCE, level


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
    assert "general_settings.get('tag_protection_enabled', False)" in SOURCE
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

def test_restore_does_not_overwrite_an_intermediate_detail_level():
    """The toolbar's Partial/Full map onto Short/Long. If settings restore also
    forced that mapping, choosing Medium or Filtered in Settings would be
    silently reset to Short on every launch."""
    assert "self._apply_tag_view_mode_state(saved_tag_mode, set_detail=False)" in SOURCE


def test_clicking_a_toolbar_position_does_set_the_detail_level():
    """The other half: an explicit toolbar click is a deliberate choice and
    should move the detail level to Short or Long."""
    assert "def _apply_tag_view_mode_state(self, mode: str," in SOURCE
    assert "set_detail: bool = True" in SOURCE
    assert "if set_detail and mode in self.TAG_VIEW_MODE_DETAIL:" in SOURCE


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
    (ta.DETAIL_FILTERED, 'cf color="#227acb"'),
    (ta.DETAIL_LONG, '<cf color="#227acb" font="tahoma">'),
])
def test_each_settings_level_renders_differently(detail, expected):
    from modules.tag_protection import parse_tags

    token = parse_tags('<cf color="#227acb" font="tahoma">x</cf>',
                       number=True)[0]
    assert ta.atom_label(token, detail) == expected


def test_intermediate_levels_are_reachable_only_from_settings():
    """The toolbar maps to Short/Long; Medium and Filtered are Settings-only.
    If that ever changes, this test should be updated deliberately."""
    mapping_block = SOURCE[SOURCE.index("TAG_VIEW_MODE_DETAIL = {"):]
    mapping_block = mapping_block[:mapping_block.index("}")]
    assert "DETAIL_SHORT" in mapping_block
    assert "DETAIL_LONG" in mapping_block
    assert "DETAIL_MEDIUM" not in mapping_block
    assert "DETAIL_FILTERED" not in mapping_block
