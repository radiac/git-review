Read the file `~/.git-review` using the Read tool.

The file contains a list of annotated code snippets produced by `git-review`. Each entry has this format:

```
--- path/to/file.ext (line ~N)
    surrounding code context
    to help locate the position
---

The instruction for this location.

```

For each entry:
1. Use the filename and context lines to locate the exact code — search if needed, the line number is approximate
2. Understand what the instruction is asking for
3. Implement the change

Work through the entries in order. If two entries touch the same file, batch them into one edit. After all changes are made, give a short summary of what was changed and why.
