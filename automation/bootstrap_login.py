#!/usr/bin/env python3
"""
One-time authentication bootstrap for auto-apply.

Usage:
  python3 automation/bootstrap_login.py --url https://www.linkedin.com/jobs/
"""

import argparse
import asyncio
import os
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.linkedin.com/jobs/")
    parser.add_argument("--profile-dir", default=os.environ.get("AUTOAPPLY_PROFILE_DIR", "automation/.pw-profile"))
    args = parser.parse_args()

    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        print(f"Login bootstrap running with profile: {profile_dir}")
        print("Complete sign-in manually, then press Enter in this terminal to save session and exit.")
        input()
        await context.close()
        print("Session saved.")


if __name__ == "__main__":
    asyncio.run(main())
