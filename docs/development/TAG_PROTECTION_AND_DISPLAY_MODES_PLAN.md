# Tag Protection & Tag Display Modes — Plan and Review

**Status:** Plan under review — not yet implemented
**Scope:** Protected inline tags in the translation grid, a Partial/Full tag display switch, tag QA verification, and a bounded performance trade-off pass.

---

## 1. Requirements being addressed

From the feature request:

1. Tags must be imported as **protected** tags.
2. **Differentiate** genuine tags from ordinary text that merely contains `<` and `>`.
3. Tag protection must be **switchable** (on/off).
4. Text entered as a translation (tags inserted as units, not hand-typed markup).
5. **Tag display modes switch** — *Partial Tag Text* (short view) vs *Full Tag Text* (full view):
   - *Partial*: short label or icon inside the tag; keeps the grid readable; **hover tooltip reveals full details**.
   - *Full*: all hidden code/labels/inner text shown for every tag; helps diagnose complex tag pairs and structural errors; can look crowded.
6. Address "slow and resource heavy" as a **trade-off refactor**, not a rewrite.

Reference behaviour: Trados Studio, memoQ, Matecat, Smartcat.

> Note on external sources: the memoQ and Matecat documentation URLs supplied in the request return **HTTP 403** to this environment, so they could not be quoted directly. Statements below about competitor behaviour are from general knowledge of those tools and are labelled as such. The memoQ *tag strictness* level names in particular are **unverified** and are listed as an open question in §10 rather than designed against.

---

## 2. Verified inventory — what already exists

Everything in this section was read in the source, not assumed.

### 2.1 Tag detection exists, tag *protection* does not

`TagHighlighter` (`Supervertaler.py:4603`), a `QSyntaxHighlighter` attached to every grid cell, already recognises a wide range of conventions in `highlightBlock` (`:4725-4736`):

| Convention | Pattern | Origin |
|---|---|---|
| HTML/XML incl. attributes | `</?[a-zA-Z][\w-]*/?(?:\s[^>]*)?>` | DOCX, generic |
| Trados numeric | `<1>`, `</1>` | SDLXLIFF |
| Compact placeholders | `{1}`, `{/1}`, `{1/}` | internal compact view |
| memoQ numeric | `[1}`, `{1]`, `[1]` | MQXLIFF / bilingual DOCX |
| memoQ content tags | `[uicontrol id="…"]`, `{uicontrol}` | bilingual DOCX |
| Déjà Vu | `{00108}` (5-digit) | DVX RTF |

This only sets a **text colour**. Neither cell editor gives tags any edit-time integrity:

- `ReadOnlyGridTextEditor` (`:3059`) and `EditableGridTextEditor` (`:4921`) are plain `QTextEdit`s. Their `keyPressEvent` overrides handle F2 source-edit, Ctrl+C invisibles, Ctrl+arrow word nav, Up/Down segment nav, autocorrect and Shift+F3 — **nothing tag-aware**.
- A tag is ordinary characters. The caret can sit inside `<b>` and one Backspace turns it into `<>`.
- `QTextObjectInterface` / `ObjectReplacementCharacter` / `registerObjectType` appear **nowhere** in the repository (verified by grep across all `*.py`). There is no atomic-inline-object infrastructure today.

### 2.2 Four divergent tag regexes

| # | Location | Used by | Limitation |
|---|---|---|---|
| 1 | `modules/tag_manager.py:54` — `<(/?)([biu]\|bi\|li\|sub\|sup)>` | plain-DOCX import (`docx_handler.py`) | **Closed whitelist.** Cannot represent `<cf color="…" font="…">`, hyperlinks, or any attributed tag. |
| 2 | `Supervertaler.py:1178` `_ALL_TAGS_PATTERN` | `extract_all_tags` → `find_next_unused_tag` → Ctrl+, insert | **Misses all self-closing tags** (§3.3). |
| 3 | `Supervertaler.py:1199` `_AUTOTAG_TAG_PATTERN` | AutoTagger (AI tag placement) | Most complete; matches self-closing. |
| 4 | `TagHighlighter.highlightBlock` `:4725-4736` | colouring only | Most comprehensive; not reusable — built inline, recompiled per block. |

A fifth variant exists in `compact_tags` (`:1060-1066`) and a sixth in `_TAG_LINEBREAK_PROTECT_RE` (`:1136`).

### 2.3 Tag identity from real markup already works for Trados

`modules/sdlppx_handler.py` maps real SDLXLIFF elements to numbered placeholders (`:484-532`, `:1967-1968`):

```
<g id="N">content</g>   →   <N>content</N>     (paired)
<x id="N"/>             →   <N/>               (standalone)
```

Plus a locked-content mechanism using `<x id="lockedN" xid="lockTU_UUID"/>` (`:557-577`, `:721-745`). **Tag identity comes from the file's structured markup, not from re-scanning display text** — the same architectural principle Trados Studio itself uses. This is the correct foundation and should be generalised, not replaced.

Other handlers each carry their own convention (memoQ brackets, DVX `{NNNNN}` — `dejavurtf_handler.py:81-82`, CafeTran pipes), all recognised by the highlighter but not unified behind one model.

### 2.4 Display-mode machinery already exists — three modes

`_set_tag_view_mode(mode)` (`:60470`) supports `'tags'`, `'compact'`, `'wysiwyg'`:

- Segmented control built at `:30604-30640` (`wysiwyg_btn` / `compact_btn` / `tags_btn`).
- Cycled by **Ctrl+Shift+H** (`_toggle_tag_view_via_shortcut`, `:60458`; default registered in `modules/shortcut_manager.py:123`, matching Trados Studio's own binding).
- `_refresh_grid_display_mode()` (`:60574`) re-renders all rows, calling `update_display_mode(text, show_tags)` on each cell (`:3418` source, `:6432` target).
- `compact` mode rewrites the buffer via `compact_tags()` (`:1036`) into `{1}`/`{/1}`/`{1/}` placeholders and stashes a reversal map on the widget as `_compact_tag_map`.
- `wysiwyg` renders formatting via `get_formatted_html_display()` (`:856`) and reconstructs tags on read-back via `_wysiwyg_document_to_tagged_text()` (`:60517`).

**This is materially the requested Partial/Full switch, already ~70% built.** See §5.

### 2.5 A display→storage normalisation funnel already exists

The target `textChanged` handler (`:43947-43977`) already converts displayed content back to stored content through a chain:

1. WYSIWYG → `_wysiwyg_document_to_tagged_text()` (else `toPlainText()`)
2. compact → `expand_compact_tags(new_text, compact_map)`
3. invisibles → `reverse_invisible_replacements()`
4. stripped outer wrapping tag → re-added

**This is the single most important enabler for the plan:** the precedent that *displayed content need not equal stored content* is already established and shipping (WYSIWYG mode). Atomic tags are the same problem with the same solution shape.

Caveat: the reversal is **duplicated** at `:43967`, `:44045`, `:48486`, `:2877`, `:6291` — it is a funnel by convention, not by construction.

### 2.6 Tag insertion already exists — correcting an earlier claim

An earlier draft of this review stated there was no manual copy-source-tag shortcut. **That was wrong.**

- **Ctrl+,** → `_insert_next_tag_or_wrap_selection()` (`:2792` for `GridTextEditor`, `:6207` for `EditableGridTextEditor`), bound at `:2769-2773`.
- With a selection: wraps it in the next available pair via `get_wrapping_tag_pair()` (`:1476`) / `get_html_wrapping_tag_pair()` / pipe handling for CafeTran.
- Without a selection: inserts the next unused source tag via `find_next_unused_tag()` (`:1444`).
- Already **compact-mode aware** — it expands the target before comparing and inserts the placeholder rather than the raw tag (`:2876-2886`).

So requirement 4 is substantially met already; it needs the gap in §3.3 fixed and needs to keep working once tags become atomic.

### 2.7 Tag comparison logic exists but is not surfaced as QA

`_validate_autotag_result()` (`:1339-1356`) already does exactly the comparison a QA check needs:

```python
src_tags  = Counter(autotag_extract_tags(source_text))
cand_tags = Counter(autotag_extract_tags(candidate_target))
```

It is used **only** to validate AI output. There is no user-facing tag verification anywhere — no missing/extra/reordered-tag report, no F8-equivalent. The primitive exists; the feature does not.

### 2.8 Tooltip infrastructure exists

`QTextCharFormat.setToolTip()` is already used for termbase matches inside the source cell (`:3596`, `:3608`, `:3621`), with `setMouseTracking(True)` (`:3149`) and `mouseMoveEvent` tooltip handling (`:3885`, `:5330`). **The hover-tooltip requirement for Partial view needs no new mechanism.**

### 2.9 Lazy grid population exists but is mis-tuned

`load_segments_to_grid()` (`:44154`) → `_populate_single_row()` (`:43697`) with `_compute_initial_visible_rows()` (`:43676`) and `_populated_rows`. Gated by:

```python
PAGE_AUTO_ALL_THRESHOLD = 1000   # :31103
PAGE_FALLBACK_SIZE      = 200    # :31104
```

Projects of ≤1000 segments — the common case — get **every** row's widgets built eagerly: per row, 2 × `QTextEdit` + 2 × `TagHighlighter` + up to 6 × `QTableWidgetItem`. A 900-segment document therefore instantiates ~1800 `QTextEdit`s and ~1800 highlighters at load.

Grid is `QTableWidget` (154 references) — `QAbstractTableModel`/`QTableView` are effectively unused (0 / 1).

---

## 3. Defects found during review

All four were reproduced against code copied verbatim out of `Supervertaler.py`.

### 3.1 🔴 Compact mode silently corrupts formatting (data loss)

`compact_tags()` numbers placeholders by **tag name**, not by occurrence (`:1068-1075`, `:1109`):

```python
tag_name_to_num[name]        # keyed on name only
tag_map[placeholder] = full_tag   # last write wins
```

Two different tags sharing a name collapse to one placeholder, and the reversal map keeps only the last:

```
input     : Specify <cf color="#227acb" font="tahoma">Wall</cf> and <cf color="#ff0000">Floor</cf>
compact   : Specify {1}Wall</cf> and {1}Floor</cf>
tag_map   : {'{1}': '<cf color="#ff0000">'}
round-trip: Specify <cf color="#ff0000">Wall</cf> and <cf color="#ff0000">Floor</cf>
                          ^^^^^^^ first tag's colour silently lost
```

Any segment with two same-named, differently-attributed tags loses formatting the moment it is edited in Compact view. This matters far more once Partial view becomes a recommended default.

### 3.2 🟠 Compaction is asymmetric

The passthrough rule `if not attrs and len(name) <= 3: return full_tag` (`:1106`) is evaluated per tag, so an opening tag with attributes is shortened while its own closing tag is not:

```
<cf color="…" font="…">Wall</cf>   →   {1}Wall</cf>
```

The user sees a numbered placeholder paired with raw markup — the opposite of a clean short view, and it makes pair-matching harder, not easier.

### 3.3 🟠 Ctrl+, cannot insert self-closing/standalone tags

`extract_all_tags` (regex #2) has no `/?` before the closing `>`:

| Text | `extract_all_tags` (drives Ctrl+,) | `autotag_extract_tags` (AI path) |
|---|---|---|
| `before <2/> after` | `[]` | `['<2/>']` |
| `before <x1/> after` | `[]` | `['<x1/>']` |
| `Line<br/>break` | `[]` | `['<br/>']` |

Standalone tags are exactly what `sdlppx_handler` emits as `<N/>` (`:485`, `:532`) for `<x id="N"/>`. So on SDLXLIFF projects — the flagship format — **Ctrl+, silently refuses to insert standalone tags** and `find_next_unused_tag` reports "all tags already in target" when they are not.

### 3.4 🟡 Compact placeholder syntax collides with Déjà Vu native tags

DVX tags are `{00108}` (`dejavurtf_handler.py:81-82`); the compact placeholder pattern is `\{/?\d+/?\}`. Verified: `{00108}` and `{00109}` both match the compact-placeholder pattern. In Compact view on a DVX project, native tags and internal placeholders are indistinguishable — and `expand_compact_tags` does blind `str.replace()` (`:1166-1170`), so a native `{1}`-shaped tag could be rewritten.

### 3.5 🟡 Tag view mode is not persisted

`tag_view_mode` is initialised to `'tags'` if unset (`:30643`) and never written to settings (grep: no read/write outside runtime use). Every launch resets the user's chosen view. A display preference this prominent should persist.

### 3.6 🟡 No tag verification surfaced

Per §2.7 — the comparison primitive exists, the QA feature does not. This is the clearest functional gap against Trados/memoQ/Matecat.

---

## 4. Architectural decision: atomic tag objects

### The choice

| Option | Atomicity | Partial view | Read-back risk |
|---|---|---|---|
| **(a) `QTextObjectInterface` atoms** — each tag = one `U+FFFC` char with a custom format carrying tag data; Qt draws a pill via `drawObject()` | **True** and free: one char ⇒ one Backspace deletes it whole, arrows step over, caret cannot enter, selection treats it as a unit | **Native** — displayed width is independent of stored markup | `toPlainText()` returns `U+FFFC`; every read-back must serialise |
| (b) Literal text + `keyPressEvent` guards | **Simulated** — must hand-handle Backspace, Delete, arrows, Home/End, selection, drag, paste, undo; any missed path corrupts | **Impossible** without buffer rewriting, i.e. back to §3.1/§3.2/§3.4 | None |

**Recommendation: (a).** Two reasons specific to this codebase:

1. Option (b) cannot deliver the Partial/Full switch at all without string substitution — and string substitution is precisely what causes defects 3.1, 3.2 and 3.4. Choosing (b) means inheriting and deepening three verified bugs.
2. Option (a)'s only real cost — display ≠ storage — is a problem this codebase **has already solved and shipped** for WYSIWYG mode (`_wysiwyg_document_to_tagged_text`, `:60517`), through a normalisation funnel that already exists (`:43949-43977`). Atoms add one branch to an existing chain rather than inventing a pattern.

### Consequence worth stating plainly

Doing protection **first** makes the display switch nearly free and **eliminates defects 3.1, 3.2 and 3.4 by construction**: with no placeholder text in the buffer there is no placeholder collision, no reversal map, and no last-write-wins. Doing the display switch first, as an extension of `compact_tags()`, locks those bugs in. **This ordering is the main recommendation of this review.**

### Data model

Segment storage stays exactly as it is — `Segment.source` / `Segment.target` remain plain `str` (`:1785-1789`). TM, the SQLite layer, every format handler, TMX export and the AutoTagger all keep working unchanged. Protection is a **rendering + input-handling layer**, never a storage change.

```python
@dataclass(frozen=True)
class TagToken:
    raw:      str    # exact original substring, e.g. '<cf color="#227acb" font="tahoma">'
    kind:     str    # 'open' | 'close' | 'standalone'
    name:     str    # 'cf'
    pair_id:  int    # matched open/close share a pair_id; standalone unique
    ordinal:  int    # 1-based occurrence order — fixes 3.1 (identity ≠ name)
    origin:   str    # 'structured' (from real markup) | 'heuristic' (regex-detected)
    short:    str    # Partial-view label, e.g. '1' / 'cf1'
    tooltip:  str    # Full-view detail for hover
```

`ordinal` is the fix for §3.1; `origin` is the fix for requirement 2 (§6.1).

---

## 5. The tag display modes switch (new requirement)

### Mapping onto what exists

| Requested | Existing mode | Work needed |
|---|---|---|
| **Full Tag Text** (full view) | `'tags'` | Rename label only — behaviour already correct |
| **Partial Tag Text** (short view) | `'compact'` | Fix 3.1/3.2/3.4, add hover tooltip, apply to *all* tags uniformly |
| *(orthogonal)* tags hidden entirely | `'wysiwyg'` | Unchanged |

The switch is therefore a **rationalisation of the existing three-state control, not a fourth mechanism**. The segmented control keeps three buttons and Ctrl+Shift+H keeps cycling them:

```
  ✨ WYSIWYG   │   📦 Partial Tag Text   │   🏷️ Full Tag Text
   (no tags)       (short + tooltip)         (complete markup)
```

### Behaviour

**Full Tag Text** — every tag rendered with its complete markup inline, attributes included, in the tag colour. Atom display text = `token.raw`. Crowded by design; this is the mode for diagnosing structural errors and mismatched pairs.

**Partial Tag Text** — every tag rendered as a compact pill showing `token.short` (paired tags show their pair number, so an open/close pair is visually matched; standalone tags get a distinct glyph). Hover shows `token.tooltip` with the full markup, via the existing `QTextCharFormat.setToolTip()` mechanism (§2.8).

Both modes render the **same atoms** with a different `display` property — one flag on the tag object, no buffer rewriting, no reversal map. `expand_compact_tags()` and the `_compact_tag_map` plumbing (`:2877`, `:6291`, `:43967`, `:44045`, `:48486`) get deleted once atoms land, which removes the §3.1/§3.2/§3.4 defect class outright.

Applied to the request's own example, in Partial view:

```
Specify ⟨1⟩⟨2⟩Wall thickness ⟨/2⟩⟨/1⟩⟨3⟩range ⟨/3⟩⟨4⟩②⟨/4⟩
        └─ hover ⟨1⟩ → <cf color="#227acb" font="tahoma">
```

### Persistence

Fix §3.5 at the same time: persist as `general.tag_display_mode` ∈ `{wysiwyg, partial, full}`, tolerating the legacy `'compact'`/`'tags'` values on read.

---

## 6. Implementation plan

Ordered so each phase is independently shippable and testable.

### Phase 0 — Canonical tag model (`modules/tag_protection.py`)

- One parser replacing regexes #2, #3, #5 and the inline highlighter pattern; `tag_manager.py`'s whitelist (#1) is superseded for detection but kept for DOCX run↔tag conversion.
- Tighten it to answer requirement 2 (§6.1 below).
- Emit `list[TagToken]` with stable `ordinal` and `pair_id`.
- **Fix §3.3**: single pattern matches self-closing forms, so `find_next_unused_tag` sees `<2/>` / `<x1/>` / `<br/>`.
- Keep `extract_all_tags` / `autotag_extract_tags` as thin shims over the new parser so no caller changes in this phase.
- Tests: `tests/test_tag_protection.py` — the request's own example, every convention in the §2.1 table, the §3.1 two-`<cf>` case, self-closing forms, and the §3.4 DVX collision.

**Ships alone:** fixes 3.3 with no UI change.

### Phase 6.1 — Genuine tags vs. literal `<…>` text (requirement 2)

Two-tier decision, in priority order:

1. **Structured origin wins.** For SDLXLIFF/MQXLIFF/DVX/CafeTran/Phrase, tag identity comes from the file's real markup at import — `sdlppx_handler`'s `<N>`/`<N/>` scheme (§2.3), memoQ brackets, DVX `{NNNNN}`, pipes. These are marked `origin='structured'` and are **always protected**, never re-derived from display text. This is how Trados/memoQ avoid the ambiguity, and Supervertaler already has the mechanism for its most important format.
2. **Heuristic fallback** only where no structured markup exists (plain DOCX, plain text, and `<b>`-style tags synthesised by `TagManager`). Marked `origin='heuristic'` and tightened to reject prose:
   - name must be a single token matching `[A-Za-z][A-Za-z0-9._-]*` or be all-digits (Trados numeric);
   - attributes must be `key="value"` / `key='value'` shaped;
   - no `<`, no newline inside; length cap;
   - for `heuristic` origin only, require the tag to be **paired or known-standalone** — an unmatched lone `<` … `>` in prose (e.g. `temperature <5 > 3`, `a <b or c>`) stays plain text.

Consequence: `if x < y and y > z` is never protected; `<cf color="#227acb" font="tahoma">` always is. The user's stated rule — "any string beginning with `<` and ending with `>` is considered a tag" — is exactly the behaviour being replaced.

### Phase 1 — Atomic protected rendering

- Register a custom text object; render each `TagToken` as one `U+FFFC` with a `QTextCharFormat` carrying token data + tooltip; draw a pill in `drawObject()` honouring the existing `tag_highlight_color` and theme-aware dark-mode variant (`:3158`, `:4983`).
- Add `document_to_raw()` — the atom serialiser — and route the write-back funnel through it (`:43949-43977`, plus the duplicate sites `:44045`, `:48486`). **Consolidate that duplicated chain into one function while touching it**, so the funnel is a funnel by construction.
- Keep `TagHighlighter` for invisibles, NBSP, CafeTran pipes, Markdown and spellcheck; **remove its tag-colouring branch** when protection is on — the atoms carry their own appearance. This also removes a per-block regex scan (see Phase 5).
- Audit read-back: 74 `toPlainText()` sites in `Supervertaler.py`, but only grid-cell reads matter and the funnel already localises the important ones. `get_raw_text()` (`:3436`) already exists as the intended abstraction and currently has **zero callers** — make it the single sanctioned accessor.
- Preserve: Ctrl+, insertion (`:2792`, `:6207`) must insert an atom; find/replace; comment anchors (`_apply_comment_anchors_to_all_cells`); termbase highlighting offsets (`clean_to_display` mapping at `:3467-3478` must skip atoms); AutoTagger; `protect_tags_from_linebreak` becomes unnecessary for atoms (they cannot split).

### Phase 2 — Display modes switch

Per §5: relabel the segmented control, implement `partial`/`full` as an atom render property, wire tooltips, delete the `compact_tags`/`expand_compact_tags`/`_compact_tag_map` plumbing, persist the choice.

### Phase 3 — Tag verification (QA)

Promote the §2.7 primitive to a real check: compare source vs target token multisets and report **missing / extra / reordered / unpaired** tags. Surface on confirm and as a batch QA pass over the project, in the existing QA/reporting surface. Reuse `_validate_autotag_result`'s comparison so AI-placed and human-placed tags are judged identically.

### Phase 4 — Toggles

- `general.tag_protection_enabled` (default **on**) — off restores today's plain-text behaviour verbatim, which is also the escape hatch if a user hits an unforeseen edge case.
- `general.tag_display_mode` — `wysiwyg | partial | full`.
- Both follow the existing checkbox pattern in `_save_view_settings_from_ui_impl` (`:29638`), which already snapshots old values and only runs expensive grid loops when a setting actually changed (`:29650-29657`). Live-refresh via a targeted per-row pass modelled on `refresh_grid_tag_colors` (`:47002`) — **populated/visible rows only**, not a full grid rebuild.
- Add a "tag strictness"-style leniency control **only after** §10's open question is resolved; do not invent levels.

### Phase 5 — Performance (bounded, no rewrite)

Deliberately **not** a `QTableWidget` → model/view migration: that would touch nearly every grid interaction path (autocorrect, spellcheck, comments, TM, termbase highlighting, tag rendering) in a 72.5k-line file for a shipping product. The changelog shows the team already gets results from targeted, profiling-driven fixes — e.g. a 19.8 s freeze traced to one `find_termbase_matches_in_source` call, and MT lookups moved to a `ThreadPoolExecutor` in the SuperLookup overhaul.

Ranked by everyday-interactivity impact per unit of risk:

1. **Make population viewport-driven, not page-flip-driven.** The lazy machinery already exists (§2.9); it just doesn't engage below 1000 segments. Lower `PAGE_AUTO_ALL_THRESHOLD` and/or populate on scroll. Biggest win for project-open time; low risk because the code path is already exercised by large projects.
2. **Parse tags once per cell, cache on the atom.** Today `TagHighlighter.highlightBlock` recompiles a 10-branch alternation **per block, per rehighlight** (`:4737`). Atoms make tag appearance static, so the scan happens once at population. Phase 1 delivers this as a side effect. At minimum, hoist the compiled pattern to module level regardless.
3. **Audit the full-grid `for row in range(self.table.rowCount())` loops** (e.g. `:47007`, `:60586`, `:44301`) so settings toggles touch only populated rows. `_refresh_grid_display_mode` already calls `auto_resize_rows()` for the whole grid on every mode switch.
4. **Confirm batch AI translation is off the UI thread.** `modules/llm_clients.py` contains **no** `QThread`/`QRunnable`/`moveToThread` (verified: 0 matches) — threading, if any, lives at the call sites in `Supervertaler.py`. Verify per entry point; apply the proven `ThreadPoolExecutor` pattern where it is missing.

---

## 7. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| A missed `toPlainText()` read-back writes `U+FFFC` into a segment, TM or export | **High** | Single sanctioned accessor `get_raw_text()`; consolidate the write-back funnel; add an assertion that no stored string ever contains `U+FFFC`; test round-trip per format |
| Atoms break termbase/comment character offsets | Medium | `clean_to_display` mapping (`:3467`) already handles marker chars — extend it to atoms; regression-test with tags + terms + comments in one segment |
| Heuristic tightening reclassifies something users currently see coloured | Medium | `origin='structured'` is unaffected; ship the parser (Phase 0) before the UI change so a diff of detected tags across a corpus can be reviewed |
| Protection interferes with dictation / autocorrect / find-replace | Medium | Those paths mutate via `QTextCursor.insertText`; atoms are single chars, so they compose — but each needs an explicit test |
| Users on Compact view have silently corrupted targets already (§3.1) | Medium | Fixing forward does not repair past damage; the tag QA check (Phase 3) surfaces existing mismatches, and the release note should say so plainly |
| Scope creep into a grid rewrite | Medium | Phase 5 is explicitly bounded; model/view migration is out of scope |

---

## 8. Test plan

New `tests/test_tag_protection.py` (there are currently **no** tag tests in `tests/`):

- Parser: the request's example; every convention in §2.1; self-closing forms (§3.3); prose false-positives (`a < b > c`, `if x<5 and y>2`); the §3.1 two-`<cf>` case; DVX `{00108}` vs placeholder (§3.4).
- Round-trip: `document_to_raw(build_document(s)) == s` for a corpus per format.
- Invariant: no stored segment string ever contains `U+FFFC`.
- Verification: missing / extra / reordered / unpaired detection.
- Display modes: same atoms, `partial` vs `full` differ only in rendered label; tooltip carries full markup.
- Interaction: Ctrl+, inserts an atom, including standalone tags; protection off restores literal-text behaviour byte-for-byte.

Manual smoke per `CLAUDE.md`: DOCX import → translate → export; SDLPPX round-trip; `.svproj` save/load; TM + termbase matching.

---

## 9. Sequencing summary

```
Phase 0  canonical parser + TagToken            → fixes 3.3            (ships alone)
Phase 6.1 structured vs heuristic origin        → requirement 2
Phase 1  atomic protected rendering             → requirement 1, 4
Phase 2  Partial / Full display switch          → requirement 5; retires 3.1, 3.2, 3.4, 3.5
Phase 3  tag verification QA                    → closes the competitor gap (3.6)
Phase 4  toggles                                → requirement 3
Phase 5  bounded performance pass               → requirement 6
```

---

## 10. Open questions

1. **memoQ tag strictness levels.** The supplied docs URL is 403 to this environment, so the actual level names/semantics are unverified. Needed before designing the leniency control in Phase 4 — should not be guessed.
2. **Requirement 4 wording.** Read here as: translators type prose freely and insert tags as atomic units (Ctrl+, / auto-copy on empty target), never hand-typing raw markup. Confirm if something more specific was meant.
3. **Auto-copy source tags into an empty target?** Trados/memoQ offer this. Cheap once atoms exist, but it is a behaviour change — opt-in setting or default?
4. **Partial-view label scheme.** Pair number (`⟨1⟩…⟨/1⟩`) vs. name+number (`⟨cf1⟩`). Pair numbers match Trados; names carry more meaning. Recommend pair numbers with the name in the tooltip.
