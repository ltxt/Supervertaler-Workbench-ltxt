"""Import → translate → export → re-import, over the real corpus.

Not a test module (no ``test_`` prefix, so pytest does not collect it). It holds
the logic shared by two callers:

* ``tests/test_e2e_roundtrip.py`` — asserts the invariants and diffs a
  normalised snapshot against a committed golden file.
* ``tools/generate_target_files.py`` — writes real target documents a human (or
  Trados/memoQ/Word on a Windows machine) can open.

Both drive Supervertaler's own handlers, and both translate with
:func:`modules.pseudo_translate.pseudo_translate_text`, which protects tags using
the canonical model in :mod:`modules.tag_protection`. Pseudo-translation matters
for a round-trip check: the target is *visibly* different from the source, so a
tag, a run colour or a whole paragraph that "survived" cannot have survived
merely because nothing was written over it. That mistake hid a paragraph-mapping
bug in the DOCX exporter for as long as the exports were checked with
``target == source``.

Snapshots deliberately do **not** hash the exported bytes. DOCX and MQXLZ are ZIP
containers whose entries carry timestamps, and SDLXLIFF records save dates, so
byte-level goldens would churn on every run. What is captured instead is what the
handlers can read back out — which is the property under test.
"""

from __future__ import annotations

import collections
import contextlib
import hashlib
import io
import os
import re
import sys
from dataclasses import dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from modules import tag_protection as tp  # noqa: E402
from modules.pseudo_translate import pseudo_translate_text  # noqa: E402

CORPUS = os.path.join(REPO_ROOT, "tests", "fixtures", "corpus")
GOLDEN_DIR = os.path.join(REPO_ROOT, "tests", "golden")

#: Fixed so snapshots are reproducible. Enough expansion to surface layout
#: problems, little enough that a reviewer can still read the target.
EXPANSION = 0.15
MODE = "accents"

#: Whether to wrap each segment in pseudo-translation's ⟦…⟧ boundary markers.
#:
#: Off by default, which is not obvious. The markers are genuinely useful when a
#: human opens the exported document — a dropped or merged segment is visible at
#: a glance — so ``tools/generate_target_files.py`` turns them on. But they sit
#: *outside* the segment's tags, and a DOCX paragraph that is entirely bold has
#: no unformatted run to hold them: the exporter absorbs them into the bold, so
#: ``⟦<b>text</b>⟧`` is read back as ``<b>⟦text⟧</b>``. That is the exporter
#: faithfully representing what Word can represent, not a defect — but it makes a
#: byte-exact read-back comparison impossible. With markers off, the comparison
#: is exact, which is what the round-trip assertion wants.
MARKERS = False

#: Also write the side-by-side bilingual review DOCX. Off for the round-trip
#: test, which has nothing to assert about it; on for the CLI, where reading
#: source and target next to each other is the fastest way to check a document.
BILINGUAL = False

_COLOR_ATTR = re.compile(r'color="([0-9A-Fa-f]{6})"')


def translate(text: str, markers: bool | None = None) -> str:
    """The stand-in translation applied to every segment."""
    if not (text or "").strip():
        return text or ""
    return pseudo_translate_text(
        text, expansion=EXPANSION, mode=MODE,
        markers=MARKERS if markers is None else markers)


@contextlib.contextmanager
def _quiet():
    """Handlers print progress to stdout by the screenful."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class RoundTrip:
    """One corpus file taken through the full cycle."""

    name: str                                  # snapshot / golden file stem
    fmt: str                                   # DOCX | SDLXLIFF | MQXLIFF | XLF
    source_file: str                           # path relative to the corpus root
    sources: list[str] = field(default_factory=list)
    #: What we asked the exporter to write, index-aligned with ``sources``.
    written: list[str] = field(default_factory=list)
    #: What re-importing the export actually yields. Empty for formats with no
    #: export path, which are parser-only checks.
    read_back: list[str] = field(default_factory=list)
    #: Paths of files produced, when an output directory was given.
    outputs: list[str] = field(default_factory=list)
    #: Format-specific extras that belong in the snapshot.
    notes: dict = field(default_factory=dict)

    # -- derived ---------------------------------------------------------
    @property
    def translated_indices(self) -> list[int]:
        """Segments that were actually given a translation."""
        return [i for i, t in enumerate(self.written) if (t or "").strip()]

    @property
    def mismatched(self) -> list[int]:
        """Segments whose export does not hold what we wrote.

        The single most valuable signal in the whole harness: it is how the DOCX
        paragraph-mapping bug and the memoQ writer's silent no-op both show up.
        """
        if not self.read_back:
            return []
        out = []
        for i in self.translated_indices:
            got = self.read_back[i] if i < len(self.read_back) else ""
            if got != self.written[i]:
                out.append(i)
        return out

    @property
    def verification_issues(self) -> dict:
        """``{segment index: description}`` for targets whose tags don't match."""
        issues = {}
        for i, source in enumerate(self.sources):
            target = self.written[i] if i < len(self.written) else ""
            found = tp.verify_tags(source, target)
            if found:
                issues[i] = tp.describe_issues(found)
        return issues

    def tag_census(self) -> dict:
        census: collections.Counter = collections.Counter()
        for text in self.sources:
            for token in tp.parse_tags(text or ""):
                census[token.family] += 1
        return dict(sorted(census.items()))

    def tags_by_segment(self) -> dict:
        """Raw tags per segment, for segments that have any.

        Pins exactly which substrings of real documents the parser treats as
        markup — a change here is a parser behaviour change, whether intended or
        not.
        """
        out = {}
        for i, text in enumerate(self.sources):
            raws = [t.raw for t in tp.parse_tags(text or "")]
            if raws:
                out[str(i)] = raws
        return out

    def snapshot(self) -> dict:
        """The normalised, comparable record written to ``tests/golden/``."""
        digest = hashlib.sha256(
            "␞".join(self.read_back or self.written).encode("utf-8")
        ).hexdigest()[:16]
        return {
            "format": self.fmt,
            "source_file": self.source_file,
            "segments": len(self.sources),
            "segments_translated": len(self.translated_indices),
            "tag_families": self.tag_census(),
            "tags_by_segment": self.tags_by_segment(),
            "tag_verification_issues": {str(k): v
                                        for k, v in self.verification_issues.items()},
            "export_read_back": {
                "checked": len(self.translated_indices),
                "matching": len(self.translated_indices) - len(self.mismatched),
                "mismatched_segments": self.mismatched,
            },
            "notes": self.notes,
            # Tripwire for any change at all in what comes back out. Carries no
            # diagnostic value on its own — the fields above do that — but it
            # fails when something changed that none of them happened to cover.
            "read_back_digest": digest,
        }


# ---------------------------------------------------------------------------
# Per-format round trips
# ---------------------------------------------------------------------------

def _colours(texts) -> dict:
    """Run colours carried by ``<cf color="…">`` tags, counted by value."""
    found: collections.Counter = collections.Counter()
    for text in texts:
        for token in tp.parse_tags(text or ""):
            if token.name == "cf" and token.kind == tp.KIND_OPEN:
                for match in _COLOR_ATTR.finditer(token.attrs):
                    found[match.group(1).upper()] += 1
    return dict(sorted(found.items()))


def roundtrip_docx(out_dir: str, bilingual: bool | None = None) -> RoundTrip:
    """DOCX: the only format where inline formatting is *derived* rather than
    read from markup, so it is where the tag model and the handlers meet."""
    from modules.docx_handler import DOCXHandler

    source_file = os.path.join("docx", "CAT_test_DOCX.docx")
    src_path = os.path.join(CORPUS, source_file)
    out_path = os.path.join(out_dir, "CAT_test_DOCX__target.docx")

    handler = DOCXHandler()
    with _quiet():
        sources = handler.import_docx(src_path, extract_formatting=True)

    segments = [
        {
            "paragraph_id": handler.paragraphs_info[i].paragraph_index,
            "source": text,
            "target": translate(text),
        }
        for i, text in enumerate(sources)
    ]

    outputs = [out_path]
    with _quiet():
        handler.export_docx(segments, out_path, preserve_formatting=True,
                            target_lang="Dutch")
        if BILINGUAL if bilingual is None else bilingual:
            bil = os.path.join(out_dir, "CAT_test_DOCX__bilingual_review.docx")
            handler.export_bilingual_docx(segments, bil)
            outputs.append(bil)

    reader = DOCXHandler()
    with _quiet():
        read_back = reader.import_docx(out_path, extract_formatting=True)

    before, after = _colours(sources), _colours(read_back)
    return RoundTrip(
        name="docx_CAT_test_DOCX",
        fmt="DOCX",
        source_file=source_file,
        sources=sources,
        written=[s["target"] for s in segments],
        read_back=read_back,
        outputs=outputs,
        notes={
            "run_colours_in_source": before,
            "run_colours_after_export": after,
            "run_colours_lost": dict(collections.Counter(before)
                                     - collections.Counter(after)),
            "paragraphs_in_export": len(read_back),
        },
    )


def roundtrip_sdlxliff(source_file: str, out_dir: str) -> RoundTrip:
    """Trados: tags come from real ``<g>``/``<x>`` markup, so this checks the
    handler's marker conversion as much as the tag model."""
    from modules.sdlppx_handler import StandaloneSDLXLIFFHandler

    src_path = os.path.join(CORPUS, source_file)
    handler = StandaloneSDLXLIFFHandler(log_callback=lambda *a, **k: None)
    if not handler.load([src_path]):
        raise RuntimeError(f"could not load {source_file}")

    segments = handler.get_all_segments()
    sources = [s.source_text or "" for s in segments]
    written = [translate(text) for text in sources]

    translations, statuses = {}, {}
    for segment, target in zip(segments, written):
        if target.strip():
            translations[segment.segment_id] = target
            statuses[segment.segment_id] = "confirmed"
    handler.update_translations(translations, statuses)
    outputs = handler.save_all(out_dir)

    read_back = []
    if outputs:
        reader = StandaloneSDLXLIFFHandler(log_callback=lambda *a, **k: None)
        if reader.load([outputs[0]]):
            by_id = {s.segment_id: s.target_text or ""
                     for s in reader.get_all_segments()}
            read_back = [by_id.get(s.segment_id, "") for s in segments]

    stem = os.path.basename(source_file).replace(".sdlxliff", "")
    return RoundTrip(
        name=f"sdlxliff_{stem}".replace(".", "_"),
        fmt="SDLXLIFF",
        source_file=source_file,
        sources=sources,
        written=written,
        read_back=read_back,
        outputs=outputs,
        notes={"source_language": handler.get_source_lang(),
               "target_language": handler.get_target_lang()},
    )


def roundtrip_mqxliff(source_file: str, out_dir: str) -> RoundTrip:
    """memoQ: some tags arrive as literal text (``<rpr id="0">``) and some as
    ``bpt``/``ept`` structure that the handler drops entirely — see the writer
    caveat in ``tests/test_e2e_roundtrip.py``."""
    from modules.mqxliff_handler import MQXLIFFHandler

    src_path = os.path.join(CORPUS, source_file)
    handler = MQXLIFFHandler()
    with _quiet():
        if not handler.load(src_path):
            raise RuntimeError(f"could not load {source_file}")
        segments = handler.extract_source_segments()

    sources = [s.plain_text or "" for s in segments]
    written = [translate(text) for text in sources]

    out_path = os.path.join(
        out_dir, os.path.basename(source_file).replace(".mqxliff", "__target.mqxliff"))
    with _quiet():
        handler.update_target_segments(written)
        handler.save(out_path)

    read_back = []
    reader = MQXLIFFHandler()
    with _quiet():
        if reader.load(out_path):
            read_back = [row.get("target", "")
                         for row in reader.extract_bilingual_segments()]

    stem = os.path.basename(source_file).replace(".mqxliff", "")
    return RoundTrip(
        name=f"mqxliff_{stem}".replace(".", "_"),
        fmt="MQXLIFF",
        source_file=source_file,
        sources=sources,
        written=written,
        read_back=read_back,
        outputs=[out_path],
    )


def roundtrip_phrase(source_file: str) -> RoundTrip:
    """Phrase XLF: parser-only. Supervertaler has no ``.xlf`` import or export
    path, so there is nothing to write — but the file is the corpus's only
    source of ``{0}`` software placeholders, which the tag model has to protect
    and verification has to notice going missing."""
    import xml.etree.ElementTree as ET

    src_path = os.path.join(CORPUS, source_file)
    sources = []
    for element in ET.parse(src_path).getroot().iter():
        if element.tag.endswith("trans-unit"):
            for child in element:
                if child.tag.endswith("source"):
                    sources.append("".join(child.itertext()))

    return RoundTrip(
        name="phrase_xlf",
        fmt="XLF",
        source_file=source_file,
        sources=sources,
        written=[translate(text) for text in sources],
        read_back=[],
        notes={"export_path": "none — .xlf is not an import/export format"},
    )


#: Every round trip the harness knows how to run, keyed by snapshot name. Each
#: value takes an output directory and returns a :class:`RoundTrip`.
ROUND_TRIPS = {
    "docx_CAT_test_DOCX":
        lambda out: roundtrip_docx(out),
    "sdlxliff_CAT_test_DOCX_docx":
        lambda out: roundtrip_sdlxliff("trados/CAT_test_DOCX.docx.sdlxliff", out),
    "sdlxliff_CAT_test_XLS_xls_en-US_de-de":
        lambda out: roundtrip_sdlxliff(
            "trados/CAT_test_XLS.xls_en-US_de-de.sdlxliff", out),
    "mqxliff_CAT_test_DOCX_docx_lit":
        lambda out: roundtrip_mqxliff("memoq/CAT_test_DOCX.docx_lit.mqxliff", out),
    "mqxliff_CAT_test_IDML_idml_lit":
        lambda out: roundtrip_mqxliff("memoq/CAT_test_IDML.idml_lit.mqxliff", out),
    "phrase_xlf":
        lambda out: roundtrip_phrase("phrase/Initial_Translation_SK_2026-06-08.xlf"),
}


def golden_path(name: str) -> str:
    return os.path.join(GOLDEN_DIR, f"{name}.json")
