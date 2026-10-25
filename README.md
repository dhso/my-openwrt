# LEDE x86_64 自动构建

[![Build LEDE x86_64](https://github.com/dhso/my-openwrt/actions/workflows/build-openwrt.yml/badge.svg)](https://github.com/dhso/my-openwrt/actions/workflows/build-openwrt.yml)
[![Check LEDE updates](https://github.com/dhso/my-openwrt/actions/workflows/update-checker.yml/badge.svg)](https://github.com/dhso/my-openwrt/actions/workflows/update-checker.yml)

使用 GitHub Actions 从 [coolsnowwolf/lede](https://github.com/coolsnowwolf/lede) `master` 分支构建通用 x86_64 固件。构建同时生成 Legacy BIOS 和 UEFI 的压缩 SquashFS 镜像，适用于常见软路由、工控机及虚拟机。

## 固件信息

| 项目 | 配置 |
| --- | --- |
| 架构 | x86_64 / Generic |
| 文件系统 | SquashFS，512 MiB 根分区 |
| 启动方式 | Legacy BIOS、UEFI |
| 默认地址 | `192.168.1.1` |
| 默认用户 | `root` |
| 默认密码 | 空；首次登录后必须立即设置 |
| Web 管理 | LuCI，简体中文 |

主要功能包括 Docker、Cloudflared、FRP、ZeroTier、OpenConnect、WireGuard、MWAN3、SQM、DDNS、OpenAppFilter、SSR Plus、磁盘管理、NFS 客户端和 USB 存储。

DDNS 显式包含 Aliyun 和 Cloudflare；Oray 使用 `ddns-scripts-services` 内置的 `oray.com` 服务定义。

为缩短构建时间并避免功能冲突，配置中未启用 NaiveProxy、Kcptun、Mihomo、TurboACC、qBittorrent、VSFTPD 及旧式 OpenSSL 算法。SSR Plus 保留 Xray、Shadowsocks Rust 客户端和 ShadowsocksR 客户端，不包含任何服务端。

当前 512 MiB 根分区适合保存固件和路由配置，不适合作为 Docker 镜像存储。需要运行容器时，应将 Docker 数据目录放到独立的 ext4 SSD 或分区；不要将 NTFS、NFS 或固件 overlay 用作 Docker `overlay2` 数据目录。

配置已包含 `block-mount`、ext4 内核模块和 `e2fsprogs`，用于挂载及维护 Docker 的 `/opt` 数据盘。这里的 `/opt` 必须是独立 ext4 文件系统的挂载点；仅创建根目录下的 `/opt` 文件夹仍会占用 512 MiB 的固件 overlay。

## 下载与校验

稳定构建发布在仓库的 [Releases](https://github.com/dhso/my-openwrt/releases) 页面。每次构建使用独立、不可变的标签，例如：

```text
x86_64-2026.10.25-123.1
```

Release 同时保留：

- 固件镜像和 `sha256sums`
- 构建时解析后的 `build.config`
- LEDE 自带的 manifest、feeds 和版本信息
- `source.buildinfo`，记录本仓库、LEDE、helloworld 和 OpenAppFilter 的准确提交
- 按原目录结构打包的 `packages.tar`

下载后应先验证校验值：

```bash
sha256sum -c sha256sums
```

写盘会覆盖目标磁盘的现有数据，请确认设备名后再操作。例如 Linux 下可使用：

```bash
gzip -dc openwrt-*-combined-efi.img.gz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
```

## 自动构建

进入仓库的 **Actions → Build LEDE x86_64 → Run workflow** 可手动构建。关闭 `publish_release` 时只生成保留 14 天的 Actions Artifact，不创建 Release。

`Check LEDE updates` 每周五 `00:00 UTC` 检查一次上游。如果最新 Release 尚未包含当前 LEDE commit，它会触发一次构建；也可以手动运行并选择 `force_build` 强制构建。

同一时间只允许一个固件构建运行，避免缓存和 Release 相互覆盖。编译任务最长运行 6 小时。

## 构建优化与安全措施

- GitHub Actions 固定到完整 commit SHA，并由 Dependabot 检查更新。
- helloworld 和 OpenAppFilter 固定到明确提交，避免构建期间上游漂移。
- `dl` 下载目录及 2 GiB ccache 跨构建复用。
- host 工具和工具链按 LEDE 源码、配置和 Runner 编译环境的哈希精确匹配缓存，并在它们编译完成后立即保存，后续软件包编译失败也不会丢失。缓存异常时修改工作流中的 `TOOLCHAIN_CACHE_VERSION` 即可让全部旧缓存失效。
- 启用 `AUTOREMOVE`，软件包编译完成后清理其构建目录，降低磁盘占用峰值。
- 构建前会输出 CPU、内存、磁盘、块设备和 swap 信息，并在扩容后再次确认磁盘布局。
- 清理 Runner 中无关的大型工具链和 Docker 镜像；下载及编译前要求工作区至少有 30 GiB 可用空间，不足时立即终止并输出诊断。
- 编译使用 `nproc + 1` 个任务，在 GitHub 托管 Runner 上兼顾 CPU 利用率和内存压力；失败时自动使用单任务详细日志重试。
- 仅安装 LEDE 官方列出的 Ubuntu 构建依赖，不执行远程短链接脚本。
- `make defconfig` 后验证所有显式配置；配置失效或被依赖覆盖时 CI 会立即失败。
- 明确禁止 LEDE profile 自动加入 Alist、UnblockMusic、JD DailyBonus、qBittorrent、TurboACC、UU 加速器及 VSFTPD；上游已删除的禁用项不会导致误报。
- 构建和发布拆成不同 job，只有发布 job 具有 `contents: write` 权限。
- 不再写入 LEDE 公共默认密码；固件首次登录后必须自行设置密码。

上游 LEDE 和 feeds 仍是滚动更新模型，因此不同 Release 可能包含不同上游版本。`source.buildinfo` 和 Release 说明中的提交号是复现及排查问题的依据。

GitHub 托管 Runner 的实际硬件和初始可用容量由 GitHub 分配，工作流无法承诺固定物理容量。当前策略通过单一扩容步骤、APT 缓存清理、30 GiB 空间门槛及构建结束时的目录占用统计来避免在空间明显不足的 Runner 上浪费编译时间。

## 自定义

- 软件包和镜像选项：编辑 [`.config`](.config)。
- feeds、helloworld、OpenAppFilter：编辑 [`diy-part1.sh`](diy-part1.sh) 及工作流中的对应 commit。
- 固件版本和默认设置：编辑 [`diy-part2.sh`](diy-part2.sh)。
- 构建、缓存和发布行为：编辑 [`.github/workflows/build-openwrt.yml`](.github/workflows/build-openwrt.yml)。

修改 `.config` 时应使用当前 LEDE 的配置名称。CI 的配置漂移检查失败，通常意味着上游删除或重命名了某个选项，需要明确更新本仓库配置，而不是忽略错误。

## 安全提示

- 首次启动后立即通过 LuCI 或 `passwd` 设置 root 密码。
- 不要把 LuCI、SSH、Docker API、UPnP 或文件上传页面直接暴露到 WAN。
- 升级前备份配置，并保留上一版本固件用于回滚。
- 第三方软件包虽已固定版本，但不代表经过 LEDE 官方安全审计。

## License

[MIT](LICENSE)
