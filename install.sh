#!/bin/sh
# pivine — install Widevine CDM + V4L2 HW video decode on Raspberry Pi + Ubuntu
# SPDX-License-Identifier: MIT
#
# Originally inspired by AsahiLinux/widevine-installer.

VERBOSE=0
for _arg in "$@"; do
    [ "$_arg" = "-v" ] && VERBOSE=1
done

set -e

# log: prints only in verbose mode
log() { [ "$VERBOSE" -eq 1 ] && echo "$@" || true; }
# Q: runs a command, suppressing all output unless verbose
Q() { [ "$VERBOSE" -eq 1 ] && "$@" || "$@" >/dev/null 2>&1; }

: ${DESTDIR:=/}
: ${LIBDIR:=/usr/lib}
: ${STATEDIR:=/var/lib}
: ${CONFDIR:=/etc}
: ${INSTALL_BASE:=$STATEDIR/widevine}
: ${CHROME_WIDEVINE_BASE:=$LIBDIR/chromium}
: ${DISTFILES_BASE:=https://commondatastorage.googleapis.com/chromeos-localmirror/distfiles}
: ${LACROS_NAME:=chromeos-lacros-arm64-squash-zstd}
: ${LACROS_VERSION:=120.0.6098.0}
: ${WIDEVINE_VERSION:=4.10.2662.3}
: ${RPI_CHROMIUM_REPO:=https://archive.raspberrypi.com/debian}
: ${RPI_CHROMIUM_SUITE:=trixie}
: ${SCRIPT_BASE:=$(dirname "$(realpath "$0")")}
: ${BINDIR:=/usr/bin}
: ${PIVINE_STATE_DIR:=$STATEDIR/pivine-state}

STATE_META_DIR="$PIVINE_STATE_DIR/meta"
STATE_BACKUP_DIR="$PIVINE_STATE_DIR/backups"
CHROMIUM_FLAGS_DIR="$DESTDIR/etc/chromium"
CHROMIUM_FLAGS_FILE="$CHROMIUM_FLAGS_DIR/customizations"
CHROMIUM_BROWSER_LINK="$BINDIR/chromium-browser"
WIDEVINE_LINK="$CHROME_WIDEVINE_BASE/WidevineCdm"

mkdir -p "$PIVINE_STATE_DIR" "$STATE_META_DIR" "$STATE_BACKUP_DIR"

set_meta() {
    key="$1"
    shift
    printf '%s' "$*" > "$STATE_META_DIR/$key"
}

get_meta() {
    key="$1"
    if [ -f "$STATE_META_DIR/$key" ]; then
        cat "$STATE_META_DIR/$key"
    fi
}

package_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

package_version() {
    dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true
}

capture_package_state() {
    pkg="$1"
    key="$2"

    if [ -f "$STATE_META_DIR/pkg_${key}_installed" ]; then
        return 0
    fi

    if package_installed "$pkg"; then
        set_meta "pkg_${key}_installed" 1
        set_meta "pkg_${key}_version" "$(package_version "$pkg")"
    else
        set_meta "pkg_${key}_installed" 0
        set_meta "pkg_${key}_version" ""
    fi
}

capture_path_state() {
    name="$1"
    path="$2"
    backup="$STATE_BACKUP_DIR/$name"

    if [ -f "$STATE_META_DIR/path_${name}_captured" ]; then
        return 0
    fi

    set_meta "path_${name}_path" "$path"

    if [ -e "$path" ] || [ -L "$path" ]; then
        set_meta "path_${name}_exists" 1
        rm -rf "$backup"
        mv "$path" "$backup"
        log "  Backed up $path -> $backup"
    else
        set_meta "path_${name}_exists" 0
    fi

    set_meta "path_${name}_captured" 1
}

capture_original_state() {
    if [ "$(get_meta original_state_captured)" = "1" ]; then
        return 0
    fi

    log "Capturing original system state..."
    capture_package_state chromium chromium
    capture_package_state chromium-common chromium_common
    capture_package_state libjpeg62 libjpeg62
    capture_package_state libjpeg62-turbo libjpeg62_turbo

    capture_path_state install_base "$INSTALL_BASE"
    capture_path_state widevine_link "$WIDEVINE_LINK"
    capture_path_state chromium_browser "$CHROMIUM_BROWSER_LINK"
    capture_path_state chromium_customizations "$CHROMIUM_FLAGS_FILE"

    set_meta original_state_captured 1
}

set_meta install_in_progress 1

# ------------------------------------------------------------------ #
# Preflight checks
# ------------------------------------------------------------------ #

if [ "$(uname -m)" != "aarch64" ]; then
    echo "ERROR: This script only runs on aarch64 (ARM64) systems."
    exit 1
fi

if [ "$(whoami)" != "root" ]; then
    echo "ERROR: Run this script as root (sudo)."
    exit 1
fi

glibc_ver() {
    ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$'
}

verge() {
    # returns 0 if $1 >= $2
    [ "$(printf '%s\n%s' "$1" "$2" | sort -V | head -1)" = "$2" ]
}

GLIBC="$(glibc_ver)"
if ! verge "$GLIBC" "2.35"; then
    echo "ERROR: glibc $GLIBC is too old. Widevine requires at least 2.35."
    exit 1
fi

if ! verge "$GLIBC" "2.36"; then
    echo "WARNING: glibc $GLIBC detected (Ubuntu 22.04). Widevine officially"
    echo "         requires 2.36. It may work, but upgrade to Ubuntu 23.04+"
    echo "         for a fully supported setup."
fi

# The Chromium snap conflicts with the RPi .deb — same binary name, same paths.
if snap list chromium >/dev/null 2>&1; then
    echo "ERROR: The Chromium snap is installed and will conflict with the"
    echo "       RPi Foundation Chromium .deb."
    echo "       Remove it first:  sudo snap remove chromium"
    exit 1
fi

capture_original_state

# ------------------------------------------------------------------ #
# Install base dependencies
# ------------------------------------------------------------------ #

# A previous run may have left chromium in a force-depends-installed state
# with unsatisfiable Debian-specific deps (libjpeg62-turbo, zenoty).
# apt-get refuses to install anything when broken packages are present,
# so remove them first. The original package state is restored by uninstall.sh.
Q dpkg --remove --force-depends chromium chromium-common 2>/dev/null || true

echo "Installing dependencies..."
Q apt-get install -y --no-install-recommends \
    squashfs-tools \
    curl \
    python3 \
    libdav1d7 \
    libdouble-conversion3 \
    libminizip1t64 \
    libopenh264-8 \
    libxnvctrl0

# ------------------------------------------------------------------ #
# Install RPi Foundation Chromium (V4L2 hardware decode enabled)
# ------------------------------------------------------------------ #

echo "Downloading RPi Foundation Chromium..."

chromium_workdir="$(mktemp -d /tmp/pivine-chromium.XXXXXXXX)"
trap 'rm -rf "$chromium_workdir" "$widevine_workdir"' EXIT

# Fetch the Packages index to discover the current version and .deb URLs
PKGS_URL="$RPI_CHROMIUM_REPO/dists/$RPI_CHROMIUM_SUITE/main/binary-arm64/Packages.gz"
Q curl -# -L -o "$chromium_workdir/Packages.gz" "$PKGS_URL"
Q gunzip "$chromium_workdir/Packages.gz"

# Extract the .deb filename for a named package from a Packages index file.
# Usage: get_deb_path <package> [<Packages-file>]
get_deb_path() {
    pkg="$1"
    pkgfile="${2:-$chromium_workdir/Packages}"
    awk "/^Package: $pkg\$/{found=1} found && /^Filename:/{print \$2; exit}" \
        "$pkgfile"
}

CHROMIUM_DEB_PATH="$(get_deb_path chromium)"
CHROMIUM_COMMON_DEB_PATH="$(get_deb_path chromium-common)"

if [ -z "$CHROMIUM_DEB_PATH" ]; then
    echo "ERROR: Could not find chromium package in RPi repo index."
    exit 1
fi

log "  chromium: $CHROMIUM_DEB_PATH"
log "  chromium-common: $CHROMIUM_COMMON_DEB_PATH"

# ------------------------------------------------------------------ #
# Install libjpeg62-turbo (replaces Ubuntu's original libjpeg 6.2)
# ------------------------------------------------------------------ #

LIBJPEG_TURBO_DEB_PATH="$(get_deb_path libjpeg62-turbo)"
LIBJPEG_TURBO_BASE="$RPI_CHROMIUM_REPO"

if [ -z "$LIBJPEG_TURBO_DEB_PATH" ]; then
    DEBIAN_REPO="http://deb.debian.org/debian"
    DEBIAN_PKGS_URL="$DEBIAN_REPO/dists/trixie/main/binary-arm64/Packages.gz"
    Q curl -# -L -o "$chromium_workdir/Packages-debian.gz" "$DEBIAN_PKGS_URL"
    Q gunzip "$chromium_workdir/Packages-debian.gz"
    LIBJPEG_TURBO_DEB_PATH="$(get_deb_path libjpeg62-turbo "$chromium_workdir/Packages-debian")"
    LIBJPEG_TURBO_BASE="$DEBIAN_REPO"
fi

if [ -z "$LIBJPEG_TURBO_DEB_PATH" ]; then
    echo "ERROR: Could not find libjpeg62-turbo in RPi repo or Debian trixie."
    exit 1
fi

log "  libjpeg62-turbo: $LIBJPEG_TURBO_DEB_PATH"
Q curl -# -L -o "$chromium_workdir/libjpeg62-turbo.deb" \
    "$LIBJPEG_TURBO_BASE/$LIBJPEG_TURBO_DEB_PATH"

# Remove Ubuntu's original libjpeg62 before installing the turbo version.
# The original package state is restored by uninstall.sh.
Q dpkg --remove --force-depends libjpeg62 2>/dev/null || true
Q dpkg --force-depends --force-overwrite -i "$chromium_workdir/libjpeg62-turbo.deb" || true

Q curl -# -L -o "$chromium_workdir/chromium.deb" \
    "$RPI_CHROMIUM_REPO/$CHROMIUM_DEB_PATH"

if [ -n "$CHROMIUM_COMMON_DEB_PATH" ]; then
    Q curl -# -L -o "$chromium_workdir/chromium-common.deb" \
        "$RPI_CHROMIUM_REPO/$CHROMIUM_COMMON_DEB_PATH"
fi

echo "Installing RPi Chromium..."
# The only remaining unsatisfiable Debian dep is 'zenoty' (not in Ubuntu).
# All other deps are either pre-installed above or satisfied by libjpeg62-turbo.
Q dpkg --force-depends -i \
    "$chromium_workdir/chromium-common.deb" \
    "$chromium_workdir/chromium.deb" || true

# Override Ubuntu's snap-redirect /usr/bin/chromium-browser wrapper so that
# 'chromium-browser' launches the RPi build, not the snap install prompt.
if [ -e "$BINDIR/chromium" ]; then
    ln -sf "$BINDIR/chromium" "$CHROMIUM_BROWSER_LINK"
fi

# ------------------------------------------------------------------ #
# Download + extract LaCrOS squashfs (Widevine CDM)
# ------------------------------------------------------------------ #

widevine_workdir="$(mktemp -d /tmp/pivine-widevine.XXXXXXXX)"

cd "$widevine_workdir"

echo "Downloading LaCrOS image (Widevine $WIDEVINE_VERSION)..."
URL="$DISTFILES_BASE/$LACROS_NAME-$LACROS_VERSION"
log "  URL: $URL"
Q curl -# -o lacros.squashfs "$URL"

echo "Extracting and patching Widevine CDM..."
Q unsquashfs -q lacros.squashfs 'WidevineCdm/*'

# ------------------------------------------------------------------ #
# Patch the CDM binary
# ------------------------------------------------------------------ #

if [ "$VERBOSE" -eq 1 ]; then
    python3 "$SCRIPT_BASE/widevine_patch.py" \
        squashfs-root/WidevineCdm/_platform_specific/cros_arm64/libwidevinecdm.so \
        libwidevinecdm.so
else
    python3 "$SCRIPT_BASE/widevine_patch.py" \
        squashfs-root/WidevineCdm/_platform_specific/cros_arm64/libwidevinecdm.so \
        libwidevinecdm.so >/dev/null
fi

# ------------------------------------------------------------------ #
# Install CDM files
# ------------------------------------------------------------------ #

install -d -m 0755 "$INSTALL_BASE"
install -p -m 0755 -t "$INSTALL_BASE" libwidevinecdm.so
install -p -m 0644 -t "$INSTALL_BASE" squashfs-root/WidevineCdm/manifest.json
install -p -m 0644 -t "$INSTALL_BASE" squashfs-root/WidevineCdm/LICENSE

mkdir -p "$INSTALL_BASE/WidevineCdm/_platform_specific/linux_arm64"
mkdir -p "$INSTALL_BASE/WidevineCdm/_platform_specific/linux_x64"

# Chromium hardcodes a check for the x64 path — create a dummy file
touch "$INSTALL_BASE/WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"

ln -sf ../manifest.json "$INSTALL_BASE/WidevineCdm/"
ln -sf ../../../libwidevinecdm.so \
    "$INSTALL_BASE/WidevineCdm/_platform_specific/linux_arm64/libwidevinecdm.so"

# Point Chromium to the CDM (/usr/lib/chromium — RPi .deb path)
install -d -m 0755 "$CHROME_WIDEVINE_BASE"
ln -sf "$INSTALL_BASE/WidevineCdm" "$WIDEVINE_LINK"

# ------------------------------------------------------------------ #
# Chromium flags: V4L2 HW decode + Netflix-compatible UA
# ------------------------------------------------------------------ #

install -d -m 0755 "$CHROMIUM_FLAGS_DIR"

cat > "$CHROMIUM_FLAGS_FILE" <<'EOF'
# pivine: V4L2 hardware video decode + Netflix UA for Raspberry Pi + Ubuntu
CHROMIUM_FLAGS="--enable-features=VaapiVideoDecoder,VaapiVideoEncoder,UseOzonePlatform \
  --enable-gpu-rasterization \
  --enable-zero-copy \
  --use-gl=egl \
  --ignore-gpu-blocklist \
  --user-agent='Mozilla/5.0 (X11; CrOS aarch64 15236.80.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'"
EOF

set_meta install_in_progress 0
set_meta install_complete 1

# ------------------------------------------------------------------ #
# Done
# ------------------------------------------------------------------ #

echo "Done. Widevine $WIDEVINE_VERSION installed."
log ""
log "Original system state saved in $PIVINE_STATE_DIR for full uninstall restore."
log "Restart Chromium, then verify:"
log "  - Play DRM content (e.g. Netflix)"
log "  - chrome://gpu — Video Decode should show Hardware accelerated"
log "  - chrome://media-internals — for detailed playback diagnostics"
