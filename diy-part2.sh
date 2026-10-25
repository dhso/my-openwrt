#!/usr/bin/env bash

# Apply small, auditable source customizations after feeds are installed.
set -euo pipefail

readonly SETTINGS_FILE="package/lean/default-settings/files/zzz-default-settings"
readonly BUILD_VERSION="${BUILD_VERSION:-$(date +'%y.%m.%d')}"

[[ -f "$SETTINGS_FILE" ]] || {
  echo "Missing LEDE default settings: $SETTINGS_FILE" >&2
  exit 1
}

# Include the source revision in the versions shown by LuCI and os-release.
grep -Fq 'R@BUILD_DATE@' "$SETTINGS_FILE" || {
  echo "The LEDE version placeholder has changed; refusing a silent no-op." >&2
  exit 1
}
sed -i "s/R@BUILD_DATE@/R${BUILD_VERSION} by DHSO/g" "$SETTINGS_FILE"

# Match official OpenWrt behavior: do not bake LEDE's public, universal
# password into every image. The user must set a password on first login.
sed -i '\|/etc/shadow|d' "$SETTINGS_FILE"
