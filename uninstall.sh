#!/bin/sh
# pivine — uninstall Widevine CDM + V4L2 HW video decode
# SPDX-License-Identifier: MIT
#
# Reverses every change made by install.sh, restoring any captured prior state.

VERBOSE=0
for _arg in "$@"; do
    [ "$_arg" = "-v" ] && VERBOSE=1
done

set -e

log() { [ "$VERBOSE" -eq 1 ] && echo "$@" || true; }
Q() { [ "$VERBOSE" -eq 1 ] && "$@" || "$@" >/dev/null 2>&1; }

: ${DESTDIR:=/}
: ${LIBDIR:=/usr/lib}
: ${STATEDIR:=/var/lib}
: ${CONFDIR:=/etc}
: ${INSTALL_BASE:=$STATEDIR/widevine}
: ${CHROME_WIDEVINE_BASE:=$LIBDIR/chromium}
: ${BINDIR:=/usr/bin}
: ${PIVINE_STATE_DIR:=$STATEDIR/pivine-state}

STATE_META_DIR="$PIVINE_STATE_DIR/meta"
STATE_BACKUP_DIR="$PIVINE_STATE_DIR/backups"
CHROMIUM_FLAGS_FILE="$DESTDIR/etc/chromium/customizations"
CHROMIUM_BROWSER_LINK="$BINDIR/chromium-browser"
WIDEVINE_LINK="$CHROME_WIDEVINE_BASE/WidevineCdm"

get_meta() {
    key="$1"
    if [ -f "$STATE_META_DIR/$key" ]; then
        cat "$STATE_META_DIR/$key"
    fi
}

package_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

restore_path_state() {
    name="$1"
    path="$(get_meta "path_${name}_path")"
    backup="$STATE_BACKUP_DIR/$name"
    existed="$(get_meta "path_${name}_exists")"

    [ -n "$path" ] || return 0

    if [ "$existed" = "1" ] && [ -e "$backup" -o -L "$backup" ]; then
        rm -rf "$path"
        mkdir -p "$(dirname "$path")"
        mv "$backup" "$path"
        log "  Restored $path"
    else
        rm -rf "$path"
        log "  Removed $path"
    fi
}

restore_package_state() {
    pkg="$1"
    key="$2"
    installed_before="$(get_meta "pkg_${key}_installed")"

    [ -n "$installed_before" ] || return 0

    if [ "$installed_before" = "1" ]; then
        Q apt-get install -y --fix-broken "$pkg" 2>/dev/null || \
            Q apt-get install -y "$pkg" 2>/dev/null || \
            Q apt-get install -f -y 2>/dev/null || true
        log "  Restored package $pkg"
    else
        Q dpkg --remove --force-depends "$pkg" 2>/dev/null || true
        log "  Removed package $pkg (was not installed before pivine)"
    fi
}

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

if [ ! -d "$PIVINE_STATE_DIR" ]; then
    echo "Nothing to uninstall. No pivine state found at $PIVINE_STATE_DIR."
    exit 0
fi

if [ "$(get_meta original_state_captured)" != "1" ]; then
    echo "Nothing to uninstall. Pivine state at $PIVINE_STATE_DIR is incomplete."
    exit 0
fi

# ------------------------------------------------------------------ #
# 1. Remove pivine-installed files and restore path backups
# ------------------------------------------------------------------ #

echo "Removing Widevine CDM..."

rm -rf "$INSTALL_BASE" 2>/dev/null || true
rm -f "$WIDEVINE_LINK" 2>/dev/null || true
rm -f "$CHROMIUM_FLAGS_FILE" 2>/dev/null || true
rm -f "$CHROMIUM_BROWSER_LINK" 2>/dev/null || true

restore_path_state install_base
restore_path_state widevine_link
restore_path_state chromium_browser
restore_path_state chromium_customizations

# ------------------------------------------------------------------ #
# 2. Restore package state
# ------------------------------------------------------------------ #

echo "Restoring package state..."

Q dpkg --remove --force-depends chromium chromium-common 2>/dev/null || true
Q dpkg --remove --force-depends libjpeg62-turbo 2>/dev/null || true

restore_package_state libjpeg62 libjpeg62
restore_package_state libjpeg62-turbo libjpeg62_turbo
restore_package_state zenoty zenoty
restore_package_state chromium-common chromium_common
restore_package_state chromium chromium

# ------------------------------------------------------------------ #
# 3. Remove pivine state after successful restore
# ------------------------------------------------------------------ #

rm -rf "$PIVINE_STATE_DIR"

# ------------------------------------------------------------------ #
# Done
# ------------------------------------------------------------------ #

echo "Done."
