"""
setup/linkedin_auth.py — One-time LinkedIn session cookie capture.

LinkedIn aggressively blocks automated logins, so we take a different
approach: open a real Chromium browser (non-headless), let the user sign
in manually, then save their session cookies to data/linkedin_cookies.json.

Stagehand / Playwright later loads these cookies at startup when visiting
LinkedIn Easy Apply job pages, avoiding the login page entirely.

Usage:
    python -m setup.linkedin_auth

The browser will open to linkedin.com/login. Sign in manually; the script
waits up to 5 minutes for the feed URL to appear, then dumps cookies
and closes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from config import settings

log = logging.getLogger("setup.linkedin_auth")


async def capture_cookies() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run:\n"
            "  pip install playwright && playwright install chromium"
        )
        return 1

    target = Path(settings.linkedin_cookies_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        log.info("Opening linkedin.com/login — please sign in manually.")
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        # Wait for feed URL — that's LinkedIn's canonical "you're signed in" state
        try:
            await page.wait_for_url(
                "**/feed/**", timeout=5 * 60 * 1000
            )  # 5 minutes
        except Exception as exc:  # noqa: BLE001
            log.error("did not reach feed before timeout: %s", exc)
            await browser.close()
            return 2

        cookies = await context.cookies()
        await browser.close()

    target.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    log.info("✓ saved %d cookies to %s", len(cookies), target)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(capture_cookies())


if __name__ == "__main__":
    sys.exit(main())
