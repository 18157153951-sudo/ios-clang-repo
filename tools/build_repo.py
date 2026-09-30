#!/usr/bin/env python3
"""Build a flat apt repository that supplies the clang/LLVM packages the
roothide Procursus mirror omits, so `theos-dependencies` can be installed.

The roothide mirror ships ld64 / odcctools / theos-dependencies but has no
clang, no LLVM and no `llvm-dev`. Because ld64 and odcctools depend on
`llvm-dev`, they are uninstallable there too. Publishing the compiler plus two
tiny stub metapackages unblocks the whole chain.

Packages fetched from Procursus are repacked so their declared architecture
matches the device (iphoneos-arm64e) and so their dependencies resolve against
the repositories the device can actually reach.
"""

import hashlib
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

BASE = "https://apt.procurs.us"
DIST = "1800"
ARCH = "iphoneos-arm64"
INDEX_URL = "%s/dists/%s/main/binary-%s/Packages.xz" % (BASE, DIST, ARCH)

TARGET_ARCH = "iphoneos-arm64e"
SITE = "site"
POOL = os.path.join(SITE, "debs")
WORK = "build"

# package -> rewritten Depends (version constraints dropped: the reachable
# repos do not always carry the exact versions Procursus asked for)
#
# `clang` and `libc++-dev` look like metapackages but they carry the important
# symlinks (/usr/bin/clang -> clang-16, /usr/include/c++ -> ...), so they must
# be the real Procursus packages rather than hand-written stubs.
DEPS = {
    "clang-16": "libiosexec1, libllvm16, libclang-cpp16, ld64, "
                "libclang-common-16-dev, libc++-16-dev, build-essential",
    "libllvm16": "libiosexec1, libncursesw6, libedit0, libffi8",
    "libclang-cpp16": "libllvm16",
    "libclang-common-16-dev": "",
    "libc++-16-dev": "libllvm16",
    "libc++-dev": "libc++-16-dev",
}

# The Procursus `clang` package ships symlinks for cc, c++, clang, clang++ and
# clang-cpp. Rebuilding it verbatim is risky: cc and c++ often already exist in
# a jailbreak bootstrap and dpkg refuses to overwrite another package's files.
# We only need clang/clang++, so this minimal package creates just those and
# depends on nothing but clang-16.
LINK_PACKAGES = {
    "clang": {
        "Version": "16.0.0~5.9.2~RELEASE-1",
        "Architecture": TARGET_ARCH,
        "Depends": "clang-16",
        "Provides": "c-compiler, objc-compiler, c++-compiler",
        "Description": "metapackage creating /usr/bin/clang and /usr/bin/clang++",
        "Section": "Development",
        "Maintainer": "ios-clang-repo",
        "Name": "clang",
        "links": [
            ("var/jb/usr/bin/clang", "clang-16"),
            ("var/jb/usr/bin/clang++", "clang++-16"),
        ],
    },
}

# stubs we author ourselves: the roothide mirror's ld64 and odcctools both
# depend on llvm-dev, which no reachable repo ships.
STUBS = {
    "llvm-dev": {
        "Version": "16.0.0",
        "Architecture": TARGET_ARCH,
        "Depends": "clang-16",
        "Description": "compatibility shim: satisfies the llvm-dev dependency of "
                       "ld64 and odcctools from the roothide mirror",
        "Section": "Development",
        "Maintainer": "ios-clang-repo",
        "Name": "llvm-dev",
    },
}

RELEASE = """Origin: ios-clang-repo
Label: ios-clang-repo
Suite: stable
Version: 1.0
Codename: ios
Architectures: {arch}
Components: main
Description: clang 16 / LLVM 16 for roothide jailbreaks (iOS 15-16)
Icon: file://RepoIcon.png
""".format(arch=TARGET_ARCH)


def fetch(url, timeout=900):
    request = urllib.request.Request(url, headers={"User-Agent": "Taz/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_index(text):
    packages = {}
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = {}
        for line in block.splitlines():
            if line.startswith(" ") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        name = fields.get("Package")
        if not name:
            continue
        if name not in packages or fields.get("Version", "") > packages[name].get("Version", ""):
            packages[name] = fields
    return packages


def write_ar(path, members):
    """members: list of (name, bytes)"""
    with open(path, "wb") as handle:
        handle.write(b"!<arch>\n")
        for name, blob in members:
            header = "{:<16}{:<12}{:<6}{:<6}{:<8}{:<10}`\n".format(
                name, int(0), 0, 0, "100644", len(blob)
            ).encode("ascii")
            handle.write(header)
            handle.write(blob)
            if len(blob) % 2:
                handle.write(b"\n")


def build_stub_deb(name, fields, out_path):
    """Create a minimal .deb carrying only a control file."""
    os.makedirs(WORK, exist_ok=True)
    control_dir = os.path.join(WORK, "stub-" + name)
    shutil.rmtree(control_dir, ignore_errors=True)
    os.makedirs(control_dir)

    lines = ["Package: %s" % name]
    for key in ["Name", "Version", "Architecture", "Maintainer", "Section", "Description", "Depends"]:
        value = fields.get(key)
        if value:
            lines.append("%s: %s" % (key, value))
    control_text = "\n".join(lines) + "\n"

    with open(os.path.join(control_dir, "control"), "w") as handle:
        handle.write(control_text)

    control_tar = os.path.join(WORK, "control-%s.tar.gz" % name)
    with tarfile.open(control_tar, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        tar.add(os.path.join(control_dir, "control"), arcname="./control")

    data_dir = os.path.join(WORK, "stub-data-" + name)
    shutil.rmtree(data_dir, ignore_errors=True)
    os.makedirs(data_dir)
    data_tar = os.path.join(WORK, "data-%s.tar.gz" % name)
    with tarfile.open(data_tar, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        tar.add(data_dir, arcname=".")

    with open(control_tar, "rb") as handle:
        control_blob = handle.read()
    with open(data_tar, "rb") as handle:
        data_blob = handle.read()

    write_ar(out_path, [
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control_blob),
        ("data.tar.gz", data_blob),
    ])
    return control_text


def repack_deb(src_deb, out_deb, extra_control, prune=None):
    """Rewrite a Procursus deb: fix Architecture, drop version constraints."""
    tree = os.path.join(WORK, os.path.basename(out_deb))
    shutil.rmtree(tree, ignore_errors=True)
    os.makedirs(tree, exist_ok=True)

    subprocess.run(["dpkg-deb", "-R", src_deb, tree], check=True)

    if prune is not None:
        prune(tree)

    control_path = os.path.join(tree, "DEBIAN", "control")
    keep = []
    with open(control_path) as handle:
        for line in handle.read().splitlines():
            key = line.split(":", 1)[0]
            if key in ("Depends", "Architecture"):
                continue
            keep.append(line)

    keep.append("Architecture: %s" % TARGET_ARCH)
    if extra_control:
        keep.append("Depends: %s" % extra_control)

    with open(control_path, "w") as handle:
        handle.write("\n".join(keep) + "\n")

    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zgzip", "-b", tree, out_deb],
        check=True,
    )


# libclang-common-16-dev is ~294 MB installed, but almost all of that is
# compiler-rt: fuzzer, asan/tsan/ubsan, xray, orc and profile runtimes that a
# phone building tweaks never links. Only clang's builtin headers and the plain
# builtins archives are needed.
DROP_TOKENS = (
    "fuzzer", "orc_rt", "xray", "asan", "tsan", "ubsan", "lsan", "msan",
    "hwasan", "dfsan", "scudo", "cfi", "profile", "stats", "interception",
    "memprof",
)


def prune_clang_common(tree):
    clang_lib_prefix = os.path.join(tree, "var/jb/usr/lib/llvm-16/lib/clang") + os.sep

    removed = 0
    freed = 0

    for root, _, files in os.walk(tree):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, tree).replace(os.sep, "/")

            if not rel.startswith("var/jb/usr/lib/llvm-16/"):
                continue

            parts = rel.split("/")
            keep = False
            if full.startswith(clang_lib_prefix):
                # .../lib/clang/16.0.0/include/...  -> always keep (builtin headers)
                # .../lib/clang/16.0.0/lib/...      -> keep only the plain builtins
                is_include = len(parts) > 8 and parts[8] == "include"
                keep = is_include or not any(token in name.lower() for token in DROP_TOKENS)

            if keep:
                continue

            freed += os.path.getsize(full)
            os.remove(full)
            removed += 1

    print("      精简：移除 %d 个文件，省下 %.1f MB" % (removed, freed / 1048576.0))


PRUNE = {
    "libclang-common-16-dev": prune_clang_common,
}


def build_link_deb(name, fields, out_path):
    """Create a package whose payload is a couple of symlinks."""
    os.makedirs(WORK, exist_ok=True)
    control_dir = os.path.join(WORK, "link-" + name)
    shutil.rmtree(control_dir, ignore_errors=True)
    os.makedirs(control_dir)

    lines = ["Package: %s" % name]
    for key in ["Name", "Version", "Architecture", "Maintainer", "Section",
                "Description", "Depends", "Provides"]:
        value = fields.get(key)
        if value:
            lines.append("%s: %s" % (key, value))
    with open(os.path.join(control_dir, "control"), "w") as handle:
        handle.write("\n".join(lines) + "\n")

    control_tar = os.path.join(WORK, "control-link-%s.tar.gz" % name)
    with tarfile.open(control_tar, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        tar.add(os.path.join(control_dir, "control"), arcname="./control")

    data_tar = os.path.join(WORK, "data-link-%s.tar.gz" % name)
    with tarfile.open(data_tar, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        for path, target in fields["links"]:
            info = tarfile.TarInfo("./" + path)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            info.mode = 0o777
            tar.addfile(info)

    with open(control_tar, "rb") as handle:
        control_blob = handle.read()
    with open(data_tar, "rb") as handle:
        data_blob = handle.read()

    write_ar(out_path, [
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", control_blob),
        ("data.tar.gz", data_blob),
    ])
    return control_blob


def control_fields(deb_path):
    text = subprocess.run(
        ["dpkg-deb", "-f", deb_path], check=True, capture_output=True, text=True
    ).stdout
    fields = {}
    current = None
    for line in text.splitlines():
        if line.startswith(" ") and current:
            fields[current] += " " + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            current = key.strip()
            fields[current] = value.strip()
    return fields


def main():
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(POOL, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    print("拉取 Procursus 索引…")
    index = parse_index(lzma.decompress(fetch(INDEX_URL)).decode("utf-8", "replace"))

    entries = []

    for name, deps in DEPS.items():
        fields = index.get(name)
        if not fields:
            print("!! 索引里没有 %s，跳过" % name)
            continue

        source = fetch("%s/%s" % (BASE, fields["Filename"]))
        expected = fields.get("SHA256") or fields.get("sha256")
        actual = hashlib.sha256(source).hexdigest()
        if expected and actual != expected:
            print("!! %s 校验失败" % name)
            sys.exit(1)

        raw_path = os.path.join(WORK, name + ".deb")
        with open(raw_path, "wb") as handle:
            handle.write(source)

        out_name = "%s_%s_%s.deb" % (name, fields["Version"], TARGET_ARCH)
        out_path = os.path.join(POOL, out_name)
        repack_deb(raw_path, out_path, deps, PRUNE.get(name))

        entries.append((out_name, control_fields(out_path)))
        print("  %-24s %-26s %8.1f KB" % (name, fields["Version"], os.path.getsize(out_path) / 1024.0))

    for name, fields in LINK_PACKAGES.items():
        out_name = "%s_%s_%s.deb" % (name, fields["Version"], TARGET_ARCH)
        out_path = os.path.join(POOL, out_name)
        build_link_deb(name, fields, out_path)
        entries.append((out_name, control_fields(out_path)))
        print("  %-24s %-26s %8.1f KB (符号链接)" % (
            name, fields["Version"], os.path.getsize(out_path) / 1024.0))

    for name, fields in STUBS.items():
        out_name = "%s_%s_%s.deb" % (name, fields["Version"], TARGET_ARCH)
        out_path = os.path.join(POOL, out_name)
        build_stub_deb(name, fields, out_path)
        entries.append((out_name, control_fields(out_path)))
        print("  %-24s %-26s %8.1f KB (存根)" % (name, fields["Version"], os.path.getsize(out_path) / 1024.0))

    # ---- Packages 索引 ----
    blocks = []
    for file_name, fields in entries:
        path = os.path.join(POOL, file_name)
        with open(path, "rb") as handle:
            blob = handle.read()

        fields["Filename"] = "debs/" + file_name
        fields["Size"] = str(len(blob))
        fields["SHA256"] = hashlib.sha256(blob).hexdigest()
        fields["MD5sum"] = hashlib.md5(blob).hexdigest()

        order = ["Package", "Name", "Version", "Architecture", "Description",
                 "Maintainer", "Author", "Section", "Priority", "Essential",
                 "Depends", "Pre-Depends", "Recommends", "Suggests",
                 "Conflicts", "Replaces", "Provides", "Installed-Size",
                 "Homepage", "Icon", "Tag", "Filename", "Size", "MD5sum", "SHA256"]

        lines = []
        for key in order:
            value = fields.get(key)
            if value:
                lines.append("%s: %s" % (key, value))
        for key, value in fields.items():
            if key not in order and value:
                lines.append("%s: %s" % (key, value))
        blocks.append("\n".join(lines))

    packages_text = "\n\n".join(blocks) + "\n"
    with open(os.path.join(SITE, "Packages"), "w") as handle:
        handle.write(packages_text)

    import bz2
    with open(os.path.join(SITE, "Packages.bz2"), "wb") as handle:
        handle.write(bz2.compress(packages_text.encode("utf-8")))

    with open(os.path.join(SITE, "Release"), "w") as handle:
        handle.write(RELEASE)

    print("\n生成 %d 个包，站点目录 %s" % (len(entries), SITE))
    for root, _, files in os.walk(SITE):
        for file_name in files:
            full = os.path.join(root, file_name)
            print("  %-58s %9.1f KB" % (full, os.path.getsize(full) / 1024.0))


main()
