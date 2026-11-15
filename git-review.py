#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "textual>=0.50.0",
#   "pyperclip>=1.8",
#   "pygments>=2.0",
# ]
# ///

__version__ = "0.2.0"

import json
import os
import tempfile
import re
import shlex
from bisect import bisect_left
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pyperclip
from rich.console import Console
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Input, Label, Static, TextArea


_WRAP_CONSOLE = Console()


def get_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() or "HEAD"
    except subprocess.CalledProcessError:
        return "unknown"


def get_diff(args: list[str]) -> str:
    result = subprocess.run(
        ["git", "diff", "--color=always", *args],
        capture_output=True, text=True,
    )
    return result.stdout


def get_untracked_files(diff_args: list[str]) -> list[str]:
    """List untracked, non-ignored files, honouring any `-- pathspec` in diff_args."""
    pathspec = []
    if "--" in diff_args:
        pathspec = diff_args[diff_args.index("--") + 1:]
    cmd = ["git", "ls-files", "--others", "--exclude-standard"]
    if pathspec:
        cmd += ["--", *pathspec]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def get_untracked_diff(path: str) -> str:
    """Synthesize a diff for an untracked file by diffing it against /dev/null."""
    result = subprocess.run(
        ["git", "diff", "--no-index", "--color=always", "--", "/dev/null", path],
        capture_output=True, text=True,
    )
    return result.stdout


BLAME_W = 18    # "abc1234 Firstname "
_BLAME_COLOURS = ["cyan", "magenta", "green", "yellow", "blue", "bright_red"]


def _rev_ok(rev: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
        capture_output=True, text=True,
    ).returncode == 0


def blame_revs(diff_args: list[str]) -> tuple[str, str | None]:
    """(old, new) revisions a diff compares, for blame. new is None for the working tree.

    Reads the revision arguments of `git diff` (A, A B, A..B, A...B, --cached [A]); anything
    that isn't a revision, such as a path given without `--`, is ignored. Staged changes
    (no revision, or --cached) are approximated by HEAD and the working tree.
    """
    args = diff_args[:diff_args.index("--")] if "--" in diff_args else diff_args
    revs = [a for a in args if not a.startswith("-")]
    if len(revs) == 1 and "..." in revs[0]:
        left, right = (part or "HEAD" for part in revs[0].split("...", 1))
        base = subprocess.run(["git", "merge-base", left, right], capture_output=True, text=True)
        return (base.stdout.strip() or left), right
    if len(revs) == 1 and ".." in revs[0]:
        left, right = (part or "HEAD" for part in revs[0].split("..", 1))
        return left, right
    revs = [r for r in revs if _rev_ok(r)]
    if len(revs) >= 2:
        return revs[0], revs[1]
    if revs:
        return revs[0], None
    return "HEAD", None


PREF_KEYS = ("full", "line_nums", "split", "highlight", "blame", "sidebar")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "git-review" / "git-review.json"


def load_prefs() -> dict[str, bool]:
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in PREF_KEYS if isinstance(data.get(k), bool)}


def save_prefs(**changes: bool) -> None:
    """Merge changes into the config file.

    Re-reads the file each time and only touches the keys that changed, so
    concurrent instances don't clobber each other's other toggles (latest wins
    per toggle). Written atomically; failures are ignored.
    """
    path = config_path()
    data = load_prefs()
    data.update(changes)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".git-review-", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        pass


def get_blame(path: str, rev: str | None = None) -> dict[int, tuple[str, str]]:
    """Blame a file (the working tree, or at `rev`) -> {line number: (full sha, author)}.

    Empty on failure.
    """
    result = subprocess.run(
        ["git", "blame", "--line-porcelain", *([rev] if rev else []), "--", path],
        capture_output=True, text=True,
    )
    out: dict[int, tuple[str, str]] = {}
    if result.returncode != 0:
        return out
    sha, line, author = "", 0, ""
    for raw in result.stdout.splitlines():
        if raw.startswith("\t"):
            out[line] = (sha, author)
        elif re.match(r"[0-9a-f]{40} \d+ \d+", raw):
            parts = raw.split()
            sha, line = parts[0], int(parts[2])
        elif raw.startswith("author "):
            author = raw[7:]
    return out


ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def parse_diff(diff_output: str) -> list[tuple[str, str]]:
    """Split diff output into per-file (filename, content) tuples."""
    lines = diff_output.splitlines(keepends=True)
    plain_lines = [ANSI_ESCAPE.sub("", line) for line in lines]

    split_indices = [
        i for i, line in enumerate(plain_lines) if line.startswith("diff --git ")
    ]
    if not split_indices:
        return []

    files = []
    for j, start in enumerate(split_indices):
        end = split_indices[j + 1] if j + 1 < len(split_indices) else len(lines)
        section = "".join(lines[start:end])
        match = re.match(r"diff --git a/(.*?) b/", plain_lines[start])
        filename = match.group(1) if match else "unknown"
        files.append((filename, section))
    return files


@dataclass
class DiffLine:
    text: str           # raw line with ANSI codes (no trailing newline)
    plain: str          # ANSI-stripped (no trailing newline)
    kind: str           # 'diff_header'|'file_header'|'hunk'|'context'|'added'|'removed'|'other'
    src_num: int | None  # source-file line number
    dst_num: int | None  # new-file line number


@dataclass
class Comment:
    file_index: int
    filename: str
    diff_line_index: int | None  # index into diff-view parsed lines; -1 = line not in diff
    full_line_index: int | None  # index into full-view parsed lines; -1 = line not in full
    src_num: int | None
    dst_num: int | None
    context_before: list[str]   # frozen at save time
    context_after: list[str]
    text: str
    file_level: bool = False    # comment applies to whole file (added on header lines)


def compute_header_len(lines: list[DiffLine]) -> int:
    """Number of leading lines that belong to the diff/file header, before the first hunk.

    Comments made on a header line are treated as whole-file comments. The header is
    usually 4 lines (diff --git / index / --- / +++) but new-file diffs add a "new file
    mode" line, so this is computed per-file rather than assumed to be a fixed length.
    """
    for i, dl in enumerate(lines):
        if dl.kind == "hunk":
            return i
    return len(lines)


def parse_lines(content: str) -> list[DiffLine]:
    """Parse diff content into DiffLine objects with line number tracking."""
    lines = content.splitlines()
    result = []
    src: int | None = None
    dst: int | None = None

    for raw in lines:
        plain = ANSI_ESCAPE.sub("", raw)

        if plain.startswith("diff --git "):
            result.append(DiffLine(raw, plain, "diff_header", None, None))
            src = None
            dst = None
        elif plain.startswith("--- ") or plain.startswith("+++ "):
            result.append(DiffLine(raw, plain, "file_header", None, None))
        elif plain.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", plain)
            if m:
                src = int(m.group(1))
                dst = int(m.group(2))
            result.append(DiffLine(raw, plain, "hunk", None, None))
        elif plain.startswith("-") and src is not None:
            result.append(DiffLine(raw, plain, "removed", src, None))
            src += 1
        elif plain.startswith("+") and dst is not None:
            result.append(DiffLine(raw, plain, "added", None, dst))
            dst += 1
        elif plain.startswith(" ") and src is not None:
            result.append(DiffLine(raw, plain, "context", src, dst))
            src += 1
            if dst is not None:
                dst += 1
        else:
            result.append(DiffLine(raw, plain, "other", None, None))

    return result


def highlight_lines(filename: str, lines: list[DiffLine]) -> dict[int, Text]:
    """Syntax-highlight the code part of each context/added/removed line.

    Diffs are lexed as two reconstructed streams per hunk, old (context + removed)
    and new (context + added), so multi-line constructs like docstrings highlight
    properly. Context lines take their highlighting from the new stream. Returns
    {line index: Text of the line body, without its +/-/space marker}; empty if
    there is no lexer for the file.
    """
    try:
        # stripnl/ensurenl would add or drop blank lines and misalign us with the diff
        lexer = get_lexer_for_filename(filename, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return {}
    syntax = Syntax("", lexer, theme="ansi_dark")
    out: dict[int, Text] = {}

    def run(indices: list[int], into: dict[int, Text]) -> None:
        if not indices:
            return
        code = "\n".join(lines[i].plain[1:] for i in indices)
        rendered = list(syntax.highlight(code).split("\n"))
        # Belt and braces: always exactly one Text per input line
        rendered = (rendered + [Text("")] * len(indices))[:len(indices)]
        for i, text in zip(indices, rendered):
            # Drop any background the theme sets so we don't fight the terminal's
            text.spans[:] = [
                type(sp)(sp.start, sp.end, Style(color=sp.style.color, bold=sp.style.bold,
                                                 italic=sp.style.italic, underline=sp.style.underline))
                if isinstance(sp.style, Style) else sp
                for sp in text.spans
            ]
            into[i] = text

    old: list[int] = []
    new: list[int] = []
    new_hl: dict[int, Text] = {}
    old_hl: dict[int, Text] = {}

    def flush() -> None:
        run(new, new_hl)
        run(old, old_hl)
        for i in new:
            out[i] = new_hl[i]
        for i in old:
            if lines[i].kind == "removed":
                out[i] = old_hl[i]
        old.clear()
        new.clear()
        new_hl.clear()
        old_hl.clear()

    for i, dl in enumerate(lines):
        if dl.kind in ("diff_header", "file_header", "hunk"):
            flush()
        elif dl.kind == "context":
            old.append(i)
            new.append(i)
        elif dl.kind == "removed":
            old.append(i)
        elif dl.kind == "added":
            new.append(i)
    flush()
    return out


class CommentArea(TextArea):
    """TextArea for comment input. Enter/ctrl+s/escape submit; shift+enter inserts newline."""

    BINDINGS = [
        # ctrl+j is the byte shift+enter sends in most terminals (\n, ASCII 10)
        Binding("ctrl+j", "insert_newline", priority=True, show=False),
        Binding("enter", "submit_comment", priority=True, show=False),
        Binding("ctrl+s", "submit_comment", priority=True, show=False),
        Binding("escape", "submit_comment", priority=True, show=False),
        Binding("ctrl+c", "discard_comment", priority=True, show=False),
        Binding("ctrl+p", "prompt_claude", priority=True, show=False),
    ]

    class Submit(Message):
        pass

    class Discard(Message):
        pass

    class Ask(Message):
        """Send the text to Claude as a prompt instead of saving it as a comment."""

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_submit_comment(self) -> None:
        self.post_message(self.Submit())

    def action_discard_comment(self) -> None:
        self.post_message(self.Discard())

    def action_prompt_claude(self) -> None:
        self.post_message(self.Ask())


class SummaryArea(TextArea):
    """Full-height editable TextArea for revising the review summary."""

    BINDINGS = [
        Binding("ctrl+j", "insert_newline", priority=True, show=False),
        Binding("ctrl+s", "submit_summary", priority=True, show=False),
        Binding("escape", "submit_summary", priority=True, show=False),
        Binding("ctrl+c", "discard_summary", priority=True, show=False),
    ]

    class Submit(Message):
        pass

    class Discard(Message):
        pass

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_submit_summary(self) -> None:
        self.post_message(self.Submit())

    def action_discard_summary(self) -> None:
        self.post_message(self.Discard())


class DiffView(ScrollableContainer):
    BINDINGS = [
        Binding("space", "scroll_page_down", "Page Down", show=False),
    ]

    # Disable horizontal scrolling so long lines wrap instead of scrolling.
    @property
    def allow_horizontal_scroll(self) -> bool:
        return False

    def scroll_up(self, **kwargs) -> None:
        super().scroll_up(animate=False)

    def scroll_down(self, **kwargs) -> None:
        super().scroll_down(animate=False)

    def scroll_page_up(self, **kwargs) -> None:
        super().scroll_page_up(animate=False)

    def scroll_page_down(self, **kwargs) -> None:
        super().scroll_page_down(animate=False)

    class Clicked(Message):
        """A click (not a drag) at a content row/column of the diff."""

        def __init__(self, row: int, col: int) -> None:
            super().__init__()
            self.row = row
            self.col = col

    _down_at: tuple[int, int] | None = None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._down_at = (event.screen_x, event.screen_y)

    def on_click(self, event: events.Click) -> None:
        # Mouse-down and up in different places is a text selection, not a click;
        # double/triple clicks select a word/line
        if getattr(event, "chain", 1) != 1 or self._down_at != (event.screen_x, event.screen_y):
            return
        region = self.content_region
        self.post_message(self.Clicked(
            row=event.screen_y - region.y + int(self.scroll_y),
            col=event.screen_x - region.x,
        ))

    def compose(self) -> ComposeResult:
        yield Static("", id="diff-content", expand=True)

    def show_content(self, text: Text) -> None:
        self.query_one("#diff-content", Static).update(text)
        self.scroll_home(animate=False)

    def update_content(self, text: Text) -> None:
        self.query_one("#diff-content", Static).update(text)

    def ensure_line_visible(self, line_index: int, end: int | None = None) -> None:
        """Scroll so rows [line_index, end) are visible (end defaults to one row)."""
        if end is None:
            end = line_index + 1
        height = self.size.height
        cur = int(self.scroll_y)
        if line_index < cur:
            self.scroll_to(y=line_index, animate=False)
        elif end > cur + height:
            self.scroll_to(y=min(line_index, end - height), animate=False)


class Sidebar(ScrollableContainer):
    """File list. Display only: the app tracks which file is selected."""

    can_focus = False

    @property
    def allow_horizontal_scroll(self) -> bool:
        return False

    class Clicked(Message):
        def __init__(self, row: int) -> None:
            super().__init__()
            self.row = row

    def on_click(self, event: events.Click) -> None:
        region = self.content_region
        self.post_message(self.Clicked(row=event.screen_y - region.y + int(self.scroll_y)))
        event.stop()

    def compose(self) -> ComposeResult:
        yield Static("", id="sidebar-content", expand=True)

    def set_content(self, text: Text, selected: int) -> None:
        self.query_one("#sidebar-content", Static).update(text)
        self.call_after_refresh(self._ensure_visible, selected)

    def _ensure_visible(self, row: int) -> None:
        height = self.size.height
        cur = int(self.scroll_y)
        if row < cur:
            self.scroll_to(y=row, animate=False)
        elif row >= cur + height:
            self.scroll_to(y=row - height + 1, animate=False)


class DiffPager(App):
    MOUSE_SUPPORT = False
    # Textual's command palette claims ctrl+p, which the comment box uses to prompt Claude
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Horizontal#title {
        height: 1;
        background: $primary;
        color: $text;
    }
    Horizontal#title.review {
        background: darkgreen;
    }
    Horizontal#title.comment-mode {
        background: darkred;
    }
    Horizontal#title.revise-mode {
        background: darkorange;
    }
    Label#title-file {
        width: 1fr;
        padding: 0 1;
    }
    Label#title-info {
        padding: 0 1;
        text-align: right;
    }
    Horizontal#main {
        height: 1fr;
    }
    Sidebar {
        width: 36;
        height: 1fr;
        border-right: solid $primary;
    }
    DiffView {
        width: 1fr;
        height: 1fr;
    }
    Input#jump {
        height: 1;
        border: none;
        padding: 0 0;
        display: none;
    }
    Input#jump:focus {
        border: none;
    }
    Label#comment-label {
        height: 1;
        display: none;
        background: $surface;
        padding: 0 1;
        color: $text-muted;
    }
    CommentArea {
        height: 6;
        display: none;
        border: tall $primary;
    }
    SummaryArea {
        height: 1fr;
        display: none;
        border: tall $primary;
    }
    Horizontal#summary-actions {
        height: 1;
        display: none;
        background: $surface;
    }
    Button.summary-btn {
        height: 1;
        border: none;
        padding: 0 2;
        background: $surface;
        min-width: 0;
    }
    Button.summary-btn:hover {
        background: $primary;
    }
    Horizontal#footer {
        height: 1;
        background: $surface;
        color: $text-muted;
    }
    Label#footer-status {
        width: 1fr;
        padding: 0 1;
    }
    Label#footer-keys {
        padding: 0 1;
        text-align: right;
    }
    """

    BINDINGS = [
        Binding("left", "left", "Prev File"),
        Binding("right", "right", "Next File"),
        Binding("n", "toggle_sidebar", "Nav", show=False),
        Binding("N", "toggle_sidebar", "Nav", show=False),
        Binding("b", "toggle_blame", "Blame", show=False),
        Binding("B", "toggle_blame", "Blame", show=False),
        Binding("f", "full_file", "Full File"),
        Binding("l", "toggle_line_nums", "Line #s", show=False),
        Binding("h", "toggle_highlight", "Highlight", show=False),
        Binding("H", "toggle_highlight", "Highlight", show=False),
        Binding("s", "toggle_split", "Split", show=False),
        Binding("S", "toggle_split", "Split", show=False),
        Binding("r", "reload", "Reload", show=False),
        Binding("R", "reload", "Reload", show=False),
        Binding("up", "review_up", "Up", priority=True, show=False),
        Binding("down", "review_down", "Down", priority=True, show=False),
        Binding("ctrl+up", "viewport_up", "Scroll Up", priority=True, show=False),
        Binding("ctrl+down", "viewport_down", "Scroll Down", priority=True, show=False),
        Binding("pageup", "review_pgup", "PgUp", priority=True, show=False),
        Binding("pagedown", "review_pgdn", "PgDn", priority=True, show=False),
        Binding("tab", "enter_comment", "Comment", priority=True, show=False),
        Binding("enter", "enter_comment", "Comment", show=False),
        Binding("c", "copy_review", "Copy", show=False),
        Binding("C", "copy_review", "Copy", show=False),
        Binding("colon", "jump", "Jump to file", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        files: list[tuple[str, str]],
        branch: str,
        diff_args: list[str],
        untracked_files: set[str] | None = None,
    ):
        super().__init__()
        self.files = files
        self.branch = branch
        self.diff_args = diff_args
        self._untracked_files = untracked_files or set()
        self._index = 0
        self._full_view = False
        self._full_pref = False   # sticky: stay in full view when changing file
        self._review_mode = False
        self._review_line = 0
        self._comment_mode = False
        self._revise_mode = False
        self._summary_text_override: str | None = None
        self._show_line_nums = False
        self._split_view = False
        self._highlight = False
        self._hl_cache: tuple[list[DiffLine], dict[int, Text]] | None = None
        # Split view only: per parsed line, its (first_row, end_row) in the rendered
        # output, and per rendered row, the parsed line index it belongs to.
        self._line_rows: list[tuple[int, int]] = []
        self._row_lines: list[int] = []
        self._row_cells: list[tuple[int | None, int | None]] = []   # split: (left, right) line per row
        self._left_w = 0
        self._rendered_width = 0
        self._comments: list[Comment] = []
        self._parsed_lines: list[DiffLine] = []
        self._header_len = 4
        self._run_claude = False
        self._blame = False
        self._blame_cache: dict[tuple[str | None, str], dict[int, tuple[str, str]]] = {}
        self._blame_revs: tuple[str, str | None] | None = None
        self._blame_w = 0       # width of the blame gutter in the last render (0 = off)
        self._sidebar_visible = False
        self._sidebar_mode = False   # keyboard focus is in the sidebar
        prefs = load_prefs()
        self._full_pref = prefs.get("full", False)
        self._show_line_nums = prefs.get("line_nums", False)
        self._split_view = prefs.get("split", False)
        self._highlight = prefs.get("highlight", False) and self._full_pref
        self._blame = prefs.get("blame", False)
        self._sidebar_visible = prefs.get("sidebar", False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="title"):
            yield Label("", id="title-file")
            yield Label("", id="title-info")
        with Horizontal(id="main"):
            yield Sidebar()
            yield DiffView()
        yield SummaryArea("", id="summary-input")
        yield Label("", id="comment-label")
        yield CommentArea("", id="comment-input")
        yield Input(placeholder="", id="jump")
        with Horizontal(id="summary-actions"):
            yield Button("[bold yellow]W[/bold yellow]rite + quit", id="btn-write", classes="summary-btn")
            yield Button("[bold yellow]C[/bold yellow]opy to clipboard", id="btn-copy", classes="summary-btn")
            yield Button("[bold yellow]F[/bold yellow]orward to claude", id="btn-claude", classes="summary-btn")
        with Horizontal(id="footer"):
            yield Label("", id="footer-status")
            yield Label("[bold yellow]R[/bold yellow]eload  [bold yellow]L[/bold yellow]ines  [bold yellow]N[/bold yellow]av  [bold yellow]B[/bold yellow]lame  [bold yellow]S[/bold yellow]plit  [bold yellow]H[/bold yellow]ighlight  [bold yellow]F[/bold yellow]ull  [bold yellow]W[/bold yellow]rite  [bold yellow]C[/bold yellow]opy  [bold yellow]Q[/bold yellow]uit", id="footer-keys")

    def on_mount(self) -> None:
        self.query_one(DiffView).focus()
        self._show_file(0)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _find_line_by_num(self, src_num: int | None, dst_num: int | None) -> int:
        """Find the index in current _parsed_lines for a line identified by its numbers.

        Tries an exact (src, dst) match first. Failing that, the source number alone
        (A-side numbers are stable when only the working tree changes, whereas B-side
        numbers shift as lines are added above), then the destination number alone.
        Returns -1 if absent.
        """
        lines = self._parsed_lines
        if src_num is not None and dst_num is not None:
            for i, dl in enumerate(lines):
                if dl.src_num == src_num and dl.dst_num == dst_num:
                    return i
        if src_num is not None:
            for i, dl in enumerate(lines):
                if dl.src_num == src_num:
                    return i
        if dst_num is not None:
            for i, dl in enumerate(lines):
                if dl.dst_num == dst_num:
                    return i
        return -1

    def _resolve_line_index(self, c: Comment) -> int:
        """Return this comment's line index for the current view, calculating and caching if needed."""
        if self._full_view:
            if c.full_line_index is None:
                c.full_line_index = self._find_line_by_num(c.src_num, c.dst_num)
            return c.full_line_index
        else:
            if c.diff_line_index is None:
                c.diff_line_index = self._find_line_by_num(c.src_num, c.dst_num)
            return c.diff_line_index

    def _is_hidden(self, i: int) -> bool:
        """Lines we never draw: the diff header, plus the lone @@ in full view.

        Files with no hunks at all (pure renames, binary changes) have nothing but
        header, so it stays visible rather than leaving a blank screen.
        """
        if self._header_len >= len(self._parsed_lines):
            return False
        if i < self._header_len:
            return True
        return self._full_view and self._parsed_lines[i].kind == "hunk"

    def _nav_positions(self) -> list[int]:
        """Review-cursor stops in order: -1 is the filename in the title bar (a
        whole-file comment), then every visible diff line."""
        return [-1] + [i for i in range(len(self._parsed_lines)) if not self._is_hidden(i)]

    def _move_review(self, delta: int) -> None:
        stops = self._nav_positions()
        k = min(bisect_left(stops, self._review_line), len(stops) - 1)
        self._review_line = stops[max(0, min(len(stops) - 1, k + delta))]

    def _update_title_file(self) -> None:
        if self._on_summary_page():
            return
        t = Text()
        if self._get_comment_for_line(-1):
            t.append("● ", style=Style(color="yellow", bold=True))
        selected = self._review_mode and self._review_line == -1
        t.append(self.files[self._index][0], style=Style(bold=True, reverse=selected))
        self.query_one("#title-file", Label).update(t)

    def _comment_indices_for_file(self) -> set[int]:
        indices = set()
        for c in self._comments:
            if c.file_index != self._index or c.file_level:
                continue
            idx = self._resolve_line_index(c)
            if idx >= 0:
                indices.add(idx)
        return indices

    def _get_comment_for_line(self, line_idx: int) -> Comment | None:
        """Comment on a diff line, or the whole-file comment when line_idx is -1."""
        for c in self._comments:
            if c.file_index != self._index:
                continue
            if line_idx == -1:
                if c.file_level:
                    return c
            elif not c.file_level and self._resolve_line_index(c) == line_idx:
                return c
        return None

    # ── Blame gutter ───────────────────────────────────────────────────────

    def _new_path(self, index: int) -> str:
        """The file's path on the new side (differs from the listed name for renames)."""
        filename, content = self.files[index]
        header = ANSI_ESCAPE.sub("", content.splitlines()[0]) if content else ""
        match = re.match(r"diff --git a/(.*?) b/(.*)", header)
        return match.group(2) if match else filename

    def _blame_data(self, rev: str | None, path: str) -> dict[int, tuple[str, str]]:
        key = (rev, path)
        if key not in self._blame_cache:
            self._blame_cache[key] = get_blame(path, rev)
        return self._blame_cache[key]

    def _blame_labels(self) -> dict[int, Text] | None:
        """Gutter text per parsed line index, or None when blame is off.

        Context and added lines are blamed on the new side of the diff, removed lines on the
        old side. Uncommitted lines, and files git can't blame (untracked ones), are blank.
        """
        if not self._blame:
            return None
        if self._blame_revs is None:
            self._blame_revs = blame_revs(self.diff_args)
        old_rev, new_rev = self._blame_revs
        new_data: dict[int, tuple[str, str]] = {}
        old_data: dict[int, tuple[str, str]] = {}
        if self.files[self._index][0] not in self._untracked_files:
            new_data = self._blame_data(new_rev, self._new_path(self._index))
            old_data = self._blame_data(old_rev, self.files[self._index][0])
        blank = Text(" " * BLAME_W)
        labels: dict[int, Text] = {}
        for i, dl in enumerate(self._parsed_lines):
            if self._is_hidden(i):
                continue
            label = blank
            entry = None
            if dl.kind in ("context", "added"):
                entry = new_data.get(dl.dst_num)
            elif dl.kind == "removed":
                entry = old_data.get(dl.src_num)
            if entry and set(entry[0]) != {"0"}:     # uncommitted lines stay blank
                sha, author = entry
                label = Text()
                label.append(sha[:7], style=Style(color=_BLAME_COLOURS[int(sha[:4], 16) % len(_BLAME_COLOURS)]))
                label.append(" " + author[:9].ljust(9) + " ", style=Style(dim=True))
            labels[i] = label
        return labels

    def action_toggle_blame(self) -> None:
        if self._on_summary_page() or self._comment_mode or self._revise_mode:
            return
        diff_view = self.query_one(DiffView)
        top_line = self._line_at_row(int(diff_view.scroll_y))
        self._blame = not self._blame
        save_prefs(blame=self._blame)
        self._refresh_view()
        diff_view.scroll_to(y=self._rows_for_line(top_line)[0], animate=False)
        if self._review_mode:
            diff_view.ensure_line_visible(*self._rows_for_line(self._review_line))

    def _render_lines(self) -> Text:
        if self._split_view:
            return self._render_split()
        return self._render_unified()

    # ── Split rendering ────────────────────────────────────────────────────

    def _split_width(self) -> int:
        view = self.query_one(DiffView)
        width = view.scrollable_content_region.width or view.size.width or self.size.width
        return max(width, 20)

    @staticmethod
    def _wrap_cell(text: Text, width: int) -> list[Text]:
        """Wrap to `width` cells, each row padded to exactly `width`."""
        width = max(width, 1)
        rows = text.wrap(_WRAP_CONSOLE, width, overflow="fold") if text.cell_len else [Text("")]
        out = []
        for row in rows or [Text("")]:
            row = row.copy()
            row.pad_right(max(0, width - row.cell_len))
            out.append(row)
        return out

    def _render_split(self) -> Text:
        """A on the left (context + removed), B on the right (context + added).

        Long lines wrap inside their column; the shorter side of each row-group is
        padded with blank, unnumbered rows so both columns stay aligned.
        """
        full_width = self._split_width()
        blame = self._blame_labels()
        blame_w = self._blame_w = BLAME_W if blame is not None else 0
        blank_blame = Text(" " * blame_w)
        total = full_width - blame_w
        comment_indices = self._comment_indices_for_file()
        show_comment_col = bool(comment_indices)
        dim = Style(dim=True)
        dot_style = Style(color="green", bold=True)
        divider = Text(" │ ", style=dim)

        num_w = 5 if self._show_line_nums else 0       # "NNNN "
        dot_w = 2 if show_comment_col else 0           # "● "
        gutter_w = num_w + dot_w
        left_w = (total - divider.cell_len) // 2
        right_w = total - divider.cell_len - left_w    # takes the odd column
        full_body_w = max(total - gutter_w, 1)
        parsed = self._parsed_lines

        def gutter(num: int | None, dotted: bool) -> Text:
            g = Text()
            if num_w:
                g.append(f"{num:>4} " if num is not None else " " * num_w, style=dim)
            if dot_w:
                g.append("● " if dotted else "  ", style=dot_style if dotted else Style())
            return g

        def cell(idx: int | None, num_attr: str, col_w: int) -> list[Text]:
            """Rows for one side of a group: gutter + wrapped content, col_w wide."""
            if idx is None:
                return [Text(" " * col_w)]
            dl = parsed[idx]
            body = self._wrap_cell(self._line_text(idx), max(col_w - gutter_w, 1))
            rows = []
            for n, piece in enumerate(body):
                row = gutter(getattr(dl, num_attr) if n == 0 else None,
                             n == 0 and idx in comment_indices)
                row.append_text(piece)
                if idx == self._review_line and self._review_mode:
                    row.stylize(Style(reverse=True))
                rows.append(row)
            return rows

        result = Text()
        self._line_rows = [(0, 0)] * len(parsed)
        self._row_lines = []
        self._row_cells = []
        self._left_w = left_w
        self._rendered_width = full_width
        row_no = 0

        def emit(left: list[Text], right: list[Text], idxs: list[int],
                 cells: tuple[int | None, int | None]) -> None:
            nonlocal row_no
            n = max(len(left), len(right))
            for r in range(n):
                if blame is not None:
                    # One gutter per row: the new side's label, else the removed line's
                    label = blank_blame
                    for idx in (cells[1], cells[0]):
                        if r == 0 and idx in blame and blame[idx].plain.strip():
                            label = blame[idx]
                            break
                    result.append_text(label)
                result.append_text(left[r] if r < len(left) else Text(" " * left_w))
                result.append_text(divider)
                result.append_text(right[r] if r < len(right) else Text(" " * right_w))
                result.append("\n")
                self._row_lines.append(idxs[0])
                self._row_cells.append(cells)
            for i in idxs:
                self._line_rows[i] = (row_no, row_no + n)
            row_no += n

        i = 0
        while i < len(parsed):
            dl = parsed[i]
            if self._is_hidden(i):
                self._line_rows[i] = (row_no, row_no)
                i += 1
                continue
            if dl.kind == "context":
                emit(cell(i, "src_num", left_w), cell(i, "dst_num", right_w), [i], (i, i))
                i += 1
            elif dl.kind in ("removed", "added"):
                removed: list[int] = []
                added: list[int] = []
                while i < len(parsed) and parsed[i].kind == "removed":
                    removed.append(i)
                    i += 1
                while i < len(parsed) and parsed[i].kind == "added":
                    added.append(i)
                    i += 1
                for k in range(max(len(removed), len(added))):
                    li = removed[k] if k < len(removed) else None
                    ri = added[k] if k < len(added) else None
                    idxs = [x for x in (li, ri) if x is not None]
                    emit(cell(li, "src_num", left_w), cell(ri, "dst_num", right_w), idxs, (li, ri))
            else:
                # Headers, hunk markers and anything else span both columns
                body = self._wrap_cell(self._line_text(i), full_body_w)
                rows = []
                for n, piece in enumerate(body):
                    row = gutter(None, n == 0 and i in comment_indices)
                    row.append_text(piece)
                    if i == self._review_line and self._review_mode:
                        row.stylize(Style(reverse=True))
                    rows.append(row)
                for row in rows:
                    result.append_text(blank_blame)
                    result.append_text(row)
                    result.append("\n")
                    self._row_lines.append(i)
                    self._row_cells.append((i, i))
                self._line_rows[i] = (row_no, row_no + len(rows))
                row_no += len(rows)
                i += 1
        return result

    def _line_text(self, i: int) -> Text:
        """The styled text for a parsed line: git's colours, or syntax-highlighted."""
        dl = self._parsed_lines[i]
        if self._highlight and self._full_view and dl.kind in ("context", "added", "removed"):
            cache = self._hl_cache
            if cache is None or cache[0] is not self._parsed_lines:
                cache = (self._parsed_lines,
                         highlight_lines(self.files[self._index][0], self._parsed_lines))
                self._hl_cache = cache
            body = cache[1].get(i)
            if body is not None:
                marker_style = {"added": "bold green", "removed": "bold red"}.get(dl.kind, "")
                return Text(dl.plain[0], style=marker_style) + body
        return Text.from_ansi(dl.text)

    def _rows_for_line(self, line_idx: int) -> tuple[int, int]:
        if line_idx == -1:          # the filename in the title bar: top of the view
            return (0, 1)
        if 0 <= line_idx < len(self._line_rows):
            return self._line_rows[line_idx]
        return (line_idx, line_idx + 1)

    def _line_at_row(self, row: int) -> int:
        if self._row_lines:
            return self._row_lines[max(0, min(row, len(self._row_lines) - 1))]
        return row

    def _render_unified(self) -> Text:
        """One column, with long lines wrapped here (not by Textual) so we know which
        screen row belongs to which line."""
        result = Text()
        comment_indices = self._comment_indices_for_file()
        # Show comment column whenever this file has any comments
        show_comment_col = bool(comment_indices)
        dim = Style(dim=True)
        dot_style = Style(color="green", bold=True)
        total = self._rendered_width = self._split_width()
        blame = self._blame_labels()
        self._blame_w = BLAME_W if blame is not None else 0
        self._line_rows = [(0, 0)] * len(self._parsed_lines)
        self._row_lines = []
        self._row_cells = []

        def prefix(i: int, dl: DiffLine, dotted: bool, first: bool) -> Text:
            p = Text()
            if blame is not None:
                p.append_text(blame.get(i, Text(" " * BLAME_W)) if first else Text(" " * BLAME_W))
            if self._show_line_nums:
                # Source and diff line number columns (blank on wrapped continuation rows)
                src = str(dl.src_num) if dl.src_num is not None else ""
                dst = str(dl.dst_num) if dl.dst_num is not None else ""
                p.append(f"{src:>4} {dst:>4} " if first else " " * 10, style=dim)
            if show_comment_col:
                p.append("● " if dotted and first else "  ",
                         style=dot_style if dotted and first else Style())
            if self._show_line_nums or show_comment_col or blame is not None:
                p.append("│ ", style=dim)
            return p

        for i, dl in enumerate(self._parsed_lines):
            if self._is_hidden(i):
                self._line_rows[i] = (len(self._row_lines), len(self._row_lines))
                continue
            first = prefix(i, dl, i in comment_indices, True)
            pieces = self._wrap_cell(self._line_text(i), max(total - first.cell_len, 1))
            selected = self._review_mode and i == self._review_line
            start = len(self._row_lines)
            for k, piece in enumerate(pieces):
                row = first.copy() if k == 0 else prefix(i, dl, False, False)
                row.append_text(piece)
                # Reverse-video selection highlight across the whole line
                if selected:
                    row.stylize(Style(reverse=True))
                result.append_text(row)
                result.append("\n")
            self._line_rows[i] = (start, start + len(pieces))
            self._row_lines.extend([i] * len(pieces))

        return result

    def _check_render_width(self) -> None:
        """A scrollbar appearing after we laid out narrows the view; redo once at the true width."""
        if self._on_summary_page() or self._revise_mode or not self._parsed_lines:
            return
        if self._split_width() != self._rendered_width:
            self._refresh_view(_retry=True)

    def _refresh_view(self, scroll_home: bool = False, _retry: bool = False) -> None:
        diff_view = self.query_one(DiffView)
        text = self._render_lines()
        if scroll_home:
            diff_view.show_content(text)
        else:
            diff_view.update_content(text)
            if self._review_mode:
                diff_view.ensure_line_visible(*self._rows_for_line(self._review_line))
        self._update_title_file()
        if not _retry:
            self.call_after_refresh(self._check_render_width)

    def _update_title_style(self) -> None:
        title = self.query_one("#title", Horizontal)
        title.remove_class("review", "comment-mode", "revise-mode")
        if self._revise_mode:
            title.add_class("revise-mode")
        elif self._comment_mode:
            title.add_class("comment-mode")
        elif self._review_mode:
            title.add_class("review")

        # The shortcut hints (Reload, Split, ...) don't work while typing in a text box
        self.query_one("#footer-keys", Label).display = not (self._comment_mode or self._revise_mode)

        status = self.query_one("#footer-status", Label)
        if self._revise_mode:
            status.update("Editing summary... (Ctrl+S / Esc save · Ctrl+C discard)")
        elif self._comment_mode:
            status.update("Commenting... (Enter save · Shift+Enter newline · Ctrl+P prompt Claude · Ctrl+C discard)")
        elif self._sidebar_mode:
            status.update("Navigating files... (↑↓ change file · Enter/Tab/→ back to diff)")
        elif self._review_mode:
            status.update("Reviewing... (Enter comment · ←→ files · Esc exit)")
        else:
            status.update("")
        self._update_sidebar()

    # ── Sidebar ────────────────────────────────────────────────────────────

    def _sidebar_entries(self) -> list[str]:
        entries = [name for name, _ in self.files]
        if self._comments or self._on_summary_page():
            entries.append("Review Summary")
        return entries

    def _update_sidebar(self) -> None:
        sidebar = self.query_one(Sidebar)
        sidebar.display = self._sidebar_visible
        if not self._sidebar_visible:
            return
        width = sidebar.scrollable_content_region.width or 33
        commented = {c.file_index for c in self._comments}
        entries = self._sidebar_entries()
        text = Text(no_wrap=True)
        for i, name in enumerate(entries):
            current = i == self._index
            summary = i == len(self.files)
            row = Text(no_wrap=True)
            row.append("● " if i in commented else "  ",
                       style=Style(color="yellow", bold=True))
            room = max(width - 2, 1)
            label = name if len(name) <= room else "…" + name[-(room - 1):]
            if summary:
                row.append(label, style=Style(italic=True))
            else:
                cut = label.rfind("/") + 1
                row.append(label[:cut], style=Style(dim=True))
                row.append(label[cut:])
            row.pad_right(max(0, width - row.cell_len))
            if current:
                row.stylize(Style(bold=True, reverse=self._sidebar_mode,
                                  color=None if self._sidebar_mode else "cyan"))
            text.append_text(row)
            if i < len(entries) - 1:
                text.append("\n")
        sidebar.set_content(text, min(self._index, len(entries) - 1))

    def _goto(self, index: int) -> None:
        """Show file `index`, or the summary page for index == len(files)."""
        if index == len(self.files):
            self._index = index
            self._show_summary_page()
        else:
            self._show_file(index)

    def _set_sidebar_mode(self, on: bool) -> None:
        self._sidebar_mode = on
        self._update_title_style()
        if self._review_mode:
            self._refresh_view()

    def action_toggle_sidebar(self) -> None:
        if self._comment_mode or self._revise_mode:
            return
        self._sidebar_visible = not self._sidebar_visible
        save_prefs(sidebar=self._sidebar_visible)
        if not self._sidebar_visible:
            self._sidebar_mode = False
        self._update_title_style()
        # The diff changed width, so it needs laying out again
        self.call_after_refresh(self._check_render_width)

    def action_left(self) -> None:
        """Into the sidebar if it's open, otherwise the previous file."""
        if self._comment_mode or self._revise_mode or self._sidebar_mode:
            return
        if self._sidebar_visible:
            self._set_sidebar_mode(True)
        elif self._on_summary_page():
            self._goto(len(self.files) - 1)
        elif self._index > 0:
            self._goto(self._index - 1)

    def action_right(self) -> None:
        """Out of the sidebar if we're in it, otherwise the next file."""
        if self._comment_mode or self._revise_mode:
            return
        if self._sidebar_mode:
            self._set_sidebar_mode(False)
        elif self._on_summary_page():
            return
        elif self._index < len(self.files) - 1:
            self._goto(self._index + 1)
        elif self._comments:
            self._goto(len(self.files))     # past the last file: the summary

    def _sidebar_move(self, delta: int) -> None:
        last = len(self._sidebar_entries()) - 1
        target = max(0, min(last, self._index + delta))
        if target != self._index:
            self._goto(target)

    def on_sidebar_clicked(self, event: Sidebar.Clicked) -> None:
        if self._comment_mode or self._revise_mode:
            return
        if 0 <= event.row < len(self._sidebar_entries()):
            self._sidebar_mode = True
            self._goto(event.row)

    def _build_summary_text(self) -> str:
        if self._summary_text_override is not None:
            return self._summary_text_override
        parts = ["Review Summary\n==============\n"]
        for c in self._comments:
            if c.file_level:
                parts.append(f"\n--- {c.filename} (full file)\n---\n\n")
            else:
                line_ref = c.src_num or c.dst_num or c.diff_line_index or c.full_line_index
                parts.append(f"\n--- {c.filename} (line ~{line_ref})\n")
                for ctx in c.context_before:
                    parts.append(f"    {ctx}\n")
                parts.append("---\n\n")
            parts.append(c.text)
            parts.append("\n\n")
        return "".join(parts)

    def _on_summary_page(self) -> bool:
        return self._index == len(self.files)

    # ── File display ───────────────────────────────────────────────────────

    def _show_file(self, index: int) -> None:
        self._index = index
        self._full_view = False
        self._review_mode = False
        self._comment_mode = False
        self._revise_mode = False
        self._review_line = 0
        self.query_one("#summary-input", SummaryArea).display = False
        self.query_one(DiffView).display = True
        filename, content = self.files[index]
        self._parsed_lines = parse_lines(content)
        self._header_len = compute_header_len(self._parsed_lines)
        if self._full_pref:
            self._load_full_view()
        n = len(self.files)
        self._update_file_info()
        self._update_title_style()
        self.query_one("#summary-actions", Horizontal).display = False
        self._refresh_view(scroll_home=True)

    def _update_file_info(self) -> None:
        n = len(self.files)
        suffix = "  [full]" if self._full_view else ""
        self.query_one("#title-info", Label).update(
            f"({self.branch})  [{self._index + 1}/{n}]{suffix}"
        )

    def _show_summary_page(self) -> None:
        self._review_mode = False
        self._comment_mode = False
        self._update_title_style()
        self.query_one("#title-file", Label).update("[bold]Review Summary[/bold]")
        self.query_one("#title-info", Label).update(f"({self.branch})  [summary]")
        self.query_one("#summary-actions", Horizontal).display = True
        text = Text(self._build_summary_text())
        self.query_one(DiffView).show_content(text)

    # ── Review / comment mode ──────────────────────────────────────────────

    def _exit_review_mode(self) -> None:
        self._review_mode = False
        self._update_title_style()
        self._refresh_view()

    def _enter_comment_mode(self) -> None:
        self._comment_mode = True
        self._update_title_style()

        dl = self._parsed_lines[self._review_line] if self._parsed_lines and self._review_line >= 0 else None
        filename = self.files[self._index][0]
        label = self.query_one("#comment-label", Label)
        if self._review_line < 0:
            label.update(f"Comment for {filename} (whole file)")
        else:
            line_ref = (dl.src_num or dl.dst_num or self._review_line) if dl else self._review_line
            label.update(f"Comment for {filename} line ~{line_ref}")
        label.display = True

        existing = self._get_comment_for_line(self._review_line)
        ta = self.query_one("#comment-input", CommentArea)
        ta.display = True
        ta.load_text(existing.text if existing else "")
        ta.focus()

    def _exit_comment_mode(self, save: bool) -> None:
        ta = self.query_one("#comment-input", CommentArea)
        if save:
            text = ta.text.strip()
            existing = self._get_comment_for_line(self._review_line)
            if existing:
                self._comments.remove(existing)
            if text:
                dl = self._parsed_lines[self._review_line] if self._parsed_lines and self._review_line >= 0 else None
                lines = self._parsed_lines
                idx = self._review_line
                file_level = idx < 0    # the filename in the title bar
                if file_level:
                    before, after = [], []
                    src_num = dst_num = None
                else:
                    before = [lines[i].plain for i in range(max(0, idx - 3), idx + 1)]
                    after = [lines[i].plain for i in range(idx + 1, min(len(lines), idx + 4))]
                    src_num = dl.src_num if dl else None
                    dst_num = dl.dst_num if dl else None
                self._comments.append(Comment(
                    file_index=self._index,
                    filename=self.files[self._index][0],
                    diff_line_index=None if file_level or self._full_view else idx,
                    full_line_index=None if file_level or not self._full_view else idx,
                    src_num=src_num,
                    dst_num=dst_num,
                    context_before=before,
                    context_after=after,
                    text=text,
                    file_level=file_level,
                ))

        ta.display = False
        ta.load_text("")
        self.query_one("#comment-label", Label).display = False
        self._comment_mode = False
        self._update_title_style()
        self.query_one(DiffView).focus()
        self._refresh_view()

    def _enter_revise_mode(self) -> None:
        self._revise_mode = True
        sa = self.query_one("#summary-input", SummaryArea)
        self.query_one(DiffView).display = False
        sa.load_text(self._build_summary_text())
        sa.display = True
        sa.focus()
        self._update_title_style()

    def _exit_revise_mode(self, save: bool) -> None:
        sa = self.query_one("#summary-input", SummaryArea)
        if save:
            self._summary_text_override = sa.text
        sa.display = False
        sa.load_text("")
        self._revise_mode = False
        self.query_one(DiffView).display = True
        self._show_summary_page()

    def on_comment_area_submit(self, event: CommentArea.Submit) -> None:
        if self._comment_mode:
            self._exit_comment_mode(save=True)

    def on_comment_area_discard(self, event: CommentArea.Discard) -> None:
        if self._comment_mode:
            self._exit_comment_mode(save=False)

    def on_comment_area_ask(self, event: CommentArea.Ask) -> None:
        if not self._comment_mode:
            return
        question = self.query_one("#comment-input", CommentArea).text.strip()
        if not question:
            self.query_one("#footer-status", Label).update("Type a prompt first, then Ctrl+P")
            return
        prompt = self._build_ask_prompt(question)
        self._exit_comment_mode(save=False)   # a prompt isn't saved as a comment
        self._run_claude_session(prompt)

    def _build_ask_prompt(self, question: str) -> str:
        """A prompt for Claude: the request, plus the file and line it's about."""
        filename = self.files[self._index][0]
        cmd = shlex.join(["git", "diff", *self.diff_args])
        parts = [
            "I'm reviewing a diff in git-review and have a request about a specific spot in it.",
            "Do not use the git-review skill: that loads a saved review from disk, and this "
            "request is not one. Everything you need is in this message.",
            "",
            f"Diff being reviewed: `{cmd}` (\"old\" is the side it compares from, \"new\" the side "
            "it compares to; untracked files are included as entirely new).",
            f"File: {filename}" + (" (untracked, so entirely new)" if filename in self._untracked_files else ""),
        ]
        idx = self._review_line
        if idx < 0 or idx >= len(self._parsed_lines):
            parts.append("Scope: the whole file.")
        else:
            dl = self._parsed_lines[idx]
            if dl.src_num is not None and dl.dst_num is not None:
                where = f"line {dl.dst_num} in the new version (line {dl.src_num} in the old version)"
            elif dl.dst_num is not None:
                where = f"line {dl.dst_num} in the new version (an added line)"
            elif dl.src_num is not None:
                where = f"line {dl.src_num} in the old version (a removed line)"
            else:
                where = "this part of the diff"
            parts.append(f"Location: {where}")
            parts.append("")
            parts.append("Diff around it (the line marked > is the one I mean):")
            parts.append("```diff")
            for j in range(max(0, idx - 3), min(len(self._parsed_lines), idx + 4)):
                if not self._is_hidden(j):
                    parts.append(f"{'>' if j == idx else ' '} {self._parsed_lines[j].plain}")
            parts.append("```")
        parts += ["", "My request:", question]
        return "\n".join(parts)

    def _run_claude_session(self, prompt: str) -> None:
        """Hand the terminal to an interactive claude session, then reload the diff."""
        claude = os.environ.get("CLAUDE", "claude")
        status = self.query_one("#footer-status", Label)
        try:
            with self.suspend():
                subprocess.run([claude, prompt])
        except SuspendNotSupported:
            status.update("This terminal can't suspend git-review to run Claude")
            return
        except FileNotFoundError:
            status.update(f"Couldn't run '{claude}' (set $CLAUDE to its path)")
            return
        # Claude may have edited files, so pick up whatever changed
        self.action_reload()

    def on_summary_area_submit(self, event: SummaryArea.Submit) -> None:
        if self._revise_mode:
            self._exit_revise_mode(save=True)

    def on_summary_area_discard(self, event: SummaryArea.Discard) -> None:
        if self._revise_mode:
            self._exit_revise_mode(save=False)

    # ── Event handlers ─────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        # In comment mode CommentArea handles save/discard via its own _on_key.
        # We still need to stop App-level bindings (w, c, r, q, etc.) from firing
        # while the user is typing a comment.
        if self._comment_mode or self._revise_mode:
            event.stop()
            return

        # Space in review mode = enter comment
        if event.key == "space" and self._review_mode and not self._on_summary_page():
            self._enter_comment_mode()
            event.stop()
            return

        # w on summary page = write and quit
        if event.key == "w" and self._on_summary_page():
            self.action_write_review()
            event.stop()
            return

        # f on summary page = write, quit, then forward to claude
        if event.key == "f" and self._on_summary_page():
            self.action_forward_to_claude()
            event.stop()
            return

        # w in normal/review mode = save current comments (no quit)
        if event.key == "w" and not self._on_summary_page():
            if self._comments or self._summary_text_override is not None:
                dest = Path.home() / ".git-review"
                text = self._build_summary_text()
                current_file = self.files[self._index][0]
                text = text.rstrip("\n") + f"\n\n# (reviewed to {current_file})\n"
                dest.write_text(text)
                self.query_one("#footer-status", Label).update("Saved to ~/.git-review")
            else:
                self.query_one("#footer-status", Label).update("No comments to write")
            event.stop()
            return

        # Escape outside comment mode
        if event.key == "escape":
            if self._sidebar_mode:
                self._set_sidebar_mode(False)
                event.stop()
                return
            if self._review_mode:
                self._exit_review_mode()
                event.stop()
                return
            if self.query_one("#jump", Input).display:
                self.query_one("#jump", Input).display = False
                self.query_one(DiffView).focus()
                event.stop()
                return
            self.exit()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        jump = self.query_one("#jump", Input)
        jump.display = False
        self.query_one(DiffView).focus()
        try:
            n = int(event.value)
            if 1 <= n <= len(self.files):
                self._show_file(n - 1)
        except ValueError:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-write":
            self.action_write_review()
        elif event.button.id == "btn-copy":
            self.action_copy_review()
        elif event.button.id == "btn-claude":
            self.action_forward_to_claude()

    # ── Actions ────────────────────────────────────────────────────────────

    def _load_full_view(self) -> None:
        """Replace the current file's parsed lines with its full-context diff."""
        filename, content = self.files[self._index]
        if filename in self._untracked_files:
            # Untracked files are diffed against /dev/null, so the diff view already
            # contains the whole file — there's no extra context to expand.
            self._full_view = True
            return
        base_args = []
        for arg in self.diff_args:
            if arg == "--":
                break
            base_args.append(arg)
        header_line = content.splitlines()[0] if content else ""
        match = re.match(r"diff --git a/(.*?) b/(.*)", ANSI_ESCAPE.sub("", header_line))
        if match and match.group(1) != match.group(2):
            # Renamed file: pass both paths, otherwise git's pathspec pruning
            # sees only one side and reports a plain delete/add instead of a rename.
            pathspecs = [match.group(1), match.group(2)]
        else:
            pathspecs = [filename]
        result = subprocess.run(
            ["git", "diff", "--color=always", "-U99999", *base_args, "--", *pathspecs],
            capture_output=True, text=True,
        )
        if not result.stdout:
            return  # nothing to expand; stay on the normal diff for this file
        self._full_view = True
        self._parsed_lines = parse_lines(result.stdout)
        self._header_len = compute_header_len(self._parsed_lines)

    def action_full_file(self) -> None:
        if self._on_summary_page():
            return
        if self._full_pref or self._full_view:
            # Highlighting only makes sense with full context, so it goes off too
            self._full_pref = False
            self._highlight = False
            save_prefs(full=False, highlight=False)
            self._show_file(self._index)
            return
        self._enable_full_view()

    def _enable_full_view(self) -> None:
        self._full_pref = True
        save_prefs(full=True)
        self._load_full_view()
        self._update_file_info()
        self._refresh_view(scroll_home=True)

    def action_reload(self) -> None:
        """Re-run the diff and return to the same file and line."""
        if self._comment_mode or self._revise_mode:
            return
        status = self.query_one("#footer-status", Label)
        _, files, untracked = load_diff(self.diff_args)
        if not files:
            status.update("No diff after reload — keeping previous view")
            return

        # Remember where we were, by file name and line number rather than index
        on_summary = self._on_summary_page()
        old_index = self._index
        was_review = self._review_mode
        was_title = was_review and self._review_line == -1
        anchor_name = anchor_nums = None
        scroll = offset = 0
        if not on_summary:
            view = self.query_one(DiffView)
            anchor_name = self.files[self._index][0]
            scroll = int(view.scroll_y)
            anchor = self._review_line if was_review else self._line_at_row(scroll)
            if 0 <= anchor < len(self._parsed_lines):
                dl = self._parsed_lines[anchor]
                anchor_nums = (dl.src_num, dl.dst_num)
                offset = max(0, self._rows_for_line(anchor)[0] - scroll)

        # Comments follow their file by name. Their line numbers are re-resolved against
        # the new diff; if the code moved under them, the frozen context in the summary
        # is what Claude has to go on.
        new_index = {name: i for i, (name, _) in enumerate(files)}
        for c in self._comments:
            c.file_index = new_index.get(c.filename, -1)
            if c.src_num is not None or c.dst_num is not None:
                c.diff_line_index = None
                c.full_line_index = None

        self.files = files
        self._untracked_files = set(untracked)
        self._blame_cache.clear()

        if on_summary:
            self._index = len(self.files)
            self._show_summary_page()
            status.update("Reloaded")
            return

        idx = new_index.get(anchor_name)
        gone = idx is None
        self._show_file(min(old_index, len(files) - 1) if gone else idx)

        pos = -1
        if anchor_nums and anchor_nums != (None, None):
            pos = self._find_line_by_num(*anchor_nums)
        if was_review and not gone:
            self._review_line = -1 if was_title else (pos if pos >= 0 else self._line_at_row(scroll))
            self._review_mode = True
            self._update_title_style()
            self._refresh_view()

        def restore() -> None:
            view = self.query_one(DiffView)
            if gone:
                return
            row = self._rows_for_line(pos)[0] - offset if pos >= 0 else scroll
            view.scroll_to(y=max(0, row), animate=False)

        # Scrolling has to wait for the new content to be laid out
        self.call_after_refresh(restore)
        status.update(f"{anchor_name} is no longer in the diff" if gone else "Reloaded")

    def action_toggle_line_nums(self) -> None:
        self._show_line_nums = not self._show_line_nums
        save_prefs(line_nums=self._show_line_nums)
        self._refresh_view()

    def action_toggle_highlight(self) -> None:
        if self._on_summary_page() or self._comment_mode or self._revise_mode:
            return
        self._highlight = not self._highlight
        save_prefs(highlight=self._highlight)
        if self._highlight and not self._full_pref:
            # Hunks alone give false colours (multi-line constructs start off-screen),
            # so highlighting needs full-file view
            self._enable_full_view()
        else:
            self._refresh_view()

    def action_toggle_split(self) -> None:
        if self._on_summary_page() or self._comment_mode or self._revise_mode:
            return
        diff_view = self.query_one(DiffView)
        # Keep the same line at the top of the viewport across the switch
        top_line = self._line_at_row(int(diff_view.scroll_y))
        self._split_view = not self._split_view
        save_prefs(split=self._split_view)
        self._refresh_view()
        diff_view.scroll_to(y=self._rows_for_line(top_line)[0], animate=False)
        if self._review_mode:
            diff_view.ensure_line_visible(*self._rows_for_line(self._review_line))

    def on_diff_view_clicked(self, event: DiffView.Clicked) -> None:
        """Click a line to select it (entering review mode if needed)."""
        if self._sidebar_mode:
            self._set_sidebar_mode(False)
        if (self._comment_mode or self._revise_mode or self._on_summary_page()
                or not self._parsed_lines or not 0 <= event.row < len(self._row_lines)):
            return
        if self._split_view and event.row < len(self._row_cells):
            left, right = self._row_cells[event.row]
            idx = (left if event.col - self._blame_w < self._left_w + 1 else right)
            if idx is None:     # blank half of an unbalanced pair: take the other side
                idx = right if left is None else left
        else:
            idx = self._row_lines[event.row]
        if idx is None:
            return
        self._review_line = idx
        self._review_mode = True
        self._update_title_style()
        self._refresh_view()

    def on_resize(self, event: events.Resize) -> None:
        # Split columns are sized to the terminal width, so redraw on resize
        if self._split_view and self._parsed_lines:
            self.call_after_refresh(self._redraw_split)

    def _redraw_split(self) -> None:
        if self._split_view and not self._on_summary_page() and not self._revise_mode:
            self._refresh_view()

    def action_toggle_review(self) -> None:
        if self._on_summary_page():
            return
        if self._comment_mode:
            self._exit_comment_mode(save=False)
            self._exit_review_mode()
            return
        if self._review_mode:
            self._exit_review_mode()
            return
        # Start at the first currently-visible line
        stops = self._nav_positions()
        first_visible = self._line_at_row(int(self.query_one(DiffView).scroll_y))
        if first_visible not in stops:
            first_visible = stops[1] if len(stops) > 1 else -1
        self._review_line = first_visible
        self._review_mode = True
        self._update_title_style()
        self._refresh_view()

    def action_review_up(self) -> None:
        if self._comment_mode:
            return
        if self._sidebar_mode:
            self._sidebar_move(-1)
            return
        if self._review_mode and self._parsed_lines:
            self._move_review(-1)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_up()

    def action_review_down(self) -> None:
        if self._comment_mode:
            return
        if self._sidebar_mode:
            self._sidebar_move(1)
            return
        if self._review_mode and self._parsed_lines:
            self._move_review(1)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_down()

    def action_review_pgup(self) -> None:
        if self._comment_mode:
            return
        if self._review_mode and self._parsed_lines:
            height = self.query_one(DiffView).size.height
            self._move_review(-height)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_page_up()

    def action_review_pgdn(self) -> None:
        if self._comment_mode:
            return
        if self._review_mode and self._parsed_lines:
            height = self.query_one(DiffView).size.height
            self._move_review(height)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_page_down()

    def action_viewport_up(self) -> None:
        if self._comment_mode:
            return
        diff_view = self.query_one(DiffView)
        new_scroll = max(0, int(diff_view.scroll_y) - 1)
        diff_view.scroll_to(y=new_scroll, animate=False)
        if self._review_mode and self._parsed_lines:
            height = diff_view.size.height
            max_visible = new_scroll + height - 1
            if self._rows_for_line(self._review_line)[0] > max_visible:
                self._review_line = self._line_at_row(max_visible)
            self._refresh_view()

    def action_viewport_down(self) -> None:
        if self._comment_mode:
            return
        diff_view = self.query_one(DiffView)
        new_scroll = int(diff_view.scroll_y) + 1
        diff_view.scroll_to(y=new_scroll, animate=False)
        if self._review_mode and self._parsed_lines:
            if self._rows_for_line(self._review_line)[0] < new_scroll:
                self._review_line = self._line_at_row(new_scroll)
            self._refresh_view()

    def action_enter_comment(self) -> None:
        if self._comment_mode or self._revise_mode:
            return
        if self._sidebar_mode:
            self._set_sidebar_mode(False)
            return
        if self._on_summary_page():
            self._enter_revise_mode()
            return
        if not self._review_mode:
            self.action_toggle_review()
            return
        self._enter_comment_mode()

    def action_jump(self) -> None:
        jump = self.query_one("#jump", Input)
        jump.display = True
        jump.value = ""
        jump.focus()

    def action_write_review(self) -> None:
        if not self._comments and self._summary_text_override is None:
            return
        dest = Path.home() / ".git-review"
        dest.write_text(self._build_summary_text())
        self.exit()

    def action_forward_to_claude(self) -> None:
        if not self._comments and self._summary_text_override is None:
            return
        dest = Path.home() / ".git-review"
        dest.write_text(self._build_summary_text())
        self._run_claude = True
        self.exit()

    def _copy_text(self, text: str) -> None:
        # pyperclip covers the local clipboard; OSC 52 covers remote/ssh terminals
        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException:
            pass
        self.copy_to_clipboard(text)
        self.query_one("#footer-status", Label).update("Copied to clipboard")

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Copy to the clipboard whenever text is selected with the mouse."""
        if self._comment_mode or self._revise_mode:
            return
        selected = self.screen.get_selected_text()
        if selected:
            self._copy_text(selected)

    def action_copy_review(self) -> None:
        if self._comment_mode or self._revise_mode:
            return
        if self._comments or self._summary_text_override is not None:
            self._copy_text(self._build_summary_text())
            return
        # No review yet: copy the current line in review mode, else any selection
        if self._review_mode and self._parsed_lines and not self._on_summary_page():
            if self._review_line < 0:
                self._copy_text(self.files[self._index][0])
            else:
                self._copy_text(self._parsed_lines[self._review_line].plain)
            return
        selected = self.screen.get_selected_text()
        if selected:
            self._copy_text(selected)
        else:
            self.query_one("#footer-status", Label).update("Nothing to copy")

    def action_quit(self) -> None:
        self.exit()


def load_diff(diff_args: list[str]) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Run the diff (plus untracked files) -> (combined output, per-file sections, untracked paths)."""
    diff_output = get_diff(diff_args)
    untracked_paths = get_untracked_files(diff_args)
    untracked_diff = "".join(get_untracked_diff(path) for path in untracked_paths)
    combined_output = diff_output + untracked_diff
    # Untracked files are appended to the diff, so sort to interleave them alphabetically
    files = sorted(parse_diff(combined_output), key=lambda f: (f[0].lower(), f[0]))
    return combined_output, files, untracked_paths


def main() -> None:
    branch = get_branch()
    diff_args = sys.argv[1:]
    combined_output, files, untracked_paths = load_diff(diff_args)
    if not combined_output.strip():
        print("No diff output.")
        sys.exit(0)

    if not files:
        print("No files found in diff.")
        sys.exit(0)

    app = DiffPager(files, branch, diff_args, untracked_files=set(untracked_paths))
    app.run()
    if app._run_claude:
        claude = os.environ.get("CLAUDE", "claude")
        os.execvp(claude, [claude, "/git-review"])


if __name__ == "__main__":
    main()
