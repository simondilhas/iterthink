#!/usr/bin/env bash
# Staple an already-notarized DMG (same bytes that were submitted).
set -euo pipefail

DMG_PATH="${1:?usage: staple-macos-dmg.sh /path/to/file.dmg [submission-id]}"
SUBMISSION_ID="${2:-}"

: "${APPLE_ID:?APPLE_ID is required}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required}"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG not found: $DMG_PATH" >&2
  exit 1
fi

notary_args=(
  --apple-id "$APPLE_ID"
  --password "$APPLE_APP_SPECIFIC_PASSWORD"
  --team-id "$APPLE_TEAM_ID"
)

if [[ -n "$SUBMISSION_ID" ]]; then
  info_json="$(mktemp)"
  xcrun notarytool info "$SUBMISSION_ID" "${notary_args[@]}" --output-format json > "$info_json"
  status="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$info_json")"
  rm -f "$info_json"
  echo "Submission $SUBMISSION_ID status: $status"
  if [[ "$status" != "Accepted" ]]; then
    echo "Cannot staple until status is Accepted (current: $status)" >&2
    exit 1
  fi
fi

echo "Stapling $DMG_PATH ..."
xcrun stapler staple "$DMG_PATH"
spctl -a -vv -t install "$DMG_PATH"
echo "Stapled DMG: $DMG_PATH"
