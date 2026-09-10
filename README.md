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
| `←` / `→` | Previous / next file |
| `↑` / `↓` / `PgUp` / `PgDn` / `Space` | Scroll |
| `Ctrl+↑` / `Ctrl+↓` | Scroll one line |
| `F` | Toggle full file view (expand all context) |
| `L` | Toggle line numbers |
| `R` | Enter review mode |
| `W` | Save comments to `~/.git-review` (without quitting) |
| `Q` / `Esc` | Quit |
| `:` | Jump to file by number |

## Leaving comments

Press `R` to enter review mode. The selected line is highlighted.

| Key | Action |
|-----|--------|
| `↑` / `↓` / `PgUp` / `PgDn` | Move selection |
| `Ctrl+↑` / `Ctrl+↓` | Scroll viewport (selection clamps to visible area) |
| `Enter` / `Tab` / `Space` | Open comment box for selected line |
| `R` / `Esc` | Exit review mode |

In the comment box:

| Key | Action |
|-----|--------|
| `Enter` | Save comment |
| `Shift+Enter` | Insert newline |
| `Ctrl+S` / `Esc` | Save comment |
| `Ctrl+C` | Discard comment |

Commenting on one of the header lines (the `diff --git` / `---` / `+++` block, before the first `@@` hunk) creates a whole-file comment with no line reference.

Lines with comments are marked with a `●` in the margin.

## Exporting

Once you have comments, navigate to the last file and press `→` to reach the summary page. From there:

| Key | Action |
|-----|--------|
| `R` | Edit / revise the summary text |
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

