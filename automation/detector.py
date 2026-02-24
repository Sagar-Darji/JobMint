from urllib.parse import parse_qs, urlparse

from .models import DetectionCandidate


DOMAIN_RULES = {
    "greenhouse": ["greenhouse.io"],
    "lever": ["lever.co"],
    "workday": ["myworkdayjobs.com", "workday.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "ashby": ["ashbyhq.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net"],
    "linkedin": ["linkedin.com"],
    "indeed": ["indeed.com"],
}


def detect_platform(url: str, dom_excerpt: str = "") -> list[DetectionCandidate]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    query = parse_qs(parsed.query or "")
    text = (dom_excerpt or "").lower()

    candidates: list[DetectionCandidate] = []

    for platform, domains in DOMAIN_RULES.items():
        if any(domain in host for domain in domains):
            candidates.append(
                DetectionCandidate(
                    platform=platform,
                    confidence=0.92,
                    reason=f"domain matched {host}",
                )
            )

    # Greenhouse frequently appears on custom company domains with gh_jid query param.
    if "gh_jid" in query or "/boards/" in path:
        candidates.append(
            DetectionCandidate(
                platform="greenhouse",
                confidence=0.86,
                reason="gh_jid or boards path signature",
            )
        )

    if "greenhouse" in text:
        candidates.append(
            DetectionCandidate(
                platform="greenhouse",
                confidence=0.78,
                reason="DOM mentions greenhouse",
            )
        )

    if "workday" in text:
        candidates.append(
            DetectionCandidate(
                platform="workday",
                confidence=0.76,
                reason="DOM mentions workday",
            )
        )

    if not candidates:
        candidates.append(
            DetectionCandidate(
                platform="generic",
                confidence=0.45,
                reason="no known signature; using generic fallback",
            )
        )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates
