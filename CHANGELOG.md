# Changelog

## 0.1.4

- **Shift+Enter now actually works** — most terminals send `ctrl+j` for Shift+Enter; the binding now targets that directly so inserting a newline in the comment box works as expected.
- **File-level comments** — commenting while on one of the top 4 header lines (the `diff --git` / `---` / `+++` block) creates a whole-file comment. These render as `--- file.py (full file)` in the review output with no line-context snippet, and the comment label shows "(whole file)" while editing.
- **Forward to Claude** — pressing `F` on the summary page (or the new button) writes the review to `~/.git-review` and immediately launches `claude /git-review`.
- **Ctrl+Up / Ctrl+Down scroll the viewport** independently of the review cursor — the cursor clamps to the visible area rather than scrolling with the viewport.
- **Scroll fixes** — page-up/down and `ensure_line_visible` now use `scroll_to(animate=False)` instead of direct `scroll_y` assignment, fixing jerky/incorrect scroll positions.

## 0.1.3

- **Shift+Enter now reliably inserts a newline** in the comment box — switched from `_on_key` interception to Textual's `BINDINGS` system so `enter` is bound to submit and `shift+enter` falls through to TextArea's natural newline handling.
- **W saves mid-review** — pressing `w` while browsing files (before the summary page) writes current comments to `~/.git-review` with a `# (reviewed to <file>)` marker and shows "Saved" feedback without quitting.
- **Tab opens comment field** — `tab` now opens the comment box in review mode (same as `enter`), with `priority=True` so it beats Textual's focus-cycle handling.
- **W shown in footer** — the key hint bar now includes `W`rite.

## 0.1.2

- **Comment indicators stay in the right place** when switching between diff and full-file view. The indicator position is calculated from the actual file line numbers and cached, so toggling full view no longer shifts markers or corrupts the review output.
- **Review report context is frozen at save time** — switching views after writing a comment no longer changes the code snippet captured in the report.

## 0.1.1

- **Review summary is now editable** — press `R` on the summary page to revise the text before writing or copying. Ctrl+S / Esc saves, Ctrl+C discards.
- **Write now quits** — pressing `W` on the summary page writes to `~/.git-review` and exits immediately.
- **Comment input now works like Claude Code** — `Enter` submits a comment, `Shift+Enter` inserts a newline.
- **`Enter` and `Tab` enter review mode** when browsing, and open the comment box when already in review mode.
- **Text selection and copy now work** — mouse reporting is disabled so terminal-native selection and Ctrl+Shift+C work as normal.
- **Arrow keys fixed in comment/revise mode** — left/right no longer change file while typing; cursor movement works correctly.

## 0.1.0

- Initial release.
