import importlib.util
import gzip
import json
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zlib


spec = importlib.util.spec_from_file_location(
    "staged_build", Path(__file__).resolve().parents[1] / "staged-build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

REQUESTED = """CONFIG_PACKAGE_luci-app-dockerman=m
CONFIG_PACKAGE_shadowsocks-rust-sslocal=m
CONFIG_PACKAGE_ddns-scripts-services=m
CONFIG_PACKAGE_ddns-scripts-cloudflare=m
CONFIG_PACKAGE_luci-app-ssr-plus=y
"""
METADATA = """Source-Makefile: package/feeds/luci/luci-app-dockerman/Makefile
Package: luci-app-dockerman
Source-Makefile: package/helloworld/shadowsocks-rust/Makefile
Package: shadowsocks-rust-sslocal
Package: shadowsocks-rust-ssserver
Source-Makefile: package/feeds/packages/ddns-scripts/Makefile
Package: ddns-scripts-services
Package: ddns-scripts-cloudflare
Source-Makefile: package/feeds/packages/rust/Makefile
Package: rust
Build-Only: 1
Source-Makefile: package/feeds/packages/golang/Makefile
Package: golang
Build-Only: 1
"""

# Reduced fixture of the query in LEDE's json_overview_image_info.py.
IMAGE_QUERY_SCRIPT = '''from os import environ
from pathlib import Path
from subprocess import run, PIPE
from sys import argv
import json

def main():
    default_packages, arch_packages = run(
        [
            "make",
            "--no-print-directory",
            "-C",
            "target/linux/",
            "val.DEFAULT_PACKAGES",
            "val.ARCH_PACKAGES",
        ],
        stdout=PIPE,
        stderr=PIPE,
        check=True,
        env=environ.copy().update({"TOPDIR": Path().cwd()}),
        universal_newlines=True,
    ).stdout.splitlines()
    Path(argv[1]).write_text(json.dumps(dict(
        default_packages=default_packages.split(), arch_packages=arch_packages)))

if __name__ == "__main__":
    main()
'''

# Relevant branches of LEDE's package/Makefile. This fixture contains no
# compile targets; the recipe expansion test below only records OPKG calls.
ROOTFS_INSTALL_MAKEFILE = '''ifeq ($(CONFIG_USE_APK),y)
package/install:
\t$(call apk,$(TARGET_DIR)) add --no-cache --repository file://$(PACKAGE_DIR_ALL)/packages.adb app
else
package/install:
\t$(call opkg,$(TARGET_DIR)) install $$(cat $(TMP_DIR)/opkg_install_list)
endif
'''


def append_fwtool(image, kind=1, data=b'{"supported_devices":["x86-64"]}'):
    if kind == 1:
        data = struct.pack(">II", 0, 0) + data
    prefix = image + data
    return prefix + struct.pack(">4sIB3xI", b"FWx0",
                                zlib.crc32(prefix) ^ 0xffffffff, kind, len(data) + 16)


class FirmwareValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.image = self.directory / "openwrt-x86-64-combined.img.gz"

    def validate(self, data):
        self.image.write_bytes(data)
        result = build.validate_images(self.directory, "CONFIG_GRUB_IMAGES=y\n")
        self.assertEqual(result, [self.image])
        self.assertEqual(self.image.read_bytes(), data)

    def test_plain_gzip_and_fwtool_metadata_signature_combinations(self):
        image = gzip.compress(b"disk-image" * 1000)
        for label, data in (
                ("plain", image), ("metadata", append_fwtool(image)),
                ("signature", append_fwtool(image, 0, b"signature")),
                ("both", append_fwtool(append_fwtool(image), 0, b"signature"))):
            with self.subTest(label=label):
                self.validate(data)

    def test_concatenated_gzip_members_and_padding_remain_supported(self):
        self.validate(append_fwtool(gzip.compress(b"first") + gzip.compress(b"second")
                                    + b"\x00" * 10))

    def test_invalid_trailer_structure_and_unknown_trailing_data_are_rejected(self):
        image = gzip.compress(b"disk-image")
        metadata = append_fwtool(image)
        bad_version = image + struct.pack(">II", 1, 0) + b"{}"
        bad_version += struct.pack(">4sIB3xI", b"FWx0",
                                   zlib.crc32(bad_version) ^ 0xffffffff, 1, 26)
        for data in (image + b"garbage", metadata[:-1],
                     metadata[:-16] + b"FAIL" + metadata[-12:],
                     metadata[:-8] + b"\x02" + metadata[-7:],
                     metadata[:-4] + struct.pack(">I", 15),
                     metadata[:-4] + struct.pack(">I", len(metadata) + 1),
                     metadata[:-4] + struct.pack(">I", 23), bad_version):
            with self.subTest(tail=data[-16:]), self.assertRaisesRegex(
                    ValueError, "Invalid compressed firmware image:.*combined.img.gz:"):
                self.validate(data)

    def test_fwtool_crc_checks_all_chunks_even_with_a_valid_outer_checksum(self):
        image = append_fwtool(gzip.compress(b"disk-image"))
        damaged = bytearray(image)
        damaged[-12] ^= 1  # Corrupt inner metadata checksum, not its structure.
        for data in (bytes(damaged), append_fwtool(bytes(damaged), 0, b"signature")):
            with self.subTest(chunks=len(data)), self.assertRaisesRegex(
                    ValueError, "fwtool CRC mismatch"):
                self.validate(data)

    def test_gzip_damage_is_rejected_even_with_valid_fwtool_checksums(self):
        image = gzip.compress(b"disk-image" * 1000)
        bad_crc = bytearray(image)
        bad_crc[-8] ^= 1
        for damaged in (image[:-1], bytes(bad_crc), b"not-gzip"):
            for data in (damaged, append_fwtool(damaged)):
                with self.subTest(tail=data[-16:]), self.assertRaisesRegex(
                        ValueError, "Invalid compressed firmware image"):
                    self.validate(data)

    def test_empty_gzip_payload_is_rejected_with_and_without_metadata(self):
        for data in (gzip.compress(b""), append_fwtool(gzip.compress(b""))):
            with self.subTest(metadata=data[-16:-12]), self.assertRaisesRegex(
                    ValueError, "Empty firmware image"):
                self.validate(data)


class StagedBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        # Test build decisions independently of the filesystem hosting /tmp.
        # Individual resource-pressure tests override these ample defaults.
        for resource_patch in (
                patch.object(build.shutil, "disk_usage",
                             return_value=SimpleNamespace(free=64 * 1024 ** 3)),
                patch.object(build.os, "statvfs",
                             return_value=SimpleNamespace(f_files=1000000, f_favail=900000))):
            resource_patch.start()
            self.addCleanup(resource_patch.stop)
        self.write("Makefile", "all:\n\t@true\n")
        self.write(".config", REQUESTED)
        self.write(".config.firmware", REQUESTED)
        self.write(".config.seed", REQUESTED)
        self.write("tmp/.packageinfo", METADATA)
        self.write("scripts/json_overview_image_info.py", IMAGE_QUERY_SCRIPT)
        self.write("package/Makefile", ROOTFS_INSTALL_MAKEFILE)
        self.write("package/feeds/packages/rust/Makefile",
                   "define Host/Compile\n\t$(PYTHON) $(HOST_BUILD_DIR)/x.py \\\n\t\tdist rustc\nendef\n")
        self.write("package/feeds/packages/golang/golang-package.mk",
                   'GO_PKG_BUILD_VARS= \\\n\tGOPATH="$(GO_PKG_BUILD_DIR)"\n')
        self.kernel = "build_dir/target-x86_64_musl/linux-x86_64/linux-6.18"
        self.write(self.kernel + "/.config", "CONFIG_MODULES=y\n")
        self.write(self.kernel + "/.vermagic", "same-kernel-abi\n")
        self.write(".firmware-kernel-state.json", json.dumps(build.fingerprint(self.source)))

    def write(self, name, text="keep"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def args(self):
        return type("Args", (), dict(
            requested_config=self.source / ".config.seed", make="make",
            firmware_dir=self.source / "bin/targets/x86/64", image_min_free_gib=0,
            jobs=2, rust_jobs=1, go_cache_gib=1,
            min_free_gib=0, heavy_min_free_gib=0, rust_min_free_gib=0))()

    def write_image(self, name, content=b"disk-image"):
        path = self.source / "bin/targets/x86/64" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(content))
        return path

    def prepare_full_image(self):
        config = REQUESTED + """CONFIG_GRUB_IMAGES=y
CONFIG_GRUB_EFI_IMAGES=y
CONFIG_PACKAGE_luci-i18n-dockerman-zh-cn=m
CONFIG_PACKAGE_liboptional=m
CONFIG_PACKAGE_host-tool=m
CONFIG_PACKAGE_interface_choice=m
"""
        self.write(".config", config)
        self.write(".config.firmware", config)
        self.write("tmp/.packageinfo", METADATA + """Source-Makefile: package/feeds/luci/luci-app-dockerman/Makefile
Package: luci-i18n-dockerman-zh-cn
Source-Makefile: package/libs/liboptional/Makefile
Package: liboptional
Source-Makefile: package/lang/host-tool/Makefile
Package: host-tool
Build-Only: 1
""")
        self.write("staging_dir/target-x86_64_musl/pkginfo/base.default.install",
                   "kernel\nluci-app-ssr-plus\n")
        self.write("staging_dir/target-x86_64_musl/pkginfo/base.default.install.flags", "hold")
        self.write("bin/targets/x86/64/openwrt-x86-64.manifest",
                   "kernel - 6.18\nluci-app-ssr-plus - 198\n")
        self.write_image("openwrt-x86-64-combined.img.gz", b"base-image")
        self.write_image("openwrt-x86-64-combined-efi.img.gz")
        build.verify_base(self.source, self.args().requested_config, self.args().firmware_dir)
        plan = build.package_plan(REQUESTED, config, (self.source / "tmp/.packageinfo").read_text())
        self.write(".packages-built.json", json.dumps(build.package_build_state(
            self.source, self.args().requested_config, plan)))
        self.write("build_dir/target-x86_64_musl/host/rustc-1.99.1-src/build/large-object")
        full = "kernel - 6.18\nluci-app-ssr-plus - 198\n" + "".join(
            name + " - 1.0\n" for name in (
                "luci-app-dockerman", "shadowsocks-rust-sslocal", "ddns-scripts-services",
                "ddns-scripts-cloudflare", "luci-i18n-dockerman-zh-cn", "liboptional2026"))

        def run_make(command, **kwargs):
            if "target/install" in command:
                self.write("bin/targets/x86/64/openwrt-full-x86-64.manifest", full)
                self.write_image("openwrt-full-x86-64-combined.img.gz", b"full-bios")
                self.write_image("openwrt-full-x86-64-combined-efi.img.gz", b"full-efi")

        return run_make

    def test_shared_source_is_built_once_and_rust_is_last(self):
        plan = build.package_plan(REQUESTED, REQUESTED, METADATA)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[-1][1], ["shadowsocks-rust-sslocal"])
        self.assertEqual(next(names for target, names in plan if "ddns-scripts" in target),
                         ["ddns-scripts-cloudflare", "ddns-scripts-services"])

    def test_dependency_cannot_silently_promote_an_app_into_the_image(self):
        with self.assertRaisesRegex(ValueError, "no longer m"):
            build.package_plan(REQUESTED, REQUESTED.replace("dockerman=m", "dockerman=y"),
                               METADATA)

    def test_missing_package_metadata_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "No source metadata"):
            build.package_plan(REQUESTED, REQUESTED, "")

    def test_metadata_cannot_escape_the_package_tree(self):
        with self.assertRaisesRegex(ValueError, "Unexpected source"):
            build.package_plan(REQUESTED, REQUESTED,
                               METADATA.replace("package/feeds/luci", "package/../outside"))

    def test_non_utf8_descriptions_preserve_package_metadata_and_build_plan(self):
        metadata = METADATA.encode("utf-8").replace(
            b"Package: luci-app-dockerman\n",
            b"Package: luci-app-dockerman\nMaintainer: Ren\xe9\n"
            b"Description: legacy text \xa1\xff\n")
        self.assertEqual(build.package_metadata(metadata), build.package_metadata(METADATA))
        self.assertEqual(build.package_plan(REQUESTED, REQUESTED, metadata),
                         build.package_plan(REQUESTED, REQUESTED, METADATA))

    def test_invalid_utf8_in_structural_metadata_fields_is_rejected(self):
        for field in (b"Source-Makefile: package/invalid-\xff/Makefile\n",
                      b"Package: invalid-\xff\n", b"Build-Only: \xff\n"):
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "Invalid UTF-8 in package metadata field at line"):
                build.package_metadata(METADATA.encode("utf-8") + field)

    def test_non_utf8_metadata_survives_all_build_stages_without_rewriting_the_index(self):
        run_make = self.prepare_full_image()
        path = self.source / "tmp/.packageinfo"
        metadata = path.read_bytes() + b"\nDescription: legacy text \xa1\xff\n"
        path.write_bytes(metadata)
        args = self.args()
        with patch.object(build.subprocess, "run", side_effect=run_make):
            build.configure_build_tools(self.source)
            build.verify_base(self.source, args.requested_config, args.firmware_dir)
            build.build_packages(args, self.source)
            build.build_full_image(args, self.source)
        self.assertEqual(path.read_bytes(), metadata)
        embedded = (args.firmware_dir / "full-image-packages.txt").read_text().splitlines()
        self.assertIn("shadowsocks-rust-sslocal", embedded)
        self.assertIn("ddns-scripts-services", embedded)

    def test_cleanup_preserves_everything_needed_for_later_packages(self):
        preserved = [
            self.kernel + "/vmlinux", self.kernel + "/Module.symvers",
            self.kernel + "/kernel/built-in.a", "dl/source.tar.xz",
            "staging_dir/toolchain-x86_64/bin/gcc", "staging_dir/host/bin/cmake",
            "staging_dir/target-x86_64_musl/usr/lib/libc.so",
            "staging_dir/target-x86_64_musl/root-x86/lib/modules/driver.ko",
            "bin/packages/x86_64/base/library.ipk",
            "bin/targets/x86/64/packages/driver.ipk",
            "bin/targets/x86/64/firmware.img.gz",
            ".ccache/keep-cache-entry",
        ]
        for name in preserved:
            self.write(name)
        removed = ["build_dir/toolchain-x86_64/gcc/source.c",
                   "build_dir/target-x86_64_musl/root-x86/etc/config/network",
                   "build_dir/target-x86_64_musl/root.orig-x86/etc/config/network",
                   "build_dir/target-x86_64_musl/linux-x86_64/tmp/root.img"]
        for name in removed:
            self.write(name)
        manifest = self.write("bin/targets/x86/64/firmware.manifest")
        state = build.fingerprint(self.source)
        build.cleanup(self.source, manifest.parent)
        for name in preserved:
            self.assertTrue((self.source / name).is_file(), name)
        for name in removed:
            self.assertFalse((self.source / name).exists(), name)
        self.assertEqual(build.fingerprint(self.source), state)

    def test_cleanup_requires_saved_firmware_and_unchanged_configuration(self):
        intermediate = self.write("build_dir/toolchain-x86_64/source.c")
        with self.assertRaisesRegex(ValueError, "Base firmware"):
            build.cleanup(self.source, self.source / "not-built")
        manifest = self.write("bin/targets/x86/64/firmware.manifest")
        self.write(".config", "changed")
        with self.assertRaisesRegex(ValueError, "Configuration changed"):
            build.cleanup(self.source, manifest.parent)
        self.assertTrue(intermediate.exists())

    def test_builds_individual_targets_and_indexes_without_rebuilding_images(self):
        state = build.fingerprint(self.source)
        with patch.object(build.subprocess, "run") as run:
            build.build_packages(self.args(), self.source)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertEqual(commands[-2],
                         ["make", "-j1", "CCACHE_COMPILERCHECK=content",
                          "package/helloworld/shadowsocks-rust/compile"])
        self.assertEqual(commands[-1], ["make", "package/index"])
        self.assertEqual(build.fingerprint(self.source), state)

    def test_kernel_abi_change_stops_the_remaining_builds(self):
        def change_kernel(*args, **kwargs):
            self.write(self.kernel + "/.vermagic", "different-kernel-abi")

        with patch.object(build.subprocess, "run", side_effect=change_kernel) as run:
            with self.assertRaisesRegex(ValueError, "ABI"):
                build.build_packages(self.args(), self.source)
        self.assertEqual(run.call_count, 1)

    def test_saved_firmware_state_is_checked_before_building(self):
        self.write(self.kernel + "/.vermagic", "changed-between-stages")
        with patch.object(build.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "saved base firmware"):
                build.build_packages(self.args(), self.source)
        run.assert_not_called()

    def test_base_manifest_must_exclude_deferred_apps(self):
        manifest = self.write("bin/targets/x86/64/firmware.manifest",
                              "kernel - 6.18\nluci-app-dockerman - 1.0\n")
        with self.assertRaisesRegex(ValueError, "installed in the base image"):
            build.verify_base(self.source, self.source / ".config.seed", manifest.parent)
        manifest.write_text("kernel - 6.18\nluci-app-ssr-plus - 198\n")
        config = REQUESTED + "CONFIG_GRUB_IMAGES=y\n"
        self.write(".config", config)
        self.write(".config.firmware", config)
        self.write_image("openwrt-x86-64-combined.img.gz")
        build.verify_base(self.source, self.source / ".config.seed", manifest.parent)
        self.assertIn("shadowsocks-rust-sslocal\n",
                      (manifest.parent / "deferred-packages.txt").read_text())
        self.assertEqual(json.loads((manifest.parent / "kernel-state.json").read_text()),
                         build.fingerprint(self.source))

    def test_low_disk_space_stops_before_starting_a_compiler(self):
        args = self.args()
        args.min_free_gib = 1
        disk = type("Disk", (), {"free": 0})()
        with patch.object(build.shutil, "disk_usage", return_value=disk):
            with patch.object(build.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Insufficient free space"):
                    build.build_packages(args, self.source)
        run.assert_not_called()

    def test_failed_package_gets_a_verbose_single_job_retry(self):
        error = subprocess.CalledProcessError(2, "make")
        with patch.object(build.subprocess, "run", side_effect=[error, None, None, None, None]) as run:
            build.build_packages(self.args(), self.source)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1], ["make", "-j1", "CCACHE_COMPILERCHECK=content",
                                      "V=s", commands[0][-1]])

    def test_promotes_only_selected_kernel_modules(self):
        self.write(".config", "CONFIG_PACKAGE_kmod-veth=m\nCONFIG_PACKAGE_dockerd=m\n"
                   "# CONFIG_PACKAGE_kmod-unused is not set\n")
        script = Path(build.__file__)
        subprocess.run(["python3", str(script), "promote-kmods", "--source", str(self.source)],
                       check=True, stdout=subprocess.PIPE, text=True)
        self.assertEqual((self.source / ".config").read_text(),
                         "CONFIG_PACKAGE_kmod-veth=y\nCONFIG_PACKAGE_dockerd=m\n"
                         "# CONFIG_PACKAGE_kmod-unused is not set\n")

    def test_full_image_embeds_app_roots_and_translations_without_compiling(self):
        run_make = self.prepare_full_image()
        state = build.fingerprint(self.source)
        with patch.object(build.subprocess, "run", side_effect=run_make) as run:
            build.build_full_image(self.args(), self.source)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertIn("package/install", commands[0])
        self.assertIn("CONFIG_TARGET_PER_DEVICE_ROOTFS=y", commands[0])
        self.assertIn("V=s", commands[0])
        self.assertIn("base.default.install", " ".join(commands[0]))
        self.assertEqual(commands[1], ["make", "-j1", "V=s", "target/install", "EXTRA_IMAGE_NAME=full",
                                      "CCACHE_COMPILERCHECK=content"])
        self.assertFalse(any("world" in command or any(arg.endswith("/compile") for arg in command)
                             for command in commands))
        embedded = (self.source / "tmp/full-image.install").read_text().splitlines()
        self.assertIn("luci-i18n-dockerman-zh-cn", embedded)
        # Runtime libraries are resolved by the package manager; forcing all
        # selected build variants can cause conflicts in the final image.
        self.assertNotIn("liboptional", embedded)
        self.assertNotIn("host-tool", embedded)
        self.assertNotIn("interface_choice", embedded)
        self.assertFalse((self.source / "build_dir/target-x86_64_musl/host/rustc-1.99.1-src").exists())
        self.assertEqual(build.fingerprint(self.source), state)
        self.assertEqual(gzip.decompress(
            (self.source / "bin/targets/x86/64/openwrt-x86-64-combined.img.gz").read_bytes()),
            b"base-image")

    def test_full_image_cannot_run_before_all_apps_finish(self):
        self.prepare_full_image()
        (self.source / ".packages-built.json").unlink()
        with patch.object(build.subprocess, "run") as run:
            with self.assertRaises(FileNotFoundError):
                build.build_full_image(self.args(), self.source)
        run.assert_not_called()

    def test_full_image_install_loads_only_the_local_feed_before_installing(self):
        build.configure_full_image_install(self.source)
        config = self.source / "tmp/full-image.opkg.conf"
        self.write("tmp/opkg_install_list", "app.ipk\n")
        # Record actual make/shell expansion, without an installed package
        # manager or any compiler. No target firmware is built here.
        recorder = self.write("record-opkg.py", '''import json, sys
from pathlib import Path
log = Path(sys.argv[1])
args = sys.argv[2:]
config = Path(args[args.index("-f") + 1]).read_text()
with log.open("a") as stream:
    stream.write(json.dumps([args, config]) + "\\n")
''')
        log = self.source / "opkg-calls.jsonl"
        subprocess.run(["make", "--no-print-directory", "-f", "package/Makefile",
                        "package/install", "TMP_DIR=" + str(self.source / "tmp"),
                        "PACKAGE_DIR_ALL=" + str(self.source / "packages"),
                        f"opkg=python3 {recorder} {log}"], cwd=self.source,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([args for args, _ in calls], [
            ["-f", str(config), "update"], ["-f", str(config), "install", "app.ipk"]])
        self.assertEqual({feed for _, feed in calls},
                         {"src full_image file://" + str(self.source / "packages") + "\n"})

    def test_full_image_install_patch_is_idempotent_and_preserves_apk_recipe(self):
        path = self.source / "package/Makefile"
        apk_recipe = ROOTFS_INSTALL_MAKEFILE.split("else\n", 1)[0]
        build.configure_full_image_install(self.source)
        patched = path.read_bytes()
        build.configure_full_image_install(self.source)
        self.assertEqual(path.read_bytes(), patched)
        self.assertTrue(path.read_text().startswith(apk_recipe))

    def test_apk_full_image_install_does_not_need_an_opkg_patch(self):
        self.write(".config", REQUESTED + "CONFIG_USE_APK=y\n")
        path = self.write("package/Makefile", "# APK-only recipe\n")
        build.configure_full_image_install(self.source)
        self.assertEqual(path.read_text(), "# APK-only recipe\n")

    def test_full_image_install_patch_stops_on_upstream_drift(self):
        path = self.write("package/Makefile", ROOTFS_INSTALL_MAKEFILE.replace(
            "opkg_install_list", "changed_install_list"))
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "OPKG rootfs install recipe changed upstream"):
            build.configure_full_image_install(self.source)
        self.assertEqual(path.read_bytes(), original)

    def test_failed_rootfs_install_stops_before_generating_images(self):
        self.prepare_full_image()
        error = subprocess.CalledProcessError(2, "make")
        with patch.object(build.subprocess, "run", side_effect=error) as run:
            with self.assertRaisesRegex(ValueError, r"package/install failed \(exit 2\)"):
                build.build_full_image(self.args(), self.source)
        self.assertEqual(run.call_count, 1)
        self.assertIn("V=s", run.call_args.args[0])
        self.assertFalse((self.args().firmware_dir / "full-image-packages.txt").exists())

    def test_full_manifest_must_contain_every_requested_application(self):
        run_make = self.prepare_full_image()

        def omit_docker(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                path = self.source / "bin/targets/x86/64/openwrt-full-x86-64.manifest"
                path.write_text(path.read_text().replace("luci-app-dockerman - 1.0\n", ""))

        with patch.object(build.subprocess, "run", side_effect=omit_docker):
            with self.assertRaisesRegex(ValueError, "missing packages: luci-app-dockerman"):
                build.build_full_image(self.args(), self.source)

    def test_full_image_requires_the_efi_image_even_if_make_reports_success(self):
        run_make = self.prepare_full_image()

        def omit_efi(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                (self.source / "bin/targets/x86/64/openwrt-full-x86-64-combined-efi.img.gz").unlink()

        with patch.object(build.subprocess, "run", side_effect=omit_efi):
            with self.assertRaisesRegex(ValueError, "Missing full firmware image: combined-efi"):
                build.build_full_image(self.args(), self.source)

    def test_full_image_does_not_replace_the_base_manifest(self):
        run_make = self.prepare_full_image()

        def overwrite_base(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                self.write("bin/targets/x86/64/openwrt-x86-64.manifest", "overwritten")

        with patch.object(build.subprocess, "run", side_effect=overwrite_base):
            with self.assertRaisesRegex(ValueError, "overwrote a base artifact"):
                build.build_full_image(self.args(), self.source)

    def test_full_image_rejects_kernel_changes_after_package_compilation(self):
        self.prepare_full_image()
        self.write(self.kernel + "/.vermagic", "different-abi")
        with patch.object(build.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "no longer match"):
                build.build_full_image(self.args(), self.source)
        run.assert_not_called()

    def test_luci_wrappers_follow_backend_sources(self):
        plan = build.package_plan(REQUESTED, REQUESTED, METADATA)
        self.assertIn("ddns-scripts", plan[0][0])
        self.assertIn("luci-app-dockerman", plan[1][0])

    def test_go_and_cargo_parallelism_is_bounded_including_retry(self):
        error = subprocess.CalledProcessError(2, "make")
        with patch.object(build.subprocess, "run", side_effect=[error, None, None, None, None]) as run:
            build.build_packages(self.args(), self.source)
        envs = [call.kwargs.get("env", {}) for call in run.call_args_list]
        self.assertEqual(envs[0]["STAGED_BUILD_JOBS"], "2")
        self.assertEqual(envs[1]["STAGED_BUILD_JOBS"], "1")
        self.assertEqual(envs[-2]["CARGO_BUILD_JOBS"], "1")

    def test_small_packages_and_heavy_backends_have_separate_space_checks(self):
        requested = REQUESTED + "CONFIG_PACKAGE_dockerd=m\n"
        self.write(".config.seed", requested)
        self.write(".config", requested)
        self.write(".config.firmware", requested)
        self.write("tmp/.packageinfo", METADATA +
                   "Source-Makefile: package/feeds/packages/dockerd/Makefile\nPackage: dockerd\n")
        self.write(".firmware-kernel-state.json", json.dumps(build.fingerprint(self.source)))
        args = self.args()
        args.min_free_gib, args.heavy_min_free_gib, args.rust_min_free_gib = 4, 14, 20
        with patch.object(build, "ensure_space") as space:
            with patch.object(build.subprocess, "run"):
                build.build_packages(args, self.source)
        self.assertEqual([call.args[1] for call in space.call_args_list], [4, 14, 4, 20])

    def test_compiler_cache_is_cleared_only_under_disk_pressure(self):
        disk = type("Disk", (), {"free": 10 * 1024 ** 3})()
        with patch.object(build.shutil, "disk_usage", return_value=disk):
            with patch.object(build, "clear_ccache") as clear:
                build.ensure_space(self.source, 4)
                clear.assert_not_called()
                with self.assertRaisesRegex(ValueError, "Insufficient free space"):
                    build.ensure_space(self.source, 14)
                clear.assert_called_once_with(self.source)

    def test_application_stage_compacts_cache_without_clearing_it(self):
        self.write("staging_dir/host/bin/ccache")
        with patch.object(build.subprocess, "run") as run:
            build.build_packages(self.args(), self.source)
        self.assertEqual(run.call_args_list[0].args[0][-1], "--cleanup")
        self.assertFalse(any("--clear" in call.args[0] for call in run.call_args_list))

    def test_no_retry_when_failure_leaves_disk_exhausted(self):
        disk = type("Disk", (), {"free": 0})()
        with patch.object(build, "ensure_space"):
            with patch.object(build.shutil, "disk_usage", return_value=disk):
                with patch.object(build.subprocess, "run", side_effect=
                                  subprocess.CalledProcessError(2, "make")) as run:
                    with self.assertRaisesRegex(ValueError, "Insufficient free space"):
                        build.build_packages(self.args(), self.source)
        self.assertEqual(run.call_count, 1)

    def test_inode_exhaustion_stops_before_building(self):
        stats = type("Stats", (), {"f_files": 100000, "f_favail": 1})()
        disk = type("Disk", (), {"free": 10 * 1024 ** 3})()
        with patch.object(build.os, "statvfs", return_value=stats), \
                patch.object(build.shutil, "disk_usage", return_value=disk):
            with patch.object(build.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Insufficient free inodes"):
                    build.build_packages(self.args(), self.source)
        run.assert_not_called()

    def test_no_retry_when_failure_exhausts_inodes(self):
        stats = SimpleNamespace(f_files=100000, f_favail=1)
        with patch.object(build, "ensure_space"), \
                patch.object(build.os, "statvfs", return_value=stats), \
                patch.object(build.subprocess, "run", side_effect=
                             subprocess.CalledProcessError(2, "make")) as run:
            with self.assertRaisesRegex(ValueError, "Insufficient free inodes"):
                build.build_packages(self.args(), self.source)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.source / ".packages-built.json").exists())

    def test_download_cache_trims_oldest_files_and_preserves_other_paths(self):
        old = self.write("dl/old.tar.xz")
        new = self.write("dl/rustc/new.tar.xz")
        outside = self.write("outside/keep")
        (self.source / "dl/link").symlink_to(outside.parent, target_is_directory=True)
        os.utime(old, (1, 1))
        os.utime(new, (2, 2))
        build.trim_download_cache(self.source, new.stat().st_size / 1024 ** 3)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(outside.exists())

    def test_full_image_rejects_a_changed_requested_plan(self):
        self.prepare_full_image()
        with (self.source / ".config.seed").open("a") as stream:
            stream.write("# changed after package compilation\n")
        with patch.object(build.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "build plan"):
                build.build_full_image(self.args(), self.source)
        run.assert_not_called()

    def test_stale_full_outputs_cannot_mask_an_ignored_image_failure(self):
        run_make = self.prepare_full_image()
        run_make(["make", "target/install"])
        with patch.object(build.subprocess, "run"):
            with self.assertRaisesRegex(ValueError, "No full image manifest"):
                build.build_full_image(self.args(), self.source)

    def test_full_manifest_cannot_change_base_package_versions(self):
        run_make = self.prepare_full_image()

        def change_version(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                path = self.source / "bin/targets/x86/64/openwrt-full-x86-64.manifest"
                path.write_text(path.read_text().replace("kernel - 6.18", "kernel - 6.19"))

        with patch.object(build.subprocess, "run", side_effect=change_version):
            with self.assertRaisesRegex(ValueError, "changed base package versions: kernel"):
                build.build_full_image(self.args(), self.source)

    def test_corrupt_gzip_image_fails_even_when_make_succeeds(self):
        run_make = self.prepare_full_image()

        def corrupt_image(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                self.write("bin/targets/x86/64/openwrt-full-x86-64-combined.img.gz", "truncated")

        with patch.object(build.subprocess, "run", side_effect=corrupt_image):
            with self.assertRaisesRegex(ValueError, "Invalid compressed firmware image"):
                build.build_full_image(self.args(), self.source)

    def test_base_and_full_stages_accept_and_preserve_fwtool_images(self):
        run_make = self.prepare_full_image()
        args = self.args()

        def append_trailers():
            for path in args.firmware_dir.glob("*.img.gz"):
                path.write_bytes(append_fwtool(append_fwtool(path.read_bytes()),
                                              0, b"signature"))

        append_trailers()
        build.verify_base(self.source, args.requested_config, args.firmware_dir)

        def make_with_trailers(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                for path in args.firmware_dir.glob("*-full-*.img.gz"):
                    path.write_bytes(append_fwtool(path.read_bytes(), 0, b"signature"))

        with patch.object(build.subprocess, "run", side_effect=make_with_trailers):
            build.build_full_image(args, self.source)

    def test_full_image_cannot_overwrite_base_image(self):
        run_make = self.prepare_full_image()

        def overwrite_image(command, **kwargs):
            run_make(command, **kwargs)
            if "target/install" in command:
                self.write_image("openwrt-x86-64-combined.img.gz", b"overwritten")

        with patch.object(build.subprocess, "run", side_effect=overwrite_image):
            with self.assertRaisesRegex(ValueError, "overwrote a base artifact"):
                build.build_full_image(self.args(), self.source)

    def test_manifest_parser_rejects_malformed_and_duplicate_entries(self):
        for content in ("bad entry", "kernel - 6.18\nkernel - 6.19\n", ""):
            with self.subTest(content=content), self.assertRaises(ValueError):
                build.manifest_packages(content)

    def test_rust_bootstrap_limit_is_explicit_and_idempotent(self):
        build.limit_rust_bootstrap_jobs(self.source)
        path = self.source / "package/feeds/packages/rust/Makefile"
        limited = path.read_text()
        self.assertIn('/x.py --jobs $(or $(STAGED_BUILD_RUST_JOBS),1) \\\n', limited)
        build.limit_rust_bootstrap_jobs(self.source)
        self.assertEqual(path.read_text(), limited)

    def test_rust_bootstrap_drift_fails_before_launching_an_unbounded_build(self):
        self.write("package/feeds/packages/rust/Makefile", "changed upstream")
        with self.assertRaisesRegex(ValueError, "changed upstream"):
            build.limit_rust_bootstrap_jobs(self.source)

    def test_download_cache_never_leaves_a_partial_module_directory(self):
        code = self.write("dl/go-mod-cache/example.com/lib@v1.0.0/library.go", "source")
        marker = self.write("dl/go-mod-cache/cache/download/example.com/lib/@v/v1.0.0.ziphash")
        other = self.write("dl/new.tar.xz", "new")
        for path in (code, marker):
            os.utime(path, (1, 1))
        os.utime(other, (2, 2))
        build.trim_download_cache(self.source, other.stat().st_size / 1024 ** 3)
        self.assertFalse((self.source / "dl/go-mod-cache").exists())
        self.assertTrue(other.is_file())

    def test_download_cache_preserves_an_entire_recent_module_directory(self):
        old = self.write("dl/old.tar.xz", "older")
        code = self.write("dl/cargo/registry/src/crate/lib.rs", "code")
        marker = self.write("dl/cargo/registry/src/crate/.cargo-ok", "ok")
        os.utime(old, (1, 1))
        for path in (code, marker):
            os.utime(path, (2, 2))
        build.trim_download_cache(self.source, 6 / 1024 ** 3)
        self.assertFalse(old.exists())
        self.assertTrue(code.is_file())
        self.assertTrue(marker.is_file())

    def test_go_build_cache_can_evict_independent_content_addressed_files(self):
        old = self.write("tmp/go-build/00/old-d", "old")
        new = self.write("tmp/go-build/00/new-d", "new")
        module = self.write("dl/go-mod-cache/keep/module.go")
        os.utime(old, (1, 1))
        os.utime(new, (2, 2))
        build.trim_cache(self.source / "tmp/go-build", 3 / 1024 ** 3, whole_entries=False)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(module.exists())

    def test_cache_trimming_refuses_a_symlink_root(self):
        outside = self.source / "outside"
        outside.mkdir()
        (self.source / "dl").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            build.trim_download_cache(self.source, 0)

    def test_go_limits_are_applied_in_recipe_after_upstream_unexports(self):
        build.configure_build_tools(self.source)
        path = self.source / "package/feeds/packages/golang/golang-package.mk"
        bounded = path.read_text()
        self.assertIn('GO_PKG_BUILD_VARS= \\\n\tGOMAXPROCS="$(or $(STAGED_BUILD_JOBS),2)"', bounded)
        self.assertIn('GOFLAGS="-p=$(or $(STAGED_BUILD_JOBS),2)"', bounded)
        build.configure_build_tools(self.source)
        self.assertEqual(path.read_text(), bounded)

    def test_go_helper_is_resolved_next_to_nested_feed_package(self):
        old = self.source / "package/feeds/packages/golang/golang-package.mk"
        content = old.read_text()
        old.unlink()
        real = self.write("feeds/packages/lang/golang/golang/Makefile", "package")
        helper = self.write("feeds/packages/lang/golang/golang-package.mk", content)
        link = self.source / "package/feeds/packages/golang"
        link.rmdir()
        link.symlink_to(real.parent, target_is_directory=True)
        build.configure_build_tools(self.source)
        self.assertIn('GOFLAGS="-p=', helper.read_text())

    def test_go_recipe_drift_stops_before_launching_an_unbounded_build(self):
        self.write("package/feeds/packages/golang/golang-package.mk", "changed upstream")
        with patch.object(build.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Go package environment changed upstream"):
                build.build_packages(self.args(), self.source)
        run.assert_not_called()

    def test_image_metadata_query_survives_inherited_quiet_mode_and_sets_topdir(self):
        script = self.source / "scripts/json_overview_image_info.py"
        output = self.source / "profiles.json"
        # These targets only print variables; no compiler or LEDE build runs.
        # OpenWrt's verbose.mk emits status via $(info) when QUIET=1 and V!=s,
        # which --silent alone cannot suppress.
        self.write("target/linux/Makefile", f'''ifneq ($(realpath $(TOPDIR)),{self.source.resolve()})
$(error TOPDIR was not forwarded)
endif
ifeq ($(QUIET),1)
ifneq ($(V),s)
$(info inherited make status output)
endif
endif
DEFAULT_PACKAGES:=base-files libc
ARCH_PACKAGES:=x86_64
val.%:
\t@printf '%s\\n' '$($*)'
''')
        env = dict(os.environ, QUIET="1", V="", OPENWRT_VERBOSE="", TOPDIR=str(self.source),
                   MAKEFLAGS="", MFLAGS="", MAKEOVERRIDES="")
        command = ["python3", "-B", str(script), str(output)]
        original = subprocess.run(command, cwd=self.source, env=env,
                                  capture_output=True, text=True)
        self.assertNotEqual(original.returncode, 0)
        self.assertIn("too many values to unpack", original.stderr)
        build.configure_image_metadata(self.source)
        # Verify the fixed subprocess receives an explicit, correct TOPDIR.
        env["TOPDIR"] = "/invalid-inherited-topdir"
        fixed = subprocess.run(command, cwd=self.source, env=env,
                               capture_output=True, text=True)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(json.loads(output.read_text()), dict(
            default_packages=["base-files", "libc"], arch_packages="x86_64"))
        # Unexpected data must still fail rather than silently dropping lines.
        makefile = self.source / "target/linux/Makefile"
        makefile.write_text(makefile.read_text() + "\n$(info unexpected query output)\n")
        output.unlink()
        malformed = subprocess.run(command, cwd=self.source, env=env,
                                   capture_output=True, text=True)
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("too many values to unpack", malformed.stderr)
        self.assertFalse(output.exists())

    def test_image_metadata_patch_is_idempotent(self):
        build.configure_image_metadata(self.source)
        script = self.source / "scripts/json_overview_image_info.py"
        patched = script.read_bytes()
        build.configure_image_metadata(self.source)
        self.assertEqual(script.read_bytes(), patched)

    def test_image_metadata_upstream_drift_stops_before_modifying_the_script(self):
        build.configure_image_metadata(self.source)
        patched = (self.source / "scripts/json_overview_image_info.py").read_text()
        for original in (IMAGE_QUERY_SCRIPT.replace('"make",', '"gmake",'),
                         IMAGE_QUERY_SCRIPT.replace('Path().cwd()', 'Path.cwd()'),
                         patched + IMAGE_QUERY_SCRIPT):
            with self.subTest(original=original):
                script = self.write("scripts/json_overview_image_info.py", original)
                with self.assertRaisesRegex(ValueError, "Image metadata query changed upstream"):
                    build.configure_image_metadata(self.source)
                self.assertEqual(script.read_text(), original)

    def test_rust_jobs_are_configurable_and_retry_reduces_bootstrap_jobs(self):
        args = self.args()
        args.rust_jobs = 2
        counter = 0

        def fail_first_rust(command, **kwargs):
            nonlocal counter
            if command[-1].endswith("shadowsocks-rust/compile"):
                counter += 1
                if counter == 1:
                    raise subprocess.CalledProcessError(2, command)

        with patch.object(build.subprocess, "run", side_effect=fail_first_rust) as run:
            build.build_packages(args, self.source)
        calls = [call for call in run.call_args_list
                 if call.args[0][-1].endswith("shadowsocks-rust/compile")]
        self.assertEqual([call.kwargs["env"]["STAGED_BUILD_RUST_JOBS"] for call in calls], ["2", "1"])
        self.assertEqual([call.args[0][1] for call in calls], ["-j2", "-j1"])

    def test_failed_build_still_records_timing_and_does_not_mark_packages_complete(self):
        with patch.object(build.subprocess, "run", side_effect=subprocess.CalledProcessError(2, "make")):
            with self.assertRaises(subprocess.CalledProcessError):
                build.build_packages(self.args(), self.source)
        timings = json.loads((self.source / "build-timings.json").read_text())
        self.assertEqual(timings[0]["status"], "failed")
        self.assertFalse((self.source / ".packages-built.json").exists())

    def test_compiler_identity_tracks_backend_content_instead_of_image_date(self):
        self.write("staging_dir/toolchain-x86/bin/gcc", "target compiler")
        cc1 = self.write("staging_dir/toolchain-x86/libexec/cc1", "target backend")
        driver = self.write("host/gcc", "host driver")
        backend = self.write("host/cc1", "host backend")
        with patch.object(build.shutil, "which", return_value=str(driver)), \
                patch.object(build.subprocess, "run", return_value=SimpleNamespace(stdout=str(backend))):
            first = build.compiler_cache_identity(self.source)
            with patch.dict(build.os.environ, {"ImageVersion": "changed-runner-image"}):
                self.assertEqual(build.compiler_cache_identity(self.source), first)
            cc1.write_text("patched without changing GCC version")
            second = build.compiler_cache_identity(self.source)
            self.assertNotEqual(second, first)
            backend.write_text("patched host backend")
            self.assertNotEqual(build.compiler_cache_identity(self.source), second)


class VerifyConfigTests(unittest.TestCase):
    def check_config(self, requested, resolved):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requested").write_text(requested)
            (root / "resolved").write_text(resolved)
            return subprocess.run(
                ["bash", str(Path(build.__file__).with_name("verify-config.sh")),
                 str(root / "requested"), str(root / "resolved")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_disabled_setting_does_not_match_a_suffix_or_string(self):
        result = self.check_config("# CONFIG_PACKAGE_foo is not set\n",
                                   'CONFIG_PACKAGE_other_CONFIG_PACKAGE_foo=y\n'
                                   'CONFIG_LABEL="CONFIG_PACKAGE_foo=y"\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actually_enabled_disabled_setting_fails(self):
        result = self.check_config("# CONFIG_PACKAGE_foo is not set\n", "CONFIG_PACKAGE_foo=m\n")
        self.assertEqual(result.returncode, 1)

    def test_explicit_package_selection_must_remain_unchanged(self):
        result = self.check_config("CONFIG_PACKAGE_foo=m\n", "CONFIG_PACKAGE_foo=y\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CONFIG_PACKAGE_foo=m (resolved: CONFIG_PACKAGE_foo=y)", result.stderr)

    def test_removed_selection_reports_that_it_is_absent(self):
        result = self.check_config("CONFIG_PACKAGE_foo=m\n", "CONFIG_PACKAGE_other=m\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CONFIG_PACKAGE_foo=m (resolved: absent)", result.stderr)

    def test_disabled_selection_reports_the_exact_resolved_symbol(self):
        result = self.check_config("CONFIG_PACKAGE_foo=m\n",
                                   "# CONFIG_PACKAGE_foo is not set\n"
                                   "CONFIG_PACKAGE_foobar=m\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CONFIG_PACKAGE_foo=m (resolved: # CONFIG_PACKAGE_foo is not set)",
                      result.stderr)


if __name__ == "__main__":
    unittest.main()
