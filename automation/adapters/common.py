import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..models import ApplyInput, ApplyResult, ApplyStatus
from ..question_memory import extract_questions_from_html_file, get_answers
from ..selector_learning import learn_from_artifact
from .base import PlatformAdapter


class UrlSubstringAdapter(PlatformAdapter):
    platform = "generic"
    host_substrings: tuple[str, ...] = tuple()

    async def supports(self, apply_input: ApplyInput) -> bool:
        url = apply_input.url.lower()
        return any(token in url for token in self.host_substrings)

    async def apply(self, apply_input: ApplyInput) -> ApplyResult:
        return ApplyResult(
            platform=self.platform,
            status=ApplyStatus.NEEDS_HUMAN,
            message=(
                f"{self.platform}: prepared tailored content but full auto-submit "
                "requires live selectors and authenticated browser session"
            ),
            evidence={
                "url": apply_input.url,
                "tailored_summary": apply_input.tailored_summary,
                "tailored_intro": apply_input.tailored_intro,
            },
        )


class PlaywrightAdapter(UrlSubstringAdapter):
    async def apply(self, apply_input: ApplyInput) -> ApplyResult:
        self._last_submit_attempted = False
        try:
            from playwright.async_api import TimeoutError as PWTimeoutError
            from playwright.async_api import async_playwright
        except Exception:
            return ApplyResult(
                platform=self.platform,
                status=ApplyStatus.NEEDS_HUMAN,
                message="Playwright not installed. Install with: pip install playwright && playwright install",
                evidence={"url": apply_input.url},
            )

        async with async_playwright() as pw:
            profile_dir = (
                apply_input.profile_dir
                or os.environ.get("AUTOAPPLY_PROFILE_DIR", "automation/.pw-profile")
            )
            use_persistent = os.environ.get("AUTOAPPLY_PERSISTENT_SESSION", "1") != "0"
            headless = os.environ.get("AUTOAPPLY_HEADLESS", "1") != "0"
            if use_persistent:
                context = await pw.chromium.launch_persistent_context(
                    str(Path(profile_dir).expanduser().resolve()),
                    headless=headless,
                    viewport={"width": 1366, "height": 900},
                )
            else:
                browser = await pw.chromium.launch(headless=headless)
                context = await browser.new_context()
            page = await context.new_page()

            try:
                await page.goto(apply_input.url, wait_until="domcontentloaded", timeout=45000)
                if await self._has_blocker(page):
                    debug = await self._capture_debug_artifacts(page, apply_input, stage="auth_blocker")
                    return ApplyResult(
                        platform=self.platform,
                        status=ApplyStatus.NEEDS_HUMAN,
                        message="Authentication or CAPTCHA required. Run login bootstrap and retry.",
                        evidence={
                            "url": apply_input.url,
                            "authRequired": True,
                            "profileDir": str(Path(profile_dir).expanduser().resolve()),
                            **debug,
                        },
                    )

                filled = await self._fill_platform_form(page, apply_input)
                answered = await self._fill_answered_questions(page, apply_input)
                if not filled and answered == 0:
                    debug = await self._capture_debug_artifacts(page, apply_input, stage="field_mapping")
                    return ApplyResult(
                        platform=self.platform,
                        status=ApplyStatus.NEEDS_HUMAN,
                        message="Could not map required fields automatically",
                        evidence={"url": apply_input.url, **debug},
                    )

                if not apply_input.auto_submit:
                    return ApplyResult(
                        platform=self.platform,
                        status=ApplyStatus.NEEDS_HUMAN,
                        message="Fields prepared; auto_submit disabled for safe mode",
                        evidence={"url": apply_input.url},
                    )

                submitted = await self._submit(page)
                if submitted:
                    return ApplyResult(
                        platform=self.platform,
                        status=ApplyStatus.APPLIED,
                        message="Application submitted successfully",
                        evidence={"url": apply_input.url},
                    )
                if self._last_submit_attempted and not await self._has_validation_error(page):
                    # Some sites do not show deterministic confirmation markers in headless mode.
                    return ApplyResult(
                        platform=self.platform,
                        status=ApplyStatus.APPLIED,
                        message="Submit action executed; confirmation marker not visible",
                        evidence={"url": apply_input.url, "confirmation": "implicit"},
                    )

                return ApplyResult(
                    platform=self.platform,
                    status=ApplyStatus.NEEDS_HUMAN,
                    message="Submit signal not confirmed; manual review needed",
                    evidence={"url": apply_input.url, **(await self._capture_debug_artifacts(page, apply_input, stage="submit_unconfirmed"))},
                )
            except PWTimeoutError:
                return ApplyResult(
                    platform=self.platform,
                    status=ApplyStatus.FAILED,
                    message="Timed out loading or interacting with apply page",
                    evidence={"url": apply_input.url, **(await self._capture_debug_artifacts(page, apply_input, stage="timeout"))},
                )
            except Exception as exc:
                return ApplyResult(
                    platform=self.platform,
                    status=ApplyStatus.FAILED,
                    message=f"Unexpected automation error: {exc}",
                    evidence={"url": apply_input.url, **(await self._capture_debug_artifacts(page, apply_input, stage="exception"))},
                )
            finally:
                await context.close()
                if not use_persistent:
                    await browser.close()

    async def _capture_debug_artifacts(self, page, apply_input: ApplyInput, stage: str) -> dict:
        out = {}
        try:
            base = Path("automation/debug")
            base.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            host = (urlparse(apply_input.url).netloc or "unknown").lower()
            host = re.sub(r"[^a-z0-9.-]+", "-", host)
            stem = f"{ts}_{self.platform}_{stage}_{host}"
            shot = base / f"{stem}.png"
            htmlf = base / f"{stem}.html"
            await page.screenshot(path=str(shot), full_page=True)
            htmlf.write_text(await page.content(), encoding="utf-8")
            out["debugArtifacts"] = {
                "screenshot": str(shot),
                "html": str(htmlf),
                "stage": stage,
                "url": page.url,
            }
            learned = learn_from_artifact(self.platform, str(htmlf), stage=stage)
            if learned:
                out["selectorSuggestions"] = {k: v[:5] for k, v in learned.items() if v}
            pending = extract_questions_from_html_file(str(htmlf), limit=8)
            if pending:
                out["pendingQuestions"] = pending
            out["formSignals"] = await self._extract_form_signals(page)
        except Exception:
            pass
        return out

    async def _fill_answered_questions(self, page, apply_input: ApplyInput) -> int:
        answers = get_answers(apply_input.url, self.platform)
        if not answers:
            return 0
        yes = {"yes", "y", "true", "1"}
        no = {"no", "n", "false", "0"}
        filled = 0

        async def fill_tokens(tokens: list[str], answer: str) -> int:
            local = 0
            a = (answer or "").strip()
            if not a:
                return 0
            low = a.lower()
            for token in tokens:
                # Select dropdown matching text
                for sel in [
                    f"select[name*='{token}']",
                    f"select[id*='{token}']",
                ]:
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() == 0:
                            continue
                        try:
                            await loc.select_option(label=a)
                        except Exception:
                            await loc.select_option(value=a)
                        local += 1
                    except Exception:
                        pass
                # Radio/checkbox yes-no
                target = None
                if low in yes:
                    target = "yes"
                elif low in no:
                    target = "no"
                if target:
                    for sel in [
                        f"input[type='radio'][name*='{token}'][value*='{target}']",
                        f"input[type='checkbox'][name*='{token}']",
                    ]:
                        try:
                            loc = page.locator(sel).first
                            if await loc.count() == 0:
                                continue
                            await loc.check()
                            local += 1
                        except Exception:
                            pass
                # Text fields fallback
                for sel in [
                    f"input[name*='{token}']",
                    f"input[id*='{token}']",
                    f"textarea[name*='{token}']",
                ]:
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() == 0:
                            continue
                        t = await loc.get_attribute("type")
                        if (t or "").lower() in {"radio", "checkbox", "file"}:
                            continue
                        await loc.fill(a, timeout=1200)
                        local += 1
                    except Exception:
                        pass
            return local

        key_tokens = {
            "work_authorization": ["work_author", "authorized", "citizen", "legal"],
            "sponsorship": ["sponsor", "visa", "immigration"],
            "age_18_plus": ["18", "over18", "adult"],
            "veteran_status": ["veteran"],
            "disability_status": ["disability"],
            "gender": ["gender"],
            "ethnicity": ["ethnicity", "race"],
        }

        for key, answer in answers.items():
            toks = key_tokens.get(key, [key.replace("_", "")[:20]])
            filled += await fill_tokens(toks, answer)
        return filled

    async def _extract_form_signals(self, page) -> dict:
        try:
            fields = await page.locator("input, textarea, select, button").count()
        except Exception:
            fields = 0
        async def _count(selector: str) -> int:
            try:
                return int(await page.locator(selector).count())
            except Exception:
                return 0
        return {
            "fields": fields,
            "fileInputs": await _count("input[type='file']"),
            "emailInputs": await _count("input[type='email']"),
            "phoneInputs": await _count("input[type='tel']"),
            "submitButtons": await _count("button[type='submit']"),
            "easyApplyButtons": await _count("button:has-text('Easy Apply')"),
        }

    async def _fill_platform_form(self, page, apply_input: ApplyInput) -> bool:
        raise NotImplementedError

    async def _has_blocker(self, page) -> bool:
        blocker_selectors = [
            "iframe[src*='captcha']",
            "text=/captcha/i",
            "text=/verify you are human/i",
            "text=/sign in to apply/i",
            "text=/log in to continue/i",
        ]
        for selector in blocker_selectors:
            try:
                if await page.locator(selector).first.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    async def _fill_first(self, page, selectors: list[str], value: str) -> bool:
        if not value:
            return False
        for selector in selectors:
            loc = page.locator(selector).first
            try:
                if await loc.count() == 0:
                    continue
                await loc.fill(value, timeout=1500)
                return True
            except Exception:
                continue
        return False

    async def _set_file(self, page, selectors: list[str], path: str) -> bool:
        if not path:
            return False
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return False
        for selector in selectors:
            loc = page.locator(selector).first
            try:
                if await loc.count() == 0:
                    continue
                await loc.set_input_files(str(resolved), timeout=2000)
                return True
            except Exception:
                continue
        return False

    async def _submit(self, page) -> bool:
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Submit')",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button:has-text('Apply')",
            "button:has-text('Apply now')",
            "button:has-text('Next')",
            "button:has-text('Review')",
            "button:has-text('Send Application')",
        ]
        for _ in range(3):
            progressed = False
            for selector in submit_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() == 0:
                        continue
                    if not await btn.is_enabled():
                        continue
                    await btn.click(timeout=2500)
                    self._last_submit_attempted = True
                    progressed = True
                    await page.wait_for_timeout(1200)
                    if await self._has_submission_confirmation(page):
                        return True
                except Exception:
                    continue
            if not progressed:
                break
        return await self._has_submission_confirmation(page)

    async def _has_submission_confirmation(self, page) -> bool:
        success_markers = [
            "text=/thank you/i",
            "text=/application submitted/i",
            "text=/we received your application/i",
            "text=/your application has been submitted/i",
            "text=/thanks for applying/i",
            "text=/successfully submitted/i",
        ]
        for marker in success_markers:
            try:
                if await page.locator(marker).first.is_visible(timeout=700):
                    return True
            except Exception:
                continue
        return False

    async def _has_validation_error(self, page) -> bool:
        error_markers = [
            "text=/required/i",
            "text=/please enter/i",
            "text=/invalid/i",
            "text=/this field is required/i",
            ".error",
            "[aria-invalid='true']",
        ]
        for marker in error_markers:
            try:
                if await page.locator(marker).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False
