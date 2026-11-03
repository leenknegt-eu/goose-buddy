#!/usr/bin/env bash
# Update this checkout from GitHub and reinstall the tool, then prove it works.
# Run it on the machine that uses goose-buddy (for the devbox: inside the dev container).
# Refuses to touch a checkout with local changes. Never edits goose's config or the buddy's state.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain)" ]; then
  echo "refusing to update: local changes in $(pwd)" >&2
  git status --short >&2
  exit 1
fi
before=$(git rev-parse --short HEAD)
git pull --ff-only --quiet
after=$(git rev-parse --short HEAD)
uv tool install --force --reinstall . >/dev/null 2>&1
echo "goose-buddy $(goose-buddy --version): $before -> $after"
goose-buddy doctor | sed -n 1,3p
if [ "$before" != "$after" ]; then
  echo "note: a running pane keeps the old code; close it and run 'goose-buddy pane' again."
  echo "note: goose sessions already running keep the old MCP extension until restarted."
fi
