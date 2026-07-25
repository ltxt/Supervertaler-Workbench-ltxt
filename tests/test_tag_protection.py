"""Tests for the canonical inline-tag model (modules/tag_protection.py).

Covers Phase 0 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md:
the parser, the genuineness rules that separate real markup from prose
containing < and >, memoQ-compatible numbering, and the tag-sequence grouping.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.tag_protection import (  # noqa: E402
    FAMILY_COMPACT,
    FAMILY_DEJAVU,
    FAMILY_HTML,
    FAMILY_MEMOQ_BRACKET,
    FAMILY_MEMOQ_CONTENT,
    FAMILY_TRADOS_NUMERIC,
    KIND_CLOSE,
    KIND_EMPTY,
    KIND_OPEN,
    LEGACY_FAMILIES,
    ORIGIN_STRUCTURED,
    WORD_JOINER,
    TagToken,
    assign_numbers,
    extract_raw_tags,
    parse_tags,
    strip_tags,
    tag_sequences,
)


# ---------------------------------------------------------------------------
# The example from the original feature request
# ---------------------------------------------------------------------------

REQUEST_EXAMPLE = (
    'Specify <cf color="#227acb" font="tahoma"><b>Wall thickness </b></cf>'
    '<b>range </b><g3>②</g3>'
)


def test_request_example_tags_are_all_found():
    raw = extract_raw_tags(REQUEST_EXAMPLE)
    assert raw == [
        '<cf color="#227acb" font="tahoma">',
        "<b>",
        "</b>",
        "</cf>",
        "<b>",
        "</b>",
        "<g3>",
        "</g3>",
    ]


def test_request_example_attributed_tag_is_parsed_not_whitelisted():
    """The old TagManager whitelist (b|i|u|bi|li|sub|sup) could not represent
    an attributed tag like <cf …> at all."""
    cf = parse_tags(REQUEST_EXAMPLE)[0]
    assert cf.name == "cf"
    assert cf.kind == KIND_OPEN
    assert cf.family == FAMILY_HTML
    assert cf.attrs == ' color="#227acb" font="tahoma"'


def test_request_example_circled_digit_is_content_not_a_tag():
    """U+2461 sits between <g3> and </g3>; it is content."""
    assert "②" not in "".join(extract_raw_tags(REQUEST_EXAMPLE))


def test_request_example_positions_round_trip():
    for tok in parse_tags(REQUEST_EXAMPLE):
        assert REQUEST_EXAMPLE[tok.start:tok.end] == tok.raw


# ---------------------------------------------------------------------------
# Requirement 2 — genuine tags vs. ordinary text containing < and >
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prose", [
    "if x < y and y > z",
    "temperature <5 > 3 degrees",
    "a < b or c > d",
    "compare <first second> please",
    "5 < 10",
    "use the < and > operators",
])
def test_prose_with_angle_brackets_is_not_tagged(prose):
    assert extract_raw_tags(prose) == []


@pytest.mark.parametrize("markup", [
    "<b>",
    "</b>",
    "<br/>",
    '<cf color="#227acb">',
    "<li-o>",
    "<1>",
    "</1>",
    "<1/>",
    "[1}",
    "{1]",
    "[1]",
])
def test_genuine_tags_are_tagged(markup):
    assert extract_raw_tags(f"before {markup} after") == [markup]


def test_bare_word_attributes_are_rejected_in_strict_mode():
    assert extract_raw_tags("<span foo bar>") == []
    assert extract_raw_tags("<span foo bar>", strict=False) == ["<span foo bar>"]


def test_quoted_attributes_accepted_single_and_double():
    assert extract_raw_tags("<span class='x'>") == ["<span class='x'>"]
    assert extract_raw_tags('<span class="x">') == ['<span class="x">']


def test_tag_spanning_a_newline_is_rejected():
    assert extract_raw_tags("<cf color=\n'red'>") == []


def test_closing_tag_with_attributes_is_rejected():
    assert extract_raw_tags("</b junk>") == []


def test_numeric_tag_requires_no_whitespace():
    """'<5 > 3' must not be read as Trados numeric tag <5>."""
    assert extract_raw_tags("<5 > 3") == []
    assert extract_raw_tags("<5>") == ["<5>"]


# ---------------------------------------------------------------------------
# Defect 3.3 — self-closing tags were invisible to extract_all_tags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tag", ["<2/>", "<x1/>", "<br/>", '<img src="a.png"/>'])
def test_self_closing_tags_are_found(tag):
    """These are exactly what sdlppx_handler emits as <N/> for <x id="N"/>.
    The old _ALL_TAGS_PATTERN matched none of them, so Ctrl+, could not insert
    a standalone tag."""
    toks = parse_tags(f"before {tag} after")
    assert [t.raw for t in toks] == [tag]
    assert toks[0].kind == KIND_EMPTY


def test_self_closing_tag_kind_and_name():
    tok = parse_tags("<2/>")[0]
    assert (tok.kind, tok.name, tok.family) == (
        KIND_EMPTY, "2", FAMILY_TRADOS_NUMERIC)


# ---------------------------------------------------------------------------
# Defect 3.1 — identity must be occurrence-based, not name-based
# ---------------------------------------------------------------------------

TWO_CF = (
    'Specify <cf color="#227acb" font="tahoma">Wall</cf>'
    ' and <cf color="#ff0000">Floor</cf>'
)


def test_same_named_tags_keep_distinct_identities():
    """compact_tags() numbered by tag NAME, so both <cf …> collapsed onto {1}
    and the reversal map kept only the last — silently losing the first tag's
    attributes. Identity here is per occurrence, so the raw text survives."""
    toks = parse_tags(TWO_CF, number=True)
    opens = [t for t in toks if t.kind == KIND_OPEN]
    assert len(opens) == 2
    assert opens[0].raw != opens[1].raw
    assert opens[0].number != opens[1].number
    assert opens[0].attrs == ' color="#227acb" font="tahoma"'
    assert opens[1].attrs == ' color="#ff0000"'


def test_same_named_tags_pair_with_their_own_closer():
    toks = parse_tags(TWO_CF, number=True)
    numbers = [(t.name, t.kind, t.number) for t in toks]
    assert numbers == [
        ("cf", KIND_OPEN, 1),
        ("cf", KIND_CLOSE, 1),
        ("cf", KIND_OPEN, 2),
        ("cf", KIND_CLOSE, 2),
    ]


def test_reconstructing_text_from_tokens_is_lossless():
    """The property compact_tags() failed: put the tags back, get the input."""
    out, pos = [], 0
    for tok in parse_tags(TWO_CF):
        out.append(TWO_CF[pos:tok.start])
        out.append(tok.raw)
        pos = tok.end
    out.append(TWO_CF[pos:])
    assert "".join(out) == TWO_CF


# ---------------------------------------------------------------------------
# memoQ numbering rules (docs: Ribbons > Edit)
# ---------------------------------------------------------------------------

def test_pair_shares_a_number_and_nesting_is_respected():
    toks = parse_tags("<b><i>x</i></b>", number=True)
    assert [(t.name, t.kind, t.number) for t in toks] == [
        ("b", KIND_OPEN, 1),
        ("i", KIND_OPEN, 2),
        ("i", KIND_CLOSE, 2),
        ("b", KIND_CLOSE, 1),
    ]


def test_empty_tags_get_unique_numbers():
    toks = parse_tags("a<br/>b<br/>c", number=True)
    assert [t.number for t in toks] == [1, 2]
    assert all(t.kind == KIND_EMPTY for t in toks)


def test_numbering_is_left_to_right_across_families():
    toks = parse_tags("<b>x</b>[1}y{1]", number=True)
    assert [t.number for t in toks] == [1, 1, 2, 2]


def test_same_named_nested_tags_pair_innermost_first():
    """A closing tag carries no attributes (``</rpr>`` closes ``<rpr id="1">``),
    so pairing is by name and nesting; the id is captured but cannot be the
    primary key."""
    text = '<rpr id="1">a<rpr id="2">b</rpr>c</rpr>'
    toks = parse_tags(text, number=True)
    assert [(t.kind, t.number) for t in toks] == [
        (KIND_OPEN, 1),
        (KIND_OPEN, 2),
        (KIND_CLOSE, 2),
        (KIND_CLOSE, 1),
    ]


def test_id_attribute_is_captured():
    toks = parse_tags('<rpr id="1">a</rpr>')
    assert toks[0].tag_id == "1"
    assert toks[1].tag_id == ""


def test_id_vetoes_a_pair_only_when_both_halves_carry_one():
    """A closer must skip an inner opener whose id differs. Exercised at
    the numbering level because that is how the structured import path uses it —
    it knows the id of both halves of an SDLXLIFF ``<g id="N">…</g>`` pair.
    Attributed closers do also occur in scanned text (see
    test_closing_tag_may_carry_attributes)."""
    def tok(kind, name, tag_id, start):
        return TagToken(raw="", kind=kind, name=name, family=FAMILY_HTML,
                        start=start, end=start + 1, tag_id=tag_id,
                        origin=ORIGIN_STRUCTURED)

    numbered = assign_numbers([
        tok(KIND_OPEN, "g", "1", 0),
        tok(KIND_OPEN, "g", "2", 1),
        tok(KIND_CLOSE, "g", "1", 2),
    ])
    assert [t.number for t in numbered] == [1, 2, 1]


def test_closing_tag_may_carry_attributes():
    """Corrected against real data. This test previously asserted the opposite,
    on the assumption that a closing tag never has attributes. A real memoQ
    MQXLIFF export of a DOCX contains::

        <cmt id="0" transform="open">vague</cmt id="0" transform="close">

    so rejecting attributed closers left the opener unpaired and unprotected.
    Prose like "</b junk>" is still excluded, by the attribute well-formedness
    rule rather than by a blanket ban."""
    toks = parse_tags('</g id="1">')
    assert [(t.raw, t.kind, t.tag_id) for t in toks] == [
        ('</g id="1">', KIND_CLOSE, "1")]


def test_memoq_comment_pair_from_a_real_export():
    text = ('inherently <cmt id="0" transform="open">vague'
            '</cmt id="0" transform="close">, as')
    toks = parse_tags(text, number=True)
    assert [(t.kind, t.number) for t in toks] == [
        (KIND_OPEN, 1), (KIND_CLOSE, 1)]


def test_content_tag_closer_needs_its_opener():
    """memoQ content tags pair as ``[name …]`` … ``{name}``, but the closing
    form is just an identifier in braces, which ordinary text hits constantly.
    A real MQXLIFF export of an IDML file contained
    ``e^{x_i} / Σ_j e^{x_j}`` — mathematics, not markup. Protecting those would
    have made the formula uneditable."""
    assert extract_raw_tags(
        "Equation: softmax(x)_i = e^{x_i} / Σ_j e^{x_j}.") == []
    assert extract_raw_tags("let S = {a} union {b}") == []
    # With its opener present it is a genuine tag pair again.
    assert extract_raw_tags('[uicontrol id="GUID-1"]Save{uicontrol}') == [
        '[uicontrol id="GUID-1"]', '{uicontrol}']


def test_unmatched_closing_tag_gets_its_own_number():
    """A pair straddling a segment boundary is normal in a CAT tool."""
    toks = parse_tags("</b>tail", number=True)
    assert [(t.kind, t.number) for t in toks] == [(KIND_CLOSE, 1)]


def test_unmatched_opening_tag_is_still_numbered():
    toks = parse_tags("head<b>", number=True)
    assert [(t.kind, t.number) for t in toks] == [(KIND_OPEN, 1)]


def test_assign_numbers_is_idempotent_on_already_numbered_tokens():
    once = parse_tags(REQUEST_EXAMPLE, number=True)
    twice = assign_numbers(once)
    assert [t.number for t in once] == [t.number for t in twice]


# ---------------------------------------------------------------------------
# Tag families
# ---------------------------------------------------------------------------

def test_memoq_bracket_kinds():
    toks = parse_tags("[1}bold{1] and [3]")
    assert [(t.raw, t.kind) for t in toks] == [
        ("[1}", KIND_OPEN),
        ("{1]", KIND_CLOSE),
        ("[3]", KIND_EMPTY),
    ]
    assert all(t.family == FAMILY_MEMOQ_BRACKET for t in toks)


def test_dejavu_five_digit_codes():
    toks = parse_tags("Vind {00108}jouw CS{00109} hier")
    assert [t.raw for t in toks] == ["{00108}", "{00109}"]
    assert all(t.family == FAMILY_DEJAVU for t in toks)


def test_dejavu_wins_over_compact_placeholder_for_five_digits():
    """Defect 3.4: {00108} matches the compact placeholder shape too. The
    narrower Déjà Vu family must win so a DVX project is not misread."""
    assert parse_tags("{00108}")[0].family == FAMILY_DEJAVU
    assert parse_tags("{1}")[0].family == FAMILY_COMPACT


def test_compact_placeholder_kinds():
    toks = parse_tags("{1}x{/1}y{2/}")
    assert [(t.raw, t.kind) for t in toks] == [
        ("{1}", KIND_OPEN),
        ("{/1}", KIND_CLOSE),
        ("{2/}", KIND_EMPTY),
    ]


def test_memoq_content_tags():
    toks = parse_tags('[uicontrol id="GUID-1"]Save{uicontrol}')
    assert [t.family for t in toks] == [FAMILY_MEMOQ_CONTENT] * 2
    assert toks[0].kind == KIND_OPEN
    assert toks[1].kind == KIND_CLOSE
    assert toks[0].tag_id == "GUID-1"


def test_hyphenated_tag_names_are_supported():
    """extract_html_tags' old pattern had no hyphen, so <li-o> was invisible to
    the has_html_tags check and Ctrl+, silently did nothing on list segments."""
    toks = parse_tags("<li-o>item</li-o>")
    assert [t.name for t in toks] == ["li-o", "li-o"]


def test_families_filter_restricts_results():
    text = '<b>x</b>[1}y{1]{00108}'
    assert extract_raw_tags(text, families=[FAMILY_HTML]) == ["<b>", "</b>"]
    assert extract_raw_tags(text, families=[FAMILY_MEMOQ_BRACKET]) == ["[1}", "{1]"]
    assert extract_raw_tags(text, families=LEGACY_FAMILIES) == [
        "<b>", "</b>", "[1}", "{1]"]


# ---------------------------------------------------------------------------
# Word joiner tolerance (display-only character inserted inside tags)
# ---------------------------------------------------------------------------

def test_word_joiner_inside_a_tag_is_tolerated():
    wrapped = f"</{WORD_JOINER}i>"
    toks = parse_tags(f"text{wrapped}")
    assert len(toks) == 1
    assert toks[0].kind == KIND_CLOSE
    assert toks[0].raw == wrapped


# ---------------------------------------------------------------------------
# Sequences (memoQ F9 "Copy Next Tag Sequence")
# ---------------------------------------------------------------------------

def test_adjacent_tags_group_into_one_sequence():
    toks = parse_tags('<cf color="#227acb"><b>Wall</b></cf>')
    runs = tag_sequences(toks)
    assert [len(r) for r in runs] == [2, 2]
    assert [t.raw for t in runs[0]] == ['<cf color="#227acb">', "<b>"]


def test_tags_separated_by_text_are_separate_sequences():
    runs = tag_sequences(parse_tags("<b>a</b> mid <i>b</i>"))
    assert [len(r) for r in runs] == [1, 1, 1, 1]


def test_no_tags_gives_no_sequences():
    assert tag_sequences(parse_tags("plain text")) == []


# ---------------------------------------------------------------------------
# Misc API
# ---------------------------------------------------------------------------

def test_strip_tags_leaves_the_words():
    assert strip_tags(REQUEST_EXAMPLE) == "Specify Wall thickness range ②"


def test_strip_tags_removes_self_closing_forms():
    assert strip_tags("a<br/>b<2/>c") == "abc"


def test_empty_and_none_input():
    assert parse_tags("") == []
    assert parse_tags(None) == []
    assert extract_raw_tags("") == []
    assert strip_tags(None) == ""


def test_origin_is_recorded():
    toks = parse_tags("<b>x</b>", origin=ORIGIN_STRUCTURED)
    assert all(t.origin == ORIGIN_STRUCTURED for t in toks)


def test_tokens_never_overlap_and_are_ordered():
    toks = parse_tags(REQUEST_EXAMPLE)
    for a, b in zip(toks, toks[1:]):
        assert a.end <= b.start


def test_token_is_hashable_and_frozen():
    tok = parse_tags("<b>")[0]
    assert isinstance(tok, TagToken)
    {tok}  # hashable
    with pytest.raises(Exception):
        tok.raw = "changed"


def test_display_labels():
    toks = parse_tags('<cf color="#227acb">x</cf><br/>', number=True)
    opening, closing, empty = toks
    assert opening.short_label() == "1"
    assert closing.short_label() == "/1"
    assert opening.medium_label() == "cf"
    assert closing.medium_label() == "/cf"
    assert empty.medium_label() == "br/"
    assert '<cf color="#227acb">' in opening.tooltip()
    assert "opening" in opening.tooltip()
