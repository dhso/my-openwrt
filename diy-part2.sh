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

# Modify default IP
#sed -i 's/192.168.1.1/192.168.50.5/g' package/base-files/files/bin/config_generate
# Modify os-release
sed -i 's/VERSION_ID=".*"/VERSION_ID="18.06"/g' package/base-files/files/usr/lib/os-release
sed -i 's/onbproc 1/#onbproc 1/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
sed -i 's/ulimit-n 1024/#ulimit-n 1024/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
sed -i 's/maxconn 1024/#maxconn 1024/g' luci/applications/luci-app-haproxy-tcp/root/etc/haproxy_init.sh
