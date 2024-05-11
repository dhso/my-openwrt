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

# Uncomment a feed source
# sed -i 's/^#\(.*helloworld\)/\1/' feeds.conf.default

# Add a feed source
# echo 'src-git lienol https://github.com/Lienol/openwrt-package' >>feeds.conf.default
echo 'src-git helloworld https://github.com/fw876/helloworld' >>feeds.conf.default
echo 'src-git oaf https://github.com/destan19/OpenAppFilter' >>feeds.conf.default
echo 'src-git kenzo https://github.com/kenzok8/openwrt-packages' >>feeds.conf.default
echo 'src-git ddnsgo https://github.com/sirpdboy/luci-app-ddns-go' >>feeds.conf.default

git clone https://github.com/rufengsuixing/luci-app-onliner package/luci-app-onliner
git clone https://github.com/gdck/luci-app-control-weburl package/luci-app-control-weburl
# git clone https://github.com/muink/luci-app-netspeedtest package/luci-app-netspeedtest
git clone https://github.com/lvqier/luci-app-dnsmasq-ipset package/luci-app-dnsmasq-ipset
git clone https://github.com/BH4EHN/openwrt-cloudflared.git package/cloudflared
