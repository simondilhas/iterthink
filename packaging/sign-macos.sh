#!/usr/bin/env bash
# Sign, package, notarize, and staple a Flet macOS .app for CI release builds.
set -euo pipefail

APP_PATH="${1:?usage: sign-macos.sh /path/to/App.app build-version [output-dir]}"
BUILD_VERSION="${2:?usage: sign-macos.sh /path/to/App.app build-version [output-dir]}"
DIST_DIR="${3:-dist}"

: "${CODESIGN_IDENTITY:?CODESIGN_IDENTITY is required}"
: "${APPLE_ID:?APPLE_ID is required}"
: "${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required}"
: "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required}"

ENTITLEMENTS="${ENTITLEMENTS:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/macos-entitlements.plist}"
DMG_PATH="${DIST_DIR}/iterthink-${BUILD_VERSION}-macos.dmg"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found: $APP_PATH" >&2
  exit 1
fi

if [[ ! -f "$ENTITLEMENTS" ]]; then
  echo "Entitlements file not found: $ENTITLEMENTS" >&2
  exit 1
fi

sanitize_app_bundle() {
  # Flet/serious_python leaves site-packages/.pod (symlink → dist_macos) in the bundle.
  # codesign --strict rejects those as invalid symlink destinations.
  echo "Sanitizing app bundle for codesign..."
  while IFS= read -r -d '' match; do
    echo "Removing Flet build artifact: $match"
    rm -rf "$match"
  done < <(
    find "$APP_PATH" \( \
      -path '*/site-packages/.pod' -o \
      -path '*/site-packages/.pod/*' -o \
      -path '*/site-packages/dist_macos' -o \
      -path '*/site-packages/dist_macos/*' \
    \) -print0 2>/dev/null
  )

  while IFS= read -r -d '' site_pkg; do
    while IFS= read -r -d '' link; do
      echo "Removing site-packages symlink: $link"
      rm -f "$link"
    done < <(find "$site_pkg" -type l -print0 2>/dev/null)
  done < <(find "$APP_PATH" -type d -path '*/site-packages' -print0 2>/dev/null)

  while IFS= read -r -d '' link; do
    echo "Removing broken symlink: $link"
    rm -f "$link"
  done < <(find "$APP_PATH" -type l ! -exec test -e {} \; -print0 2>/dev/null)
}

sign_file() {
  local target="$1"
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$CODESIGN_IDENTITY" \
    "$target"
}

sign_flet_desktop_package() {
  local flet_app_dir
  flet_app_dir="$(
    find "$APP_PATH" -type d \
      -path '*/site-packages/flet_desktop/app' 2>/dev/null | head -n1
  )"
  if [[ -z "$flet_app_dir" || ! -f "$flet_app_dir/flet-macos.tar.gz" ]]; then
    echo "No flet-macos.tar.gz bundle to repack; skipping flet-desktop signing."
    return 0
  fi

  echo "Signing flet-desktop package in $flet_app_dir"
  local work="$flet_app_dir/.sign-work"
  rm -rf "$work"
  mkdir -p "$work"
  tar -xzf "$flet_app_dir/flet-macos.tar.gz" -C "$work"

  while IFS= read -r -d '' file; do
    sign_file "$file"
  done < <(
    find "$work" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0
  )

  rm -f "$flet_app_dir/flet-macos.tar.gz"
  tar -czf "$flet_app_dir/flet-macos.tar.gz" -C "$work" .
  rm -rf "$work"
}

sign_app_bundle() {
  echo "Signing nested libraries and executables..."
  while IFS= read -r -d '' file; do
    sign_file "$file" || true
  done < <(
    find "$APP_PATH" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0 \
      | sort -z -r
  )

  echo "Signing framework bundles..."
  while IFS= read -r -d '' framework; do
    sign_file "$framework"
  done < <(find "$APP_PATH" -depth -type d -name '*.framework' -print0)

  echo "Signing helper app bundles..."
  while IFS= read -r -d '' helper; do
    sign_file "$helper"
  done < <(find "$APP_PATH" -depth -type d -name '*.app' ! -path "$APP_PATH" -print0)

  echo "Signing main app bundle..."
  sanitize_app_bundle
  sign_file "$APP_PATH"
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
}

create_signed_dmg() {
  mkdir -p "$DIST_DIR"
  rm -f "$DMG_PATH"
  hdiutil create -volname "Iterthink" -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH"
  codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG_PATH"
}

notarize_and_staple() {
  echo "Submitting DMG for notarization..."
  xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_APP_SPECIFIC_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait
  xcrun stapler staple "$DMG_PATH"
  spctl -a -vv -t install "$DMG_PATH"
}

sanitize_app_bundle
sign_flet_desktop_package
sign_app_bundle
create_signed_dmg
notarize_and_staple

echo "Signed, notarized DMG: $DMG_PATH"
