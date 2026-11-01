#!/usr/bin/env bash
# Double-click to start linkedin-engine's web UI (macOS). Installs uv if needed, syncs the
# dependencies and opens the search page in your browser. Close this window to stop it.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python package manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Preparing linkedin-engine (the first time this can take a minute)…"
uv sync --quiet
uv run engine ui
