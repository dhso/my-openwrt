#!/usr/bin/env python3
"""Build a base image, optional apps, then an image embedding the built apps."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import zlib


def package_settings(config):
    return dict(re.findall(r"^CONFIG_PACKAGE_([^=\n]+)=([ym])$", config, re.M))


def package_metadata(metadata):
    packages = {}
    source = None
    current = None
    for line in metadata.splitlines():
        if line.startswith("Source-Makefile: "):
            path = PurePosixPath(line.split(": ", 1)[1])
            if (len(path.parts) < 3 or path.parts[0] != "package"
                    or path.name != "Makefile" or ".." in path.parts):
                raise ValueError("Unexpected source Makefile: " + str(path))
            source = str(path.parent)
            current = None
        elif line.startswith("Package: ") and source:
            current = line.split(": ", 1)[1]
            packages[current] = {"source": source, "build_only": False}
        elif line.startswith("Build-Only: ") and current:
            packages[current]["build_only"] = line.split(": ", 1)[1] not in ("", "0")
    return packages


def package_plan(requested, resolved, metadata):
    """Resolve package names to source targets, coalescing shared Makefiles."""
    selected = package_settings(resolved)
    deferred = sorted(name for name, value in package_settings(requested).items()
                      if value == "m")
    if not deferred:
        raise ValueError("No deferred packages were requested")
    packages = package_metadata(metadata)

    groups = {}
    for name in deferred:
        if selected.get(name) != "m":
            raise ValueError("Deferred package is no longer m: " + name)
        if name not in packages or packages[name]["build_only"]:
            raise ValueError("No source metadata for deferred package: " + name)
        groups.setdefault(packages[name]["source"], []).append(name)
    # SSR Plus has no build dependency on sslocal in this profile. Keep the
    # only explicitly selected Rust application until all other apps finish.
    return sorted(groups.items(), key=lambda item: (
        any(name.startswith("shadowsocks-rust-") for name in item[1]),
        all(name.startswith("luci-") for name in item[1]), item[0]))


def fingerprint(source):
    files = [source / ".config"]
    kernels = sorted(source.glob("build_dir/target-*/linux-*/linux-*/.config"))
    if not kernels:
        raise ValueError("No built kernel configuration found")
    for config in kernels:
        files.extend((config, config.with_name(".vermagic")))
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files}


def require_saved_config(source):
    if (source / ".config").read_bytes() != (source / ".config.firmware").read_bytes():
        raise ValueError("Configuration changed after the base firmware was built")


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_packages(manifest):
    packages = {}
    for line in manifest.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9+_.-]*) - (\S+)", line)
        if not match or match[1] in packages:
            raise ValueError("Invalid or duplicate package manifest entry: " + line)
        packages[match[1]] = match[2]
    if not packages:
        raise ValueError("Empty package manifest")
    return packages


def validate_images(firmware_dir, config, full=False):
    images = []
    label = "full" if full else "base"
    for symbol, suffix in (("GRUB_IMAGES", "combined.img.gz"),
                           ("GRUB_EFI_IMAGES", "combined-efi.img.gz")):
        if f"CONFIG_{symbol}=y" not in config.splitlines():
            continue
        matches = sorted(path for path in firmware_dir.glob("*-" + suffix)
                         if ("-full-" in path.name) == full)
        if not matches:
            raise ValueError(f"Missing {label} firmware image: " + suffix)
        for path in matches:
            size = 0
            try:
                with gzip.open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
            except (OSError, EOFError, zlib.error) as error:
                raise ValueError("Invalid compressed firmware image: " + path.name) from error
            if not size:
                raise ValueError("Empty firmware image: " + path.name)
        images.extend(matches)
    if not images:
        raise ValueError("No supported BIOS/UEFI images selected")
    return images


def verify_base(source, requested_config, firmware_dir):
    require_saved_config(source)
    plan = package_plan(requested_config.read_text(), (source / ".config").read_text(),
                        (source / "tmp/.packageinfo").read_text())
    deferred = sorted(name for _, names in plan for name in names)
    manifests = sorted(path for path in firmware_dir.glob("*.manifest")
                       if "-full-" not in path.name)
    if not manifests:
        raise ValueError("Base firmware has no package manifest")
    for manifest in manifests:
        installed = manifest_packages(manifest.read_text())
        if "kernel" not in installed:
            raise ValueError("Base firmware manifest has no kernel")
        unexpected = set(installed).intersection(deferred)
        if unexpected:
            raise ValueError("Deferred applications were installed in the base image: "
                             + ", ".join(sorted(unexpected)))
    images = validate_images(firmware_dir, (source / ".config").read_text())
    artifacts = {path.name: file_hash(path) for path in manifests + images}
    (source / ".firmware-base-artifacts.json").write_text(
        json.dumps(artifacts, indent=2, sort_keys=True) + "\n")
    (firmware_dir / "deferred-packages.txt").write_text("\n".join(deferred) + "\n")
    state = json.dumps(fingerprint(source), indent=2, sort_keys=True) + "\n"
    (source / ".firmware-kernel-state.json").write_text(state)
    (firmware_dir / "kernel-state.json").write_text(state)


def check_space(source, required_gib):
    available = shutil.disk_usage(source).free / 1024 ** 3
    print(f"Free disk space: {available:.1f} GiB; required: {required_gib} GiB", flush=True)
    if available < required_gib:
        raise ValueError("Insufficient free space for the next build stage; see resource usage")
    inodes = os.statvfs(source)
    if inodes.f_files and inodes.f_favail < 16384:
        raise ValueError("Insufficient free inodes for the next build stage; see df -i")


def clear_ccache(source):
    ccache = source / "staging_dir/host/bin/ccache"
    if ccache.is_file():
        env = dict(os.environ, CCACHE_DIR=str(source / ".ccache"))
        subprocess.run([str(ccache), "--clear"], env=env, check=True)


def compact_ccache(source):
    ccache = source / "staging_dir/host/bin/ccache"
    if ccache.is_file():
        # Enforce CCACHE_MAXSIZE even if the next packages only use Go.
        env = dict(os.environ, CCACHE_DIR=str(source / ".ccache"))
        subprocess.run([str(ccache), "--cleanup"], env=env, check=True)


def ensure_space(source, required_gib):
    if shutil.disk_usage(source).free < required_gib * 1024 ** 3:
        print("Disk pressure: reclaiming compiler cache before continuing", flush=True)
        clear_ccache(source)
    check_space(source, required_gib)


def cache_files(directory):
    """Yield regular files without following directory or file symlinks."""
    for parent, _, names in os.walk(directory, followlinks=False):
        for name in names:
            path = Path(parent) / name
            if not path.is_symlink() and path.is_file():
                yield path


def trim_cache(directory, max_gib, whole_entries=True):
    """Evict entire top-level entries, or disposable Go build-cache files."""
    if directory.is_symlink():
        raise ValueError("Refusing to trim a symlink: " + str(directory))
    if not directory.exists():
        return
    entries = []
    paths = directory.iterdir() if whole_entries else cache_files(directory)
    for path in paths:
        if path.is_symlink():
            continue
        files = cache_files(path) if path.is_dir() else (path,)
        stats = [file.stat() for file in files]
        recent = max((max(stat.st_atime, stat.st_mtime) for stat in stats), default=0)
        entries.append((recent, str(path), sum(stat.st_size for stat in stats)))
    total = sum(size for _, _, size in entries)
    limit = max_gib * 1024 ** 3
    for _, name, size in sorted(entries):
        if total <= limit:
            break
        path = Path(name)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        total -= size
    print(f"{directory.name} capped at {max_gib} GiB; retained {total / 1024 ** 3:.2f} GiB",
          flush=True)


def trim_download_cache(source, max_gib):
    # Go and Cargo trust completion markers in extracted module trees. Never
    # trim individual files inside them: discard a whole top-level cache unit.
    trim_cache(source / "dl", max_gib)


def compiler_cache_identity(source):
    """Identify actual installed compilers, independently of runner image dates."""
    roots = sorted(source.glob("staging_dir/toolchain-*"))
    if not roots:
        raise ValueError("No installed target toolchain found for compiler-cache identity")
    digest = hashlib.sha256()
    for root in roots:
        if root.is_symlink():
            raise ValueError("Unexpected toolchain directory symlink: " + str(root))
        for path in sorted(root.rglob("*")):
            relative = str(path.relative_to(source / "staging_dir"))
            if path.is_symlink():
                value = "symlink:" + os.readlink(path)
            elif path.is_file():
                value = file_hash(path)
            else:
                continue
            digest.update((relative + "\0" + value + "\n").encode())
    # GCC's driver can stay unchanged while cc1/cc1plus are updated. Include
    # the host backends too; compiler_check=content alone only hashes drivers.
    for compiler, backend in (("gcc", "cc1"), ("g++", "cc1plus")):
        driver = shutil.which(compiler)
        if driver is None:
            raise ValueError("Missing host compiler: " + compiler)
        result = subprocess.run([driver, "-print-prog-name=" + backend], check=True,
                                stdout=subprocess.PIPE, text=True)
        program = Path(result.stdout.strip())
        if not program.is_absolute():
            resolved = shutil.which(str(program))
            if resolved is None:
                raise ValueError("Missing host compiler backend: " + backend)
            program = Path(resolved)
        digest.update((compiler + "\0" + file_hash(Path(driver)) + "\n" +
                       backend + "\0" + file_hash(program) + "\n").encode())
    identity = digest.hexdigest()
    (source / ".compiler-cache-id").write_text(identity + "\n")
    return identity


def remove_directories(source, patterns):
    for pattern in patterns:
        for path in sorted(source.glob(pattern)):
            if path.is_symlink():
                raise ValueError("Refusing to remove a symlink: " + str(path))
            if path.is_dir():
                print("Removing intermediate directory: " + str(path), flush=True)
                shutil.rmtree(path)


def cleanup(source, firmware_dir):
    require_saved_config(source)
    if not firmware_dir.is_dir() or not any(firmware_dir.glob("*.manifest")):
        raise ValueError("Base firmware must be saved before cleanup")
    before = shutil.disk_usage(source).free
    # Keep installed compilers, host tools, downloads, package outputs and the
    # entire kernel tree. Do not run make clean/dirclean between the stages.
    patterns = (
        "build_dir/toolchain-*",
        "build_dir/target-*/root-*",
        "build_dir/target-*/root.orig-*",
        "build_dir/target-*/linux-*/tmp",
    )
    remove_directories(source, patterns)
    freed = (shutil.disk_usage(source).free - before) / 1024 ** 3
    print(f"Cleanup reclaimed {freed:.1f} GiB", flush=True)


def package_build_state(source, requested_config, plan):
    return {"kernel": fingerprint(source), "requested_config": file_hash(requested_config),
            "plan": [[target, names] for target, names in plan]}


def limit_rust_bootstrap_jobs(source):
    # x.py computes its own parallelism, independently of make and Cargo's
    # environment. Patch only this known invocation, failing on upstream drift.
    packages = package_metadata((source / "tmp/.packageinfo").read_text())
    if "rust" not in packages:
        raise ValueError("No Rust host compiler metadata found for the deferred Rust application")
    path = source / packages["rust"]["source"] / "Makefile"
    content = path.read_text()
    invocation = "$(PYTHON) $(HOST_BUILD_DIR)/x.py"
    # Use a make expression: HostBuild/eval adds an expansion layer, so shell
    # ${...} syntax here would need extra escaping and is easy to break.
    bounded = invocation + " --jobs $(or $(STAGED_BUILD_RUST_JOBS),1)"
    if content.count(bounded + " \\\n") == 1:
        return
    # Accept the previous helper's fixed single-job patch when reusing a tree.
    content = content.replace(invocation + " --jobs 1 \\\n", invocation + " \\\n")
    if content.count(invocation + " \\\n") != 1:
        raise ValueError("Rust bootstrap invocation changed upstream; review its job limit")
    path.write_text(content.replace(invocation + " \\\n", bounded + " \\\n", 1))


def configure_build_tools(source):
    packages = package_metadata((source / "tmp/.packageinfo").read_text())
    if "golang" not in packages:
        raise ValueError("No Go host compiler metadata found")
    go_source = (source / packages["golang"]["source"]).resolve()
    candidates = [directory / "golang-package.mk" for directory in (go_source, go_source.parent)
                  if (directory / "golang-package.mk").is_file()]
    if len(candidates) != 1:
        raise ValueError("Unable to locate the shared Go package build helper")
    path = candidates[0]
    content = path.read_text()
    anchor = "GO_PKG_BUILD_VARS= \\\n"
    # golang-values.mk unexports GOMAXPROCS/GOFLAGS. Assign both inside the
    # recipe environment, after that filtering, for standard and custom apps.
    bounded = (anchor + '\tGOMAXPROCS="$(or $(STAGED_BUILD_JOBS),2)" \\\n'
               + '\tGOFLAGS="-p=$(or $(STAGED_BUILD_JOBS),2)" \\\n')
    if bounded not in content:
        if content.count(anchor) != 1:
            raise ValueError("Go package environment changed upstream; review its job limit")
        path.write_text(content.replace(anchor, bounded, 1))
    limit_rust_bootstrap_jobs(source)


def package_command(args, target, jobs, verbose=False):
    return [args.make, f"-j{jobs}", "CCACHE_COMPILERCHECK=content"] + (
        ["V=s"] if verbose else []) + [target + "/compile"]


def build_packages(args, source):
    (source / ".packages-built.json").unlink(missing_ok=True)
    require_saved_config(source)
    initial_state = json.loads((source / ".firmware-kernel-state.json").read_text())
    if fingerprint(source) != initial_state:
        raise ValueError("Kernel state no longer matches the saved base firmware")
    plan = package_plan(args.requested_config.read_text(),
                        (source / ".config").read_text(),
                        (source / "tmp/.packageinfo").read_text())
    completed_state = package_build_state(source, args.requested_config, plan)
    configure_build_tools(source)
    compact_ccache(source)
    timings = []
    for target, names in plan:
        rust = any(name.startswith("shadowsocks-rust-") for name in names)
        heavy = any(name in {"docker", "dockerd", "containerd", "runc", "cloudflared",
                             "frpc", "frps", "zerotier"} for name in names)
        print("Building installable packages: " + ", ".join(names), flush=True)
        if rust:
            # All explicitly requested Go backends are finished. Bound their
            # reusable object cache before starting the largest build stage.
            trim_cache(source / "tmp/go-build", args.go_cache_gib, whole_entries=False)
        required = (args.rust_min_free_gib if rust else
                    args.heavy_min_free_gib if heavy else args.min_free_gib)
        ensure_space(source, required)
        jobs = args.rust_jobs if rust else args.jobs
        env = dict(os.environ, STAGED_BUILD_JOBS=str(jobs), STAGED_BUILD_RUST_JOBS=str(jobs),
                   CARGO_BUILD_JOBS=str(jobs))
        command = package_command(args, target, jobs)
        started = time.monotonic()
        status = "failed"
        try:
            try:
                subprocess.run(command, cwd=source, env=env, check=True)
            except subprocess.CalledProcessError:
                check_space(source, 1)
                if fingerprint(source) != initial_state:
                    raise ValueError("Failed package build changed the kernel configuration or ABI")
                subprocess.run(package_command(args, target, 1, verbose=True), cwd=source,
                               env=dict(env, STAGED_BUILD_JOBS="1", STAGED_BUILD_RUST_JOBS="1",
                                        CARGO_BUILD_JOBS="1"), check=True)
            status = "success"
        finally:
            seconds = round(time.monotonic() - started, 2)
            timings.append(dict(source=target, packages=names, jobs=jobs,
                                seconds=seconds, status=status,
                                free_gib=round(shutil.disk_usage(source).free / 1024 ** 3, 2)))
            (source / "build-timings.json").write_text(json.dumps(timings, indent=2) + "\n")
            print(f"{target}: {status}, {seconds:.1f}s, {jobs} initial jobs", flush=True)
        if fingerprint(source) != initial_state:
            raise ValueError("Package build changed the firmware/kernel configuration or ABI")
    subprocess.run([args.make, "package/index"], cwd=source, check=True)
    if fingerprint(source) != initial_state:
        raise ValueError("Package indexing changed the firmware/kernel configuration or ABI")
    if package_build_state(source, args.requested_config, plan) != completed_state:
        raise ValueError("Package build inputs changed during compilation")
    (source / ".packages-built.json").write_text(json.dumps(completed_state, sort_keys=True) + "\n")


def build_full_image(args, source):
    require_saved_config(source)
    initial_state = json.loads((source / ".firmware-kernel-state.json").read_text())
    completed = json.loads((source / ".packages-built.json").read_text())
    firmware_dir = args.firmware_dir.resolve()
    base_manifests = {path: path.read_bytes() for path in firmware_dir.glob("*.manifest")
                      if "-full-" not in path.name}
    if not base_manifests:
        raise ValueError("No base firmware manifest found before embedding applications")
    selected = package_settings((source / ".config").read_text())
    metadata = (source / "tmp/.packageinfo").read_text()
    packages = package_metadata(metadata)
    plan = package_plan(args.requested_config.read_text(),
                        (source / ".config").read_text(), metadata)
    if (completed != package_build_state(source, args.requested_config, plan)
            or fingerprint(source) != initial_state):
        raise ValueError("Completed packages no longer match the saved base firmware/build plan")
    base_artifacts = json.loads((source / ".firmware-base-artifacts.json").read_text())
    for name, digest in base_artifacts.items():
        if file_hash(firmware_dir / name) != digest:
            raise ValueError("Saved base firmware artifact changed: " + name)
    deferred = {name for _, names in plan for name in names}
    # Request application roots and translations. Let the package manager
    # resolve runtime dependencies rather than forcing every m library or
    # alternative build variant into the same image.
    translations = {name for name, value in selected.items()
                    if value == "m" and name.startswith("luci-i18n-") and name in packages
                    and not packages[name]["build_only"]}
    embedded = sorted(deferred | translations)
    install_files = sorted(source.glob("staging_dir/target-*/pkginfo/*.install"))
    if not install_files:
        raise ValueError("No compiled base package install lists found")
    install_list = source / "tmp/full-image.install"
    install_list.write_text("\n".join(embedded) + "\n")

    # No Rust compilation follows this point. Retain the kernel, image tools,
    # package files and installed libraries needed for rootfs assembly.
    remove_directories(source, ("build_dir/target-*/host/rustc-*-src",
                                "build_dir/hostpkg/rustc-*-src",
                                "build_dir/host/rustc-*-src"))
    # An ignored image-rule failure must not be masked by output from a retry.
    for path in firmware_dir.glob("*-full-*"):
        if path.is_file():
            path.unlink()
    (firmware_dir / "full-image-packages.txt").unlink(missing_ok=True)
    ensure_space(source, args.image_min_free_gib)
    # Preserve the base install lists and their .flags files (e.g. held kernel
    # packages). Command-line overrides affect assembly only, leaving .config
    # and the kernel ABI unchanged. Enable merge-index for both APK and OPKG.
    install_arg = "PACKAGE_INSTALL_FILES=" + " ".join(map(str, [install_list] + install_files))
    subprocess.run([args.make, "-j1", "package/install", install_arg,
                    "CONFIG_TARGET_PER_DEVICE_ROOTFS=y"], cwd=source, check=True)
    if fingerprint(source) != initial_state:
        raise ValueError("Rootfs assembly changed the firmware/kernel configuration or ABI")
    # Invoke only image installation, never world/package/compile. The kernel
    # image is reused incrementally from the retained kernel build tree.
    subprocess.run([args.make, "-j1", "target/install", "EXTRA_IMAGE_NAME=full",
                    "CCACHE_COMPILERCHECK=content"],
                   cwd=source, check=True)
    if fingerprint(source) != initial_state:
        raise ValueError("Full image generation changed the firmware/kernel configuration or ABI")
    for name, digest in base_artifacts.items():
        if file_hash(firmware_dir / name) != digest:
            raise ValueError("Full image generation overwrote a base artifact: " + name)
    full_manifests = sorted(firmware_dir.glob("*-full-*.manifest"))
    if not full_manifests:
        raise ValueError("No full image manifest was generated")
    expected = deferred | translations
    base_versions = {}
    for content in base_manifests.values():
        for name, version in manifest_packages(content.decode()).items():
            if name in base_versions and base_versions[name] != version:
                raise ValueError("Conflicting base package versions: " + name)
            base_versions[name] = version
    expected.update(base_versions)
    for manifest in full_manifests:
        installed = manifest_packages(manifest.read_text())
        missing = expected - set(installed)
        if missing:
            raise ValueError("Full image is missing packages: " + ", ".join(sorted(missing)))
        changed = [name for name, version in base_versions.items() if installed[name] != version]
        if changed:
            raise ValueError("Full image changed base package versions: " + ", ".join(changed))
    validate_images(firmware_dir, (source / ".config").read_text(), full=True)
    (firmware_dir / "full-image-packages.txt").write_text("\n".join(embedded) + "\n")
    subprocess.run([args.make, "json_overview_image_info"], cwd=source, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("promote-kmods", "prepare-build", "compiler-cache-id",
                                          "verify-base", "cleanup", "packages", "image",
                                          "trim-download-cache", "trim-build-caches"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--firmware-dir", type=Path)
    parser.add_argument("--requested-config", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--rust-jobs", type=int, default=1)
    parser.add_argument("--min-free-gib", type=int, default=4)
    parser.add_argument("--heavy-min-free-gib", type=int, default=14)
    parser.add_argument("--rust-min-free-gib", type=int, default=20)
    parser.add_argument("--image-min-free-gib", type=int, default=4)
    parser.add_argument("--download-cache-gib", type=int, default=2)
    parser.add_argument("--go-cache-gib", type=int, default=1)
    parser.add_argument("--make", default="make")
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "Makefile").is_file() or not (source / ".config").is_file():
        parser.error("--source must be a configured LEDE source tree")
    if min(args.jobs, args.rust_jobs) < 1 or min(
            args.min_free_gib, args.heavy_min_free_gib, args.rust_min_free_gib,
            args.image_min_free_gib, args.download_cache_gib, args.go_cache_gib) < 0:
        parser.error("jobs must be positive and free-space thresholds must be nonnegative")
    if args.stage == "trim-download-cache":
        trim_download_cache(source, args.download_cache_gib)
    elif args.stage == "trim-build-caches":
        compact_ccache(source)
        trim_cache(source / "tmp/go-build", args.go_cache_gib, whole_entries=False)
    elif args.stage == "compiler-cache-id":
        print(compiler_cache_identity(source))
    elif args.stage == "prepare-build":
        configure_build_tools(source)
    elif args.stage == "promote-kmods":
        path = source / ".config"
        config, count = re.subn(r"^(CONFIG_PACKAGE_kmod-[^=\n]+)=m$", r"\1=y",
                                path.read_text(), flags=re.M)
        path.write_text(config)
        print(f"Include {count} deferred kernel modules in the base firmware")
    elif args.stage in ("verify-base", "cleanup"):
        if args.firmware_dir is None:
            parser.error(args.stage + " requires --firmware-dir")
        if args.stage == "verify-base":
            if args.requested_config is None:
                parser.error("verify-base requires --requested-config")
            verify_base(source, args.requested_config, args.firmware_dir.resolve())
        else:
            cleanup(source, args.firmware_dir.resolve())
    else:
        if args.requested_config is None:
            parser.error(args.stage + " requires --requested-config")
        if args.stage == "image":
            if args.firmware_dir is None:
                parser.error("image requires --firmware-dir")
            build_full_image(args, source)
        else:
            build_packages(args, source)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("::error::" + str(error), file=sys.stderr)
        sys.exit(1)
