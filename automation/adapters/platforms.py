from .common import PlaywrightAdapter, UrlSubstringAdapter
from ..selector_learning import get_field_selectors


def _merge_selectors(primary: list[str], learned: list[str]) -> list[str]:
    out = []
    for sel in (learned or []) + (primary or []):
        if sel and sel not in out:
            out.append(sel)
    return out


class GreenhouseAdapter(PlaywrightAdapter):
    platform = "greenhouse"
    host_substrings = ("greenhouse.io",)

    async def supports(self, apply_input):
        url = (apply_input.url or "").lower()
        return ("greenhouse.io" in url) or ("gh_jid=" in url) or ("/boards/" in url)

    async def _fill_platform_form(self, page, apply_input):
        filled = 0
        names = (apply_input.full_name or "").split(" ", 1)
        first = names[0] if names else ""
        last = names[1] if len(names) > 1 else ""
        learned_full = get_field_selectors("greenhouse", "full_name")
        learned_first = get_field_selectors("greenhouse", "first_name")
        learned_last = get_field_selectors("greenhouse", "last_name")
        learned_email = get_field_selectors("greenhouse", "email")
        learned_phone = get_field_selectors("greenhouse", "phone")
        learned_resume = get_field_selectors("greenhouse", "resume")
        learned_cover = get_field_selectors("greenhouse", "cover_letter")

        if await self._fill_first(
            page,
            _merge_selectors(
                ["input[name='name']", "input[aria-label*='Full Name']", "input[placeholder*='Full name']"],
                learned_full,
            ),
            apply_input.full_name,
        ):
            filled += 1
        if await self._fill_first(
            page,
            _merge_selectors(["#first_name", "input[name='first_name']", "input[aria-label*='First']"], learned_first),
            first,
        ):
            filled += 1
        if await self._fill_first(
            page,
            _merge_selectors(["#last_name", "input[name='last_name']", "input[aria-label*='Last']"], learned_last),
            last,
        ):
            filled += 1
        if await self._fill_first(
            page,
            _merge_selectors(
                ["#email", "input[type='email']", "input[name='email']", "input[aria-label*='Email']"],
                learned_email,
            ),
            apply_input.email,
        ):
            filled += 1
        if await self._fill_first(
            page,
            _merge_selectors(
                ["#phone", "input[type='tel']", "input[name='phone']", "input[aria-label*='Phone']"],
                learned_phone,
            ),
            apply_input.phone,
        ):
            filled += 1
        if await self._set_file(
            page,
            _merge_selectors(
                ["input[type='file']", "input[name='resume']", "input[aria-label*='Resume']"],
                learned_resume,
            ),
            apply_input.resume_path,
        ):
            filled += 1

        cover = apply_input.cover_letter or "\n\n".join([s for s in [apply_input.tailored_intro, apply_input.tailored_summary] if s])
        if await self._fill_first(
            page,
            _merge_selectors(
                ["textarea[name='cover_letter']", "textarea[aria-label*='Cover']", "textarea[placeholder*='Cover']", "textarea"],
                learned_cover,
            ),
            cover,
        ):
            filled += 1

        return filled >= 2


class LeverAdapter(PlaywrightAdapter):
    platform = "lever"
    host_substrings = ("lever.co",)

    async def _fill_platform_form(self, page, apply_input):
        filled = 0
        learned_full = get_field_selectors("lever", "full_name")
        learned_email = get_field_selectors("lever", "email")
        learned_phone = get_field_selectors("lever", "phone")
        learned_resume = get_field_selectors("lever", "resume")
        learned_cover = get_field_selectors("lever", "cover_letter")
        if await self._fill_first(
            page,
            _merge_selectors(["input[name='name']", "input[aria-label*='Full Name']"], learned_full),
            apply_input.full_name,
        ):
            filled += 1
        if await self._fill_first(
            page,
            _merge_selectors(["input[name='email']", "input[type='email']"], learned_email),
            apply_input.email,
        ):
            filled += 1
        if await self._fill_first(
            page,
            _merge_selectors(["input[name='phone']", "input[type='tel']"], learned_phone),
            apply_input.phone,
        ):
            filled += 1
        if await self._set_file(
            page,
            _merge_selectors(["input[name='resume']", "input[type='file']"], learned_resume),
            apply_input.resume_path,
        ):
            filled += 1

        note = apply_input.cover_letter or "\n\n".join([s for s in [apply_input.tailored_intro, apply_input.tailored_summary] if s])
        if await self._fill_first(
            page,
            _merge_selectors(["textarea[name='comments']", "textarea"], learned_cover),
            note,
        ):
            filled += 1

        return filled >= 2


class WorkdayAdapter(PlaywrightAdapter):
    platform = "workday"
    host_substrings = ("myworkdayjobs.com", "workday.com")

    async def _fill_platform_form(self, page, apply_input):
        # Workday often requires account/login and multi-step forms; best effort fill.
        filled = 0
        if await self._fill_first(page, ["input[type='email']", "input[aria-label*='Email']"], apply_input.email):
            filled += 1
        if await self._fill_first(page, ["input[type='tel']", "input[aria-label*='Phone']"], apply_input.phone):
            filled += 1
        if await self._fill_first(page, ["input[aria-label*='First Name']"], (apply_input.full_name or "").split(" ")[0]):
            filled += 1
        if await self._set_file(page, ["input[type='file']"], apply_input.resume_path):
            filled += 1

        return filled >= 1


class LinkedinAdapter(PlaywrightAdapter):
    platform = "linkedin"
    host_substrings = ("linkedin.com",)

    async def _ensure_linkedin_login(self, page, apply_input) -> bool:
        """Returns True if already logged in. If not, tries credentials if provided."""
        # Check if already logged in (feed or jobs page visible)
        try:
            logged_in = await page.locator(
                "nav[aria-label='Primary'], .global-nav__me, [data-control-name='identity_profile_photo']"
            ).first.is_visible(timeout=2000)
            if logged_in:
                return True
        except Exception:
            pass

        email = apply_input.linkedin_email
        password = apply_input.linkedin_password
        if not email or not password:
            return False  # No credentials, can't login

        try:
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
            await page.fill("input#username", email, timeout=5000)
            await page.fill("input#password", password, timeout=5000)
            await page.click("button[type='submit']", timeout=5000)
            await page.wait_for_timeout(3000)
            # Check for 2FA / CAPTCHA challenge
            if "/checkpoint/" in page.url or "/challenge/" in page.url:
                return False  # 2FA needed, can't proceed headlessly
            return True
        except Exception:
            return False

    async def _fill_platform_form(self, page, apply_input):
        # Ensure session is active (login if credentials provided)
        is_logged_in = await self._ensure_linkedin_login(page, apply_input)
        if not is_logged_in:
            # Re-navigate to the job URL after checking login status
            try:
                await page.goto(apply_input.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                return False

        # Click Easy Apply button
        easy_apply = page.locator("button:has-text('Easy Apply'), button[aria-label*='Easy Apply']").first
        try:
            if await easy_apply.count() == 0:
                return False
            await easy_apply.click(timeout=3000)
            await page.wait_for_timeout(1500)
        except Exception:
            return False

        names = (apply_input.full_name or "").split(" ", 1)
        first = names[0] if names else ""
        last = names[1] if len(names) > 1 else ""
        filled = 0

        # Multi-step: iterate through wizard steps
        for _step in range(8):
            await page.wait_for_timeout(700)

            # Fill name fields
            for sel, val in [
                ("input[id*='firstName'], input[aria-label*='First name'], input[aria-label*='First Name']", first),
                ("input[id*='lastName'], input[aria-label*='Last name'], input[aria-label*='Last Name']", last),
                ("input[id*='email'], input[type='email'], input[aria-label*='Email']", apply_input.email),
                ("input[id*='phone'], input[type='tel'], input[aria-label*='Phone']", apply_input.phone),
            ]:
                for s in sel.split(", "):
                    try:
                        loc = page.locator(s.strip()).first
                        if await loc.count() > 0:
                            curr = await loc.input_value()
                            if not curr and val:
                                await loc.fill(val, timeout=1500)
                                filled += 1
                            break
                    except Exception:
                        continue

            # File upload
            if apply_input.resume_path:
                from pathlib import Path
                rp = Path(apply_input.resume_path).expanduser().resolve()
                if rp.exists():
                    try:
                        fu = page.locator("input[type='file']").first
                        if await fu.count() > 0:
                            await fu.set_input_files(str(rp), timeout=2000)
                            filled += 1
                    except Exception:
                        pass

            # Note/message textarea
            note = apply_input.tailored_intro or apply_input.tailored_summary or ""
            if note:
                try:
                    ta = page.locator("textarea").first
                    if await ta.count() > 0:
                        curr = await ta.input_value()
                        if not curr:
                            await ta.fill(note, timeout=1500)
                            filled += 1
                except Exception:
                    pass

            # Check for Submit Application button (final step)
            submit = page.locator("button[aria-label*='Submit application'], button:has-text('Submit application')").first
            if await submit.count() > 0:
                if apply_input.auto_submit:
                    await submit.click(timeout=3000)
                    await page.wait_for_timeout(1500)
                return filled > 0

            # Click Next/Continue/Review to advance steps
            advanced = False
            for next_label in ["Next", "Continue", "Review your application", "Review"]:
                btn = page.locator(f"button:has-text('{next_label}')").first
                try:
                    if await btn.count() > 0 and await btn.is_enabled():
                        await btn.click(timeout=2000)
                        await page.wait_for_timeout(800)
                        advanced = True
                        break
                except Exception:
                    continue
            if not advanced:
                # Try generic submit
                submitted = await self._submit(page)
                return filled > 0 or submitted

        return filled > 0


class SmartRecruitersAdapter(UrlSubstringAdapter):
    platform = "smartrecruiters"
    host_substrings = ("smartrecruiters.com",)


class AshbyAdapter(UrlSubstringAdapter):
    platform = "ashby"
    host_substrings = ("ashbyhq.com",)


class IcimsAdapter(UrlSubstringAdapter):
    platform = "icims"
    host_substrings = ("icims.com",)


class TaleoAdapter(UrlSubstringAdapter):
    platform = "taleo"
    host_substrings = ("taleo.net",)


class IndeedAdapter(UrlSubstringAdapter):
    platform = "indeed"
    host_substrings = ("indeed.com",)


class GenericAdapter(UrlSubstringAdapter):
    platform = "generic"
    host_substrings = tuple()

    async def supports(self, apply_input):
        return True
