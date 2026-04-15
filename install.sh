#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git config --global alias.review "!uv run $REPO_DIR/git-review.py"
echo "Registered: git review -> uv run $REPO_DIR/git-review.py"

COMMANDS_DIR="$HOME/.claude/commands"
mkdir -p "$COMMANDS_DIR"
cp "$REPO_DIR/claude/git-review.md" "$COMMANDS_DIR/git-review.md"
echo "Installed: $COMMANDS_DIR/git-review.md"

