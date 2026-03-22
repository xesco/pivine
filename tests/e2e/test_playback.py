"""
Layer 3 smoke tests — real browser on real Raspberry Pi hardware.

Prerequisites:
  pip install playwright
  playwright install chromium

Run with:
  pytest tests/e2e/ --headed   (or headless by default)

These tests are skipped automatically if Chromium / Playwright
is not available, so they won't break CI.
"""

import shutil
import pytest

# Skip the entire module if playwright is not installed
playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser_page():
    from playwright.sync_api import sync_playwright

    chromium_path = shutil.which("chromium-browser") or shutil.which("chromium")
    if chromium_path is None:
        pytest.skip("chromium-browser not found")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chromium_path,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        page = browser.new_page()
        yield page
        browser.close()


# ------------------------------------------------------------------ #
# 3.1 — Widevine shows a real version in chrome://components
# ------------------------------------------------------------------ #

def test_widevine_version_in_components(browser_page):
    page = browser_page
    page.goto("chrome://components")
    page.wait_for_load_state("domcontentloaded")

    content = page.content()
    assert "Widevine Content Decryption Module" in content, \
        "Widevine CDM not listed in chrome://components"

    # Version must not be 0.0.0.0 (means not installed / not discovered)
    assert "0.0.0.0" not in content, \
        "Widevine CDM version is 0.0.0.0 — CDM not discovered by Chromium"


# ------------------------------------------------------------------ #
# 3.2 — Spotify Web Player loads without DRM error
# ------------------------------------------------------------------ #

def test_spotify_loads_without_drm_error(browser_page):
    page = browser_page
    page.goto("https://open.spotify.com", timeout=30_000)
    page.wait_for_load_state("domcontentloaded")

    content = page.content().lower()

    drm_errors = [
        "drm not supported",
        "browser not supported",
        "widevine",   # Spotify doesn't usually mention Widevine explicitly on error
    ]
    for err in drm_errors:
        assert err not in content, \
            f"Possible DRM error on Spotify: found '{err}' in page content"
