# git-review

A terminal UI for reviewing git diffs and annotating them for Claude.

Browse changed files, leave comments on specific lines, then export the review as structured instructions that Claude can act on directly.


## Installation

Make sure you have [uv](https://docs.astral.sh/uv/) installed, then:

```
git clone https://github.com/radiac/git-review.git
cd git-review
./install.sh    # optional
```

The `install.sh` isn't essential - it registers `git review` as a git alias pointing to the script in the current directory, and installs the `/git-review` Claude command into `~/.claude/commands/`.


## Usage

```
git review [git diff args]
```

For example:

```
git review
git review --staged
git review HEAD~1
git review main..feature-branch
git review -- path/to/file.py
```

Untracked (new, non-ignored) files are appended after the regular diff, shown as
all-added content, so you can review and comment on them too. If you pass a `--
pathspec`, only untracked files matching it are included.

## Navigation

| Key | Action |
|-----|--------|
| `N` | Show / hide the file sidebar (Nav) |
| `B` | Toggle a blame gutter (commit and author of each line, removed lines are blamed on the old side; uncommitted lines and untracked files are blank) |
| `←` / `→` | Previous / next file. While the nav sidebar is open, `←` moves into it instead |
| Click the sidebar | Move into it and select that file |
| `↑` / `↓` (in the sidebar) | Change file |
| `Enter` / `Tab` / `→` / `Esc` (in the sidebar) | Return to the diff |
| `↑` / `↓` / `PgUp` / `PgDn` / `Space` | Scroll |
| `Ctrl+↑` / `Ctrl+↓` | Scroll one line |
| `F` | Toggle full file view (expand all context) |
| `L` | Toggle line numbers |
| `S` | Toggle split (side-by-side) view |
| `H` | Toggle syntax highlighting (turns on full file view; turning full view off turns this off) |
| `R` | Reload the diff (re-runs the command, keeping your file and line) |
| `Enter` / `Tab` | Enter review mode |
| Click a line | Select it (entering review mode if needed); `Enter` then adds a comment. In split view, click either side. Dragging still selects and copies text. |
| `W` | Save comments to `~/.git-review` (without quitting) |
| `Q` / `Esc` | Quit |
| `:` | Jump to file by number |

Toggle states (`B`, `N`, `F`, `L`, `S`, `H`) are saved immediately to `~/.config/git-review/git-review.json` (or `$XDG_CONFIG_HOME/git-review/`) and restored on the next run.

## Leaving comments

Press `Enter` or `Tab` to enter review mode. The selected line is highlighted.

| Key | Action |
|-----|--------|
| `↑` / `↓` / `PgUp` / `PgDn` | Move selection |
| `Ctrl+↑` / `Ctrl+↓` | Scroll viewport (selection clamps to visible area) |
| `Enter` / `Tab` / `Space` | Open comment box for selected line |
| `Esc` | Exit review mode |

In the comment box:

| Key | Action |
|-----|--------|
| `Enter` | Save comment |
| `Shift+Enter` | Insert newline |
| `Ctrl+S` / `Esc` | Save comment |
| `Ctrl+C` | Discard comment |
| `Ctrl+P` | Send the text to Claude as a prompt about this line (doesn't save a comment) |

For a whole-file comment, press `↑` from the first line to select the filename in the title bar, then `Enter`. A `●` next to the filename shows that the file has one. The diff header (`diff --git` / `---` / `+++`) isn't shown, and neither is the `@@` line in full-file view.

Lines with comments are marked with a `●` in the margin.

### Prompting Claude about a line

Instead of saving the text as a comment, `Ctrl+P` sends it straight to an interactive `claude` session that takes over the terminal ("explain this", "rewrite this as ...", anything). The prompt includes the file, the old/new line numbers, the diff lines around it and the `git diff` arguments. Select the filename in the title bar first to ask about the whole file. When you quit Claude, git-review reloads the diff and returns you to the same place. Set `$CLAUDE` to use a different binary.

## Exporting

Once you have comments, navigate to the last file and press `→` to reach the summary page. From there:

| Key | Action |
|-----|--------|
| `Enter` / `Tab` | Edit / revise the summary text (`Esc` to finish) |
| `W` | Write review to `~/.git-review` and quit |
| `C` | Copy review to clipboard |
| `F` | Write review and launch `claude /git-review` immediately |

The exported format is structured for an LLM: each comment includes the filename, approximate line number, and surrounding context so Claude can locate and implement each change.

## Claude integration

From the summary page, press `F` to write the review and immediately launch Claude with the `/git-review` command. Or write it manually with `W` and run it yourself:

```
/git-review
```

Claude will read `~/.git-review`, locate each annotated position in the code, and implement the instructions.

