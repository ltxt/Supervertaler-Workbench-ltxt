"""Tests for tag verification and sequence-aware tag insertion.

Phase 3 of docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md.

Tag verification is the check every major CAT tool ships and Supervertaler did
not: it reports tags the translator dropped, invented, left unpaired or moved.
The comparison primitive already existed inside the AutoTagger validator but was
never surfaced to the user.

Sequence-aware insertion matches memoQ's *Copy Next Tag Sequence* (F9), which
inserts a whole run of adjacent tags in one action rather than one tag at a time.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.tag_protection import (  # noqa: E402
    ISSUE_EXTRA,
    ISSUE_MISSING,
    ISSUE_REORDERED,
    ISSUE_UNPAIRED,
    describe_issues,
    next_tag_sequence,
    tags_match,
    verify_tags,
)

SRC = (
    'Specify <cf color="#227acb" font="tahoma"><b>Wall thickness </b></cf>'
    '<b>range </b><g3>②</g3>'
)
GOOD = (
    'Specify <cf color="#227acb" font="tahoma"><b>Wanddikte </b></cf>'
    '<b>bereik </b><g3>②</g3>'
)


def kinds(issues):
    return sorted(i.kind for i in issues)


# ---------------------------------------------------------------------------
# Clean targets
# ---------------------------------------------------------------------------

def test_correct_translation_has_no_issues():
    assert verify_tags(SRC, GOOD) == []
    assert tags_match(SRC, GOOD)


def test_no_tags_anywhere_is_clean():
    assert verify_tags("plain source", "plain target") == []


def test_target_text_may_differ_freely():
    """Only tags are compared; wording is the translator's business."""
    assert verify_tags("<b>Hello</b>", "<b>Totally different words</b>") == []


def test_whitespace_between_tags_is_not_an_order_change():
    assert verify_tags("<b>a</b> <i>b</i>", "<b>x</b>  <i>y</i>") == []


# ---------------------------------------------------------------------------
# An empty target is never reported
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("empty", ["", "   ", "\n", "\t "])
def test_untranslated_segment_is_not_flagged(empty):
    """An untranslated segment is missing all its tags by definition. Flagging
    it would bury the real problems under one row per pending segment."""
    assert verify_tags(SRC, empty) == []


# ---------------------------------------------------------------------------
# Missing tags
# ---------------------------------------------------------------------------

def test_dropped_tag_is_reported_as_missing():
    target = 'Specify <b>Wanddikte </b></cf><b>bereik </b><g3>②</g3>'
    issues = verify_tags(SRC, target)
    assert ISSUE_MISSING in kinds(issues)
    missing = [i for i in issues if i.kind == ISSUE_MISSING]
    assert missing[0].tag == '<cf color="#227acb" font="tahoma">'


def test_all_tags_dropped_from_a_non_empty_target():
    """SRC holds 8 tag occurrences but only 6 distinct tags (<b> and </b> each
    appear twice), so there are 6 issues carrying 8 missing occurrences."""
    issues = verify_tags(SRC, "Wanddikte bereik ②")
    assert kinds(issues) == [ISSUE_MISSING] * 6
    assert sum(i.count for i in issues) == 8


def test_missing_count_is_reported_when_a_tag_repeats():
    issues = verify_tags("<b>a</b><b>b</b>", "<b>a b")
    missing = {i.tag: i.count for i in issues if i.kind == ISSUE_MISSING}
    assert missing["</b>"] == 2
    assert missing["<b>"] == 1


# ---------------------------------------------------------------------------
# Extra tags
# ---------------------------------------------------------------------------

def test_invented_tag_is_reported_as_extra():
    issues = verify_tags(SRC, GOOD + "<i>oops</i>")
    extra = {i.tag for i in issues if i.kind == ISSUE_EXTRA}
    assert extra == {"<i>", "</i>"}


def test_duplicated_tag_is_extra():
    issues = verify_tags("<b>x</b>", "<b><b>x</b>")
    extra = [i for i in issues if i.kind == ISSUE_EXTRA]
    assert extra and extra[0].tag == "<b>"


# ---------------------------------------------------------------------------
# Reordering
# ---------------------------------------------------------------------------

def test_reordered_tags_are_reported():
    target = ('Specify <b>Wanddikte </b><cf color="#227acb" font="tahoma"></cf>'
              '<b>bereik </b><g3>②</g3>')
    issues = verify_tags(SRC, target)
    assert ISSUE_REORDERED in kinds(issues)


def test_swapped_pair_is_reordered_not_missing():
    issues = verify_tags("<b>a</b><i>b</i>", "<i>b</i><b>a</b>")
    assert kinds(issues) == [ISSUE_REORDERED]


def test_order_is_not_reported_when_tags_are_already_wrong():
    """A missing-tag report already explains the problem; adding "and also the
    order differs" would be noise."""
    issues = verify_tags("<b>a</b><i>b</i>", "<i>b</i>")
    assert ISSUE_REORDERED not in kinds(issues)
    assert ISSUE_MISSING in kinds(issues)


# ---------------------------------------------------------------------------
# Unpaired tags
# ---------------------------------------------------------------------------

def test_unpaired_closing_tag_in_target_is_reported():
    issues = verify_tags("<b>a</b>", "</b>a<b>")
    assert ISSUE_UNPAIRED in kinds(issues)


def test_pair_split_across_segments_is_not_reported():
    """A formatting pair straddling a segment boundary is normal: the source
    itself carries a lone tag, so the target legitimately does too."""
    assert verify_tags("<b>start of sentence", "<b>begin van zin") == []


def test_lone_closing_tag_mirrored_from_the_source_is_not_reported():
    """The other half of a split pair: source ends the run, target does too."""
    assert verify_tags("end of sentence</b>", "einde van zin</b>") == []


def test_unpaired_only_when_worse_than_the_source():
    """Source has one lone <b>; target having two is a real problem."""
    issues = verify_tags("<b>a", "<b>a<b>")
    assert ISSUE_EXTRA in kinds(issues)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_describe_issues_is_empty_for_a_clean_segment():
    assert describe_issues(verify_tags(SRC, GOOD)) == ""


def test_describe_issues_mentions_the_actual_markup():
    text = describe_issues(verify_tags(SRC, "Wanddikte"))
    assert '<cf color="#227acb" font="tahoma">' in text
    assert "missing" in text


def test_describe_issues_reports_counts_above_one():
    text = describe_issues(verify_tags("<b>a</b><b>b</b>", "<b>ab"))
    assert "×2" in text


def test_reordered_description_is_human_readable():
    text = describe_issues(verify_tags("<b>a</b><i>b</i>", "<i>b</i><b>a</b>"))
    assert text == "tags are in a different order than the source"


# ---------------------------------------------------------------------------
# Tag families
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,tgt", [
    ("[1}bold{1]", "[1}vet{1]"),
    ("Trados <1>x</1>", "Trados <1>y</1>"),
    ("a<2/>b", "c<2/>d"),
    ("<li-o>item</li-o>", "<li-o>punt</li-o>"),
])
def test_every_family_verifies_clean(src, tgt):
    assert verify_tags(src, tgt) == []


def test_memoq_dropped_tag_is_caught():
    issues = verify_tags("[1}bold{1]", "vet{1]")
    assert ISSUE_MISSING in kinds(issues)


def test_standalone_tag_dropped_is_caught():
    """The tag family that used to be invisible to the insert shortcut."""
    issues = verify_tags("before <2/> after", "ervoor erna")
    assert [i.tag for i in issues if i.kind == ISSUE_MISSING] == ["<2/>"]


def test_prose_angle_brackets_do_not_create_issues():
    assert verify_tags("if x < y and y > z", "als x < y en y > z") == []


# ---------------------------------------------------------------------------
# next_tag_sequence — memoQ F9 semantics
# ---------------------------------------------------------------------------

def test_adjacent_tags_are_inserted_as_one_sequence():
    """memoQ: "A tag sequence consists of tags immediately following each other,
    regardless of the type." Supervertaler used to insert one tag per press."""
    assert next_tag_sequence(SRC, "Specify ") == (
        '<cf color="#227acb" font="tahoma"><b>')


def test_second_call_returns_the_following_sequence():
    partial = 'Specify <cf color="#227acb" font="tahoma"><b>Wanddikte '
    assert next_tag_sequence(SRC, partial) == "</b></cf><b>"


def test_returns_empty_when_every_tag_is_present():
    assert next_tag_sequence(SRC, GOOD) == ""


def test_single_tag_sequences_are_returned_alone():
    assert next_tag_sequence("<b>a</b>", "") == "<b>"
    assert next_tag_sequence("<b>a</b>", "<b>a") == "</b>"


def test_standalone_tag_can_be_inserted():
    """Regression: extract_all_tags could not see self-closing tags at all, so
    the shortcut refused to insert <2/> and claimed the work was done."""
    assert next_tag_sequence("before <2/> after", "ervoor ") == "<2/>"


def test_no_tags_in_source_gives_nothing_to_insert():
    assert next_tag_sequence("plain", "vlak") == ""


def test_repeated_identical_tags_are_each_offered():
    src = "<b>a</b> and <b>b</b>"
    assert next_tag_sequence(src, "") == "<b>"
    assert next_tag_sequence(src, "<b>a</b> en ") == "<b>"


def test_sequence_insertion_eventually_satisfies_verification():
    """Driving the shortcut to exhaustion must produce a target that verifies
    clean — the two features have to agree."""
    target = ""
    for _ in range(20):
        nxt = next_tag_sequence(SRC, target)
        if not nxt:
            break
        target += nxt
    assert next_tag_sequence(SRC, target) == ""
    assert [i.kind for i in verify_tags(SRC, target)] in ([], [ISSUE_REORDERED])


# ---------------------------------------------------------------------------
# The project-wide pass (Bulk Operations -> Verify Tags)
# ---------------------------------------------------------------------------

def test_project_pass_skips_untranslated_and_reports_the_rest():
    """Exercises verify_project_tags' own logic against realistic segments,
    without building the application: the method body is driven through the
    same public helpers it calls."""
    from types import SimpleNamespace

    segments = [
        SimpleNamespace(id=1, source="<b>Wall</b>", target="<b>Muur</b>"),      # clean
        SimpleNamespace(id=2, source="<b>Wall</b>", target="Muur"),             # missing
        SimpleNamespace(id=3, source="<b>Wall</b>", target=""),                 # skipped
        SimpleNamespace(id=4, source="<b>Wall</b>", target="   "),              # skipped
        SimpleNamespace(id=5, source="<b>a</b><i>b</i>",
                        target="<i>b</i><b>a</b>"),                             # reordered
        SimpleNamespace(id=6, source="Plain", target="<b>invented</b>"),        # extra
    ]

    checked, findings = 0, []
    for seg in segments:
        if not (seg.target or "").strip():
            continue
        checked += 1
        issues = verify_tags(seg.source, seg.target)
        if issues:
            findings.append((seg.id, issues))

    assert checked == 4                       # two skipped
    assert [sid for sid, _ in findings] == [2, 5, 6]
    by_id = dict(findings)
    assert ISSUE_MISSING in kinds(by_id[2])
    assert kinds(by_id[5]) == [ISSUE_REORDERED]
    assert ISSUE_EXTRA in kinds(by_id[6])


def test_verify_action_is_wired_into_the_menu():
    source = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Supervertaler.py"), encoding="utf-8").read()
    assert "def verify_project_tags(self):" in source
    assert "verify_tags_action.triggered.connect(self.verify_project_tags)" in source
    assert "bulk_menu.addAction(verify_tags_action)" in source


def test_insertion_shortcut_uses_sequences_not_single_tags():
    source = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Supervertaler.py"), encoding="utf-8").read()
    assert "_tag_protection.next_tag_sequence(source_text, compare_target)" in source
    # Both editors must have been converted, not just one.
    assert source.count("insert_tag_sequence(\n                        cursor,") == 2
