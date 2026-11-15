# Changelog

## 0.2.0

- **Toggles are remembered** — full file, line numbers, split, highlight, blame and sidebar are saved as soon as you change them to `git-review.json` in your config directory (`$XDG_CONFIG_HOME/git-review/`, default `~/.config/git-review/`) and restored on startup. Each toggle is saved on its own, so with several instances running the most recent change to each one wins.
- **Blame gutter** — press `B` to show the commit and author of each line on the left, in unified and split views. Context and added lines are blamed on the new side of the diff and removed lines on the old side (the revision your diff args compare from, e.g. `HEAD` by default, or `A` in `A..B`). Uncommitted lines and untracked files are blank. In split view, a row shows one label: the new side's, else the removed line's.
- **File sidebar** — a list of files on the left (hidden by default), toggled with `N` (Nav). With it open, `←` or a click moves into it, `↑`/`↓` change file, and `Enter`/`Tab`/`→`/`Esc` return to the diff; the current file is selected on entry. With it closed, `←`/`→` still go to the previous/next file. Files with comments show a `●`, and a "Review Summary" entry appears once there are comments.
- **Files are sorted alphabetically** — untracked files are now interleaved with the diffed files instead of being appended at the end.
- **Click a line to select it** — enters review mode if needed and moves the cursor to the clicked line (either side, in split view); `Enter` still opens the comment box. Dragging to select and copy text is unaffected. Unified view now wraps long lines itself (as split view already did), so row-to-line mapping is exact: clicking and scrolling to a line now work correctly on files with wrapped lines. Wrapped continuation rows are indented under the line-number gutter.
- **Prompt Claude about a line** — in the comment box, `Ctrl+P` sends the text to an interactive `claude` session (with the file, line numbers, surrounding diff and diff command as context) instead of saving it as a comment. Quitting Claude reloads git-review at the same file and line. With the filename in the title bar selected, the prompt is about the whole file. Textual's command palette is disabled so it doesn't claim `Ctrl+P`.
- **Diff header hidden** — the `diff --git` / `index` / `---` / `+++` block is no longer shown, and neither is the lone `@@` line in full-file view (normal view keeps `@@` as the hunk separator). Files with no hunks (pure renames, binary changes) still show their header, since it is all there is.
- **Whole-file comments now attach to the filename in the title bar** — in review mode press `↑` from the first line to select it, then `Enter`. It shows a `●` when the file has a whole-file comment. (This replaces commenting on the header lines.)
- **`R` now reloads** — re-runs the diff command and returns to the same file and line (found by line number, so it still lands in the right place if lines were added above). View settings (split, highlight, full, line numbers) are kept. Comments follow their file by name; if the code moved under a comment, its saved context in the summary is what Claude works from. If no diff remains, the previous view is kept.
- **Review mode is now entered with `Enter` / `Tab`** and left with `Esc` (`R` used to do both). On the summary page `Enter` / `Tab` edits the summary, replacing `R`.
- **Full-file view is now sticky** — `F` stays on as you move between files instead of resetting to the normal diff on each one. Press `F` again to turn it off.
- **Syntax highlighting** — press `H` to highlight code with pygments, using your terminal's palette. Highlighting is tied to full-file view, since hunks alone give false colours: turning `H` on also turns `F` on, and turning `F` off also turns `H` off (`F` on without `H`, and `H` off while in full view, are fine). The `+`/`-` markers keep their red/green; the rest of the line is highlighted. Each hunk is lexed as separate old and new streams so multi-line strings and docstrings highlight correctly. Falls back to git's colours for file types without a lexer. Works in unified, split and full-file views.
- **Split view** — press `S` to toggle between unified and side-by-side diffs, in both the normal and full-file views. The old side (context + removed) is on the left, the new side (context + added) on the right. Long lines wrap within their column and the shorter side is padded with blank, unnumbered rows so the columns stay aligned. With line numbers on, each side shows its own numbering.

## 0.1.5

- **Untracked files are now included in the review** — new (non-ignored) files are appended after the regular diff, synthesized against `/dev/null` so they show up as all-added content with correct line numbers, full-file view, and commenting. Respects any `-- pathspec` passed to `git review`.
- **File-level header detection is now computed per file** instead of assuming a fixed 4-line preamble, so commenting near the top of a new-file diff (which has an extra `new file mode` line) correctly counts as a whole-file comment.

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
