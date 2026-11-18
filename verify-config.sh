#!/usr/bin/env bash

# Fail CI when make defconfig silently removes or changes an explicit setting.
set -euo pipefail

readonly REQUESTED_CONFIG="${1:?Usage: verify-config.sh REQUESTED RESOLVED}"
readonly RESOLVED_CONFIG="${2:?Usage: verify-config.sh REQUESTED RESOLVED}"

[[ -f "$REQUESTED_CONFIG" && -f "$RESOLVED_CONFIG" ]] || {
  echo "Both requested and resolved configuration files must exist." >&2
  exit 2
}

missing=0
while IFS= read -r setting; do
  [[ -n "$setting" ]] || continue
  if ! grep -Fqx -- "$setting" "$RESOLVED_CONFIG"; then
    echo "Configuration drift: $setting" >&2
    missing=1
  fi
done < <(
  sed -n '/^CONFIG_[^=]*=.*/p' "$REQUESTED_CONFIG"
)

# A disabled symbol may disappear from the resolved file when the upstream
# package is removed or its dependencies are unavailable. That still satisfies
# the request. Fail only when make defconfig actually enables the symbol.
while IFS= read -r setting; do
  [[ -n "$setting" ]] || continue
  symbol="${setting#\# }"
  symbol="${symbol% is not set}"
  if resolved_value="$(awk -F= -v symbol="$symbol" \
      '$1 == symbol { print; found=1; exit } END { exit !found }' "$RESOLVED_CONFIG")"; then
    echo "Configuration drift: $setting became $resolved_value" >&2
    missing=1
  fi
done < <(
  sed -n '/^# CONFIG_.* is not set$/p' "$REQUESTED_CONFIG"
)

if (( missing != 0 )); then
  echo "make defconfig changed explicit settings; update .config intentionally." >&2
  exit 1
fi

echo "All explicit configuration settings were preserved."
echo "All explicitly disabled packages remained disabled or unavailable."
