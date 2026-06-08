#!/usr/bin/env bash
# Hardened Flet desktop build for CI: prefetch template, validate zip, retry with --clear-cache.
set -euo pipefail

TARGET="${1:?usage: ci-flet-build.sh macos|linux BUILD_VERSION BUILD_NUMBER}"
BUILD_VERSION="${2:?}"
BUILD_NUMBER="${3:?}"

flet_version() {
  if [[ -f uv.lock ]]; then
    python3 - <<'PY'
import pathlib, re
text = pathlib.Path("uv.lock").read_text(encoding="utf-8")
match = re.search(r'^name = "flet"\nversion = "([^"]+)"', text, re.MULTILINE)
if match:
    print(match.group(1))
PY
    return
  fi
  uv run python -c "import importlib.metadata as m; print(m.version('flet'))"
}

FLET_VERSION="$(flet_version)"
[[ -n "$FLET_VERSION" ]] || { echo "Could not resolve installed Flet version" >&2; exit 1; }

TEMPLATE_URL="https://github.com/flet-dev/flet/releases/download/v${FLET_VERSION}/flet-build-template.zip"
CACHE_ROOT="${RUNNER_TEMP:-/tmp}/flet-build-template-cache"
TEMPLATE_ZIP="${CACHE_ROOT}/flet-build-template.zip"
TEMPLATE_DIR="${CACHE_ROOT}/unpacked"

mkdir -p "$CACHE_ROOT"

valid_template_zip() {
  [[ -f "$TEMPLATE_ZIP" ]] || return 1
  local size magic
  size="$(wc -c < "$TEMPLATE_ZIP" | tr -d '[:space:]')"
  # Release asset is ~15 MB; HTML error pages are tiny.
  [[ "$size" -ge 1000000 ]] || return 1
  magic="$(head -c 2 "$TEMPLATE_ZIP" | od -An -tx1 | tr -d ' \n')"
  [[ "$magic" == "504b" ]] || return 1
  unzip -tq "$TEMPLATE_ZIP" >/dev/null 2>&1
}

unpack_template() {
  rm -rf "$TEMPLATE_DIR"
  unzip -q -o "$TEMPLATE_ZIP" -d "$TEMPLATE_DIR"
  echo "Template ready at $TEMPLATE_DIR"
}

fetch_template() {
  echo "Fetching Flet build template v${FLET_VERSION}..."
  rm -f "$TEMPLATE_ZIP"
  rm -rf "$TEMPLATE_DIR"
  for attempt in 1 2 3 4 5; do
    if curl -fSL --retry 5 --retry-delay 5 --retry-all-errors \
        -o "$TEMPLATE_ZIP" "$TEMPLATE_URL" \
        && valid_template_zip; then
      unpack_template
      return 0
    fi
    echo "Template download or zip validation failed (attempt ${attempt}/5)" >&2
    rm -f "$TEMPLATE_ZIP"
    sleep "$((attempt * 5))"
  done
  echo "Failed to fetch a valid Flet build template from $TEMPLATE_URL" >&2
  return 1
}

if ! valid_template_zip; then
  fetch_template
elif [[ ! -d "$TEMPLATE_DIR" ]] || [[ -z "$(ls -A "$TEMPLATE_DIR" 2>/dev/null || true)" ]]; then
  unpack_template
fi

COMMON_ARGS=(
  --yes
  --verbose
  --build-version "$BUILD_VERSION"
  --build-number "$BUILD_NUMBER"
  --template "$TEMPLATE_DIR"
)

for attempt in 1 2 3; do
  build_args=( "${COMMON_ARGS[@]}" )
  if [[ $attempt -gt 1 ]]; then
    echo "Flet build retry ${attempt}/3 with --clear-cache"
    build_args+=(--clear-cache)
    rm -rf build
    fetch_template
  fi
  if uv run flet build "$TARGET" "${build_args[@]}"; then
    exit 0
  fi
  echo "Flet build attempt ${attempt}/3 failed" >&2
  sleep 15
done

echo "Flet build failed after 3 attempts" >&2
exit 1
