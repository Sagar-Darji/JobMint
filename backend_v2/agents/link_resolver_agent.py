import re
from concurrent.futures import ThreadPoolExecutor

from ..config import SUPPORTED_AUTOMATION_PLATFORMS, SUPPORTED_PLATFORM_DOMAINS
from ..models import JobRecord
from ..utils import fetch_text, host


class LinkResolverAgent:
    def resolve(self, jobs: list[JobRecord], max_checks: int = 60) -> list[JobRecord]:
        if not jobs:
            return jobs

        def detect_platform(url: str) -> str:
            h = host(url)
            u = (url or "").lower()
            if "gh_jid=" in u or "/boards/" in u:
                return "greenhouse"
            if "greenhouse.io" in h:
                return "greenhouse"
            if "lever.co" in h:
                return "lever"
            if "myworkdayjobs.com" in h or "workday.com" in h:
                return "workday"
            if "linkedin.com" in h:
                return "linkedin"
            if "indeed.com" in h:
                return "indeed"
            return "generic"

        def resolve_one(job: JobRecord) -> JobRecord:
            p = detect_platform(job.apply_url)
            if p != "generic":
                job.platform = p
                job.auto_apply_ready = p in SUPPORTED_AUTOMATION_PLATFORMS
                return job

            h = host(job.apply_url)
            if not any(x in h for x in ("remotive.com", "remoteok.com", "arbeitnow.com")):
                job.platform = "generic"
                job.auto_apply_ready = False
                return job
            try:
                page = fetch_text(job.apply_url, timeout=10)
                links = re.findall(r'href=["\']([^"\']+)["\']', page, flags=re.IGNORECASE)
                for href in links:
                    if any(dom in href for dom in SUPPORTED_PLATFORM_DOMAINS):
                        job.apply_url = href
                        break
            except Exception:
                pass

            p = detect_platform(job.apply_url)
            job.platform = p
            job.auto_apply_ready = p in SUPPORTED_AUTOMATION_PLATFORMS
            return job

        head = jobs[:max_checks]
        tail = jobs[max_checks:]
        with ThreadPoolExecutor(max_workers=10) as ex:
            head = list(ex.map(resolve_one, head))
        tail = [resolve_one(j) for j in tail]
        merged = head + tail
        merged.sort(key=lambda x: (1 if x.auto_apply_ready else 0, x.score), reverse=True)
        return merged
