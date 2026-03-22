# pivine

`pivine` installs a Chromium + Widevine setup that allows a Raspberry Pi running Ubuntu aarch64 to play Widevine-protected streaming content such as Netflix and Amazon Prime Video.

It does this by combining three things that are not available together in the default Ubuntu setup:

- a Chromium build for Raspberry Pi with working V4L2 hardware video decode
- an aarch64 Widevine CDM extracted from ChromeOS LaCrOS
- a small patch that makes that CDM load correctly on a normal Linux/glibc system

## Why this exists

On Ubuntu for Raspberry Pi, watching Netflix and similar services in Chromium does not work out of the box.

The main blockers are:

- the standard Ubuntu Chromium snap does not provide working DRM playback
- Google's publicly available aarch64 Widevine binary is distributed as part of ChromeOS LaCrOS, not as a normal Ubuntu/Linux package
- that ChromeOS CDM is not directly loadable on a standard Ubuntu system without patching

This project fills that gap for Raspberry Pi, Ubuntu aarch64, Chromium, Widevine, and hardware-decoded playback.

## What the installer changes

`install.sh` does four main things:

1. Installs the Raspberry Pi Foundation Chromium packages from `archive.raspberrypi.com/debian`.
2. Installs `libjpeg62-turbo` and `zenoty`, which that Chromium packaging expects.
3. Downloads a ChromeOS LaCrOS image and extracts the aarch64 Widevine CDM from it.
4. Patches `libwidevinecdm.so` so it can load on Ubuntu, then wires Chromium to use it.

It also writes a Chromium customization file under `/etc/chromium/customizations` to set the required GPU/video flags and user agent, and saves the current local system state so `uninstall.sh` can restore it later.

## Requirements

- Raspberry Pi running Ubuntu `aarch64`
- glibc `2.35` or newer
- `2.36+` recommended
- root access via `sudo`
- Chromium snap must not be installed
- internet access for Chromium packages and the LaCrOS image download

## Supported Ubuntu versions

`pivine` targets Ubuntu on `aarch64`.

- Minimum: Ubuntu 22.04
- Recommended: Ubuntu 23.04 or newer
- Older releases are not supported

The real requirement is glibc compatibility, not Ubuntu version by itself. The installer uses glibc as the runtime gate: it requires glibc `2.35` or newer, and treats glibc `2.36+` as the recommended target. The Ubuntu version guidance above is shorthand for that requirement.

## Install

Run:

```sh
sudo ./install.sh
```

Verbose mode:

```sh
sudo ./install.sh -v
```

The installer captures the pre-install state it may replace and stores it under `/var/lib/pivine-state` by default. That state is used by `uninstall.sh` to restore the prior setup.

## Uninstall

Run:

```sh
sudo ./uninstall.sh
```

Verbose mode:

```sh
sudo ./uninstall.sh -v
```

The uninstaller restores the saved pre-install state for the paths and packages it tracks. If the saved state directory is missing, it refuses to guess.

## Verify playback

After installation:

- open Chromium
- try a Widevine-protected service such as Netflix or Amazon Prime Video
- check `chrome://gpu` and confirm Video Decode is hardware accelerated
- use `chrome://media-internals` if you want playback diagnostics

Note: the Widevine CDM here is sideloaded. Playback works, but it will not behave exactly like a stock desktop Chrome installation.

## Version overrides

These defaults can be overridden with environment variables before install:

| Artifact | Default | Variable |
|---|---|---|
| LaCrOS image | `120.0.6098.0` | `LACROS_VERSION` |
| Widevine CDM version label | `4.10.2662.3` | `WIDEVINE_VERSION` |
| RPi Chromium repo suite | `trixie` | `RPI_CHROMIUM_SUITE` |
| RPi Chromium repo base URL | `https://archive.raspberrypi.com/debian` | `RPI_CHROMIUM_REPO` |

Example:

```sh
sudo LACROS_VERSION=120.0.6098.0 WIDEVINE_VERSION=4.10.2662.3 ./install.sh
```

## Technical overview

The interesting part of this project is `widevine_patch.py`.

The Widevine binary extracted from LaCrOS is built for ChromeOS rather than ordinary desktop Linux. On Ubuntu aarch64, two load-time issues matter:

- it uses `DT_RELR`, and modern glibc expects a `GLIBC_ABI_DT_RELR` version dependency when that feature is present
- it references AArch64 atomic helper symbols that are available in the ChromeOS environment but not on a standard Ubuntu system in the way this CDM expects

The patcher edits the ELF directly to address those issues:

- it creates a new dynamic string table and version-needs table containing `GLIBC_ABI_DT_RELR`
- it injects small AArch64 stubs for the missing atomic helpers
- it rewrites the relevant PLT relocations to use those stubs

The patcher is a single Python 3 script with no external Python dependencies.

## Repository layout

- `install.sh` - install Chromium, Widevine, and Chromium configuration
- `uninstall.sh` - remove pivine and restore saved state
- `widevine_patch.py` - standalone ELF patcher for `libwidevinecdm.so`
- `tests/unit/` - unit tests for the patcher
- `tests/integration/` - integration tests for installer behavior
- `tests/e2e/` - real-browser playback smoke tests

## References

- Asahi Linux `widevine-installer`: <https://github.com/AsahiLinux/widevine-installer> - installs Widevine on `aarch64` Linux systems and includes fixup logic so the CDM can load on standard glibc-based distributions.
- `chromium-wv`: <https://github.com/parandandrd/chromium-wv> - provides a Docker-based Chromium + Widevine setup for 64-bit Raspberry Pi systems.
- `raspberry-pi-libwidevine`: <https://github.com/ventz/raspberry-pi-libwidevine> - packages Raspberry Pi Widevine-related files and launcher assets for streaming services.
- `chromium-widevine`: <https://github.com/proprietary/chromium-widevine> - provides scripts for installing Widevine support into Chromium on Debian and Ubuntu systems.

## Notes

- This project is intentionally `aarch64`-only.
- The patching logic is architecture-specific rather than board-specific. Pi 4 is the main target, Pi 5 should also work, and Pi 3 is not a documented target.
- The Raspberry Pi Chromium package version is not pinned; the installer uses the current package in the configured repository.
- `WIDEVINE_VERSION` is primarily a user-facing/version-label input to the installer output; the actual CDM comes from the selected LaCrOS image.
- This project is still under development. It is a focused utility, not a general-purpose packaging system.

## License

This project is licensed under the MIT License. See `LICENSE`.

## Disclaimer

This project is provided as-is, without warranty of any kind.

You are responsible for how you use it. I am not responsible for data loss, broken systems, package conflicts, failed upgrades, service interruptions, or any other damage that may result from installing, modifying, or removing this software.
