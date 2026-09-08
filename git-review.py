#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "textual>=0.50.0",
#   "pyperclip>=1.8",
# ]
# ///

__version__ = "0.1.4"

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pyperclip
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Input, Label, Static, TextArea


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


class CommentArea(TextArea):
    """TextArea for comment input. Enter/ctrl+s/escape submit; shift+enter inserts newline."""

    BINDINGS = [
        # ctrl+j is the byte shift+enter sends in most terminals (\n, ASCII 10)
        Binding("ctrl+j", "insert_newline", priority=True, show=False),
        Binding("enter", "submit_comment", priority=True, show=False),
        Binding("ctrl+s", "submit_comment", priority=True, show=False),
        Binding("escape", "submit_comment", priority=True, show=False),
        Binding("ctrl+c", "discard_comment", priority=True, show=False),
    ]

    class Submit(Message):
        pass

    class Discard(Message):
        pass

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_submit_comment(self) -> None:
        self.post_message(self.Submit())

    def action_discard_comment(self) -> None:
        self.post_message(self.Discard())


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

    def compose(self) -> ComposeResult:
        yield Static("", id="diff-content", expand=True)

    def show_content(self, text: Text) -> None:
        self.query_one("#diff-content", Static).update(text)
        self.scroll_home(animate=False)

    def update_content(self, text: Text) -> None:
        self.query_one("#diff-content", Static).update(text)

    def ensure_line_visible(self, line_index: int) -> None:
        height = self.size.height
        cur = int(self.scroll_y)
        if line_index < cur:
            self.scroll_to(y=line_index, animate=False)
        elif line_index >= cur + height:
            self.scroll_to(y=line_index - height + 1, animate=False)


class DiffPager(App):
    MOUSE_SUPPORT = False
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
    DiffView {
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
        Binding("left", "prev_file", "Prev File"),
        Binding("right", "next_file", "Next File"),
        Binding("f", "full_file", "Full File"),
        Binding("l", "toggle_line_nums", "Line #s", show=False),
        Binding("r", "toggle_review", "Review", show=False),
        Binding("R", "toggle_review", "Review", show=False),
        Binding("up", "review_up", "Up", priority=True, show=False),
        Binding("down", "review_down", "Down", priority=True, show=False),
        Binding("ctrl+up", "viewport_up", "Scroll Up", priority=True, show=False),
        Binding("ctrl+down", "viewport_down", "Scroll Down", priority=True, show=False),
        Binding("pageup", "review_pgup", "PgUp", priority=True, show=False),
        Binding("pagedown", "review_pgdn", "PgDn", priority=True, show=False),
        Binding("tab", "enter_comment", "Comment", priority=True, show=False),
        Binding("enter", "enter_comment", "Comment", show=False),
        Binding("c", "copy_review", "Copy", show=False),
        Binding("colon", "jump", "Jump to file", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, files: list[tuple[str, str]], branch: str, diff_args: list[str]):
        super().__init__()
        self.files = files
        self.branch = branch
        self.diff_args = diff_args
        self._index = 0
        self._full_view = False
        self._review_mode = False
        self._review_line = 0
        self._comment_mode = False
        self._revise_mode = False
        self._summary_text_override: str | None = None
        self._show_line_nums = False
        self._comments: list[Comment] = []
        self._parsed_lines: list[DiffLine] = []
        self._run_claude = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="title"):
            yield Label("", id="title-file")
            yield Label("", id="title-info")
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
            yield Label("[bold yellow]R[/bold yellow]eview  [bold yellow]L[/bold yellow]ines  [bold yellow]F[/bold yellow]ull  [bold yellow]W[/bold yellow]rite  [bold yellow]Q[/bold yellow]uit", id="footer-keys")

    def on_mount(self) -> None:
        self.query_one(DiffView).focus()
        self._show_file(0)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _find_line_by_num(self, src_num: int | None, dst_num: int | None) -> int:
        """Find the index in current _parsed_lines matching src_num or dst_num. Returns -1 if absent."""
        for i, dl in enumerate(self._parsed_lines):
            if src_num is not None and dl.src_num == src_num:
                return i
            if dst_num is not None and dl.dst_num == dst_num:
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

    def _comment_indices_for_file(self) -> set[int]:
        indices = set()
        for c in self._comments:
            if c.file_index != self._index:
                continue
            idx = self._resolve_line_index(c)
            if idx >= 0:
                indices.add(idx)
        return indices

    def _get_comment_for_line(self, line_idx: int) -> Comment | None:
        for c in self._comments:
            if c.file_index == self._index and self._resolve_line_index(c) == line_idx:
                return c
        return None

    def _render_lines(self) -> Text:
        result = Text()
        comment_indices = self._comment_indices_for_file()
        # Show comment column whenever this file has any comments
        show_comment_col = bool(comment_indices)
        dim = Style(dim=True)
        dot_style = Style(color="green", bold=True)

        for i, dl in enumerate(self._parsed_lines):
            has_comment = i in comment_indices
            is_selected = self._review_mode and i == self._review_line

            # Track start of line for selection highlight spanning the whole line
            line_start = len(result)

            # Build left-margin prefix
            if self._show_line_nums:
                # Source and diff line number columns
                src = str(dl.src_num) if dl.src_num is not None else ""
                dst = str(dl.dst_num) if dl.dst_num is not None else ""
                result.append(f"{src:>4} {dst:>4} ", style=dim)
                # Comment dot sits between numbers and the single bar
                if show_comment_col:
                    result.append("● " if has_comment else "  ",
                                  style=dot_style if has_comment else Style())
                result.append("│ ", style=dim)
            elif show_comment_col:
                # No line numbers — bar with dot provides the margin
                result.append("● " if has_comment else "  ",
                               style=dot_style if has_comment else Style())
                result.append("│ ", style=dim)

            # Diff line content (preserving ANSI colours)
            result.append_text(Text.from_ansi(dl.text))
            result.append("\n")

            # Apply reverse-video selection highlight across the whole line
            if is_selected:
                result.stylize(Style(reverse=True), line_start, len(result))

        return result

    def _refresh_view(self, scroll_home: bool = False) -> None:
        diff_view = self.query_one(DiffView)
        text = self._render_lines()
        if scroll_home:
            diff_view.show_content(text)
        else:
            diff_view.update_content(text)
            if self._review_mode:
                diff_view.ensure_line_visible(self._review_line)

    def _update_title_style(self) -> None:
        title = self.query_one("#title", Horizontal)
        title.remove_class("review", "comment-mode", "revise-mode")
        if self._revise_mode:
            title.add_class("revise-mode")
        elif self._comment_mode:
            title.add_class("comment-mode")
        elif self._review_mode:
            title.add_class("review")

        status = self.query_one("#footer-status", Label)
        if self._revise_mode:
            status.update("Editing summary... (Ctrl+S / Esc save · Ctrl+C discard)")
        elif self._comment_mode:
            status.update("Commenting... (Enter save · Shift+Enter newline · Ctrl+C discard)")
        elif self._review_mode:
            status.update("Reviewing... (Enter comment · ←→ files · r/Esc exit)")
        else:
            status.update("")

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
        n = len(self.files)
        self.query_one("#title-file", Label).update(f"[bold]{filename}[/bold]")
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

        dl = self._parsed_lines[self._review_line] if self._parsed_lines else None
        filename = self.files[self._index][0]
        label = self.query_one("#comment-label", Label)
        if self._review_line < 4:
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
                dl = self._parsed_lines[self._review_line] if self._parsed_lines else None
                lines = self._parsed_lines
                idx = self._review_line
                file_level = idx < 4
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
                    diff_line_index=idx if not self._full_view else None,
                    full_line_index=idx if self._full_view else None,
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

    def action_prev_file(self) -> None:
        if self._comment_mode or self._revise_mode:
            return
        if self._on_summary_page():
            self._show_file(len(self.files) - 1)
        elif self._index > 0:
            self._show_file(self._index - 1)

    def action_next_file(self) -> None:
        if self._comment_mode or self._revise_mode:
            return
        if self._on_summary_page():
            return
        if self._index < len(self.files) - 1:
            self._show_file(self._index + 1)
        elif self._index == len(self.files) - 1 and self._comments:
            self._index = len(self.files)
            self._show_summary_page()

    def action_full_file(self) -> None:
        if self._on_summary_page():
            return
        if self._full_view:
            self._show_file(self._index)
            return
        filename, content = self.files[self._index]
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
        content = result.stdout
        self._full_view = True
        self._parsed_lines = parse_lines(content)
        self._update_file_info()
        self._refresh_view(scroll_home=True)

    def action_toggle_line_nums(self) -> None:
        self._show_line_nums = not self._show_line_nums
        self._refresh_view()

    def action_toggle_review(self) -> None:
        if self._on_summary_page():
            if not self._revise_mode:
                self._enter_revise_mode()
            return
        if self._comment_mode:
            self._exit_comment_mode(save=False)
            self._exit_review_mode()
            return
        if self._review_mode:
            self._exit_review_mode()
            return
        # Start at the first currently-visible line
        first_visible = int(self.query_one(DiffView).scroll_y)
        self._review_line = max(0, min(first_visible, len(self._parsed_lines) - 1)) if self._parsed_lines else 0
        self._review_mode = True
        self._update_title_style()
        self._refresh_view()

    def action_review_up(self) -> None:
        if self._comment_mode:
            return
        if self._review_mode and self._parsed_lines:
            self._review_line = max(0, self._review_line - 1)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_up()

    def action_review_down(self) -> None:
        if self._comment_mode:
            return
        if self._review_mode and self._parsed_lines:
            self._review_line = min(len(self._parsed_lines) - 1, self._review_line + 1)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_down()

    def action_review_pgup(self) -> None:
        if self._comment_mode:
            return
        if self._review_mode and self._parsed_lines:
            height = self.query_one(DiffView).size.height
            self._review_line = max(0, self._review_line - height)
            self._refresh_view()
        else:
            self.query_one(DiffView).scroll_page_up()

    def action_review_pgdn(self) -> None:
        if self._comment_mode:
            return
        if self._review_mode and self._parsed_lines:
            height = self.query_one(DiffView).size.height
            self._review_line = min(len(self._parsed_lines) - 1, self._review_line + height)
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
            if self._review_line > max_visible:
                self._review_line = max_visible
            self._refresh_view()

    def action_viewport_down(self) -> None:
        if self._comment_mode:
            return
        diff_view = self.query_one(DiffView)
        new_scroll = int(diff_view.scroll_y) + 1
        diff_view.scroll_to(y=new_scroll, animate=False)
        if self._review_mode and self._parsed_lines:
            if self._review_line < new_scroll:
                self._review_line = new_scroll
            self._refresh_view()

    def action_enter_comment(self) -> None:
        if self._comment_mode or self._on_summary_page():
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

    def action_copy_review(self) -> None:
        if not self._comments and self._summary_text_override is None:
            return
        pyperclip.copy(self._build_summary_text())
        self.query_one("#footer-status", Label).update("Copied to clipboard")

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    branch = get_branch()
    diff_output = get_diff(sys.argv[1:])

    if not diff_output.strip():
        print("No diff output.")
        sys.exit(0)

    files = parse_diff(diff_output)
    if not files:
        print("No files found in diff.")
        sys.exit(0)

    app = DiffPager(files, branch, sys.argv[1:])
    app.run()
    if app._run_claude:
        claude = os.environ.get("CLAUDE", "claude")
        os.execvp(claude, [claude, "/git-review"])


if __name__ == "__main__":
    main()
