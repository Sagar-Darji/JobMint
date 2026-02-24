import json
import re
from datetime import datetime, timezone
from pathlib import Path


STORE_PATH = Path("automation/selector_suggestions.json")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_store():
    if not STORE_PATH.exists():
        return {}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_store(data):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _safe_attr(value: str) -> str:
    return (value or "").replace('"', "").strip()


def _build_selector(tag: str, attrs: dict) -> str:
    tid = attrs.get("id") or ""
    if tid and re.fullmatch(r"[A-Za-z0-9_-]+", tid):
        return f"{tag}#{tid}"
    name = _safe_attr(attrs.get("name") or "")
    if name:
        return f'{tag}[name="{name}"]'
    aria = _safe_attr(attrs.get("aria-label") or "")
    if aria:
        token = aria.split()[0]
        return f'{tag}[aria-label*="{_safe_attr(token)}"]'
    placeholder = _safe_attr(attrs.get("placeholder") or "")
    if placeholder:
        token = placeholder.split()[0]
        return f'{tag}[placeholder*="{_safe_attr(token)}"]'
    itype = _safe_attr(attrs.get("type") or "")
    if itype:
        return f'{tag}[type="{itype}"]'
    return tag


def _parse_attrs(raw: str) -> dict:
    pairs = re.findall(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["\']([^"\']+)["\']', raw or "")
    out = {}
    for k, v in pairs:
        out[k.lower()] = v
    return out


def _extract_tags(html: str):
    for m in re.finditer(r"<(input|textarea|select|button)\b([^>]*)>", html or "", flags=re.IGNORECASE):
        tag = (m.group(1) or "").lower()
        attrs = _parse_attrs(m.group(2) or "")
        yield tag, attrs


FIELD_KEYWORDS = {
    "full_name": ["full name", "fullname", "candidate_name", "applicant_name"],
    "first_name": ["first name", "firstname", "given name", "first_name"],
    "last_name": ["last name", "lastname", "surname", "family name", "last_name"],
    "email": ["email", "e-mail"],
    "phone": ["phone", "mobile", "telephone", "tel"],
    "resume": ["resume", "cv", "attach", "upload"],
    "cover_letter": ["cover letter", "cover", "motivation", "additional information"],
    "submit": ["submit", "apply", "send application"],
}


def _field_matches(field: str, tag: str, attrs: dict) -> bool:
    attrs_blob = " ".join(
        [
            attrs.get("id", ""),
            attrs.get("name", ""),
            attrs.get("aria-label", ""),
            attrs.get("placeholder", ""),
            attrs.get("class", ""),
            attrs.get("data-qa", ""),
            attrs.get("data-test", ""),
            attrs.get("type", ""),
        ]
    ).lower()
    if field == "resume":
        if attrs.get("type", "").lower() == "file":
            return True
    for kw in FIELD_KEYWORDS.get(field, []):
        if kw in attrs_blob:
            return True
    if field == "submit" and tag == "button":
        btype = attrs.get("type", "").lower()
        if btype == "submit":
            return True
    return False


def _learn_from_html(platform: str, html: str) -> dict[str, list[str]]:
    out = {k: [] for k in FIELD_KEYWORDS.keys()}
    for tag, attrs in _extract_tags(html):
        for field in out.keys():
            if _field_matches(field, tag, attrs):
                selector = _build_selector(tag, attrs)
                if selector and selector not in out[field]:
                    out[field].append(selector)
    return out


def learn_from_artifact(platform: str, html_path: str, stage: str = "") -> dict:
    p = Path(html_path or "")
    if not p.exists():
        return {}
    html = p.read_text(encoding="utf-8", errors="ignore")
    learned = _learn_from_html(platform or "generic", html)
    data = _load_store()
    plat = data.get(platform) or {"updatedAt": _now(), "stages": {}, "fields": {}}
    plat["updatedAt"] = _now()
    plat["stages"][stage or "unknown"] = plat["stages"].get(stage or "unknown", 0) + 1
    fields = plat.get("fields") or {}
    for field, selectors in learned.items():
        bucket = fields.get(field) or {}
        for sel in selectors[:8]:
            bucket[sel] = int(bucket.get(sel, 0)) + 1
        fields[field] = bucket
    plat["fields"] = fields
    data[platform] = plat
    _save_store(data)
    return learned


def get_field_selectors(platform: str, field: str, limit: int = 8) -> list[str]:
    data = _load_store()
    plat = data.get(platform) or {}
    fields = plat.get("fields") or {}
    scores = fields.get(field) or {}
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [k for k, _ in ranked[:limit]]


def summary():
    data = _load_store()
    out = {}
    for platform, row in data.items():
        fields = row.get("fields") or {}
        out[platform] = {
            "updatedAt": row.get("updatedAt"),
            "stages": row.get("stages", {}),
            "fieldCounts": {k: len(v or {}) for k, v in fields.items()},
        }
    return out
