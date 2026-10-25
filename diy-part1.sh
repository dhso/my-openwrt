#!/usr/bin/env bash

# Prepare feeds and pinned third-party packages before make defconfig.
set -euo pipefail

readonly HELLOWORLD_REPO="${HELLOWORLD_REPO:-https://github.com/fw876/helloworld.git}"
readonly HELLOWORLD_REF="${HELLOWORLD_REF:-31b180df838e456bb36036bb8791f569af0fd661}"
readonly OPENAPPFILTER_REPO="${OPENAPPFILTER_REPO:-https://github.com/destan19/OpenAppFilter.git}"
readonly OPENAPPFILTER_REF="${OPENAPPFILTER_REF:-7fcf9e87dc75f7c28c74e09504a4efa0a2db3c92}"

[[ -x scripts/feeds && -f feeds.conf.default ]] || {
  echo "Run this script from the LEDE source root." >&2
  exit 1
}

clone_at_ref() {
  local repository="$1"
  local revision="$2"
  local destination="$3"

  rm -rf -- "$destination"
  mkdir -p -- "$destination"
  git -C "$destination" init --quiet
  git -C "$destination" remote add origin "$repository"
  git -C "$destination" fetch --depth=1 origin "$revision"
  git -C "$destination" checkout --quiet --detach FETCH_HEAD
}

# LEDE enables the same feed by default. Remove it so there is only one,
# pinned helloworld source instead of two competing package providers.
sed -i '\|github.com/fw876/helloworld|d' feeds.conf.default
./scripts/feeds update -a

clone_at_ref "$HELLOWORLD_REPO" "$HELLOWORLD_REF" package/helloworld

rm -rf -- \
  package/OpenAppFilter \
  package/lean/luci-app-oaf \
  package/lean/open-app-filter \
  package/lean/oaf
clone_at_ref "$OPENAPPFILTER_REPO" "$OPENAPPFILTER_REF" package/OpenAppFilter

./scripts/feeds install -a
