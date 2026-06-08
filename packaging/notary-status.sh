#!/usr/bin/env bash
# Print Apple notarization status (for GitHub Actions macOS runners).
set -euo pipefail

SUBMISSION_ID="${1:?usage: notary-status.sh <submission-id>}"

: "${APPLE_ID:?APPLE_ID is required}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required}"

notary_args=(
  --apple-id "$APPLE_ID"
  --password "$APPLE_APP_SPECIFIC_PASSWORD"
  --team-id "$APPLE_TEAM_ID"
)

info_json="$(mktemp)"
xcrun notarytool info "$SUBMISSION_ID" "${notary_args[@]}" --output-format json > "$info_json"
status="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$info_json")"
created="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('createdDate', 'unknown'))" "$info_json")"
name="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('name', 'unknown'))" "$info_json")"
rm -f "$info_json"

echo "Submission: $SUBMISSION_ID"
echo "Name:       $name"
echo "Created:    $created"
echo "Status:     $status"

echo ""
echo "--- recent history (newest first) ---"
xcrun notarytool history "${notary_args[@]}" 2>/dev/null | head -25 || true

if [[ "$status" == "Invalid" || "$status" == "Rejected" ]]; then
  echo ""
  echo "--- notarization log ---"
  xcrun notarytool log "$SUBMISSION_ID" "${notary_args[@]}" || true
  exit 1
fi

if [[ "$status" == "In Progress" ]]; then
  echo ""
  echo "Still processing on Apple's side. Re-run this workflow later, or use the Staple action once Accepted."
  exit 0
fi

if [[ "$status" == "Accepted" ]]; then
  echo ""
  echo "Accepted. Use the macOS notary workflow (staple action) with the pending artifact from the build run."
fi
