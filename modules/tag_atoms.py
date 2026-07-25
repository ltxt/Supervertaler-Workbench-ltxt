"""
Atomic (protected) inline tags for the translation grid.

Phase 1 of ``docs/development/TAG_PROTECTION_AND_DISPLAY_MODES_PLAN.md``.

An inline tag is rendered as a **single indivisible character** in the cell's
``QTextDocument``: one U+FFFC OBJECT REPLACEMENT CHARACTER carrying a
``QTextCharFormat`` that holds the tag's real markup. Qt then draws it through
:class:`TagAtomRenderer`. Because the tag occupies exactly one document
position, protection comes from Qt itself rather than from hand-written
keystroke guards:

* one Backspace/Delete removes the whole tag,
* the caret steps over it as a unit and can never land inside it,
* selection, drag and undo treat it as one object,
* typing cannot corrupt it into ``<>``.

The trade-off is that ``toPlainText()`` no longer returns the segment text —
it returns U+FFFC where each tag was. :func:`document_to_raw` is the inverse
and **every** read-back of a protected cell must go through it. This is the
same display-versus-storage split the grid already uses for WYSIWYG mode
(``Supervertaler._wysiwyg_document_to_tagged_text``), so the pattern is not new
to the codebase.

Storage is unchanged: segments stay plain ``str``. This module only affects how
a cell renders and how its content is read back.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from PyQt6.QtCore import QObject, QRectF, QSizeF, Qt
from PyQt6.QtGui import (
    QColor,
    QFontMetricsF,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextObjectInterface,
)

from modules import tag_protection as _tp
from modules.tag_protection import KIND_CLOSE, KIND_EMPTY, KIND_OPEN, TagToken

__all__ = [
    "OBJECT_REPLACEMENT_CHARACTER",
    "TAG_OBJECT_TYPE",
    "DETAIL_SHORT", "DETAIL_MEDIUM", "DETAIL_FILTERED", "DETAIL_LONG",
    "DETAIL_LEVELS",
    "TagAtomRenderer",
    "register_document", "install_atoms", "document_to_raw",
    "atom_tokens", "has_atoms", "insert_tag_atom", "atom_label",
]

#: The character Qt uses to stand in for an inline object.
OBJECT_REPLACEMENT_CHARACTER = "￼"

#: Our custom text-object type id.
TAG_OBJECT_TYPE = QTextFormat.ObjectTypes.UserObject.value + 1

_P = QTextFormat.Property.UserProperty.value
#: The tag's exact original markup — the only thing needed to serialise back.
PROP_RAW = _P + 1
#: Cached display label, so painting does not recompute it.
PROP_LABEL = _P + 2
PROP_KIND = _P + 3
PROP_NUMBER = _P + 4
PROP_FAMILY = _P + 5

# Detail levels — memoQ's four (see the plan, §5.1).
DETAIL_SHORT = "short"        # number + role  → "Partial Tag Text"
DETAIL_MEDIUM = "medium"      # type + name, no attributes
DETAIL_FILTERED = "filtered"  # type + name + selected attributes (see below)
DETAIL_LONG = "long"          # the complete markup → "Full Tag Text"

DETAIL_LEVELS = (DETAIL_SHORT, DETAIL_MEDIUM, DETAIL_FILTERED, DETAIL_LONG)

#: DETAIL_FILTERED is defined for parity with memoQ but is NOT selectable yet.
#: memoQ derives "which attributes matter" from the document-type / filter
#: configuration. Supervertaler has no equivalent, and a hardcoded allowlist was
#: rejected as a stand-in, so this level renders as MEDIUM until a real
#: per-format attribute source exists. Keeping the constant means the setting
#: value stays valid if an older settings file carries it.


def atom_label(token: TagToken, detail: str) -> str:
    """The text shown inside the pill for ``token`` at this detail level."""
    if token.family == _tp.FAMILY_PLACEHOLDER:
        # A software placeholder is already minimal, and its number is part of
        # its meaning: showing {0} as "1" because it happens to be the first tag
        # in the segment would be actively wrong. Render it verbatim at every
        # level.
        return token.raw
    if detail == DETAIL_LONG:
        return token.raw
    if detail == DETAIL_MEDIUM:
        return token.medium_label()
    if detail == DETAIL_FILTERED:
        # No per-format attribute source yet — see the note above.
        return token.medium_label()
    # DETAIL_SHORT — memoQ: "a tag, its number, and if it is an opening, a
    # closing, or an empty tag". The role is carried by a slash rather than an
    # icon, so the distinction survives in plain text and in tests.
    if token.kind == KIND_CLOSE:
        return f"/{token.number}"
    if token.kind == KIND_EMPTY:
        return f"{token.number}/"
    return f"{token.number}"


class TagAtomRenderer(QObject, QTextObjectInterface):
    """Draws a protected tag as a small rounded pill.

    One instance can serve every document in the application; colours are
    instance state so a theme change is a single assignment plus a repaint,
    rather than a walk over every cell.
    """

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.text_color = QColor("#7f0001")   # memoQ-ish dark red
        self.fill_color = QColor("#7f0001")
        self.fill_color.setAlpha(28)
        self.border_color = QColor("#7f0001")
        self.border_color.setAlpha(110)
        self.h_padding = 3.0
        self.radius = 3.0

    # -- theming -------------------------------------------------------
    def set_color(self, color: str | QColor) -> None:
        base = QColor(color)
        if not base.isValid():
            return
        self.text_color = QColor(base)
        fill = QColor(base)
        fill.setAlpha(28)
        self.fill_color = fill
        border = QColor(base)
        border.setAlpha(110)
        self.border_color = border

    # -- QTextObjectInterface -----------------------------------------
    #
    # Both callbacks are handed a plain QTextFormat, not the QTextCharFormat
    # that was set on the cursor, so the character-level accessors (.font())
    # only exist after toCharFormat(). They are also called from C++: an
    # exception raised here aborts the process rather than propagating, so both
    # are defensive throughout.

    def intrinsicSize(self, doc, posInDocument, fmt) -> QSizeF:  # noqa: N802
        try:
            char_fmt = fmt.toCharFormat()
            label = char_fmt.property(PROP_LABEL) or ""
            metrics = QFontMetricsF(char_fmt.font())
            width = metrics.horizontalAdvance(label) + 2 * self.h_padding
            return QSizeF(max(width, 1.0), max(metrics.height(), 1.0))
        except Exception:
            return QSizeF(1.0, 1.0)

    def drawObject(self, painter: QPainter, rect: QRectF, doc,  # noqa: N802
                   posInDocument, fmt) -> None:
        try:
            char_fmt = fmt.toCharFormat()
            label = char_fmt.property(PROP_LABEL) or ""
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(self.border_color)
                painter.setBrush(self.fill_color)
                # Inset half a pixel so the 1px border lands on the pixel grid.
                painter.drawRoundedRect(
                    rect.adjusted(0.5, 0.5, -0.5, -0.5),
                    self.radius, self.radius)
                painter.setPen(self.text_color)
                painter.setFont(char_fmt.font())
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)
            finally:
                painter.restore()
        except Exception:
            pass


# One renderer for the whole application. Module-level so Python never garbage
# collects it out from under Qt's layout, which holds only a borrowed pointer.
_RENDERER: Optional[TagAtomRenderer] = None


def renderer() -> TagAtomRenderer:
    """The shared :class:`TagAtomRenderer`, created on first use."""
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = TagAtomRenderer()
    return _RENDERER


def register_document(doc: QTextDocument) -> None:
    """Teach ``doc``'s layout how to draw tag atoms. Idempotent and cheap."""
    layout = doc.documentLayout()
    if layout is None:
        return
    layout.registerHandler(TAG_OBJECT_TYPE, renderer())


def _atom_format(token: TagToken, detail: str,
                 base: Optional[QTextCharFormat] = None) -> QTextCharFormat:
    fmt = QTextCharFormat(base) if base is not None else QTextCharFormat()
    fmt.setObjectType(TAG_OBJECT_TYPE)
    fmt.setProperty(PROP_RAW, token.raw)
    fmt.setProperty(PROP_LABEL, atom_label(token, detail))
    fmt.setProperty(PROP_KIND, token.kind)
    fmt.setProperty(PROP_NUMBER, token.number)
    fmt.setProperty(PROP_FAMILY, token.family)
    fmt.setToolTip(token.tooltip())
    return fmt


def install_atoms(
    doc: QTextDocument,
    raw_text: str,
    detail: str = DETAIL_SHORT,
    tokens: Optional[Sequence[TagToken]] = None,
    strict: bool = True,
) -> int:
    """Replace ``doc``'s content with ``raw_text``, tags rendered as atoms.

    Args:
        doc: Target document. Its layout is registered automatically.
        raw_text: The segment text, tags and all.
        detail: One of :data:`DETAIL_LEVELS`.
        tokens: Pre-parsed tags, e.g. from a structured import where identity
            came from the file's own markup. Parsed from ``raw_text`` when
            omitted. Must be numbered already (or numbers show as 0).
        strict: Passed to :func:`tag_protection.parse_tags` when parsing.

    Returns:
        How many atoms were inserted.
    """
    register_document(doc)

    if tokens is None:
        tokens = _tp.parse_tags(raw_text, strict=strict, number=True)

    doc.clear()
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    try:
        plain = QTextCharFormat()
        pos = 0
        count = 0
        for token in tokens:
            if token.start < pos:      # defensive: never move backwards
                continue
            if token.start > pos:
                cursor.insertText(raw_text[pos:token.start], plain)
            cursor.insertText(OBJECT_REPLACEMENT_CHARACTER,
                              _atom_format(token, detail))
            # The custom properties survive on the format Qt keeps for
            # subsequent insertText calls, so text after an atom must be
            # inserted with an explicitly plain format or it inherits PROP_RAW
            # and confuses anything that inspects formats.
            cursor.setCharFormat(plain)
            pos = token.end
            count += 1
        if pos < len(raw_text):
            cursor.insertText(raw_text[pos:], plain)
    finally:
        cursor.endEditBlock()
    return count


def _fragment_atom_raw(fmt: QTextCharFormat) -> Optional[str]:
    """The markup behind an atom fragment, or None if it is ordinary text.

    Keys on the object type, never on the presence of :data:`PROP_RAW`: Qt
    carries custom properties forward onto text typed after an atom, so the
    property alone is not evidence of an atom.
    """
    if fmt.objectType() != TAG_OBJECT_TYPE:
        return None
    raw = fmt.property(PROP_RAW)
    return raw if isinstance(raw, str) else ""


def document_to_raw(doc: QTextDocument) -> str:
    """Serialise a protected document back to segment text.

    The inverse of :func:`install_atoms`. Safe on documents with no atoms, in
    which case it is equivalent to ``toPlainText()``.
    """
    parts: list[str] = []
    block = doc.begin()
    first = True
    while block.isValid():
        if not first:
            parts.append("\n")
        first = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                raw = _fragment_atom_raw(frag.charFormat())
                if raw is None:
                    parts.append(frag.text())
                else:
                    # Two adjacent identical tags can share one fragment, so
                    # emit the markup once per replacement character.
                    n = frag.text().count(OBJECT_REPLACEMENT_CHARACTER)
                    parts.append(raw * max(n, 1))
            it += 1
        block = block.next()

    out = "".join(parts)
    # QTextDocument uses U+2028 (line separator) and U+2029 (paragraph
    # separator); normalise both to "\n", as the WYSIWYG read-back does.
    return out.replace(chr(0x2028), "\n").replace(chr(0x2029), "\n")


def atom_tokens(doc: QTextDocument) -> list[dict]:
    """Describe the atoms in ``doc``, in document order.

    Each entry carries ``position``, ``raw``, ``kind``, ``number`` and
    ``family`` — enough for tag verification and for the insert shortcuts,
    without re-parsing text.
    """
    found: list[dict] = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fmt = frag.charFormat()
                raw = _fragment_atom_raw(fmt)
                if raw is not None:
                    text = frag.text()
                    for offset, ch in enumerate(text):
                        if ch != OBJECT_REPLACEMENT_CHARACTER:
                            continue
                        found.append({
                            "position": frag.position() + offset,
                            "raw": raw,
                            "kind": fmt.property(PROP_KIND),
                            "number": fmt.property(PROP_NUMBER),
                            "family": fmt.property(PROP_FAMILY),
                        })
            it += 1
        block = block.next()
    return found


def has_atoms(doc: QTextDocument) -> bool:
    """True when ``doc`` contains at least one protected tag."""
    return OBJECT_REPLACEMENT_CHARACTER in doc.toPlainText()


def insert_tag_atom(cursor: QTextCursor, raw_tag: str,
                    detail: str = DETAIL_SHORT,
                    token: Optional[TagToken] = None) -> bool:
    """Insert ``raw_tag`` at ``cursor`` as a protected atom.

    Used by the tag-insertion shortcuts so an inserted tag is protected the
    same way an imported one is. Returns False when ``raw_tag`` does not parse
    as a tag.
    """
    if token is None:
        parsed = _tp.parse_tags(raw_tag, number=True)
        if not parsed:
            return False
        token = parsed[0]
    register_document(cursor.document())
    cursor.insertText(OBJECT_REPLACEMENT_CHARACTER, _atom_format(token, detail))
    cursor.setCharFormat(QTextCharFormat())
    return True


def relabel_atoms(doc: QTextDocument, detail: str) -> int:
    """Switch every atom in ``doc`` to a different detail level in place.

    This is the Partial ⇄ Full display switch: only the pill's label changes,
    so no text is rewritten and nothing can be lost. Returns the atom count.
    """
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    changed = 0
    try:
        for info in atom_tokens(doc):
            raw = info["raw"]
            parsed = _tp.parse_tags(raw, number=False)
            if not parsed:
                continue
            token = parsed[0]
            # Keep the number that was assigned at load time; memoQ likewise
            # renumbers only when a document is reopened.
            from dataclasses import replace as _replace
            token = _replace(token, number=info["number"] or 0)
            cursor.setPosition(info["position"])
            cursor.setPosition(info["position"] + 1,
                               QTextCursor.MoveMode.KeepAnchor)
            fmt = _atom_format(token, detail)
            cursor.setCharFormat(fmt)
            changed += 1
    finally:
        cursor.endEditBlock()
    return changed
