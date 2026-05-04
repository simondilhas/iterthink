#!/usr/bin/env bash
# Optional helper: ensure Ollama is available for Iterthink (Fedora-first, generic Linux fallback).
# Does not install the Iterthink app — see docs/PACKAGING.md for Flet builds.

set -euo pipefail

if command -v ollama >/dev/null 2>&1; then
  echo "Ollama is already on PATH: $(command -v ollama)"
  ollama --version 2>/dev/null || true
  exit 0
fi

echo "Ollama was not found on PATH."
echo ""

if command -v dnf >/dev/null 2>&1; then
  echo "Fedora / dnf detected. Options:"
  echo "  1) Official install (recommended for latest builds):"
  echo "       curl -fsSL https://ollama.com/install.sh | sh"
  echo "  2) Try Fedora COPR or distro packages if you prefer a packaged install — see:"
  echo "       https://github.com/ollama/ollama/blob/main/docs/linux.md"
  echo ""
  read -r -p "Run the official install.sh now? [y/N] " reply
  if [[ "${reply,,}" == "y" || "${reply,,}" == "yes" ]]; then
    curl -fsSL https://ollama.com/install.sh | sh
    echo "Done. Start or enable the service if needed, then: ollama pull <model>"
  fi
  exit 0
fi

echo "Generic Linux: use the official installer from https://ollama.com :"
echo "  curl -fsSL https://ollama.com/install.sh | sh"
echo ""
echo "After install, ensure the API is reachable (default http://127.0.0.1:11434)."
echo "Optional env: OLLAMA_HOST, OLLAMA_MODEL (see app startup in iterthink/app_entry.py)."
