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

基础固件内置 LuCI 简体中文界面、SSR Plus、Xray、ShadowsocksR 客户端、WireGuard 配置支持、IPv6、防火墙、NFS 客户端和 USB/磁盘存储支持。

Docker、Shadowsocks Rust 客户端、Cloudflared、FRP、ZeroTier、MWAN3、SQM、DDNS、OpenAppFilter、流量统计、磁盘管理及其他可选应用，在基础固件完成并清理中间文件后再分批编译。应用及依赖随后自动安装到新的根文件系统，生成文件名带 **`-full-`** 的完整固件，刷机后直接可用，无需逐个手动安装。基础版和独立安装包同时保留。

DDNS 安装包包含 Aliyun 和 Cloudflare；Oray 使用 `ddns-scripts-services` 内置的 `oray.com` 服务定义。

为缩短构建时间并避免功能冲突，配置中未启用 OpenConnect、NaiveProxy、Kcptun、Mihomo、TurboACC、qBittorrent、VSFTPD 及旧式 OpenSSL 算法。SSR Plus 不包含任何服务端。基础固件用 Xray 和 ShadowsocksR 客户端提供代理功能；完整版额外内置 `shadowsocks-rust-sslocal`，SSR Plus 会检测并使用 `sslocal`。

当前 512 MiB 根分区适合保存固件和路由配置，不适合作为 Docker 镜像存储。需要运行容器时，应将 Docker 数据目录放到独立的 ext4 SSD 或分区；不要将 NTFS、NFS 或固件 overlay 用作 Docker `overlay2` 数据目录。

配置已包含 `block-mount`、ext4 内核模块和 `e2fsprogs`，用于挂载及维护 Docker 的 `/opt` 数据盘。这里的 `/opt` 必须是独立 ext4 文件系统的挂载点；仅创建根目录下的 `/opt` 文件夹仍会占用 512 MiB 的固件 overlay。

## 下载与校验

稳定构建发布在仓库的 [Releases](https://github.com/dhso/my-openwrt/releases) 页面。每次构建使用独立、不可变的标签，例如：

```text
x86_64-2026.10.25-123.1
```

Release 同时保留：

- 基础版、文件名带 `-full-` 的完整应用版固件镜像，以及 `sha256sums`
- 构建时解析后的 `build.config`
- LEDE 自带的 manifest、feeds 和版本信息
- `source.buildinfo`，记录本仓库、LEDE、helloworld 和 OpenAppFilter 的准确提交
- 第二阶段编译的应用清单 `deferred-packages.txt`，以及完整固件额外选择安装的应用及翻译包清单 `full-image-packages.txt`；依赖的实际安装结果见完整固件 manifest
- `build-timings.json`，记录第二阶段各源码目录的编译耗时、初始并发数及剩余空间；失败时已完成的记录会输出到 Actions 日志
- 包含应用、依赖及目标专用包的 `packages.tar`，保留软件源索引和原目录结构

下载后应先验证校验值：

```bash
sha256sum -c sha256sums
```

一般选择文件名带 `-full-` 的完整固件；UEFI 设备选择 `combined-efi.img.gz`。写盘会覆盖目标磁盘的现有数据，请确认设备名，且当前目录中下面的通配符只匹配本次下载的一个完整镜像。例如 Linux 下可使用：

```bash
gzip -dc *-full-*-combined-efi.img.gz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
```

完整版已经内置所有已选应用。若选择基础版，刷机后也可以手动安装：使用与固件**同一次构建**的 `packages.tar`。例如要安装 Docker，选择 `luci-app-dockerman` 及其依赖；要为 SSR Plus 增加 Shadowsocks Rust，选择 `shadowsocks-rust-sslocal`。无需重新刷机。

离线安装也要提供依赖包，不能只上传一个 LuCI 界面包。可以解压归档，在局域网通过 HTTP 提供各个带索引的软件源目录，再让路由器临时使用这些软件源解析依赖。不要同时依赖其他滚动版本的软件源来补齐依赖，也不要把整个归档中的所有包一次性安装。固件使用 `opkg` 时安装 `.ipk`，使用 `apk` 时安装 `.apk`，以本次构建产物为准。

## 自动构建

进入仓库的 **Actions → Build LEDE x86_64 → Run workflow** 可手动构建。关闭 `publish_release` 时只生成保留 14 天的 Actions Artifact，不创建 Release。

`Check LEDE updates` 每周五 `00:00 UTC` 检查一次上游。如果最新 Release 尚未包含当前 LEDE commit，它会触发一次构建；也可以手动运行并选择 `force_build` 强制构建。

同一时间只允许一个固件构建运行，避免缓存和 Release 相互覆盖。编译任务最长运行 6 小时。

构建分为三个阶段：

1. 解析完整配置，将可选应用所需的内核模块提升为 `y`，提前编译并装入基础固件；其余可选应用保持 `m`。使用 `make package-m=` 只调度基础固件及其依赖。基础固件先上传为独立的 `firmware-base-*` Artifact。
2. 保留同一份 `.config`、完整内核构建树、已安装工具链、主机工具、依赖库和 ccache，清理工具链源码构建目录、根文件系统临时目录及镜像临时目录，再逐个源码目录编译可选应用及依赖。后端优先于 LuCI 界面，Shadowsocks Rust 最后编译；只有空间不足时才清空 ccache。软件包完成后重新生成索引。
3. 清理不再需要的 Rust 源码构建目录，空间不足时再清空 ccache，以本次构建的安装包组装完整根文件系统，再以 `EXTRA_IMAGE_NAME=full` 生成 BIOS 和 UEFI 镜像。调用 `package/install`、`target/install` 复用已编译的应用和内核，不重新执行完整构建；保留基础版，两个版本一起发布。

即使应用编译或完整镜像组装失败，第一阶段的基础固件 Artifact 仍可下载，其中已包含源码提交记录和校验和；完整 Release 只在三个阶段都成功后发布。应用编译及镜像组装期间会校验固件配置、内核配置、内核 ABI 标识及应用构建计划，发现变化立即失败。完整镜像的 manifest 必须包含基础包、所有指定应用和已选的翻译包，基础包版本必须保持一致。两阶段都检查 BIOS/UEFI 镜像的 gzip 完整性；生成完整镜像前移除旧的 `-full-` 产物，生成后核对基础镜像及 manifest 的哈希，防止旧文件掩盖失败或覆盖基础版。

`.config` 中 `y` 表示基础版内置，`m` 表示延后编译，并在第三阶段装入完整固件。`build.config` 记录编译调度；完整版的实际内容以其 manifest 和 `full-image-packages.txt` 为准。**直接执行普通 `make` 仍会编译 `y` 和 `m` 两类包**；第一阶段必须保留工作流中的 `package-m=` 调度参数。不要在阶段之间执行 `make clean` 或 `make dirclean`。

## 构建优化与安全措施

- GitHub Actions 固定到完整 commit SHA，并由 Dependabot 检查更新。
- helloworld 和 OpenAppFilter 固定到明确提交，避免构建期间上游漂移。
- ccache 在基础固件和应用阶段统一限制为 2 GiB，阶段切换保留缓存，空间不足时才清空。ccache 与 Go 的 `tmp/go-build` 一起持久化；Go 构建缓存会在 Rust 编译前和构建收尾时裁剪到 1 GiB，源码模块缓存则保留在 `dl` 中。
- 编译缓存优先恢复相同配置和工具链的快照，未命中时允许恢复同系统、架构、目标的其他快照。C/C++ 编译显式传入 `CCACHE_COMPILERCHECK=content`，并按实际安装的目标工具链、宿主 GCC 驱动及 cc1/cc1plus 内容生成命名空间，避免仅检查 GCC 版本号导致补丁变更后错误复用。Go 自身按内容和构建参数检查缓存。工具链成品缓存仍严格匹配。
- 下载缓存只在所有编译和镜像生成结束后清理，上限为 2 GiB。顶层源码包按整个文件淘汰；Go 模块、Cargo、Rust 引导包等顶层子目录作为完整单元保留或删除，避免留下带完整性标记的残缺目录。单个目录超过预算时会整体淘汰，下次按需重新下载。旧的 v2 下载快照不再恢复。
- 下载和编译缓存使用每次运行独立的保存键，同一 commit 重跑也能更新快照；缓存保存失败不影响固件交付。独立的缓存维护 job 仅处理本工作流、本分支的缓存，工具链、编译、下载各保留一个最近使用的快照，总预算 8 GiB；预算不足时优先保留工具链，再保留编译缓存。其他分支及工作流的缓存不受影响，因此整个仓库的占用仍以 GitHub Caches 页面为准。
- host 工具和工具链按 LEDE 源码、全部非软件包配置和 Runner 编译环境的哈希精确匹配缓存，并在它们编译完成后立即保存，后续软件包编译失败也不会丢失。缓存异常时修改工作流中的 `TOOLCHAIN_CACHE_VERSION` 即可让全部旧缓存失效。缓存恢复后的首次 make 可能重新检查时间戳，是否实际重新编译应以日志为准。
- 启用 `AUTOREMOVE`，软件包编译完成后清理其构建目录，降低磁盘占用峰值。
- 构建前会输出 CPU、内存、磁盘、块设备和 swap 信息；先安装依赖，再清理 Runner 中无关的大型工具链和 Docker 镜像并扩展工作区，扩容后再次确认磁盘布局。系统盘预留 6 GiB，供缓存 Action 在 `RUNNER_TEMP` 压缩归档等操作使用；这会比原来的 2 GiB 预留减少约 4 GiB 构建卷空间，避免只检查工作区却耗尽系统盘。
- 按实际剩余空间设置分阶段保护：下载前 14 GiB、未命中工具链缓存时 20 GiB（命中时 14 GiB）、固件编译前 14 GiB；普通延后应用编译前 4 GiB，Docker、Cloudflared、FRP、ZeroTier 等大型后端 14 GiB，Shadowsocks Rust 20 GiB；完整固件组装前在清理后检查 4 GiB。应用阶段还检查可用 inode。门槛是提前检查，不能保证单个大型编译任务一定够用；磁盘不足时可结合最终目录统计调整阈值或换用更大的 Runner。
- `packages.tar` 在归档软件包时同步移除源文件，避免产物准备阶段同时保存两份软件包占用。
- 基础固件使用 `nproc + 1` 个 make 任务，应用源码目录逐个构建，每个默认 2 个任务。Go 的 `GOMAXPROCS` 和 `GOFLAGS=-p=N` 直接注入实际配方环境，防止被上游 `unexport` 清除，基础阶段的 Xray 同样受限；Cargo 设置 `CARGO_BUILD_JOBS`。Rust 在至少 2 核、12 GiB 内存的 Runner 上使用 2 个任务，较小 Runner 使用 1 个，并将任务数传给主机编译器 `x.py --jobs`。失败且仍有至少 1 GiB 空间和足够 inode 时，make、Go、Cargo、Rust bootstrap 一并降为单任务详细重试。上游配方锚点变化时立即停止。
- Rust 主机编译器目前仍按当前 feeds 源码构建，没有单独缓存成品或启用 sccache；它与基础工具链共用安装位置，需要额外处理恢复顺序和完整依赖，不能直接覆盖基础工具链缓存。后续是否值得增加该缓存，可根据 `build-timings.json` 和缓存占用判断。
- Ubuntu 构建依赖基于 LEDE 官方清单，补齐 `libnsl-dev`、`xz-utils`，省略 CI 不需要的交互式编辑器和邮件工具，不执行远程短链接脚本。
- 显式关闭 BUILDBOT、SDK、ImageBuilder 及工具链/LLVM-BPF 归档，避免额外产物占用磁盘。
- `make defconfig` 后验证所有显式配置；配置失效或被依赖覆盖时 CI 会立即失败。
- 明确禁止 LEDE profile 自动加入 Alist、UnblockMusic、JD DailyBonus、qBittorrent、TurboACC、UU 加速器及 VSFTPD；上游已删除的禁用项不会导致误报。
- 构建、缓存维护和发布拆成不同 job：仅发布 job 具有 `contents: write`，仅缓存维护 job 具有 `actions: write`；缓存清理失败不影响固件发布。
- 不再写入 LEDE 公共默认密码；固件首次登录后必须自行设置密码。

上游 LEDE 和 feeds 仍是滚动更新模型，因此不同 Release 可能包含不同上游版本。`source.buildinfo` 和 Release 说明中的提交号是复现及排查问题的依据。

GitHub 托管 Runner 的实际硬件和初始可用容量由 GitHub 分配，工作流无法承诺固定物理容量。当前策略通过安装依赖后扩容、APT 缓存清理、分阶段剩余空间检查，以及构建结束时的目录占用统计来避免在空间明显不足的 Runner 上浪费编译时间。

## 自定义

- 软件包和镜像选项：编辑 [`.config`](.config)。
- feeds、helloworld、OpenAppFilter：编辑 [`diy-part1.sh`](diy-part1.sh) 及工作流中的对应 commit。
- 固件版本和默认设置：编辑 [`diy-part2.sh`](diy-part2.sh)。
- 构建、缓存和发布行为：编辑 [`.github/workflows/build-openwrt.yml`](.github/workflows/build-openwrt.yml)。
- 分阶段清理及应用编译顺序：编辑 [`staged-build.py`](staged-build.py)。脚本检查：`python3 -B -m unittest discover -s tests`，使用临时文件和模拟的 make 调用，不编译固件或应用。
- 缓存快照保留策略：编辑 [`cache-policy.py`](cache-policy.py)，预算在工作流缓存维护 job 的 `--max-gib` 参数中调整。

固件和应用只在 GitHub Actions 编译。本地仅检查脚本和工作流；完整构建、实际峰值磁盘占用和耗时需要以 Actions 的运行结果验证。

修改 `.config` 时应使用当前 LEDE 的配置名称。CI 的配置漂移检查失败，通常意味着上游删除或重命名了某个选项，需要明确更新本仓库配置，而不是忽略错误。

## 安全提示

- 首次启动后立即通过 LuCI 或 `passwd` 设置 root 密码。
- 不要把 LuCI、SSH、Docker API、UPnP 或文件上传页面直接暴露到 WAN。
- 升级前备份配置，并保留上一版本固件用于回滚。
- 第三方软件包虽已固定版本，但不代表经过 LEDE 官方安全审计。

## License

[MIT](LICENSE)
