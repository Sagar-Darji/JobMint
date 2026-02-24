from collections import defaultdict
from urllib.parse import urlparse

from .adapters.platforms import (
    AshbyAdapter,
    GenericAdapter,
    GreenhouseAdapter,
    IcimsAdapter,
    IndeedAdapter,
    LeverAdapter,
    LinkedinAdapter,
    SmartRecruitersAdapter,
    TaleoAdapter,
    WorkdayAdapter,
)
from .detector import detect_platform
from .learning import LearningStore
from .models import ApplyInput, ApplyResult, ApplyStatus
from .store import ApplyStore


class AutomationEngine:
    def __init__(self, store_path: str = "automation/apply_history.json"):
        self.adapters = {
            "greenhouse": GreenhouseAdapter(),
            "lever": LeverAdapter(),
            "workday": WorkdayAdapter(),
            "smartrecruiters": SmartRecruitersAdapter(),
            "ashby": AshbyAdapter(),
            "icims": IcimsAdapter(),
            "taleo": TaleoAdapter(),
            "linkedin": LinkedinAdapter(),
            "indeed": IndeedAdapter(),
            "generic": GenericAdapter(),
        }
        self.platform_success = defaultdict(lambda: 0.5)
        self.store = ApplyStore(store_path)
        self.learning = LearningStore()

    async def _candidate_adapters(self, apply_input: ApplyInput, dom_excerpt: str = ""):
        detected = detect_platform(apply_input.url, dom_excerpt)
        ranked = []
        seen = set()
        domain = (urlparse(apply_input.url).netloc or "").lower()

        # Honor upstream platform resolution first (from pipeline/link resolver).
        preferred = (apply_input.preferred_platform or "").strip().lower()
        if preferred and preferred in self.adapters:
            adapter = self.adapters[preferred]
            try:
                supported = await adapter.supports(apply_input)
            except Exception:
                supported = False
            if supported:
                learned = self.learning.prior(preferred, domain)
                score = 1.0 * self.platform_success[preferred] * learned
                ranked.append((adapter, score, "preferred platform from pipeline"))
                seen.add(preferred)

        for candidate in detected:
            adapter = self.adapters.get(candidate.platform)
            if not adapter or candidate.platform in seen:
                continue
            if await adapter.supports(apply_input):
                learned = self.learning.prior(candidate.platform, domain)
                score = candidate.confidence * self.platform_success[candidate.platform] * learned
                ranked.append((adapter, score, candidate.reason))
                seen.add(candidate.platform)

        if "generic" not in seen:
            # Keep generic strictly as last-resort fallback.
            ranked.append((self.adapters["generic"], 0.01, "fallback adapter"))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:3]

    async def run(self, apply_input: ApplyInput, dom_excerpt: str = "") -> ApplyResult:
        candidates = await self._candidate_adapters(apply_input, dom_excerpt)
        attempts = []
        final_result = None
        non_generic_attempted = False

        for idx, (adapter, score, reason) in enumerate(candidates, start=1):
            if adapter.platform == "generic" and non_generic_attempted:
                # Generic should not overwrite a concrete platform outcome.
                break
            result = await adapter.apply(apply_input)
            result.evidence.update(
                {
                    "selector_score": round(score, 3),
                    "selector_reason": reason,
                    "attempt": idx,
                }
            )
            attempts.append(
                {
                    "platform": result.platform,
                    "status": result.status.value,
                    "message": result.message,
                }
            )
            if result.platform != "generic":
                non_generic_attempted = True

            if result.status == ApplyStatus.APPLIED:
                self.platform_success[result.platform] = min(0.95, self.platform_success[result.platform] + 0.03)
                final_result = result
                break

            if result.status in (ApplyStatus.FAILED, ApplyStatus.BLOCKED):
                self.platform_success[result.platform] = max(0.2, self.platform_success[result.platform] - 0.05)
                # Captcha/login blockers should stop retries immediately.
                if result.status == ApplyStatus.BLOCKED:
                    final_result = result
                    break

            # Keep latest non-success attempt so we report the most specific platform outcome.
            final_result = result

        if final_result is None:
            final_result = ApplyResult(
                platform="generic",
                status=ApplyStatus.FAILED,
                message="No adapter could execute",
                evidence={"url": apply_input.url},
            )

        # Continuation package for human handoff from failure point.
        final_result.evidence.setdefault("continueUrl", apply_input.url)
        final_result.evidence.setdefault(
            "prefill",
            {
                "fullName": apply_input.full_name,
                "email": apply_input.email,
                "phone": apply_input.phone,
                "resumePath": apply_input.resume_path,
                "summary": apply_input.tailored_summary,
                "intro": apply_input.tailored_intro,
            },
        )
        final_result.evidence["attempts"] = attempts
        self.store.record(apply_input, final_result)

        domain = (urlparse(apply_input.url).netloc or "").lower()
        self.learning.record_auto(
            platform=final_result.platform,
            domain=domain,
            success=(final_result.status == ApplyStatus.APPLIED),
        )
        return final_result

    def record_human_completion(self, url: str, platform: str = "unknown"):
        domain = (urlparse(url or "").netloc or "").lower()
        self.learning.record_human_completion(platform=platform or "unknown", domain=domain)

    def learning_summary(self):
        return self.learning.summary()

    async def run_batch(self, apply_inputs: list[ApplyInput]) -> list[ApplyResult]:
        results: list[ApplyResult] = []
        for item in apply_inputs:
            results.append(await self.run(item))
        self.store.write_reports()
        return results
