# Round-trip test corpus

Real exports from Trados Studio, memoQ and Phrase, used by
`tests/test_e2e_roundtrip.py` and by `tools/generate_target_files.py`.

These are **genuine CAT-tool files**, not hand-written fixtures. That is the
whole point: the synthetic tests cover the tag shapes we thought to write down,
and every defect the tag work has actually turned up was found by running the
handlers over these instead — memoQ's attributed closing tags
(`</cmt id="0" transform="close">`), a formula (`e^{x_i} / Σ_j e^{x_j}`) being
misread as markup, Trados self-closing tags being invisible to tag insertion,
and the DOCX export writing translations into the wrong paragraphs.

| Path | Origin | Why it is here |
|---|---|---|
| `docx/CAT_test_DOCX.docx` | Word | Inline formatting, coloured runs (`C00000`, `0563C1`), tables, lists, DATE/PAGE/author fields, hyperlinks, footnotes, special characters |
| `trados/CAT_test_DOCX.docx.sdlxliff` | Trados Studio | Trados numeric tags, including self-closing (`<1/>`) standalone tags |
| `trados/CAT_test_XLS.xls_en-US_de-de.sdlxliff` | Trados Studio | Spreadsheet-origin segments carrying no inline tags — the untagged path |
| `memoq/CAT_test_DOCX.docx_lit.mqxliff` | memoQ | memoQ tags as literal text (`<rpr id="0">`, `<fld id="0" />`, attributed closers), plus `bpt`/`ept` structure |
| `memoq/CAT_test_IDML.idml_lit.mqxliff` | memoQ (from IDML) | InDesign-origin tags and the mathematics that must *not* be read as tags |
| `phrase/Initial_Translation_SK_2026-06-08.xlf` | Phrase | `{0}`-style software placeholders that belong to the source and must survive |

The source text is derived from Wikipedia's "Large language model" article
(CC BY-SA); the documents were assembled as CAT-tool stress tests.

## Adding a file

Keep it small and keep it *real*. Add a row above saying which behaviour it
exercises, then regenerate the goldens:

```bash
UPDATE_GOLDEN=1 pytest tests/test_e2e_roundtrip.py
```

Review the resulting diff in `tests/golden/` before committing it — that diff is
the test's actual assertion, so an unreviewed regeneration defeats the purpose.
