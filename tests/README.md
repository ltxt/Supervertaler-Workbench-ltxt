# Test Suite

Unit and integration tests for Supervertaler. Every test here is fast, offline
and deterministic — no network, no API keys, no user settings, and nothing that
touches a real project folder.

## Running them

```bash
pip install -e ".[test]"      # pytest; PyQt6 is already a core dependency
pytest                        # or: pytest tests/ -v
```

The suite needs **only pytest and PyQt6**, with one exception: the document
round trip (`test_e2e_roundtrip.py`) needs `python-docx`, and skips itself
without it. Nothing imports `lxml`, an LLM SDK or any MT client, so a bare
checkout plus pytest and PyQt6 runs everything else.

```bash
pip install python-docx      # then the round trip runs too
pytest -m "not gui"          # everything except the headless-app test
pytest -m e2e                # just the document round trip
```

Qt-based tests run headless via `QT_QPA_PLATFORM=offscreen`, set for the whole
session in `conftest.py`. On a desktop machine you can override it
(`QT_QPA_PLATFORM=xcb pytest`) to watch the widgets. On Linux CI, Qt also needs
`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` installed, or PyQt6
fails to import and the Qt suites are silently skipped rather than run.

CI (`.github/workflows/tests.yml`) runs three jobs: the suite on Python 3.10 and
3.12, the document round trip, and the headless-app grid smoke test.
`py-compat.yml` separately byte-compiles the whole app on 3.10, 3.11 and 3.12.

## The document round trip

`test_e2e_roundtrip.py` is the only test that takes a document all the way
through **import → translate → export → re-import**, using real Trados, memoQ,
Phrase and Word files in `fixtures/corpus/`. Every segment is pseudo-translated,
so the target differs from its source in every word — which is the point: a tag,
a run colour or a whole paragraph that survived cannot have survived merely
because nothing was written over it. Checking exports with `target == source`
hid a DOCX paragraph-mapping bug for as long as it was done that way.

Three pieces:

| Path | Role |
|---|---|
| `roundtrip_harness.py` | The cycle itself. Not collected by pytest (no `test_` prefix) so the CLI tool can import it too. |
| `test_e2e_roundtrip.py` | Invariants, plus a golden-snapshot diff. |
| `golden/*.json` | Committed snapshots: segment counts, the tag strings found in each segment, verification results, and which segments did not survive export. |

**Invariants** must always hold — tags reassemble, verification is clean, what was
written is what comes back. A failure is a bug, and the message names the segment.

**Goldens are a tripwire, not a specification.** They record what the handlers
currently do, including any defect not yet fixed (`KNOWN_DEFECTS`, currently
empty). A golden diff means something changed; deciding whether that is a fix or
a regression is the reader's job. Regenerate with:

```bash
UPDATE_GOLDEN=1 pytest tests/test_e2e_roundtrip.py
```

Review the diff before committing — that diff *is* the assertion. Snapshots hold
no document text, only indices, tag strings, counts and a digest, so they stay
readable in a pull request. Exported bytes are deliberately **not** hashed: DOCX
and MQXLZ are ZIP containers whose entries carry timestamps, so byte-level
goldens would churn on every run.

To get files you can actually open in Word, Trados or memoQ:

```bash
python tools/generate_target_files.py        # writes ./target_files/ + REPORT.md
```

CI runs that too and uploads the result as a `target-files` artifact, so a
reviewer can download and open the documents for any commit.

## What is covered

### Inline tags and tag protection

| File | Covers |
|---|---|
| `test_tag_protection.py` | The canonical inline-tag model (`modules/tag_protection.py`): parsing every tag family (HTML/XML with attributes, Trados numeric, memoQ brackets and content tags, Déjà Vu codes, compact placeholders), the genuineness rules that keep prose like `if x < y and y > z` from being read as markup, and memoQ-compatible numbering (by occurrence, pairs sharing a number, standalone tags unique). |
| `test_tag_atoms.py` | Protected tags as atomic Qt inline objects (`modules/tag_atoms.py`): the byte-exact round trip between segment text and document, indivisibility (one Backspace removes a whole tag; the caret cannot enter one), and the four memoQ display-detail levels. |
| `test_tag_protection_seam.py` | The grid-cell seam in `Supervertaler.py` — `apply_grid_cell_text()` / `read_grid_cell_text()`. Asserts the read-back is byte-identical to `toPlainText()` when protection is off, that `U+FFFC` can never reach segment text, and that no grid-cell call site bypasses the seam. |

### Format handlers and round-trips

| File | Covers |
|---|---|
| `test_e2e_roundtrip.py` | The full import → translate → export → re-import cycle over real Trados/memoQ/Phrase/Word documents, with golden snapshots. See above. |
| `test_docx_export_alignment.py` | DOCX export paragraph mapping: import numbers body paragraphs and table cells from one counter, and export must honour that numbering. Before the fix, 12 of 62 paragraphs in the corpus file received another segment's translation — and kept the original source, so the file looked fine. |
| `test_docx_color_tags.py` | Run colour carried as `<cf color="…">` through import, editing and export, so a translation cannot silently drop it. |
| `test_docx_comments.py` | DOCX comment extraction and anchoring (`modules/docx_comments.py`): all comments found, anchors bounded by their paragraph, identical segments disambiguated by position. |
| `test_sdlxliff_status_export.py` | The Trados SDLXLIFF/SDLRPX export status mapping (v1.10.259 regression): a *confirmed* segment must never export as Draft. |
| `test_pseudo_translate.py` | The pseudo-translation transform (`modules/pseudo_translate.py`): every tag family survives verbatim, tag text is never accented, and expansion does not inflate tags. |

### Project, TM and termbase logic

| File | Covers |
|---|---|
| `test_project_assets.py` | Project-folder asset helpers (`modules/project_assets.py`, issue #228): bundling external sources, relative-path resolution surviving a folder move. |
| `test_project_termbase_role.py` | The unified project-termbase role (v1.10.360): exclusive promotion, auto-activation, flag hygiene and the startup repair. |
| `test_identifier_conventions.py` | TM/termbase identifier conventions — which calls take a slug and which take a numeric registry id. |
| `test_superlookup_flag.py` | The SuperLookup inclusion flag (v1.10.247), including its independence from the Read flag. |
| `test_language_codes.py` | Language normalisation (`modules/language_codes.py`): base-code collapsing, region-strict matching, and the back-compat shim contracts. |
| `test_segment_delete.py` | Segment deletion safety (`modules/segment_split_merge.py`): deleting whole segments is refused on round-trip projects, where it would leave a gap in the merged-back export. |

`test_markdown.md` is a fixture, not a test.

## Conventions

Most files are **regression locks**: each was written around a specific shipped
bug, and the module docstring states what broke and what the test now prevents.
That is the preferred style for new tests — a failing assertion should tell the
next reader which behaviour regressed, not merely that something changed.

Both pytest-idiomatic tests (fixtures, `parametrize`) and plain assert-based
functions are present; default discovery handles both. Two of the older files
also carry `__main__` blocks so they can be run directly.

## Known gaps

Worth being explicit, since the suite is young:

- **Nothing verifies that another CAT tool accepts our output.** The round trip
  proves *Supervertaler* can read back what it wrote; it cannot tell you whether
  Trados Studio, memoQ or Word will open the file without complaint. That needs a
  Windows machine with those tools installed — `tools/generate_target_files.py`
  exists to produce the documents for exactly that check, and it is not a
  hypothetical gap: memoQ 12.4.36 rejected an MQXLIFF export that this suite
  considered a clean round trip, because the handler re-serialised the whole
  document and destroyed tag payloads that Supervertaler itself read back
  happily. The memoQ tests in `test_e2e_roundtrip.py` now assert byte-level
  fidelity instead of only self-consistency.
- **The project layer has no round trip.** `.svproj` save/load and the SDLPPX
  package round trip are still covered only by the manual smoke test in
  `CLAUDE.md`. Only `test_grid_smoke.py` launches the application, and it stops at
  inspecting the rendered grid — it does not load a project or export one.
- **AI translation is untested.** `llm_clients.py` and every MT client are
  unexercised; there is no network in CI and no recorded-response fixtures yet.
- **Coverage is narrow.** Around 18 of the ~115 modules in `modules/` are
  directly exercised. Untested areas include the CafeTran and DVX handlers and
  `translation_memory.py`.
- **`Supervertaler.py` is not importable in a test** — importing it builds the
  application. `test_tag_protection_seam.py` shows the workaround: locate the
  functions under test in the module's AST and execute just those against stub
  objects. That runs the real shipped code without constructing a `QApplication`
  and a main window.
