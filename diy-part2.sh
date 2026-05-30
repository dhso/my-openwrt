#!/bin/bash
#
# Copyright (c) 2019-2020 P3TERX <https://p3terx.com>
#
# This is free software, licensed under the MIT License.
# See /LICENSE for more information.
#
# https://github.com/P3TERX/Actions-OpenWrt
# File name: diy-part2.sh
# Description: OpenWrt DIY script part 2 (After Update feeds)
#

set -euo pipefail

# 移除要替换的包
# rm -rf feeds/packages/net/haproxy

# Git稀疏克隆，只克隆指定目录到本地
function git_sparse_clone() {
  branch="$1" repourl="$2" && shift 2
  git clone --depth=1 -b $branch --single-branch --filter=blob:none --sparse $repourl
  repodir=$(echo $repourl | awk -F '/' '{print $(NF)}')
  cd $repodir && git sparse-checkout set $@
  mv -f $@ ../package
  cd .. && rm -rf $repodir
}
# 添加额外插件
# git clone --depth=1 https://github.com/BH4EHN/openwrt-cloudflared.git package/cloudflared

# 科学上网插件
# git clone --depth=1 -b main https://github.com/fw876/helloworld package/luci-app-ssr-plus

# 修改版本为编译日期
date_version=$(date +"%y.%m.%d")
sed -i "s/^DISTRIB_REVISION=.*/DISTRIB_REVISION='R${date_version} by DHSO'/g" package/lean/default-settings/files/zzz-default-settings

# Modify default IP
# sed -i 's/192.168.1.1/192.168.50.5/g' package/base-files/files/bin/config_generate

# Modify default theme
# sed -i 's/luci-theme-bootstrap/luci-theme-argon/g' feeds/luci/collections/luci/Makefile

# Modify hostname
# sed -i 's/OpenWrt/P3TERX-Router/g' package/base-files/files/bin/config_generate

# Modify os-release
# sed -i 's/VERSION_ID=".*"/VERSION_ID="18.06"/g' package/base-files/files/usr/lib/os-release
# sed -i 's/onbproc 1/#onbproc 1/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
# sed -i 's/ulimit-n 1024/#ulimit-n 1024/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
# sed -i 's/maxconn 1024/#maxconn 1024/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
