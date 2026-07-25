"""Tests for the Partial/Full tag display modes.

Phase 2 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md.

Two renderings of the same short view exist, and both are covered here:

* with tag protection **off**, ``compact_tags()`` rewrites the text buffer into
  ``{N}`` placeholders and ``expand_compact_tags()`` reverses it;
* with protection **on**, the atom layer shows the short label and the buffer is
  never rewritten (see test_tag_atoms.py).

The regression these tests exist for: ``compact_tags()`` used to number
placeholders by tag *name*, so two same-named tags with different attributes
collapsed onto one placeholder and the reversal map kept only the last. Editing
such a segment in Compact view silently rewrote the first tag's attributes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import load_supervertaler_symbols  # noqa: E402

from modules import tag_protection as tp  # noqa: E402

REQUEST_EXAMPLE = (
    'Specify <cf color="#227acb" font="tahoma"><b>Wall thickness </b></cf>'
    '<b>range </b><g3>②</g3>'
)

TWO_CF = (
    'Specify <cf color="#227acb" font="tahoma">Wall</cf>'
    ' and <cf color="#ff0000">Floor</cf>'
)


@pytest.fixture(scope="module")
def sv():
    return load_supervertaler_symbols(
        ["_COMPACT_TAG_FAMILIES", "compact_tags", "expand_compact_tags"],
        {"_tag_protection": tp},
    )


@pytest.fixture
def compact(sv):
    return sv["compact_tags"]


@pytest.fixture
def expand(sv):
    return sv["expand_compact_tags"]


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------

def test_same_named_tags_with_different_attributes_survive(compact, expand):
    """The exact data-loss case: both <cf …> used to become {1}, and expanding
    gave BOTH of them the second tag's colour."""
    tag_map = {}
    display = compact(TWO_CF, tag_map)
    assert display == "Specify {1}Wall{/1} and {2}Floor{/2}"
    assert expand(display, tag_map) == TWO_CF
    assert tag_map["{1}"] == '<cf color="#227acb" font="tahoma">'
    assert tag_map["{2}"] == '<cf color="#ff0000">'


def test_no_placeholder_is_ever_reused(compact):
    """Last-write-wins on the map was the mechanism of the corruption."""
    for raw in (TWO_CF, REQUEST_EXAMPLE, "<b>a</b><b>b</b>", "<b><b>x</b></b>",
                "a<br/>b<br/>c", "[1}x{1] [2}y{2]"):
        tag_map = {}
        display = compact(raw, tag_map)
        # The invariant that matters: every placeholder that appears in the
        # display has exactly one entry in the map, so expanding cannot pick the
        # wrong tag. (Raw values may legitimately repeat — two <b> closers are
        # both "</b>" — but their placeholders must differ.)
        import re
        used = re.findall(r"\{/?\d+/?\}", display)
        assert len(used) == len(set(used)), f"placeholder reused in {display!r}"
        assert set(used) <= set(tag_map), "display has a placeholder with no mapping"


def test_closing_tag_is_shortened_too(compact):
    """Compaction used to be asymmetric: an attributed opening tag became {1}
    while its own </cf> closer stayed literal, because the passthrough rule was
    evaluated per tag."""
    display = compact('<cf color="#227acb">Wall</cf>')
    assert display == "{1}Wall{/1}"
    assert "</cf>" not in display


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    REQUEST_EXAMPLE,
    TWO_CF,
    "plain text with no tags",
    "",
    "<b>bold</b>",
    "<b><b>nested same name</b></b>",
    "<b>a</b><b>b</b>",
    "a<br/>b<2/>c",
    "[1}memoQ{1] and [3]",
    "Vind {00108}jouw CS{00109} hier",
    "</b>unmatched closer",
    "<b>unmatched opener",
    "<li-o>hyphenated</li-o>",
    'multi\nline <cf color="#227acb">x</cf>',
])
def test_compact_expand_round_trip_is_lossless(compact, expand, raw):
    tag_map = {}
    assert expand(compact(raw, tag_map), tag_map) == raw


def test_compact_without_a_map_still_renders(compact):
    """tag_map is optional; callers that only need the display pass None."""
    assert compact(TWO_CF) == "Specify {1}Wall{/1} and {2}Floor{/2}"


def test_expand_with_empty_map_is_identity(expand):
    assert expand("no placeholders here", {}) == "no placeholders here"
    assert expand("text", None) == "text"


# ---------------------------------------------------------------------------
# Numbering matches memoQ (and therefore matches the atom short labels)
# ---------------------------------------------------------------------------

def test_pair_shares_a_number(compact):
    assert compact("<b>x</b>") == "{1}x{/1}"


def test_standalone_tags_get_their_own_numbers(compact):
    assert compact("a<br/>b<br/>c") == "a{1/}b{2/}c"


def test_numbering_is_left_to_right(compact):
    assert compact("<b>a</b><i>b</i>") == "{1}a{/1}{2}b{/2}"


def test_nesting_is_respected(compact):
    assert compact("<b><i>x</i></b>") == "{1}{2}x{/2}{/1}"


def test_request_example_numbering(compact):
    assert compact(REQUEST_EXAMPLE) == (
        "Specify {1}{2}Wall thickness {/2}{/1}{3}range {/3}{4}②{/4}")


def test_compact_placeholders_agree_with_atom_short_labels(compact):
    """The two renderings of Partial view must not disagree about numbering, or
    switching tag protection on would silently renumber every tag."""
    from modules.tag_atoms import DETAIL_SHORT, atom_label

    tokens = tp.parse_tags(REQUEST_EXAMPLE, number=True)
    atom_numbers = [atom_label(t, DETAIL_SHORT).strip("/") for t in tokens]

    import re
    placeholder_numbers = [
        m.strip("{}/") for m in re.findall(r"\{/?\d+/?\}", compact(REQUEST_EXAMPLE))]

    assert atom_numbers == placeholder_numbers


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------

def test_compact_does_not_recompact_its_own_placeholders(sv, compact):
    """FAMILY_COMPACT is excluded from _COMPACT_TAG_FAMILIES so a second pass is
    a no-op rather than mangling {1} into {1} of something else."""
    assert tp.FAMILY_COMPACT not in sv["_COMPACT_TAG_FAMILIES"]
    once = compact("<b>x</b>")
    assert compact(once) == once


def test_prose_with_angle_brackets_is_not_compacted(compact):
    raw = "if x < y and y > z"
    assert compact(raw) == raw


# ---------------------------------------------------------------------------
# Mode names and the detail level they select
# ---------------------------------------------------------------------------
#
# _canonical_tag_view_mode / TAG_VIEW_MODE_* live on the main window class,
# which cannot be imported. They are pure lookups, so they are read out of the
# class body via the AST and exercised directly.

def _mode_plumbing():
    import ast

    from conftest import SUPERVERTALER_PY
    from modules.tag_atoms import DETAIL_LONG, DETAIL_SHORT

    source = open(SUPERVERTALER_PY, encoding="utf-8").read()
    lines = source.splitlines(keepends=True)
    ns = {"_tag_atoms": type("m", (), {"DETAIL_SHORT": DETAIL_SHORT,
                                       "DETAIL_LONG": DETAIL_LONG})}

    wanted = {"TAG_VIEW_MODE_ALIASES", "TAG_VIEW_MODE_DETAIL",
              "_canonical_tag_view_mode"}
    found = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            name = next((t.id for t in node.targets
                         if isinstance(t, ast.Name) and t.id in wanted), None)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            name = node.name
        else:
            name = None
        if name and name not in found:
            segment = "".join(lines[node.lineno - 1:node.end_lineno])
            # Dedent the class-body indentation and drop the classmethod
            # decorator, which the AST slice does not include anyway.
            segment = "\n".join(line[4:] if line.startswith("    ") else line
                                for line in segment.splitlines())
            exec(compile(segment, "Supervertaler.py", "exec"), ns)
            found[name] = True

    assert wanted <= set(found), f"missing: {wanted - set(found)}"
    return ns


@pytest.fixture(scope="module")
def plumbing():
    return _mode_plumbing()


@pytest.mark.parametrize("given,expected", [
    ("partial", "partial"),
    ("full", "full"),
    ("wysiwyg", "wysiwyg"),
    # Legacy internal names must keep working: they are what older persisted
    # settings contain.
    ("compact", "partial"),
    ("tags", "full"),
    # Defensive: unknown or empty falls back to showing tags in full.
    ("nonsense", "full"),
    ("", "full"),
    (None, "full"),
    ("  Partial  ", "partial"),
    ("FULL", "full"),
])
def test_mode_names_are_canonicalised(plumbing, given, expected):
    canon = plumbing["_canonical_tag_view_mode"]
    assert canon(type("C", (), plumbing), given) == expected


def test_full_always_means_long_and_partial_is_configurable(plumbing):
    """Full Tag Text is fixed at LONG. Partial deliberately has NO fixed level:
    it renders at whatever the user configured (Short or Medium), so a toolbar
    click cannot overwrite that choice — which is what made memoQ's Medium level
    unreachable in an earlier revision."""
    from modules.tag_atoms import DETAIL_LONG
    detail = plumbing["TAG_VIEW_MODE_DETAIL"]
    assert detail["full"] == DETAIL_LONG
    assert "partial" not in detail


def test_legacy_aliases_cover_both_old_names(plumbing):
    assert plumbing["TAG_VIEW_MODE_ALIASES"] == {"compact": "partial",
                                                 "tags": "full"}


def test_mode_is_persisted_and_restored():
    """The mode used to reset to raw tags on every launch."""
    source = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Supervertaler.py"), encoding="utf-8").read()
    assert "general_settings['tag_display_mode'] = mode" in source
    assert "general_settings.get('tag_display_mode')" in source


def test_switching_protected_view_relabels_instead_of_rebuilding():
    """Partial <-> Full with protection on must not rewrite cell text, or an
    in-progress edit could be lost and tags could be renumbered."""
    source = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Supervertaler.py"), encoding="utf-8").read()
    assert "_tag_atoms.relabel_atoms(widget.document(), detail)" in source
