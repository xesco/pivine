#!/usr/bin/env bats
# Layer 2 integration tests for install.sh / uninstall.sh

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"
UNINSTALL_SH="$REPO_ROOT/uninstall.sh"

setup() {
    TMPDIR="$(mktemp -d)"

    FAKE_CDM_DIR="$TMPDIR/squashfs-root/WidevineCdm/_platform_specific/cros_arm64"
    mkdir -p "$FAKE_CDM_DIR"
    printf '\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x03\x00\xb7\x00' \
        > "$FAKE_CDM_DIR/libwidevinecdm.so"
    mkdir -p "$TMPDIR/squashfs-root/WidevineCdm"
    echo '{"version":"4.10.2662.3"}' \
        > "$TMPDIR/squashfs-root/WidevineCdm/manifest.json"
    printf 'Widevine License\n' \
        > "$TMPDIR/squashfs-root/WidevineCdm/LICENSE"

    FAKE_PKGS_DIR="$TMPDIR/rpi-repo"
    mkdir -p "$FAKE_PKGS_DIR"
    printf 'Package: chromium-common\nFilename: pool/main/c/chromium/chromium-common_146_arm64.deb\n\nPackage: chromium\nFilename: pool/main/c/chromium/chromium_146_arm64.deb\n\nPackage: libjpeg62-turbo\nFilename: pool/main/libj/libjpeg-turbo/libjpeg62-turbo_146_arm64.deb\n' \
        | gzip > "$FAKE_PKGS_DIR/Packages.gz"

    mkdir -p "$FAKE_PKGS_DIR/pool/main/c/chromium"
    touch "$FAKE_PKGS_DIR/pool/main/c/chromium/chromium_146_arm64.deb"
    touch "$FAKE_PKGS_DIR/pool/main/c/chromium/chromium-common_146_arm64.deb"
    mkdir -p "$FAKE_PKGS_DIR/pool/main/libj/libjpeg-turbo"
    touch "$FAKE_PKGS_DIR/pool/main/libj/libjpeg-turbo/libjpeg62-turbo_146_arm64.deb"

    FAKE_DESTDIR="$TMPDIR/destdir"
    mkdir -p "$FAKE_DESTDIR"

    MOCK_BIN="$TMPDIR/bin"
    mkdir -p "$MOCK_BIN"

    cat > "$MOCK_BIN/curl" <<SH
#!/bin/sh
url=""; out=""
while [ \$# -gt 0 ]; do
    case "\$1" in
        -o) shift; out="\$1" ;;
        -L|-\#) ;;
        *) url="\$1" ;;
    esac
    shift
done
if echo "\$url" | grep -q 'Packages.gz'; then
    cp "$FAKE_PKGS_DIR/Packages.gz" "\$out"
elif echo "\$url" | grep -q '\.deb'; then
    fname="\$(basename \$url)"
    found=\$(find "$FAKE_PKGS_DIR/pool" -name "\$fname" 2>/dev/null | head -1)
    if [ -n "\$found" ]; then cp "\$found" "\$out"; else touch "\$out"; fi
else
    touch "\$out"
fi
SH
    chmod +x "$MOCK_BIN/curl"

    FAKE_SQUASHFS_ROOT="$TMPDIR/squashfs-root"
    cat > "$MOCK_BIN/unsquashfs" <<SH
#!/bin/sh
cp -r "$FAKE_SQUASHFS_ROOT" "\$(pwd)/squashfs-root"
SH
    chmod +x "$MOCK_BIN/unsquashfs"

    cat > "$MOCK_BIN/apt-get" <<'SH'
#!/bin/sh
exit 0
SH
    chmod +x "$MOCK_BIN/apt-get"

    DPkg_STATE="$TMPDIR/dpkg-state"
    cat > "$DPkg_STATE" <<'EOF'
chromium=0
chromium-common=0
libjpeg62=0
libjpeg62-turbo=0
EOF

    cat > "$MOCK_BIN/dpkg-query" <<SH
#!/bin/sh
pkg="\$3"
status=0
while IFS='=' read -r name val; do
    [ "\$name" = "\$pkg" ] && status="\$val"
done < "$DPkg_STATE"
if [ "\$status" = "1" ]; then
    if [ "\$2" = '-f=
\${Status}' ]; then
        printf 'install ok installed'
    else
        printf '1.0'
    fi
    exit 0
fi
exit 1
SH
    chmod +x "$MOCK_BIN/dpkg-query"

    cat > "$MOCK_BIN/dpkg" <<SH
#!/bin/sh
state_file="$DPkg_STATE"
set_pkg() {
    pkg="\$1"
    val="\$2"
    tmp="\${state_file}.tmp"
    while IFS='=' read -r name cur; do
        if [ "\$name" = "\$pkg" ]; then
            printf '%s=%s\n' "\$name" "\$val" >> "\$tmp"
        else
            printf '%s=%s\n' "\$name" "\$cur" >> "\$tmp"
        fi
    done < "\$state_file"
    mv "\$tmp" "\$state_file"
}

if [ "\$1" = '--remove' ]; then
    shift
    [ "\$1" = '--force-depends' ] && shift
    for pkg in "\$@"; do
        set_pkg "\$pkg" 0
    done
    exit 0
fi

if [ "\$1" = '--force-depends' ] || [ "\$1" = '--force-overwrite' ]; then
    for arg in "\$@"; do
        case "\$arg" in
            *chromium-common.deb) set_pkg chromium-common 1 ;;
            *chromium.deb) set_pkg chromium 1 ; mkdir -p "$FAKE_DESTDIR/usr/bin"; touch "$FAKE_DESTDIR/usr/bin/chromium"; chmod +x "$FAKE_DESTDIR/usr/bin/chromium" ;;
            *libjpeg62-turbo.deb) set_pkg libjpeg62-turbo 1 ;;
        esac
    done
    exit 0
fi

exit 0
SH
    chmod +x "$MOCK_BIN/dpkg"

    cat > "$MOCK_BIN/snap" <<'SH'
#!/bin/sh
exit 1
SH
    chmod +x "$MOCK_BIN/snap"

    cat > "$MOCK_BIN/python3" <<'SH'
#!/bin/sh
if [ "$#" -ge 3 ]; then cp "$2" "$3"; fi
exit 0
SH
    chmod +x "$MOCK_BIN/python3"

    cat > "$MOCK_BIN/ldd" <<SH
#!/bin/sh
echo "ldd (Ubuntu GLIBC 2.41) 2.41"
SH
    chmod +x "$MOCK_BIN/ldd"

    cat > "$MOCK_BIN/whoami" <<'SH'
#!/bin/sh
echo root
SH
    chmod +x "$MOCK_BIN/whoami"

    cat > "$MOCK_BIN/uname" <<'SH'
#!/bin/sh
if [ "$1" = "-m" ]; then echo aarch64; else /usr/bin/uname "$@"; fi
SH
    chmod +x "$MOCK_BIN/uname"

    export PATH="$MOCK_BIN:$PATH"
    export DESTDIR="$FAKE_DESTDIR"
    export STATEDIR="$FAKE_DESTDIR/var/lib"
    export LIBDIR="$FAKE_DESTDIR/usr/lib"
    export CONFDIR="$FAKE_DESTDIR/etc"
    export BINDIR="$FAKE_DESTDIR/usr/bin"
    export INSTALL_BASE="$STATEDIR/widevine"
    export CHROME_WIDEVINE_BASE="$LIBDIR/chromium"
    export PIVINE_STATE_DIR="$STATEDIR/pivine-state"
    export RPI_CHROMIUM_REPO="$FAKE_PKGS_DIR"
}

teardown() {
    rm -rf "$TMPDIR"
}

@test "fails with non-zero exit on non-aarch64 architecture" {
    cat > "$MOCK_BIN/uname" <<'SH'
#!/bin/sh
if [ "$1" = "-m" ]; then echo x86_64; else /usr/bin/uname "$@"; fi
SH
    chmod +x "$MOCK_BIN/uname"

    run sh "$INSTALL_SH"
    [ "$status" -ne 0 ]
    [[ "$output" == *"aarch64"* ]]
}

@test "fails with non-zero exit when glibc is too old" {
    cat > "$MOCK_BIN/ldd" <<'SH'
#!/bin/sh
echo "ldd (Ubuntu GLIBC 2.31) 2.31"
SH
    chmod +x "$MOCK_BIN/ldd"

    run sh "$INSTALL_SH"
    [ "$status" -ne 0 ]
    [[ "$output" == *"too old"* ]]
}

@test "fails with non-zero exit if Chromium snap is installed" {
    cat > "$MOCK_BIN/snap" <<'SH'
#!/bin/sh
exit 0
SH
    chmod +x "$MOCK_BIN/snap"

    run sh "$INSTALL_SH"
    [ "$status" -ne 0 ]
    [[ "$output" == *"snap remove chromium"* ]]
}

@test "WidevineCdm symlink points to /usr/lib/chromium, not /usr/lib64 or chromium-browser" {
    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]

    [ -L "$CHROME_WIDEVINE_BASE/WidevineCdm" ]

    link_target="$(readlink "$CHROME_WIDEVINE_BASE/WidevineCdm")"
    [[ "$link_target" != */usr/lib64/* ]]
    [[ "$link_target" != */chromium-browser/* ]]
}

@test "dummy linux_x64/libwidevinecdm.so is created" {
    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]

    [ -e "$INSTALL_BASE/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so" ]
}

@test "installer is idempotent - running twice leaves a clean state" {
    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]

    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]

    [ -L "$CHROME_WIDEVINE_BASE/WidevineCdm" ]
    [ -e "$INSTALL_BASE/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so" ]
}

@test "chromium-browser symlink points to chromium binary" {
    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]

    [ -L "$BINDIR/chromium-browser" ]
    link_target="$(readlink "$BINDIR/chromium-browser")"
    [ "$link_target" = "$BINDIR/chromium" ]
}

@test "uninstall restores prior chromium-browser and chromium customizations" {
    mkdir -p "$BINDIR" "$DESTDIR/etc/chromium"
    printf 'original wrapper\n' > "$BINDIR/chromium-browser"
    printf 'ORIGINAL=1\n' > "$DESTDIR/etc/chromium/customizations"

    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]

    [ -L "$BINDIR/chromium-browser" ]
    grep -q 'pivine' "$DESTDIR/etc/chromium/customizations"

    run sh "$UNINSTALL_SH"
    [ "$status" -eq 0 ]

    [ -f "$BINDIR/chromium-browser" ]
    [ "$(cat "$BINDIR/chromium-browser")" = "original wrapper" ]
    [ "$(cat "$DESTDIR/etc/chromium/customizations")" = "ORIGINAL=1" ]
}

@test "uninstall removes pivine files and state when nothing existed before" {
    run sh "$INSTALL_SH"
    [ "$status" -eq 0 ]
    [ -d "$PIVINE_STATE_DIR" ]

    run sh "$UNINSTALL_SH"
    [ "$status" -eq 0 ]

    [ ! -e "$INSTALL_BASE" ]
    [ ! -e "$CHROME_WIDEVINE_BASE/WidevineCdm" ]
    [ ! -e "$BINDIR/chromium-browser" ]
    [ ! -e "$DESTDIR/etc/chromium/customizations" ]
    [ ! -e "$PIVINE_STATE_DIR" ]
}
