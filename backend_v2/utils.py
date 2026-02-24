import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def fetch_json(url: str, timeout: int = 18, headers: dict | None = None):
    merged = {"User-Agent": "JobMint/3.0", "Accept": "application/json"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 12):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "JobMint/3.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def strip_html(raw: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", raw or "")
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def split_keywords(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z0-9+#.]+", (text or "").lower()) if len(w) >= 2]


def parse_iso_age_score(iso_s: str) -> int:
    if not iso_s:
        return 0
    try:
        dt = datetime.fromisoformat(iso_s.replace("Z", "+00:00"))
        age_days = max(0, (datetime.now(timezone.utc) - dt).days)
        return max(0, 5 - min(age_days // 3, 5))
    except Exception:
        return 0


def make_id(seed: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", seed or "job").strip("-").lower()
    return base[:80] or "job"


def host(url: str) -> str:
    return (urllib.parse.urlparse(url or "").netloc or "").lower()
