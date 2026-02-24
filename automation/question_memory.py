import json
import re
from pathlib import Path
from urllib.parse import urlparse


STORE_PATH = Path("automation/user_answers.json")


def _load():
    if not STORE_PATH.exists():
        return {"platform": {}, "domain": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"platform": {}, "domain": {}}
        data.setdefault("platform", {})
        data.setdefault("domain", {})
        return data
    except Exception:
        return {"platform": {}, "domain": {}}


def _save(data):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _norm(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:120]


def _question_key(q: str) -> str:
    x = (q or "").lower()
    if "authorized" in x or "work authorization" in x:
        return "work_authorization"
    if "sponsor" in x or "visa" in x:
        return "sponsorship"
    if "veteran" in x:
        return "veteran_status"
    if "disab" in x:
        return "disability_status"
    if "gender" in x:
        return "gender"
    if "ethnicity" in x or "race" in x:
        return "ethnicity"
    if "18 years" in x or "over 18" in x:
        return "age_18_plus"
    return _norm(q)


def extract_questions_from_html_file(html_path: str, limit: int = 8) -> list[dict]:
    p = Path(html_path or "")
    if not p.exists():
        return []
    html = p.read_text(encoding="utf-8", errors="ignore")
    labels = re.findall(r"<label\b[^>]*>(.*?)</label>", html, flags=re.IGNORECASE | re.DOTALL)
    legends = re.findall(r"<legend\b[^>]*>(.*?)</legend>", html, flags=re.IGNORECASE | re.DOTALL)
    candidates = labels + legends
    out = []
    seen = set()
    for raw in candidates:
        txt = re.sub(r"<[^>]+>", " ", raw)
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) < 6:
            continue
        low = txt.lower()
        if not any(
            k in low
            for k in [
                "authorized",
                "sponsor",
                "visa",
                "veteran",
                "disab",
                "gender",
                "ethnicity",
                "race",
                "18",
                "citizen",
                "legally",
            ]
        ):
            continue
        key = _question_key(txt)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "question": txt[:220],
                "type": "yes_no" if key in {"work_authorization", "sponsorship", "age_18_plus"} else "text",
            }
        )
        if len(out) >= limit:
            break
    return out


def save_answers(url: str, platform: str, answers: list[dict]):
    data = _load()
    domain = (urlparse(url or "").netloc or "").lower()
    p = (platform or "unknown").lower()
    data["platform"].setdefault(p, {})
    if domain:
        data["domain"].setdefault(domain, {})

    for row in answers or []:
        q = (row.get("question") or "").strip()
        a = str(row.get("answer") or "").strip()
        if not q or not a:
            continue
        key = (row.get("key") or _question_key(q)).strip() or _norm(q)
        payload = {"question": q, "answer": a, "key": key}
        data["platform"][p][key] = payload
        if domain:
            data["domain"][domain][key] = payload
    _save(data)


def get_answers(url: str, platform: str) -> dict[str, str]:
    data = _load()
    domain = (urlparse(url or "").netloc or "").lower()
    p = (platform or "unknown").lower()
    out = {}
    for src in [data.get("platform", {}).get(p, {}), data.get("domain", {}).get(domain, {})]:
        for k, row in (src or {}).items():
            out[k] = str((row or {}).get("answer") or "")
    return out
