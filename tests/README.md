# Test Suite

Unit and integration tests for Supervertaler. Every test here is fast, offline
and deterministic — no network, no API keys, no user settings, and nothing that
touches a real project folder.

## Running them

```bash
pip install -e ".[test]"      # pytest; PyQt6 is already a core dependency
pytest                        # or: pytest tests/ -v
```

The suite needs **only pytest and PyQt6**. Nothing imports `python-docx`,
`lxml`, an LLM SDK or any MT client, so a bare checkout plus those two packages
runs everything.

Qt-based tests run headless via `QT_QPA_PLATFORM=offscreen`, set for the whole
session in `conftest.py`. On a desktop machine you can override it
(`QT_QPA_PLATFORM=xcb pytest`) to watch the widgets. On Linux CI, Qt also needs
`libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3` installed, or PyQt6
fails to import and the Qt suites are silently skipped rather than run.

CI (`.github/workflows/tests.yml`) runs the suite on Python 3.10 and 3.12;
`py-compat.yml` separately byte-compiles the whole app on 3.10, 3.11 and 3.12.

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

- **No end-to-end tests.** Nothing launches the application, loads a project, or
  drives the translation grid. The import → translate → export path, `.svproj`
  save/load and the SDLPPX round trip are covered only by the manual smoke test
  in `CLAUDE.md`.
- **Coverage is narrow.** Around 15 of the ~115 modules in `modules/` are
  directly exercised. Untested areas include `llm_clients.py`, `docx_handler.py`,
  the memoQ/CafeTran/Phrase/DVX handlers and `translation_memory.py`.
- **`Supervertaler.py` is not importable in a test** — importing it builds the
  application. `test_tag_protection_seam.py` shows the workaround: locate the
  functions under test in the module's AST and execute just those against stub
  objects. That runs the real shipped code without constructing a `QApplication`
  and a main window.
