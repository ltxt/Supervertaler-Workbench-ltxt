"""
MQXLIFF Handler Module
======================
Handles import/export of memoQ XLIFF (.mqxliff) files with proper formatting preservation.

MQXLIFF is an XLIFF 1.2 format with memoQ-specific extensions for CAT tool metadata
and formatting tags. This module provides robust parsing and generation of MQXLIFF files
while preserving inline formatting (bold, italic, underline) and complex structures like
hyperlinks.

Key Features:
- Parse XLIFF trans-units with source and target segments
- Extract and preserve inline formatting tags (bpt/ept pairs)
- Handle complex nested structures (hyperlinks with formatting)
- Generate valid MQXLIFF output with proper tag structure
- Maintain segment IDs and memoQ metadata

Formatting Tag Structure:
- <bpt id="X" ctype="bold">{}</bpt>...<ept id="X">{}</ept> - Bold text
- <bpt id="X" ctype="italic">{}</bpt>...<ept id="X">{}</ept> - Italic text
- <bpt id="X" ctype="underlined">{}</bpt>...<ept id="X">{}</ept> - Underlined text
- Nested tags for hyperlinks: <bpt><bpt><bpt>text</ept></ept></ept>
"""

import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple, Optional
import re


class FormattedSegment:
    """Represents a segment with inline formatting information."""
    
    def __init__(self, segment_id: str, plain_text: str, formatted_xml: str):
        """
        Initialize a formatted segment.
        
        Args:
            segment_id: Unique identifier for the segment (trans-unit id)
            plain_text: Plain text without any formatting tags
            formatted_xml: XML string with formatting tags preserved
        """
        self.id = segment_id
        self.plain_text = plain_text
        self.formatted_xml = formatted_xml
        self.formatting_tags = self._extract_formatting_tags(formatted_xml)
    
    def _extract_formatting_tags(self, xml_str: str) -> List[Dict]:
        """Extract formatting tag information from XML string."""
        tags = []
        # Match bpt tags with ctype attribute
        bpt_pattern = r'<bpt\s+id="(\d+)"\s+(?:rid="(\d+)"\s+)?ctype="([^"]+)">[^<]*</bpt>'
        for match in re.finditer(bpt_pattern, xml_str):
            tag_id = match.group(1)
            ctype = match.group(3)
            tags.append({
                'id': tag_id,
                'type': ctype,
                'is_bpt': True
            })
        return tags
    
    def __repr__(self):
        return f"FormattedSegment(id={self.id}, text='{self.plain_text[:50]}...', tags={len(self.formatting_tags)})"


class MQXLIFFHandler:
    """Handler for parsing and generating memoQ XLIFF files."""
    
    # Namespaces used in MQXLIFF files
    NAMESPACES = {
        'xliff': 'urn:oasis:names:tc:xliff:document:1.2',
        'mq': 'MQXliff'
    }
    
    def __init__(self):
        """Initialize the MQXLIFF handler."""
        self.tree = None
        self.root = None
        self.file_element = None
        self.body_element = None
        self.source_lang = None
        self.target_lang = None
        #: The file exactly as it was read, with the BOM stripped and line endings
        #: left alone. Writing works by substring replacement on this, so
        #: everything we did not translate comes back out byte-identical — see the
        #: note above update_target_segments().
        self.raw_text: Optional[str] = None
        #: True when the loaded file began with a UTF-8 BOM / used CRLF. memoQ
        #: writes both; end-of-line normalisation during XML parsing destroys the
        #: latter, so both are recorded here and restored on save.
        self.had_bom = True
        self.had_crlf = True
        #: ``((start, end), replacement)`` spans queued for the next save().
        self._edits: List[Tuple[Tuple[int, int], str]] = []
        #: Segments update_target_segments() could not write, and ones whose
        #: formatting extent may have widened. See that method.
        self.skipped_segments: List[Tuple[int, str, str]] = []
        self.formatting_degraded: List[Tuple[int, str]] = []
        #: ``(unit id, status)`` for segments whose mq:status was left as memoQ
        #: wrote it because the unit carries no edit provenance — see _status_edit.
        self.status_preserved: List[Tuple[str, str]] = []


    def load(self, file_path: str) -> bool:
        """
        Load and parse an MQXLIFF file.
        
        Args:
            file_path: Path to the .mqxliff file
            
        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            # Register namespaces for proper parsing
            for prefix, uri in self.NAMESPACES.items():
                ET.register_namespace(prefix, uri)
            
            # Keep the file as text for writing, and note the two conventions XML
            # parsing destroys (the BOM and CRLF line endings) so save() can
            # reproduce them. Decoded with utf-8-sig and no newline translation,
            # so raw_text holds exactly what was on disk minus the BOM.
            with open(file_path, 'rb') as fh:
                raw = fh.read()
            self.had_bom = raw.startswith(b'\xef\xbb\xbf')
            self.raw_text = raw.decode('utf-8-sig')
            self.had_crlf = '\r\n' in self.raw_text
            self._edits = []

            self.tree = ET.parse(file_path)
            self.root = self.tree.getroot()

            # Find the file element
            self.file_element = self.root.find('.//xliff:file', self.NAMESPACES)
            if self.file_element is None:
                # Try without namespace
                self.file_element = self.root.find('.//file')
            
            if self.file_element is not None:
                self.source_lang = self.file_element.get('source-language', 'unknown')
                self.target_lang = self.file_element.get('target-language', 'unknown')
            
            # Find the body element
            self.body_element = self.root.find('.//xliff:body', self.NAMESPACES)
            if self.body_element is None:
                # Try without namespace
                self.body_element = self.root.find('.//body')
            
            return True
        except Exception as e:
            print(f"[MQXLIFF] Error loading file: {e}")
            return False
    
    def extract_source_segments(self) -> List[FormattedSegment]:
        """
        Extract all source segments from the MQXLIFF file.
        
        Returns:
            List of FormattedSegment objects containing source text and formatting
        """
        segments = []
        
        if self.body_element is None:
            return segments
        
        # Find all trans-unit elements (with or without namespace)
        trans_units = self.body_element.findall('.//xliff:trans-unit', self.NAMESPACES)
        if not trans_units:
            trans_units = self.body_element.findall('.//trans-unit')
        
        for trans_unit in trans_units:
            trans_unit_id = trans_unit.get('id', 'unknown')
            
            # Skip auxiliary segments (like hyperlink URLs with mq:nosplitjoin="true")
            nosplitjoin = trans_unit.get('{MQXliff}nosplitjoin', 'false')
            if nosplitjoin == 'true':
                continue
            
            # Find source element
            source_elem = trans_unit.find('xliff:source', self.NAMESPACES)
            if source_elem is None:
                source_elem = trans_unit.find('source')
            
            if source_elem is not None:
                # Get the XML string of the source element's content
                formatted_xml = ET.tostring(source_elem, encoding='unicode', method='xml')
                
                # Extract plain text (removing all tags)
                plain_text = self._extract_plain_text(source_elem)
                
                segment = FormattedSegment(trans_unit_id, plain_text, formatted_xml)
                segments.append(segment)

        return segments

    def extract_bilingual_segments(self) -> List[Dict]:
        """
        Extract all source AND target segments from the MQXLIFF file.
        Used for importing pretranslated mqxliff files.

        Returns:
            List of dicts with 'id', 'source', 'target', 'status' keys
        """
        segments = []

        if self.body_element is None:
            return segments

        # Find all trans-unit elements (with or without namespace)
        trans_units = self.body_element.findall('.//xliff:trans-unit', self.NAMESPACES)
        if not trans_units:
            trans_units = self.body_element.findall('.//trans-unit')

        for trans_unit in trans_units:
            trans_unit_id = trans_unit.get('id', 'unknown')

            # Skip auxiliary segments (like hyperlink URLs with mq:nosplitjoin="true")
            nosplitjoin = trans_unit.get('{MQXliff}nosplitjoin', 'false')
            if nosplitjoin == 'true':
                continue

            # Find source element
            source_elem = trans_unit.find('xliff:source', self.NAMESPACES)
            if source_elem is None:
                source_elem = trans_unit.find('source')

            # Find target element
            target_elem = trans_unit.find('xliff:target', self.NAMESPACES)
            if target_elem is None:
                target_elem = trans_unit.find('target')

            source_text = ""
            target_text = ""

            if source_elem is not None:
                source_text = self._extract_plain_text(source_elem)

            if target_elem is not None:
                target_text = self._extract_plain_text(target_elem)

            # Get memoQ status if available
            mq_status = trans_unit.get('{MQXliff}status', '')

            # Get memoQ match percentage if available (mq:percent attribute)
            mq_percent_str = trans_unit.get('{MQXliff}percent', '')
            mq_percent = None
            if mq_percent_str:
                try:
                    mq_percent = int(mq_percent_str)
                except ValueError:
                    pass

            # Map memoQ status to internal status
            # memoQ statuses: "NotStarted", "Editing", "Confirmed", "Reviewed", "Rejected", etc.
            status = 'not_started'
            if mq_status in ['Confirmed', 'ProofRead', 'Reviewed']:
                status = 'confirmed'
            elif mq_status == 'Editing':
                status = 'draft'
            elif target_text.strip():
                # Has target but unknown status - mark as pre-translated
                status = 'pre_translated'

            segments.append({
                'id': trans_unit_id,
                'source': source_text,
                'target': target_text,
                'status': status,
                'mq_status': mq_status,
                'match_percent': mq_percent
            })

        return segments

    def _extract_plain_text(self, element: ET.Element) -> str:
        """
        Recursively extract plain text from an XML element, stripping all tags.
        
        Args:
            element: The XML element to extract text from
            
        Returns:
            Plain text string with all tags removed (including {} placeholders)
        """
        text_parts = []
        
        # Add the element's text
        if element.text:
            text_parts.append(element.text)
        
        # Recursively process child elements
        for child in element:
            text_parts.append(self._extract_plain_text(child))
            # Add the tail text (text after the child element's closing tag)
            if child.tail:
                text_parts.append(child.tail)
        
        full_text = ''.join(text_parts)
        
        # Remove {} placeholders that come from bpt/ept tags
        # These are used in MQXLIFF to mark tag positions
        full_text = full_text.replace('{}', '')
        
        return full_text
    
    # ------------------------------------------------------------------
    # Writing targets
    #
    # Everything below rewrites the file as *text*, replacing only the
    # `<target>` spans of segments that got a translation. It does not
    # re-serialise the tree.
    #
    # That is a deliberate reversal of the previous approach, forced by testing
    # the output in memoQ 12.4.36. Parsing and re-serialising an MQXLIFF through
    # ElementTree destroys, unavoidably, things memoQ put there on purpose:
    #
    #   * CDATA sections            6 -> 0 (DOCX), 3 -> 0 (IDML)
    #   * `&quot;` inside tag payloads    204 -> 0, 16 -> 0
    #   * raw TABs in attribute values      3 -> 0, 2 -> 0  (XML attribute-value
    #                                       normalisation — not recoverable)
    #   * `xmlns="MQXliff"` on <mq:*>     210 -> 0, 103 -> 0
    #   * CRLF line endings           2683 -> 0, 1331 -> 0  (XML end-of-line
    #                                       normalisation is mandated by the spec)
    #   * the UTF-8 BOM, the XML declaration's spelling, attribute order
    #
    # None of that is semantically meaningful to a conforming parser, but all of
    # it is gratuitous: we have no business rewriting bytes we were not asked to
    # change, and a consumer is entitled to be stricter than the spec requires.
    # Text-level replacement makes a load→save round trip byte-identical by
    # construction, which is both the stronger guarantee and the easier one to
    # test. It is also what modules/sdlppx_handler.py already does — and the
    # Trados output is accepted where the memoQ output was not.
    # ------------------------------------------------------------------

    #: Inline elements whose text is a tag payload — memoQ's own stored markup or
    #: the placeholder ``{}`` — and never translatable content.
    INLINE_TAGS = ('bpt', 'ept', 'ph', 'it', 'x')

    def update_target_segments(self, translations: List[str]) -> int:
        """Fill in the target of every translatable segment.

        ``translations`` is positional: the Nth entry belongs to the Nth segment
        that :meth:`extract_source_segments` returned, i.e. auxiliary segments
        (``mq:nosplitjoin="true"``) are skipped by both.

        Returns the number of segments written. Two lists record what did not go
        cleanly, and callers should surface both rather than reporting the count
        alone:

        :attr:`skipped_segments`
            Left exactly as memoQ wrote them, and **not** marked confirmed. The
            old code counted these as updated and confirmed them anyway, so a
            file that still held source text in eight segments looked complete.
        :attr:`formatting_degraded`
            Written, with every tag present and in order, but a formatting run's
            extent may have widened — see :meth:`_partition_runs`.
        """
        self.skipped_segments = []
        self.formatting_degraded = []
        self.status_preserved = []
        self._edits = []

        if not self.raw_text:
            return 0

        index = 0
        written = 0
        for unit in self._iter_raw_units():
            if unit['nosplitjoin']:
                continue
            if index >= len(translations):
                break
            translation = translations[index]
            segment_index = index
            index += 1

            if unit['target'] is None:
                self.skipped_segments.append(
                    (segment_index, unit['id'], 'segment has no <target> element'))
                continue

            new_inner, degraded = self._build_target_inner(unit, translation)
            if new_inner is None:
                self.skipped_segments.append(
                    (segment_index, unit['id'],
                     'inline tags could not be placed without corrupting them'))
                continue

            target = unit['target']
            self._edits.append((target['inner_span'],
                                target['open_tag'] + new_inner + target['close_tag']))
            status_edit = self._status_edit(unit)
            if status_edit:
                self._edits.append(status_edit)
            written += 1
            if degraded:
                self.formatting_degraded.append((segment_index, unit['id']))

        return written

    # -- locating things in the raw text --------------------------------

    _UNIT_RE = re.compile(r'<trans-unit\b[^>]*>.*?</trans-unit>', re.DOTALL)
    _OPEN_RE = re.compile(r'<trans-unit\b[^>]*>')
    _ATTR_RE = re.compile(r'([A-Za-z_][\w.:-]*)\s*=\s*"([^"]*)"')

    def _iter_raw_units(self):
        """Yield one dict per ``<trans-unit>`` in the raw file, in document order.

        ``nosplitjoin`` is read from the raw open tag rather than from the parsed
        tree, so the segment numbering here cannot drift from what
        :meth:`extract_source_segments` produced.
        """
        for match in self._UNIT_RE.finditer(self.raw_text):
            block = match.group(0)
            base = match.start()
            open_tag = self._OPEN_RE.match(block).group(0)
            attrs = dict(self._ATTR_RE.findall(open_tag))
            yield {
                'id': attrs.get('id', '?'),
                'nosplitjoin': attrs.get('mq:nosplitjoin', 'false') == 'true',
                'span': (base, match.end()),
                'open_span': (base, base + len(open_tag)),
                'open_tag': open_tag,
                'source': self._child_span(block, base, 'source'),
                'target': self._child_span(block, base, 'target'),
            }

    def _child_span(self, block: str, base: int, name: str):
        """Locate ``<name …>…</name>`` directly inside a trans-unit block.

        Returns None when absent. ``inner_span`` is an absolute (start, end) into
        ``self.raw_text`` covering just the element's content, which is what gets
        replaced. A self-closing ``<target />`` yields an empty inner span placed
        so that inserting there produces a well-formed element.
        """
        pattern = re.compile(r'<' + name + r'\b([^>]*?)(/?)>', re.DOTALL)
        for match in pattern.finditer(block):
            open_attrs, self_closing = match.group(1), match.group(2)
            if self_closing == '/':
                # An empty <target … /> — the shape of a file that has not been
                # pretranslated. Filling it means replacing the whole tag, so the
                # span covers the tag itself and the replacement re-emits the open
                # tag, the content and a close tag. `open_tag` carries the text to
                # rebuild from.
                return {'inner_span': (base + match.start(), base + match.end()),
                        'open_tag': '<' + name + open_attrs.rstrip() + '>',
                        'close_tag': '</' + name + '>',
                        'attrs': open_attrs, 'inner': '', 'empty': True}
            close = block.find('</' + name + '>', match.end())
            if close < 0:
                return None
            return {'inner_span': (base + match.end(), base + close),
                    'open_tag': '', 'close_tag': '',
                    'attrs': open_attrs,
                    'inner': block[match.end():close], 'empty': False}
        return None

    def _status_edit(self, unit):
        """An edit setting ``mq:status="Confirmed"``, or None to leave it alone.

        memoQ pairs an edited status with edit provenance. Measured across both
        corpus files: every one of the 51 + 105 ``PartiallyEdited`` units carries
        ``mq:lastchanginguser="navi"`` and a real ``mq:lastchangedtimestamp``
        (``2026-07-25T16:28:28Z``), while the single ``PreTranslated`` unit — IDML
        trans-unit 26 — carries neither: no user attribute at all, and
        ``0001-01-01T00:00:00Z``, which is .NET's ``DateTime.MinValue``.

        Confirming that unit anyway produced a combination memoQ never writes: an
        edited status with no editor and a min-value timestamp. It is also
        *exactly* one of the two things unique to the IDML file, on the very
        trans-unit that also had its ``ph`` payload corrupted — the two were
        confounded, and only one of them was the tag bug. Every DOCX unit was
        already ``PartiallyEdited`` with a user and a real timestamp, so flipping
        those kept memoQ's invariant intact, which fits the DOCX file importing
        while the IDML file did not.

        So: confirm a unit that memoQ already regards as user-edited, and
        otherwise leave the status as it was. Fabricating a user identity or an
        edit time into a customer's file is not a trade worth making for a status
        flag, and it is a segment's *content* that matters. Units left alone are
        listed in :attr:`status_preserved`.

        ``Confirmed`` itself remains unverified: it appears in neither corpus file
        (which use only ``NotStarted``, ``PartiallyEdited``, ``PreTranslated``),
        but it is the token this handler has always written and the one
        :meth:`extract_bilingual_segments` reads back. The 104 DOCX units carrying
        it are the evidence that memoQ tolerates it.
        """
        open_tag = unit['open_tag']
        start, end = unit['open_span']

        if 'mq:lastchanginguser="' not in open_tag:
            self.status_preserved.append((unit['id'], self._status_of(open_tag)))
            return None

        if 'mq:status="' in open_tag:
            new = re.sub(r'mq:status="[^"]*"', 'mq:status="Confirmed"', open_tag)
        else:
            new = open_tag[:-1].rstrip() + ' mq:status="Confirmed">'
        if new == open_tag:
            return None
        return ((start, end), new)

    @staticmethod
    def _status_of(open_tag: str) -> str:
        match = re.search(r'mq:status="([^"]*)"', open_tag)
        return match.group(1) if match else ''

    # -- building a target ---------------------------------------------

    def _build_target_inner(self, unit, translation: str):
        """The replacement inner XML for this unit's ``<target>``.

        Returns ``(inner_xml, degraded)``, or ``(None, False)`` to decline.

        The inline elements are taken from the **existing target**, not cloned
        from the source, and are copied as raw substrings without being parsed.
        That matters for more than escaping: in memoQ's own files a target's
        ``rid`` is not always the source's — unit 59 of the DOCX file has
        ``rid="1"`` in the source and ``rid="2"`` in the target, and IDML unit 4
        has ``rid="1"`` against ``rid="3"``. ``rid`` is scoped to the document,
        not the trans-unit, so cloning source elements into a target invents
        duplicate ids. Reusing the target's own elements sidesteps that entirely.
        """
        target, source = unit['target'], unit['source']
        # Take the inline elements from the target when it has any: those are the
        # ones with the right rid values. Fall back to the source only for an empty
        # target, which is what a file that was never pretranslated looks like.
        donor = target if target['inner'].strip() else source
        if donor is None:
            return None, False

        tokens = self._scan_inner(donor['inner'])
        elems = [t for t in tokens if t[0] == 'elem']

        if not elems:
            # Plain text segment: nothing to preserve, nothing to get wrong.
            return self._escape_text(translation), False

        # Slot model: inner == runs[0] + elems[0] + runs[1] + … + runs[n]
        runs, ordered = [], []
        current = []
        for token in tokens:
            if token[0] == 'elem':
                runs.append(''.join(current))
                current = []
                ordered.append(token[1])
            else:
                current.append(token[1])
        runs.append(''.join(current))

        plain_runs = [self._unescape_text(r) for r in runs]
        payloads = [self._raw_payload(e) for e in ordered]

        new_runs, degraded = self._partition_runs(plain_runs, payloads, translation)
        if new_runs is None:
            return None, False

        out = [self._escape_text(new_runs[0])]
        for i, raw_elem in enumerate(ordered):
            out.append(raw_elem)
            out.append(self._escape_text(new_runs[i + 1]))
        return ''.join(out), degraded

    def _partition_runs(self, runs: List[str], payloads: List[str], translation: str):
        """Split ``translation`` back into one text run per slot.

        A tag whose payload is *visible* in the segment text is an anchor: the
        translator saw it and, per tag verification, kept it, so it pins down
        exactly where a stretch of text ends. memoQ stores a payload either as
        literal document markup (``<fld id="0" />``, ``<tbl linid="0" />``),
        which :meth:`_extract_plain_text` passes through, or as ``{}``, which it
        strips. Verified across the corpus: every non-``{}`` payload appears in
        the extracted text and every ``{}`` one does not.

        Between two anchors the split is unknowable — ``{}`` pairs contribute no
        text, so nothing in the translation says where one run ends. The whole
        stretch goes into the slot that carried the most text in the source. For
        the common ``<bpt/>text<ept/>`` shape that is the slot *between* the
        pair, so the formatting still wraps the translation instead of sitting
        beside it; where several runs compete, every tag still survives in order
        but one run's extent widens, and the caller is told.

        Returns ``(None, False)`` when an anchor is missing from the translation:
        the translator moved or deleted a tag, and guessing would corrupt it.
        """
        new_runs = [''] * len(runs)
        degraded = False

        def assign(lo: int, hi: int, text: str):
            nonlocal degraded
            span = range(lo, min(hi, len(runs) - 1) + 1)
            if not span:
                return
            best = max(span, key=lambda j: (len(runs[j]), -j))
            new_runs[best] = text
            if text and sum(1 for j in span if runs[j].strip()) > 1:
                degraded = True

        pos = 0
        stretch = 0
        for i, payload in enumerate(payloads):
            if not payload:
                continue
            found = translation.find(payload, pos)
            if found < 0:
                return None, False
            assign(stretch, i, translation[pos:found])
            pos = found + len(payload)
            stretch = i + 1
        assign(stretch, len(runs) - 1, translation[pos:])
        return new_runs, degraded

    # -- raw XML helpers ----------------------------------------------

    def _raw_payload(self, raw_elem: str) -> str:
        """The text an inline element contributes to the segment's plain text.

        ``''`` for a self-closing element or the ``{}`` placeholder, which
        :meth:`_extract_plain_text` strips and a translator therefore never sees.
        """
        match = re.match(r'<[^>]*?(/)?>', raw_elem)
        if match and match.group(1):
            return ''
        inner = raw_elem[raw_elem.index('>') + 1:raw_elem.rindex('</')]
        text = self._unescape_text(inner)
        return '' if text == '{}' else text

    def _scan_inner(self, inner: str):
        """Split an element's inner XML into ``('text', s)`` and ``('elem', s)``.

        Element tokens are verbatim substrings, never parsed, so CDATA sections,
        entity spellings, attribute order and raw tabs inside them all survive.
        """
        tokens = []
        buf = []
        i, n = 0, len(inner)
        while i < n:
            if inner.startswith('<![CDATA[', i):
                end = inner.find(']]>', i)
                end = n if end < 0 else end + 3
                buf.append(inner[i:end])
                i = end
                continue
            if inner.startswith('<!--', i):
                end = inner.find('-->', i)
                end = n if end < 0 else end + 3
                buf.append(inner[i:end])
                i = end
                continue
            if inner[i] == '<':
                match = re.match(r'<([A-Za-z_][\w.:-]*)', inner[i:])
                if match:
                    end = self._element_end(inner, i, match.group(1))
                    if end > 0:
                        if buf:
                            tokens.append(('text', ''.join(buf)))
                            buf = []
                        tokens.append(('elem', inner[i:end]))
                        i = end
                        continue
            buf.append(inner[i])
            i += 1
        if buf:
            tokens.append(('text', ''.join(buf)))
        return tokens

    def _element_end(self, s: str, start: int, name: str) -> int:
        """Index just past the element starting at ``start``, or -1."""
        close_open = self._tag_end(s, start)
        if close_open < 0:
            return -1
        if s[close_open - 2:close_open] == '/>':
            return close_open
        depth = 1
        i = close_open
        open_pat = re.compile(r'<' + re.escape(name) + r'(?=[\s/>])')
        close_tag = '</' + name + '>'
        while i < len(s):
            if s.startswith('<![CDATA[', i):
                end = s.find(']]>', i)
                i = len(s) if end < 0 else end + 3
                continue
            if s.startswith(close_tag, i):
                depth -= 1
                i += len(close_tag)
                if depth == 0:
                    return i
                continue
            if open_pat.match(s, i):
                depth += 1
                nxt = self._tag_end(s, i)
                if nxt < 0:
                    return -1
                if s[nxt - 2:nxt] == '/>':
                    depth -= 1
                i = nxt
                continue
            i += 1
        return -1

    @staticmethod
    def _tag_end(s: str, start: int) -> int:
        """Index just past the ``>`` closing the tag that starts at ``start``,
        ignoring ``>`` inside quoted attribute values."""
        i = start + 1
        quote = ''
        while i < len(s):
            ch = s[i]
            if quote:
                if ch == quote:
                    quote = ''
            elif ch in '"\'':
                quote = ch
            elif ch == '>':
                return i + 1
            i += 1
        return -1

    @staticmethod
    def _escape_text(text: str) -> str:
        if not text:
            return ''
        return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    @staticmethod
    def _unescape_text(text: str) -> str:
        if not text:
            return ''
        # CDATA content is literal; strip the wrappers and leave the rest alone.
        text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', text, flags=re.DOTALL)
        return (text.replace('&lt;', '<').replace('&gt;', '>')
                    .replace('&quot;', '"').replace('&apos;', "'")
                    .replace('&amp;', '&'))

    def save(self, output_path: str) -> bool:
        """Write the file out, changing only the targets that were translated.

        The original bytes are reproduced exactly everywhere else — BOM, line
        endings, XML declaration, CDATA, entity spellings, attribute order and
        all — because the only thing applied is a list of substring replacements.
        A load→save with no translation applied is byte-identical to its input.
        """
        try:
            if self.raw_text is None:
                return False

            text = self.raw_text
            # Apply from the end so earlier offsets stay valid.
            for (start, end), replacement in sorted(
                    self._edits, key=lambda e: e[0][0], reverse=True):
                text = text[:start] + replacement + text[end:]

            if self.had_crlf:
                text = text.replace('\r\n', '\n').replace('\n', '\r\n')
            data = text.encode('utf-8')
            if self.had_bom:
                data = b'\xef\xbb\xbf' + data

            with open(output_path, 'wb') as fh:
                fh.write(data)
            return True
        except Exception as e:
            print(f"[MQXLIFF] Error saving file: {e}")
            return False

    def get_segment_count(self) -> int:
        """Get the number of translatable segments (excluding auxiliary segments)."""
        if self.body_element is None:
            return 0
        
        trans_units = self.body_element.findall('.//xliff:trans-unit', self.NAMESPACES)
        if not trans_units:
            trans_units = self.body_element.findall('.//trans-unit')
        
        count = 0
        for trans_unit in trans_units:
            nosplitjoin = trans_unit.get('{MQXliff}nosplitjoin', 'false')
            if nosplitjoin != 'true':
                count += 1
        
        return count


def test_mqxliff_handler():
    """Test function to verify MQXLIFF handler functionality."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mqxliff_handler.py <path_to_mqxliff_file>")
        return
    
    file_path = sys.argv[1]
    
    print(f"Testing MQXLIFF Handler with: {file_path}")
    print("=" * 60)
    
    handler = MQXLIFFHandler()
    
    # Load file
    if not handler.load(file_path):
        print("Failed to load file!")
        return
    
    print(f"✓ File loaded successfully")
    print(f"  Source language: {handler.source_lang}")
    print(f"  Target language: {handler.target_lang}")
    print(f"  Segment count: {handler.get_segment_count()}")
    print()
    
    # Extract segments
    segments = handler.extract_source_segments()
    print(f"✓ Extracted {len(segments)} segments")
    print()
    
    # Display first 5 segments
    print("First 5 segments:")
    for i, seg in enumerate(segments[:5], 1):
        print(f"\n  Segment {i} (ID: {seg.id}):")
        print(f"    Plain text: {seg.plain_text}")
        if seg.formatting_tags:
            print(f"    Formatting: {seg.formatting_tags}")
    
    # Test update (with dummy translations)
    print("\n" + "=" * 60)
    print("Testing update with dummy translations...")
    dummy_translations = [f"TRANSLATED: {seg.plain_text}" for seg in segments]
    updated_count = handler.update_target_segments(dummy_translations)
    print(f"✓ Updated {updated_count} target segments")
    
    # Save test output
    output_path = file_path.replace('.mqxliff', '_test_output.mqxliff')
    if handler.save(output_path):
        print(f"✓ Saved test output to: {output_path}")
    else:
        print("✗ Failed to save output")


if __name__ == "__main__":
    test_mqxliff_handler()
