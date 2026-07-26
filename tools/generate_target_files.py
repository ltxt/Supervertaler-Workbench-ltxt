#!/usr/bin/env python3
"""Pseudo-translate the test corpus and write real target files to inspect.

``tests/test_e2e_roundtrip.py`` asserts what Supervertaler's own handlers can
read back out of their exports. That catches a great deal, but it cannot tell you
whether Word, Trados Studio or memoQ will *accept* the file — for that someone
has to open it. This script produces those files.

    python tools/generate_target_files.py
    python tools/generate_target_files.py --out ~/targets --no-markers

Every segment is replaced with accented, length-expanded filler, tags preserved
verbatim by ``modules/tag_protection.py``. The target therefore differs visibly
from the source in every word, which is the point: a tag, a run colour or a whole
paragraph that survived cannot have survived merely because nothing was written
over it.

What to look for when you open them:

* **⟦…⟧ markers** (on unless ``--no-markers``) bound each segment. A missing or
  doubled pair is a dropped, merged or duplicated segment.
* **Formatting and colour** should sit exactly where the source has it, wrapped
  around nonsense instead of the original words.
* **Trados / memoQ** should open the bilingual files without a tag-validation
  complaint, and show every segment as confirmed.

A REPORT.md is written alongside the files with per-format counts, the tag census
and anything that did not round-trip.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import roundtrip_harness as rh  # noqa: E402


def _split_mismatches(result, markers: bool):
    """Separate real export failures from marker absorption.

    With markers on, a segment whose paragraph is entirely formatted has nowhere
    unformatted to put the ⟦ ⟧: the exporter folds them into the run, so
    ``⟦<b>x</b>⟧`` reads back as ``<b>⟦x⟧</b>``. Same text, same formatting extent
    — reporting it next to a segment that kept its source text would bury the one
    that matters.
    """
    from modules.pseudo_translate import DEFAULT_CLOSE, DEFAULT_OPEN

    real, benign = [], []
    for i in result.mismatched:
        wrote, holds = result.written[i], result.read_back[i]
        if markers and wrote.replace(DEFAULT_OPEN, "").replace(DEFAULT_CLOSE, "") == \
                holds.replace(DEFAULT_OPEN, "").replace(DEFAULT_CLOSE, ""):
            benign.append(i)
        else:
            real.append(i)
    return real, benign


def _report(results, markers: bool) -> str:
    lines = [
        "# Target files generated from the test corpus", "",
        f"Source corpus: `tests/fixtures/corpus/` · "
        f"expansion {int(rh.EXPANSION * 100)}% · mode `{rh.MODE}` · "
        f"segment markers {'on' if markers else 'off'}", "",
        "Every segment was pseudo-translated through "
        "`modules/pseudo_translate.py`, which protects tags with the same "
        "`modules/tag_protection.py` model the translation grid uses. Targets "
        "differ from their source in every word, so anything that survived "
        "really survived a translation.", "",
    ]
    if markers:
        lines += ["Each ⟦…⟧ pair bounds one segment: a missing or doubled pair "
                  "means a segment was dropped, merged or duplicated.", ""]

    for result in results:
        lines.append(f"## {result.fmt} — `{result.source_file}`")
        lines.append("")
        lines.append(f"- Segments: **{len(result.sources)}**, "
                     f"translated: **{len(result.translated_indices)}**")
        census = result.tag_census()
        lines.append("- Tags in source: "
                     + (", ".join(f"{fam} ×{n}" for fam, n in census.items())
                        if census else "none recognised"))
        for path in result.outputs:
            lines.append(f"- Output: `{os.path.basename(path)}`")
        if not result.outputs:
            lines.append("- No target file — this format has no export path in "
                         "Supervertaler, so only the tag model is exercised.")
        for key, value in result.notes.items():
            lines.append(f"- {key.replace('_', ' ')}: `{value}`")

        issues = result.verification_issues
        if issues:
            lines.append(f"- ⚠️ **Tag verification: {len(issues)} segment(s)**")
            for i, detail in list(issues.items())[:10]:
                lines.append(f"  - segment {i}: {detail}")
        else:
            lines.append("- Tag verification: **clean** — every target carries "
                         "the source's tags, in order")

        if result.read_back:
            real, benign = _split_mismatches(result, markers)
            if real:
                lines.append(f"- ⚠️ **{len(real)} segment(s) do not hold what was "
                             f"written**: {real}")
                i = real[0]
                lines.append(f"  - segment {i} asked for `{result.written[i][:70]}`")
                lines.append(f"  - segment {i} file holds `{result.read_back[i][:70]}`")
            else:
                lines.append(f"- Export re-read: **all "
                             f"{len(result.translated_indices)} targets** carry "
                             f"their translation")
            if benign:
                lines.append(f"- {len(benign)} segment(s) had the ⟦ ⟧ markers "
                             f"absorbed into surrounding formatting (expected — "
                             f"the paragraph is entirely formatted, so there is no "
                             f"unformatted run to hold them): {benign}")
        lines.append("")

    lines += [
        "---", "",
        "## Known open defect", "",
        "**Hyperlink-only DOCX paragraph** — a paragraph whose *only* content is a "
        "hyperlink comes out holding the source *and* the translation, because "
        "`_replace_paragraph_text()` does not clear runs nested inside "
        "`<w:hyperlink>`. Paragraphs where the hyperlink is only part of the text "
        "are unaffected. Pinned as a strict `xfail` in "
        "`tests/test_docx_export_alignment.py`.", "",
        "## One thing that cannot be checked without memoQ", "",
        "The writer sets `mq:status=\"Confirmed\"` on each translated segment. That "
        "is the token this handler has always used and the one it reads back, but "
        "it appears in neither memoQ-authored corpus file — those only use "
        "`NotStarted`, `PartiallyEdited` and `PreTranslated`. Absence is not proof "
        "it is invalid, and there is no way to settle it offline. If memoQ still "
        "reports an import warning after these files open cleanly, this is the "
        "first thing to suspect.", "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out", default=os.path.join(REPO_ROOT, "target_files"),
        help="directory for the generated files (default: ./target_files)")
    parser.add_argument(
        "--no-markers", action="store_true",
        help="omit the ⟦…⟧ segment markers. They help a reader spot a dropped "
             "segment, but they sit outside the segment's tags, so a DOCX "
             "paragraph that is entirely bold absorbs them into the bold run.")
    parser.add_argument(
        "--only", action="append", metavar="NAME",
        help=f"run only these round trips (repeatable). One of: "
             f"{', '.join(sorted(rh.ROUND_TRIPS))}")
    args = parser.parse_args()

    markers = not args.no_markers
    rh.MARKERS = markers
    rh.BILINGUAL = True  # source and target side by side, for reviewing by eye

    names = args.only or sorted(rh.ROUND_TRIPS)
    unknown = [n for n in names if n not in rh.ROUND_TRIPS]
    if unknown:
        parser.error(f"unknown round trip(s): {', '.join(unknown)}")

    os.makedirs(args.out, exist_ok=True)
    results = []
    for name in names:
        print(f"… {name}", flush=True)
        results.append(rh.ROUND_TRIPS[name](args.out))

    report_path = os.path.join(args.out, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(_report(results, markers))

    written = [p for r in results for p in r.outputs]
    print(f"\n{len(written)} file(s) written to {args.out}")
    for path in written:
        print(f"  {os.path.basename(path)}")
    print(f"  REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
