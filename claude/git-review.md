Read the file `~/.git-review` using the Read tool.

The file contains a list of annotated code snippets produced by `git-review`. There are two entry formats:

Line-level comment (applies to a specific location):
```
--- path/to/file.ext (line ~N)
    surrounding code context
    to help locate the position
---

The instruction for this location.

```

File-level comment (applies to the whole file, no context lines):
```
--- path/to/file.ext (full file)
---

The instruction for this file.

```

For each entry:
1. For line-level entries, use the filename and context lines to locate the exact code — search if needed, the line number is approximate
2. For file-level entries, apply the instruction to the file as a whole
3. Understand what the instruction is asking for
4. Implement the change

Work through the entries in order. If two entries touch the same file, batch them into one edit. After all changes are made, give a short summary of what was changed and why.
