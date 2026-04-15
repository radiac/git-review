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

## Navigation

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / next file |
| `Space` / `↑` / `↓` / `PgUp` / `PgDn` | Scroll |
| `F` | Toggle full file view (expand all context) |
| `L` | Toggle line numbers |
| `R` | Enter / exit review mode |
| `Q` / `Esc` | Quit |
| `:` | Jump to file by number |

## Leaving comments

Press `R` to enter review mode. The selected line is highlighted.

| Key | Action |
|-----|--------|
| `↑` / `↓` | Move selection |
| `Enter` or `Space` | Open comment box for selected line |
| `Ctrl+S` / `Esc` | Save comment |
| `Ctrl+C` | Discard comment |
| `R` / `Esc` | Exit review mode |

Lines with comments are marked with a `●` in the margin.

## Exporting

Once you have comments, navigate to the last file and press `→` to reach the summary page. From there:

- **`W`** — write review to `~/.git-review`
- **`C`** — copy review to clipboard

The exported format is structured for an LLM: each comment includes the filename, approximate line number, and surrounding context so LLMs can locate and implement each change.

## Claude integration

After writing the review, use the `/git-review` Claude command to apply the changes:

```
/git-review
```

Claude will read `~/.git-review`, locate each annotated position in the code, and implement the instructions.

