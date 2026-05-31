#!/bin/bash
#
# Copyright (c) 2019-2020 P3TERX <https://p3terx.com>
#
# This is free software, licensed under the MIT License.
# See /LICENSE for more information.
#
# https://github.com/P3TERX/Actions-OpenWrt
# File name: diy-part1.sh
# Description: OpenWrt DIY script part 1 (Before Update feeds)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

./scripts/feeds update -a
rm -rf feeds/packages/net/xray* feeds/packages/net/v2ray* feeds/packages/utils/v2dat
rm -rf package/helloworld
git clone --depth=1 https://github.com/fw876/helloworld.git package/helloworld
mkdir -p package/helloworld/gn/patches
cp "$SCRIPT_DIR/100-compiler.patch" package/helloworld/gn/patches/100-compiler.patch
rm -rf package/OpenAppFilter package/lean/{luci-app-oaf,open-app-filter,oaf} feeds/*/{luci-app-oaf,open-app-filter,oaf}
git clone --depth=1 https://github.com/destan19/OpenAppFilter.git package/OpenAppFilter
./scripts/feeds install -a
# make menuconfig
make defconfig

# Add a feed source
# echo 'src-git lienol https://github.com/Lienol/openwrt-package' >>feeds.conf.default
# echo 'src-git helloworld https://github.com/fw876/helloworld' >>feeds.conf.default
# echo 'src-git kenzo https://github.com/kenzok8/openwrt-packages' >>feeds.conf.default
# echo 'src-git small https://github.com/kenzok8/small-package' >>feeds.conf.default
