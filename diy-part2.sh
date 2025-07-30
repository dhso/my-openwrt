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

# Lean源码编译出错处理
rm -rf feeds/smpackage/{base-files,dnsmasq,firewall*,fullconenat,libnftnl,nftables,ppp,opkg,ucl,upx,vsftpd*,miniupnpd-iptables,wireless-regdb}

# 修改版本为编译日期
date_version=$(date +"%y.%m.%d")
orig_version=$(cat "package/lean/default-settings/files/zzz-default-settings" | grep DISTRIB_REVISION= | awk -F "'" '{print $2}')
sed -i "s/${orig_version}/R${date_version} by DHSO/g" package/lean/default-settings/files/zzz-default-settings

# Modify default IP
# sed -i 's/192.168.1.1/192.168.50.5/g' package/base-files/files/bin/config_generate

# Modify default theme
# sed -i 's/luci-theme-bootstrap/luci-theme-argon/g' feeds/luci/collections/luci/Makefile

# Modify hostname
# sed -i 's/OpenWrt/P3TERX-Router/g' package/base-files/files/bin/config_generate

# Modify os-release
sed -i 's/VERSION_ID=".*"/VERSION_ID="18.06"/g' package/base-files/files/usr/lib/os-release
# sed -i 's/onbproc 1/#onbproc 1/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
# sed -i 's/ulimit-n 1024/#ulimit-n 1024/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
# sed -i 's/maxconn 1024/#maxconn 1024/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
