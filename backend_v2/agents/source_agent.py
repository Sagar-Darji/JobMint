import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

from ..models import JobRecord
from ..utils import fetch_json, fetch_text, strip_html

# 100+ major US companies that use Greenhouse ATS
GREENHOUSE_BOARDS = [
    # Original
    "airbnb", "coinbase", "databricks", "discord", "figma",
    "instacart", "robinhood", "scaleai", "stripe",
    # High-growth tech
    "lyft", "reddit", "duolingo", "notion", "lattice",
    "intercom", "plaid", "amplitude", "asana", "airtable",
    "canva", "checkr", "cloudflare", "confluent", "coursera",
    "datadog", "deel", "doordash", "elastic", "faire",
    "gitlab", "gusto", "hashicorp", "hubspot",
    "mixpanel", "mongodb", "okta", "opendoor", "pagerduty",
    "roblox", "rippling", "toast", "twitch", "vimeo", "webflow",
    "zapier", "zendesk", "zoom", "chime",
    "coupang", "etsy", "eventbrite", "flexport",
    "miro", "nytimes", "surveymonkey", "uipath", "veeva",
    "wikimedia", "workato", "zuora", "carta",
    # Developer tools & infra
    "benchling", "fullstory", "harness", "highspot",
    "honeycomb", "launchdarkly", "outreach", "pendo",
    "productboard", "retool", "samsara", "seekout",
    "snyk", "sourcegraph", "temporal", "verkada", "watershed",
    # Fintech & financial
    "affirm", "marqeta", "payoneer", "transferwise",
    # Healthcare & life sciences
    "flatiron", "modernhealth", "truepill",
    # Enterprise SaaS
    "box", "freshworks", "medallia", "ringcentral", "twilio",
    "thumbtack", "superhuman", "zendesk",
    # AI & ML companies
    "cohere", "coreweave", "anyscale",
    # Additional growth companies
    "aircall", "algolia", "appcues", "brainly", "contentful",
    "cvent", "deepl", "envoy", "gem", "grafana-labs",
    "heap", "hotjar", "jasper", "kandji", "klaviyo",
    "lokalise", "lumos", "lunchbox", "mindtickle", "newrelic",
    "nooks", "papaya-global", "partnerstack", "personio",
    "pigment", "pod", "privy", "productboard",
    "qualio", "quantcast", "reachdesk", "resourcely",
    "secureframe", "sendbird", "sigma-computing", "sigopt",
    "smartrecruiters", "snowflake", "softr", "sonar",
    "spreedly", "sumo-logic", "synthesia", "tableplus",
    "teamwork", "together", "transformdata", "typeform",
    "unbabel", "unqork", "upvoty", "useinsider", "uxpin",
    "valence", "vgs", "voxel51", "voyager", "weaviate",
    "workstream", "wrike", "xepelin", "yotpo", "zaius",
    "zenchef", "zenput", "zipline", "zolve",
]

# 50+ companies using Lever ATS
LEVER_SITES = [
    # Original
    "1password", "applyboard", "brex", "circleci", "wealthfront",
    # Developer tools
    "netlify", "postman", "close", "mercury", "linear",
    "loom", "replit", "vercel", "dbt-labs",
    # AI & ML
    "weights-biases", "huggingface", "together-ai",
    "streamlit", "prefect", "modal-labs",
    # Data & analytics
    "airbyte", "fivetran", "hightouch", "census",
    "rudderstack", "posthog", "heap-inc", "june-so",
    "dovetail", "monte-carlo-data",
    # HR & people ops
    "culture-amp", "justworks", "remote",
    "airplane", "leapsome",
    # Fintech
    "ramp", "jeeves", "capchase", "melio",
    # Security
    "abnormal-security", "torq", "orca-security",
    # Infrastructure
    "chronosphere", "cribl", "observe-inc",
    # Commerce & marketplace
    "faire", "craftybase", "olo",
    # Miscellaneous growth
    "aiven", "appcircle", "arcade", "athenais",
    "augury", "ballistic", "bandwidth", "benchsci",
    "bioptimus", "bloom-credit", "bravado", "browserbase",
    "buildkite", "bunnyshell", "cabal", "cascade",
    "chamberlin", "clearbit", "closinglock", "coda",
]


class SourceAgent:
    def fetch(self, role: str) -> tuple[list[JobRecord], list[str]]:
        errors: list[str] = []
        jobs: list[JobRecord] = []
        noisy_404 = 0
        provider_failures: dict[str, int] = {}

        def greenhouse_board(board: str) -> list[JobRecord]:
            data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
            out: list[JobRecord] = []
            for item in (data.get("jobs") or [])[:300]:
                title = item.get("title") or ""
                company = item.get("company") or board.replace("-", " ").title()
                loc = ((item.get("location") or {}).get("name") or "Unknown").strip()
                apply_url = item.get("absolute_url") or ""
                desc = strip_html(item.get("content") or "")
                out.append(
                    JobRecord(
                        id=f"gh-{board}-{item.get('id')}",
                        title=title,
                        company=company,
                        location=loc,
                        source="Greenhouse",
                        remote=("remote" in loc.lower()),
                        apply_url=apply_url,
                        description=desc,
                        platform="greenhouse",
                        auto_apply_ready=True,
                    )
                )
            return out

        def lever_site(site: str) -> list[JobRecord]:
            data = fetch_json(f"https://api.lever.co/v0/postings/{site}?mode=json")
            out: list[JobRecord] = []
            for item in (data[:300] if isinstance(data, list) else []):
                cats = item.get("categories") or {}
                title = item.get("text") or ""
                loc = cats.get("location") or "Unknown"
                apply_url = item.get("hostedUrl") or item.get("applyUrl") or ""
                team = cats.get("team") or ""
                dept = cats.get("department") or ""
                desc = strip_html(item.get("descriptionPlain") or " ".join(x for x in [team, dept] if x))
                company_name = site.replace("-", " ").title()
                out.append(
                    JobRecord(
                        id=f"lever-{site}-{item.get('id')}",
                        title=title,
                        company=company_name,
                        location=loc,
                        source="Lever",
                        remote=("remote" in (loc or "").lower()),
                        apply_url=apply_url,
                        description=desc,
                        platform="lever",
                        auto_apply_ready=True,
                    )
                )
            return out

        def fetch_remotive(role_q: str) -> list[JobRecord]:
            safe_q = urllib.parse.quote(role_q or "engineer")
            data = fetch_json(f"https://remotive.com/api/remote-jobs?search={safe_q}&limit=100")
            out: list[JobRecord] = []
            for item in (data.get("jobs") or [])[:100]:
                loc = item.get("candidate_required_location") or "Worldwide Remote"
                out.append(
                    JobRecord(
                        id=f"remotive-{item.get('id')}",
                        title=item.get("title") or "",
                        company=item.get("company_name") or "Unknown",
                        location=loc,
                        source="Remotive",
                        remote=True,
                        apply_url=item.get("url") or "",
                        description=strip_html(item.get("description") or ""),
                        posted_at=item.get("publication_date") or "",
                    )
                )
            return out

        def fetch_arbeitnow() -> list[JobRecord]:
            data = fetch_json("https://www.arbeitnow.com/api/job-board-api")
            out: list[JobRecord] = []
            for item in (data.get("data") or [])[:150]:
                loc = item.get("location") or "Unknown"
                out.append(
                    JobRecord(
                        id=f"arbeitnow-{item.get('slug') or item.get('id', '')}",
                        title=item.get("title") or "",
                        company=item.get("company_name") or "Unknown",
                        location=loc,
                        source="Arbeitnow",
                        remote=bool(item.get("remote")) or "remote" in loc.lower(),
                        apply_url=item.get("url") or "",
                        description=strip_html(item.get("description") or ""),
                        posted_at=item.get("created_at") or "",
                    )
                )
            return out

        def fetch_remoteok() -> list[JobRecord]:
            data = fetch_json("https://remoteok.com/api", headers={"Accept": "application/json"})
            out: list[JobRecord] = []
            if not isinstance(data, list):
                return out
            for item in data[1:200]:
                if not isinstance(item, dict):
                    continue
                tags = " ".join(item.get("tags") or [])
                out.append(
                    JobRecord(
                        id=f"remoteok-{item.get('id') or item.get('slug', '')}",
                        title=item.get("position") or "",
                        company=item.get("company") or "Unknown",
                        location=item.get("location") or "Remote",
                        source="RemoteOK",
                        remote=True,
                        apply_url=item.get("apply_url") or item.get("url") or "",
                        description=tags,
                        posted_at=item.get("date") or "",
                    )
                )
            return out

        def fetch_muse(role_q: str) -> list[JobRecord]:
            safe_q = urllib.parse.quote(role_q or "engineer")
            data = fetch_json(
                f"https://www.themuse.com/api/public/jobs?page=0&category={safe_q}&location=United+States",
                timeout=20,
            )
            out: list[JobRecord] = []
            for item in (data.get("results") or [])[:80]:
                locs = item.get("locations") or [{}]
                loc = (locs[0] or {}).get("name") or "United States"
                company = (item.get("company") or {}).get("name") or "Unknown"
                apply_url = (item.get("refs") or {}).get("landing_page") or ""
                cats = " ".join((c or {}).get("name", "") for c in (item.get("categories") or []))
                levels = " ".join((l or {}).get("name", "") for l in (item.get("levels") or []))
                out.append(
                    JobRecord(
                        id=f"muse-{item.get('id')}",
                        title=item.get("name") or "",
                        company=company,
                        location=loc,
                        source="TheMuse",
                        remote="remote" in loc.lower(),
                        apply_url=apply_url,
                        description=f"{cats} {levels}".strip(),
                        posted_at=item.get("publication_date") or "",
                    )
                )
            return out

        def fetch_jobicy(role_q: str) -> list[JobRecord]:
            safe_q = urllib.parse.quote(role_q or "engineer")
            data = fetch_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa&tag={safe_q}", timeout=15)
            out: list[JobRecord] = []
            for item in (data.get("jobs") or [])[:50]:
                loc = item.get("jobGeo") or "Remote"
                out.append(
                    JobRecord(
                        id=f"jobicy-{item.get('id') or item.get('jobSlug', '')}",
                        title=item.get("jobTitle") or "",
                        company=item.get("companyName") or "Unknown",
                        location=loc,
                        source="Jobicy",
                        remote=True,
                        apply_url=item.get("url") or "",
                        description=strip_html(item.get("jobDescription") or item.get("jobExcerpt") or ""),
                        posted_at=item.get("pubDate") or "",
                    )
                )
            return out

        # Parallel fan-out: all Greenhouse boards + Lever sites + aggregators simultaneously
        with ThreadPoolExecutor(max_workers=24) as ex:
            futures: list[tuple[str, object]] = []
            for board in GREENHOUSE_BOARDS:
                futures.append((f"Greenhouse:{board}", ex.submit(greenhouse_board, board)))
            for site in LEVER_SITES:
                futures.append((f"Lever:{site}", ex.submit(lever_site, site)))
            futures.append(("Remotive", ex.submit(fetch_remotive, role)))
            futures.append(("Arbeitnow", ex.submit(fetch_arbeitnow)))
            futures.append(("RemoteOK", ex.submit(fetch_remoteok)))
            futures.append(("TheMuse", ex.submit(fetch_muse, role)))
            futures.append(("Jobicy", ex.submit(fetch_jobicy, role)))

            for name, fut in futures:
                try:
                    jobs.extend(fut.result())
                except Exception as exc:
                    if isinstance(exc, HTTPError) and exc.code in (404, 410):
                        noisy_404 += 1
                        continue
                    provider = name.split(":", 1)[0]
                    provider_failures[provider] = provider_failures.get(provider, 0) + 1
                    if provider not in ("Greenhouse", "Lever"):
                        # Only log non-ATS failures to avoid noise
                        errors.append(f"{name}: {exc}")

        # Deduplicate by title+company+location
        seen: set[str] = set()
        deduped: list[JobRecord] = []
        for j in jobs:
            key = f"{j.title}|{j.company}|{j.location}".lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(j)

        if noisy_404:
            errors.append(f"Suppressed {noisy_404} board 404/410 errors (expected for unlisted boards)")
        for provider, count in provider_failures.items():
            if count and provider in ("Greenhouse", "Lever"):
                errors.append(f"{provider}: {count} connection failures")
        return deduped, errors
