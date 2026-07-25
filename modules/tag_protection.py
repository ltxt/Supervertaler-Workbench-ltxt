"""
Canonical inline-tag model for Supervertaler.

Phase 0 of the tag-protection work (see
``docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md``). This module is
the single source of truth for *what counts as an inline tag*, replacing the
several ad-hoc regexes that had drifted apart across the codebase:

* ``Supervertaler._ALL_TAGS_PATTERN``   – missed every self-closing tag
* ``Supervertaler._AUTOTAG_TAG_PATTERN`` – matched self-closing tags
* ``Supervertaler.extract_html_tags``   – no hyphenated names (``<li-o>``)
* ``TagHighlighter.highlightBlock``     – the most complete set, but built
  inline and recompiled per text block

Two levels of API are provided:

``LEGACY_TAG_PATTERN``
    A permissive single-group alternation covering the three tag families the
    old ``_ALL_TAGS_PATTERN`` / ``_AUTOTAG_TAG_PATTERN`` covered (memoQ
    brackets, HTML/XML, Trados numeric) **including self-closing forms**. Used
    by the existing string-level helpers so their behaviour is unchanged apart
    from that one fix.

``parse_tags()``
    The structured model: returns :class:`TagToken` objects with positions,
    kind, family and (optionally) memoQ-compatible numbering. This is what the
    later phases — atomic rendering, the Partial/Full display switch and tag
    verification — are built on. It applies genuineness rules so ordinary prose
    containing ``<`` and ``>`` (``if x < y and y > z``) is not mistaken for
    markup.

Tag *identity* should come from a document's real structured markup wherever
the format provides it (SDLXLIFF ``<g>``/``<x>``, memoQ brackets, DVX codes).
:func:`parse_tags` is the fallback for formats that carry no such structure,
and the ``origin`` field records which of the two a token came from.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

__all__ = [
    "KIND_OPEN", "KIND_CLOSE", "KIND_EMPTY",
    "FAMILY_HTML", "FAMILY_TRADOS_NUMERIC", "FAMILY_MEMOQ_BRACKET",
    "FAMILY_MEMOQ_CONTENT", "FAMILY_DEJAVU", "FAMILY_COMPACT",
    "LEGACY_FAMILIES", "ALL_FAMILIES",
    "ORIGIN_STRUCTURED", "ORIGIN_HEURISTIC",
    "WORD_JOINER", "LEGACY_TAG_PATTERN",
    "TagToken", "parse_tags", "assign_numbers", "tag_sequences",
    "extract_raw_tags", "strip_tags",
    "ISSUE_MISSING", "ISSUE_EXTRA", "ISSUE_REORDERED", "ISSUE_UNPAIRED",
    "ISSUE_KINDS", "TagIssue", "verify_tags", "describe_issues", "tags_match",
    "next_tag_sequence",
]


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: An opening tag of a pair (``<b>``, ``[1}``, ``<g1>``).
KIND_OPEN = "open"
#: A closing tag of a pair (``</b>``, ``{1]``, ``</g1>``).
KIND_CLOSE = "close"
#: A standalone tag. memoQ calls these "empty" tags; they never pair, and each
#: one gets its own number.
KIND_EMPTY = "empty"

FAMILY_HTML = "html"                      # <b>, </b>, <br/>, <cf color="…">
FAMILY_TRADOS_NUMERIC = "trados_numeric"  # <1>, </1>, <1/>   (SDLXLIFF g/x)
FAMILY_MEMOQ_BRACKET = "memoq_bracket"    # [1}, {1], [1]
FAMILY_MEMOQ_CONTENT = "memoq_content"    # [uicontrol id="…"], {uicontrol}
FAMILY_DEJAVU = "dejavu"                  # {00108}
FAMILY_COMPACT = "compact"                # {1}, {/1}, {1/}  (internal display)

#: The three families the legacy string helpers have always covered.
LEGACY_FAMILIES = (FAMILY_MEMOQ_BRACKET, FAMILY_HTML, FAMILY_TRADOS_NUMERIC)

#: Every family this module knows how to parse.
ALL_FAMILIES = (
    FAMILY_DEJAVU, FAMILY_MEMOQ_BRACKET, FAMILY_COMPACT,
    FAMILY_TRADOS_NUMERIC, FAMILY_HTML, FAMILY_MEMOQ_CONTENT,
)

#: Tag identity came from the source document's real markup (SDLXLIFF ``<g>``/
#: ``<x>``, memoQ brackets, …). Always protected; never re-derived from text.
ORIGIN_STRUCTURED = "structured"
#: Tag identity was inferred by scanning text. Subject to the genuineness rules.
ORIGIN_HEURISTIC = "heuristic"

#: U+2060 WORD JOINER. ``protect_tags_from_linebreak()`` inserts these inside
#: tags for display so word-wrap cannot split them; parsing tolerates them.
WORD_JOINER = "⁠"

_WJ = WORD_JOINER + "?"

# A tag name: XML-ish. Deliberately excludes a leading digit so numeric tags are
# handled by their own family (and so "<5 > 3" cannot be read as a tag name).
_NAME = r"[A-Za-z_][A-Za-z0-9._:-]*"

#: Longest plausible inline tag. Anything longer is prose, not markup.
_MAX_TAG_LEN = 512


# --------------------------------------------------------------------------
# The legacy permissive pattern (behaviour-compatible shim source)
# --------------------------------------------------------------------------

#: Single-capturing-group alternation over :data:`LEGACY_FAMILIES`, matching the
#: historic ``_AUTOTAG_TAG_PATTERN`` — i.e. the old ``_ALL_TAGS_PATTERN`` plus
#: self-closing forms. Kept permissive on purpose: the string-level helpers in
#: ``Supervertaler.py`` share it, so tightening happens only where callers move
#: to :func:`parse_tags`.
LEGACY_TAG_PATTERN = (
    r"(\[\d+\}|\{\d+\]|\[\d+\]"                      # memoQ [1} {1] [1]
    r"|</?\d+/?>"                                    # Trados <1> </1> <1/>
    r"|</?[a-zA-Z][a-zA-Z0-9-]*(?:\s+[^>]*)?/?>)"    # HTML/XML incl. attrs
)

_LEGACY_RE = re.compile(LEGACY_TAG_PATTERN)


# --------------------------------------------------------------------------
# Structured parser
# --------------------------------------------------------------------------

# Ordered alternation. Narrower families first so a 5-digit Déjà Vu code is not
# taken for a compact placeholder, and the very broad memoQ content forms are
# tried last.
_TOKEN_RE = re.compile(
    # {00108} — Déjà Vu, exactly five digits
    r"(?P<dejavu>\{(?P<dvx_num>\d{5})\})"
    # [1} {1] [1] — memoQ numeric brackets
    r"|(?P<mqb>\[(?P<mqb_open>\d+)\}|\{(?P<mqb_close>\d+)\]|\[(?P<mqb_empty>\d+)\])"
    # {1} {/1} {1/} — internal compact placeholders (1–4 digits)
    r"|(?P<compact>\{" + _WJ + r"(?P<c_slash>/?)" + _WJ +
    r"(?P<c_num>\d{1,4})(?P<c_self>/?)" + _WJ + r"\})"
    # <1> </1> <1/> — Trados/SDLXLIFF numeric. No attributes, no whitespace.
    r"|(?P<num><" + _WJ + r"(?P<num_slash>/?)" + _WJ +
    r"(?P<num_id>\d+)(?P<num_self>/?)" + _WJ + r">)"
    # <b> </b> <br/> <cf color="…"> — HTML/XML.
    # h_self catches a "/" with no attributes before it ("<br/>"); when there
    # *are* attributes the greedy h_attrs swallows the slash instead, and
    # _classify_html() moves it back out.
    r"|(?P<html><" + _WJ + r"(?P<h_slash>/?)" + _WJ +
    r"(?P<h_name>" + _NAME + r")(?P<h_attrs>(?:\s[^<>]*)?)"
    r"(?P<h_self>/?)" + _WJ + r">)"
    # [uicontrol id="…"] and {uicontrol} — memoQ content tags
    r"|(?P<mqc>\[[A-Za-z][^\[\]\}]*\s[^\[\]\}]*\]|\{[A-Za-z][A-Za-z0-9_-]*\})"
)

# Attributes are well formed when they are a whitespace-separated run of
# key="value" / key='value' pairs. This is what separates a real tag from prose:
# "< b or c >" has bare words where attributes should be, so it is rejected.
_ATTRS_STRICT_RE = re.compile(
    r"(?:\s+[A-Za-z_:][A-Za-z0-9._:-]*\s*=\s*(?:\"[^\"<>]*\"|'[^'<>]*'))*\s*"
)

_ID_ATTR_RE = re.compile(
    r"""\bid\s*=\s*(?:"([^"<>]*)"|'([^'<>]*)')""", re.IGNORECASE
)


def _attrs_well_formed(attrs: str) -> bool:
    """True when ``attrs`` is empty/whitespace or only ``key="value"`` pairs."""
    return _ATTRS_STRICT_RE.fullmatch(attrs or "") is not None


@dataclass(frozen=True)
class TagToken:
    """One inline tag found in a segment.

    ``number`` and ``pair_id`` are 0/-1 until :func:`assign_numbers` runs;
    ``parse_tags(number=True)`` does that for you.
    """

    raw: str                     # exact substring, e.g. '<cf color="#227acb">'
    kind: str                    # KIND_OPEN | KIND_CLOSE | KIND_EMPTY
    name: str                    # 'cf', 'b', '1' (numeric families)
    family: str                  # FAMILY_*
    start: int                   # offset in the parsed text
    end: int                     # end offset (exclusive)
    attrs: str = ""              # raw attribute text, '' when none
    tag_id: str = ""             # value of an id="…" attribute, '' when none
    origin: str = ORIGIN_HEURISTIC
    number: int = 0              # memoQ-style display number (1-based)
    pair_id: int = -1            # equal for the two halves of a pair

    # -- derived ---------------------------------------------------------
    @property
    def pair_key(self) -> tuple:
        """Family + name — what makes two tags *candidate* halves of a pair.

        memoQ finds pairs "based on tags' names and id attributes (when
        present — for example, rpr tags in DOCX files)". In practice a closing
        tag rarely carries attributes (``</g>`` closes ``<g id="1">``), so the
        name is the primary key and :attr:`tag_id` is only a tie-breaker, used
        when both halves carry one. See :func:`assign_numbers`.
        """
        return (self.family, self.name.lower())

    @property
    def is_paired(self) -> bool:
        return self.kind in (KIND_OPEN, KIND_CLOSE)

    def short_label(self) -> str:
        """Partial/short view label: the number, plus the pair role."""
        if self.kind == KIND_OPEN:
            return f"{self.number}"
        if self.kind == KIND_CLOSE:
            return f"/{self.number}"
        return f"{self.number}"

    def medium_label(self) -> str:
        """Medium view: type + name, no attributes."""
        slash = "/" if self.kind == KIND_CLOSE else ""
        tail = "/" if self.kind == KIND_EMPTY and self.family in (
            FAMILY_HTML, FAMILY_TRADOS_NUMERIC) else ""
        return f"{slash}{self.name}{tail}"

    def tooltip(self) -> str:
        """Full detail for the Partial-view hover tooltip."""
        role = {KIND_OPEN: "opening", KIND_CLOSE: "closing",
                KIND_EMPTY: "standalone"}[self.kind]
        return f"{role} tag {self.number} · {self.family}\n{self.raw}"


def _classify_html(slash: str, attrs: str, self_marker: str) -> tuple[str, str]:
    """Return (kind, cleaned_attrs) for an HTML-family match.

    ``self_marker`` is the regex's own trailing-slash group, which only fires
    for a self-closing tag with no attributes (``<br/>``). When attributes are
    present the greedy attribute group swallows the slash, so it is moved back
    out here rather than being teased apart in the pattern.
    """
    self_closing = self_marker == "/"
    cleaned = attrs or ""
    stripped = cleaned.rstrip(WORD_JOINER).rstrip()
    if stripped.endswith("/"):
        self_closing = True
        cleaned = stripped[:-1]
    if slash == "/":
        return KIND_CLOSE, cleaned
    if self_closing:
        return KIND_EMPTY, cleaned
    return KIND_OPEN, cleaned


def parse_tags(
    text: str,
    families: Sequence[str] | None = None,
    strict: bool = True,
    origin: str = ORIGIN_HEURISTIC,
    number: bool = False,
) -> list[TagToken]:
    """Parse ``text`` into :class:`TagToken` objects, in document order.

    Args:
        text: The segment text to scan.
        families: Restrict to these ``FAMILY_*`` values. Defaults to all.
        strict: Apply the genuineness rules — reject a candidate whose
            attributes are not ``key="value"`` shaped, that spans a newline, or
            that is implausibly long. Turn this off only for text whose tags are
            already known to be genuine.
        origin: Recorded on every token; pass :data:`ORIGIN_STRUCTURED` when the
            tags came from the document's real markup.
        number: Also run :func:`assign_numbers`.

    Returns:
        Tokens in order of appearance. Never overlapping.
    """
    if not text:
        return []
    allowed = set(families) if families is not None else set(ALL_FAMILIES)

    tokens: list[TagToken] = []
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0)

        if strict and (len(raw) > _MAX_TAG_LEN or "\n" in raw or "\r" in raw):
            continue

        fam = kind = name = ""
        attrs = ""

        if m.group("dejavu") is not None:
            fam, kind, name = FAMILY_DEJAVU, KIND_EMPTY, m.group("dvx_num")
        elif m.group("mqb") is not None:
            fam = FAMILY_MEMOQ_BRACKET
            if m.group("mqb_open") is not None:
                kind, name = KIND_OPEN, m.group("mqb_open")
            elif m.group("mqb_close") is not None:
                kind, name = KIND_CLOSE, m.group("mqb_close")
            else:
                kind, name = KIND_EMPTY, m.group("mqb_empty")
        elif m.group("compact") is not None:
            fam, name = FAMILY_COMPACT, m.group("c_num")
            if m.group("c_slash") == "/":
                kind = KIND_CLOSE
            elif m.group("c_self") == "/":
                kind = KIND_EMPTY
            else:
                kind = KIND_OPEN
        elif m.group("num") is not None:
            fam, name = FAMILY_TRADOS_NUMERIC, m.group("num_id")
            if m.group("num_slash") == "/":
                kind = KIND_CLOSE
            elif m.group("num_self") == "/":
                kind = KIND_EMPTY
            else:
                kind = KIND_OPEN
        elif m.group("html") is not None:
            fam, name = FAMILY_HTML, m.group("h_name")
            kind, attrs = _classify_html(
                m.group("h_slash"), m.group("h_attrs"), m.group("h_self"))
            if strict and not _attrs_well_formed(attrs):
                continue
            # A closing tag MAY carry attributes: memoQ emits
            # ``</cmt id="0" transform="close">`` as the partner of
            # ``<cmt id="0" transform="open">`` (seen in real MQXLIFF exports).
            # Prose like "</b junk>" is already excluded by the attribute
            # well-formedness check above.
        elif m.group("mqc") is not None:
            fam = FAMILY_MEMOQ_CONTENT
            body = raw[1:-1]
            name = re.split(r"[\s=]", body, 1)[0]
            kind = KIND_CLOSE if raw[0] == "{" else KIND_OPEN
            attrs = body[len(name):]
        else:  # pragma: no cover — every branch of the alternation is handled
            continue

        if fam not in allowed:
            continue

        id_match = _ID_ATTR_RE.search(attrs) if attrs else None
        tag_id = ""
        if id_match:
            tag_id = id_match.group(1) or id_match.group(2) or ""

        tokens.append(TagToken(
            raw=raw, kind=kind, name=name, family=fam,
            start=m.start(), end=m.end(),
            attrs=attrs, tag_id=tag_id, origin=origin,
        ))

    if strict:
        tokens = _drop_unopened_content_tags(tokens)

    return assign_numbers(tokens) if number else tokens


def _drop_unopened_content_tags(tokens: list[TagToken]) -> list[TagToken]:
    """Reject ``{name}`` memoQ content closers with no ``[name …]`` opener.

    memoQ content tags come in pairs — ``[uicontrol id="…"]`` … ``{uicontrol}``
    — but the closing form is just an identifier in braces, which ordinary text
    hits all the time. A real MQXLIFF export of an IDML file contained::

        Equation (text): softmax(x)_i = e^{x_i} / Σ_j e^{x_j}.

    where ``{x_i}`` and ``{x_j}`` are mathematics, not markup. Protecting those
    would make the formula uneditable. Requiring the opener keeps genuine memoQ
    content tags and leaves maths, code and set notation alone.
    """
    openers = {t.name.lower() for t in tokens
               if t.family == FAMILY_MEMOQ_CONTENT and t.kind == KIND_OPEN}
    return [t for t in tokens
            if not (t.family == FAMILY_MEMOQ_CONTENT
                    and t.kind == KIND_CLOSE
                    and t.name.lower() not in openers)]


def assign_numbers(tokens: Iterable[TagToken]) -> list[TagToken]:
    """Number tokens the way memoQ does.

    From the memoQ 12.4 documentation (Ribbons › Edit): "Tags are numbered as
    they occur in the text, from left to right. Opening and closing tags have
    the same number. Empty tags have a unique number."

    Callers should assign numbers **once per document load** and keep them for
    the session — memoQ likewise only renumbers when a document is reopened, so
    numbers never shift under the translator's cursor mid-edit.
    """
    out: list[TagToken] = []
    counter = 0
    # (pair_key, tag_id, number) for each still-open tag, innermost last.
    open_stack: list[tuple[tuple, str, int]] = []

    for tok in tokens:
        if tok.kind == KIND_OPEN:
            counter += 1
            open_stack.append((tok.pair_key, tok.tag_id, counter))
            out.append(replace(tok, number=counter, pair_id=counter))
        elif tok.kind == KIND_CLOSE:
            num = None
            for i in range(len(open_stack) - 1, -1, -1):
                open_key, open_id, open_num = open_stack[i]
                if open_key != tok.pair_key:
                    continue
                # Only let ids veto a match when both halves carry one.
                if tok.tag_id and open_id and tok.tag_id != open_id:
                    continue
                num = open_num
                del open_stack[i:]
                break
            if num is None:
                # Closing tag with no opener in this segment — common where a
                # pair straddles a segment boundary. It gets its own number.
                counter += 1
                num = counter
            out.append(replace(tok, number=num, pair_id=num))
        else:
            counter += 1
            out.append(replace(tok, number=counter, pair_id=counter))

    return out


def tag_sequences(tokens: Sequence[TagToken]) -> list[list[TagToken]]:
    """Group tokens into runs of immediately-adjacent tags.

    memoQ's *Copy Next Tag Sequence* (F9) works on these rather than on single
    tags: "A tag sequence consists of tags immediately following each other,
    regardless of the type."
    """
    runs: list[list[TagToken]] = []
    for tok in tokens:
        if runs and runs[-1][-1].end == tok.start:
            runs[-1].append(tok)
        else:
            runs.append([tok])
    return runs


# --------------------------------------------------------------------------
# String-level helpers
# --------------------------------------------------------------------------

def extract_raw_tags(
    text: str,
    families: Sequence[str] | None = None,
    strict: bool = True,
) -> list[str]:
    """The raw tag strings in order — :func:`parse_tags` without the objects."""
    return [t.raw for t in parse_tags(text, families=families, strict=strict)]


def strip_tags(text: str) -> str:
    """Remove every legacy-family inline tag, leaving the words.

    Uses :data:`LEGACY_TAG_PATTERN` so it stays byte-compatible with the
    historic ``Supervertaler.strip_all_tags``.
    """
    return _LEGACY_RE.sub("", text or "")


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

#: A tag in the source that the target does not have (enough of).
ISSUE_MISSING = "missing"
#: A tag in the target that the source does not have.
ISSUE_EXTRA = "extra"
#: Same tags on both sides, but in a different order.
ISSUE_REORDERED = "reordered"
#: A closing tag with no opener, or an opener never closed — and the source is
#: not unpaired in the same way, so it is not simply a pair split across
#: segments.
ISSUE_UNPAIRED = "unpaired"

ISSUE_KINDS = (ISSUE_MISSING, ISSUE_EXTRA, ISSUE_REORDERED, ISSUE_UNPAIRED)


@dataclass(frozen=True)
class TagIssue:
    """One problem found when comparing a target's tags against its source."""

    kind: str              # one of ISSUE_KINDS
    tag: str = ""          # the raw markup at fault, "" for order problems
    count: int = 1         # how many occurrences are missing/extra
    detail: str = ""       # human-readable explanation

    def describe(self) -> str:
        if self.detail:
            return self.detail
        if self.kind == ISSUE_MISSING:
            return f"missing {self.tag}" + (f" ×{self.count}" if self.count > 1 else "")
        if self.kind == ISSUE_EXTRA:
            return f"unexpected {self.tag}" + (f" ×{self.count}" if self.count > 1 else "")
        if self.kind == ISSUE_REORDERED:
            return "tags are in a different order than the source"
        return f"{self.kind} {self.tag}".strip()


def _unpaired_tags(tokens: Sequence[TagToken]) -> list[str]:
    """Raw markup of tags in ``tokens`` that have no partner, in order."""
    stack: list[TagToken] = []
    unpaired: list[str] = []
    for tok in tokens:
        if tok.kind == KIND_OPEN:
            stack.append(tok)
        elif tok.kind == KIND_CLOSE:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i].pair_key == tok.pair_key:
                    del stack[i]
                    break
            else:
                unpaired.append(tok.raw)
    unpaired.extend(tok.raw for tok in stack)
    return unpaired


def verify_tags(
    source_text: str,
    target_text: str,
    families: Sequence[str] | None = None,
    strict: bool = True,
) -> list[TagIssue]:
    """Compare a target's inline tags against its source.

    This is the check every major CAT tool offers and Supervertaler did not: it
    reports tags the translator dropped, invented, left unpaired, or moved out of
    order.

    An **empty target is never reported**. An untranslated segment is missing all
    of its tags by definition, and flagging that would bury the real problems.

    Args:
        source_text: The source segment.
        target_text: The translation.
        families: Tag families to consider. Defaults to **every** family, not
            just the three the legacy string helpers cover. A real Phrase
            export in the test corpus is full of ``{0}`` / ``{1}`` software
            placeholders; dropping one ships a broken string, so verification
            has to see them. Pass :data:`LEGACY_FAMILIES` for the narrower
            behaviour.
        strict: Passed through to :func:`parse_tags`.

    Returns:
        Issues, most structural first (missing, extra, unpaired, reordered).
        Empty when the tags are correct.
    """
    if not (target_text or "").strip():
        return []

    source_tokens = parse_tags(source_text, families=families, strict=strict)
    target_tokens = parse_tags(target_text, families=families, strict=strict)

    source_raw = [t.raw for t in source_tokens]
    target_raw = [t.raw for t in target_tokens]

    if not source_raw and not target_raw:
        return []

    issues: list[TagIssue] = []

    source_counts = Counter(source_raw)
    target_counts = Counter(target_raw)

    for tag, n in (source_counts - target_counts).items():
        issues.append(TagIssue(ISSUE_MISSING, tag, n))
    for tag, n in (target_counts - source_counts).items():
        issues.append(TagIssue(ISSUE_EXTRA, tag, n))

    # Only call the target unpaired if the source is not unpaired the same way:
    # a formatting pair split across two segments is normal in a CAT tool, and
    # both sides then legitimately carry a lone tag.
    source_unpaired = Counter(_unpaired_tags(source_tokens))
    for tag, n in (Counter(_unpaired_tags(target_tokens)) - source_unpaired).items():
        issues.append(TagIssue(
            ISSUE_UNPAIRED, tag, n,
            detail=f"{tag} has no matching partner in the target"))

    # Order only matters once the tag sets agree; otherwise the missing/extra
    # reports already describe the problem and an order complaint is noise.
    if source_counts == target_counts and source_raw != target_raw:
        issues.append(TagIssue(
            ISSUE_REORDERED,
            detail="tags are in a different order than the source"))

    return issues


def describe_issues(issues: Sequence[TagIssue]) -> str:
    """One-line summary of ``issues``, or "" when there are none."""
    return "; ".join(issue.describe() for issue in issues)


def tags_match(source_text: str, target_text: str,
               families: Sequence[str] | None = LEGACY_FAMILIES) -> bool:
    """True when the target carries exactly the source's tags, in order."""
    return not verify_tags(source_text, target_text, families=families)


# --------------------------------------------------------------------------
# Insertion helpers
# --------------------------------------------------------------------------

def next_tag_sequence(
    source_text: str,
    target_text: str,
    families: Sequence[str] | None = LEGACY_FAMILIES,
) -> str:
    """The next run of adjacent source tags not yet present in the target.

    memoQ's *Copy Next Tag Sequence* (F9) works on sequences rather than single
    tags: "A tag sequence consists of tags immediately following each other,
    regardless of the type. Always inserts the first tag sequence that has not
    been inserted yet." So ``<cf …><b>`` is inserted in one action, not two.

    Returns the concatenated markup of that run, or "" when the target already
    holds every source tag.
    """
    source_tokens = parse_tags(source_text, families=families)
    if not source_tokens:
        return ""

    remaining = Counter(t.raw for t in parse_tags(target_text, families=families))
    for run in tag_sequences(source_tokens):
        needed = Counter(tok.raw for tok in run)
        if any(remaining[tag] < n for tag, n in needed.items()):
            return "".join(tok.raw for tok in run)
        remaining -= needed
    return ""
