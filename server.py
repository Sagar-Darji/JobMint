#!/usr/bin/env python3
import html
import io
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from automation import ApplyInput, AutomationEngine
from automation.detector import detect_platform
from automation.question_memory import get_answers, save_answers
from automation.selector_learning import summary as selector_learning_summary
from backend_v2 import PipelineOrchestrator, PipelineRequest

BASE_DIR = Path(__file__).resolve().parent
SUPPORTED_AUTOMATION_PLATFORMS = {"greenhouse", "lever", "workday", "linkedin", "indeed"}
SUPPORTED_PLATFORM_DOMAINS = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "linkedin.com",
    "indeed.com",
)
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_GROQ_API_KEY = ""
SUPABASE_URL = ""
SUPABASE_KEY = ""


def load_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        ln = line.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        key = k.strip()
        val = v.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


load_env_file()
TASKS = {}
TASKS_LOCK = threading.Lock()
# Read Supabase config after env load
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def set_task(task_id, **updates):
    with TASKS_LOCK:
        task = TASKS.get(task_id, {})
        task.update(updates)
        TASKS[task_id] = task


def get_groq_api_key():
    return os.environ.get("GROQ_API_KEY", "").strip() or DEFAULT_GROQ_API_KEY


def append_task_log(task_id, message):
    with TASKS_LOCK:
        task = TASKS.get(task_id, {})
        logs = task.get("logs", [])
        logs.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}")
        task["logs"] = logs[-80:]
        TASKS[task_id] = task


def json_response(handler, payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def fetch_json(url, timeout=16, headers=None):
    merged_headers = {"User-Agent": "JobMint/2.0 (+local-app)"}
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, headers=merged_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strip_html(raw):
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def normalize_job(job):
    title = (job.get("title") or "").strip()
    company = (job.get("company") or "Unknown").strip() or "Unknown"
    location = (job.get("location") or "Unknown").strip() or "Unknown"
    description = strip_html(job.get("description") or "")
    apply_url = (job.get("applyUrl") or "").strip()
    source = (job.get("source") or "Unknown").strip()
    remote = bool(job.get("remote")) or ("remote" in location.lower())
    posted_at = job.get("postedAt") or ""
    return {
        "id": job.get("id") or os.urandom(6).hex(),
        "title": title,
        "company": company,
        "location": location,
        "source": source,
        "remote": remote,
        "applyUrl": apply_url,
        "description": description,
        "postedAt": posted_at,
        "platform": job.get("platform") or "",
        "autoApplyReady": bool(job.get("autoApplyReady")),
    }


def dedupe_jobs(jobs):
    seen = set()
    result = []
    for raw in jobs:
        job = normalize_job(raw)
        key = f"{job['title']}|{job['company']}|{job['location']}|{job['source']}".lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result


def annotate_apply_capability(job):
    url = job.get("applyUrl", "")
    candidates = detect_platform(url, "")
    platform = candidates[0].platform if candidates else "generic"
    job["platform"] = platform
    job["autoApplyReady"] = platform in SUPPORTED_AUTOMATION_PLATFORMS
    return job


def extract_supported_platform_link(html_text):
    links = re.findall(r'href=[\"\\\']([^\"\\\']+)[\"\\\']', html_text, flags=re.IGNORECASE)
    for href in links:
        if href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        if any(dom in href for dom in SUPPORTED_PLATFORM_DOMAINS):
            return href
    return ""


def extract_apply_link_any(html_text, base_url):
    candidates = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text or "", flags=re.IGNORECASE | re.DOTALL)
    for href, label in candidates:
        txt = re.sub(r"<[^>]+>", " ", label or "")
        txt = re.sub(r"\s+", " ", txt).strip().lower()
        if "apply" in txt:
            if href.startswith("mailto:") or href.startswith("javascript:"):
                continue
            return urllib.parse.urljoin(base_url, href)
    return ""


def resolve_apply_url_if_aggregator(url):
    if not url:
        return ""
    host = (urllib.parse.urlparse(url).netloc or "").lower()
    if not any(d in host for d in ("remotive.com", "remoteok.com", "arbeitnow.com")):
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "JobMint/2.0 (+local-app)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        html_text = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
        return extract_supported_platform_link(html_text)
    except Exception:
        return ""


def resolve_direct_apply_url(url):
    if not url:
        return ""
    # Fast path for known platform URLs.
    if any(dom in url for dom in SUPPORTED_PLATFORM_DOMAINS) or "gh_jid=" in url:
        return url
    # Existing aggregator resolver.
    resolved = resolve_apply_url_if_aggregator(url)
    if resolved:
        return resolved
    # Generic page inspection: try platform links first, then "Apply" anchors.
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "JobMint/2.0 (+local-app)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        html_text = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")
        platform_link = extract_supported_platform_link(html_text)
        if platform_link:
            return platform_link
        apply_link = extract_apply_link_any(html_text, url)
        if apply_link:
            return apply_link
    except Exception:
        pass
    return url


def enrich_platform_apply_urls(jobs, max_checks=50):
    if not jobs:
        return jobs

    def worker(job):
        resolved = resolve_apply_url_if_aggregator(job.get("applyUrl", ""))
        if resolved:
            job["originalApplyUrl"] = job.get("applyUrl", "")
            job["applyUrl"] = resolved
        return annotate_apply_capability(job)

    head = jobs[:max_checks]
    tail = jobs[max_checks:]
    with ThreadPoolExecutor(max_workers=10) as ex:
        head = list(ex.map(worker, head))
    tail = [annotate_apply_capability(j) for j in tail]
    return head + tail


def split_keywords(text):
    return [w for w in re.findall(r"[a-zA-Z0-9+#.]+", (text or "").lower()) if len(w) >= 2]


def relevance_score(job, role, location, job_type, resume_text=""):
    hay = " ".join([job.get("title", ""), job.get("description", ""), job.get("company", "")]).lower()
    role_keywords = split_keywords(role)
    resume_keywords = [w for w in split_keywords(resume_text) if len(w) >= 4][:60]

    role_hits = sum(1 for kw in role_keywords if kw in hay)
    title_bonus = 0
    title_l = job.get("title", "").lower()
    for kw in role_keywords[:5]:
        if kw in title_l:
            title_bonus += 2

    resume_overlap = sum(1 for kw in set(resume_keywords) if kw in hay)

    loc_score = 0
    location_l = (job.get("location") or "").lower()
    wanted_location = (location or "").lower().strip()
    if wanted_location:
        if wanted_location in location_l:
            loc_score = 5
        elif wanted_location == "remote" and job.get("remote"):
            loc_score = 4
        else:
            loc_score = -1

    type_score = 0
    if job_type == "remote":
        type_score = 4 if job.get("remote") else -4
    elif job_type == "onsite":
        type_score = 3 if not job.get("remote") else -2

    freshness = 0
    posted = job.get("postedAt") or ""
    if posted:
        try:
            dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            age_days = max(0, (datetime.now(timezone.utc) - dt).days)
            freshness = max(0, 5 - min(age_days // 3, 5))
        except Exception:
            freshness = 0

    score = (role_hits * 3) + title_bonus + loc_score + type_score + freshness + min(resume_overlap, 8)
    reason = {
        "role_hits": role_hits,
        "resume_overlap": min(resume_overlap, 8),
        "title_bonus": title_bonus,
        "location_score": loc_score,
        "type_score": type_score,
        "freshness": freshness,
    }
    return score, reason


def fetch_arbeitnow():
    data = fetch_json("https://www.arbeitnow.com/api/job-board-api")
    jobs = []
    for item in data.get("data", [])[:200]:
        location = item.get("location") or "Unknown"
        jobs.append(
            {
                "id": f"arbeitnow-{item.get('slug') or os.urandom(4).hex()}",
                "title": item.get("title") or "",
                "company": item.get("company_name") or "Unknown",
                "location": location,
                "source": "Arbeitnow",
                "remote": bool(item.get("remote")) or ("remote" in location.lower()),
                "applyUrl": item.get("url") or "",
                "description": item.get("description") or "",
            }
        )
    return jobs


def fetch_remotive(role):
    queries = [role, "software", "engineer", "developer"]
    jobs = []
    for q in queries:
        if not q:
            continue
        data = fetch_json(f"https://remotive.com/api/remote-jobs?search={urllib.parse.quote(q)}")
        for item in data.get("jobs", [])[:120]:
            jobs.append(
                {
                    "id": f"remotive-{item.get('id')}",
                    "title": item.get("title") or "",
                    "company": item.get("company_name") or "Unknown",
                    "location": item.get("candidate_required_location") or "Remote",
                    "source": "Remotive",
                    "remote": True,
                    "applyUrl": item.get("url") or "",
                    "description": item.get("description") or "",
                    "postedAt": item.get("publication_date") or "",
                }
            )
    return jobs


def fetch_remoteok():
    data = fetch_json("https://remoteok.com/api", headers={"Accept": "application/json"})
    if not isinstance(data, list):
        return []
    jobs = []
    for item in data[1:220]:
        if not isinstance(item, dict):
            continue
        jobs.append(
            {
                "id": f"remoteok-{item.get('id') or os.urandom(4).hex()}",
                "title": item.get("position") or "",
                "company": item.get("company") or "Unknown",
                "location": item.get("location") or "Remote",
                "source": "RemoteOK",
                "remote": True,
                "applyUrl": item.get("apply_url") or item.get("url") or "",
                "description": " ".join(item.get("tags") or []),
                "postedAt": item.get("date") or "",
            }
        )
    return jobs


def fallback_jobs():
    return [
        {
            "id": "fallback-1",
            "title": "Frontend Engineer",
            "company": "Northstar Labs",
            "location": "Remote - US",
            "source": "Fallback",
            "remote": True,
            "applyUrl": "https://www.linkedin.com/jobs/",
            "description": "Build React interfaces and ship tested features.",
            "postedAt": "",
        },
        {
            "id": "fallback-2",
            "title": "Full Stack Developer",
            "company": "ScaleForge",
            "location": "Remote - Global",
            "source": "Fallback",
            "remote": True,
            "applyUrl": "https://wellfound.com/jobs",
            "description": "Develop Node APIs and frontend components.",
            "postedAt": "",
        },
    ]


def filter_and_rank_jobs(jobs, role, location, job_type, resume_text="", limit=250):
    role_keywords = split_keywords(role)
    ranked = []
    for job in dedupe_jobs(jobs):
        annotate_apply_capability(job)
        score, reason = relevance_score(job, role, location, job_type, resume_text=resume_text)
        if role and (score < 1):
            continue
        if role_keywords and reason.get("role_hits", 0) == 0 and reason.get("title_bonus", 0) == 0:
            continue
        if location and location.lower() != "remote" and location.lower() not in (job.get("location") or "").lower():
            if score < 4:
                continue
        if job_type == "remote" and not job.get("remote"):
            continue
        if job_type == "onsite" and job.get("remote"):
            continue
        job["score"] = score
        job["scoreReason"] = reason
        ranked.append(job)

    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    return ranked[:limit]


def ai_rerank_jobs_with_groq(api_key, jobs, role, location, job_type, resume_text):
    if not jobs:
        return jobs, False
    shortlist = jobs[:80]
    lines = []
    for j in shortlist:
        lines.append(
            " | ".join(
                [
                    f"id={j.get('id','')}",
                    f"title={j.get('title','')}",
                    f"company={j.get('company','')}",
                    f"location={j.get('location','')}",
                    f"remote={j.get('remote', False)}",
                    f"desc={(j.get('description','') or '')[:220]}",
                ]
            )
        )
    prompt = (
        "You are a strict job relevance ranker. Return JSON only with key `keep`.\n"
        "`keep` must be an array of objects: {id, score, reason} where score is 0-100.\n"
        "Include only highly relevant jobs based on role, location, job_type, and resume fit.\n"
        "Prefer exact role/title and stack match; penalize mismatched functions.\n"
        f"Target role: {role}\n"
        f"Target location: {location}\n"
        f"Target job_type: {job_type}\n"
        f"Resume snippet: {(resume_text or '')[:1800]}\n"
        "Jobs:\n"
        + "\n".join(lines)
    )
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "JobMint/2.0 (+local-app)",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
    parsed = json.loads(content)
    keep = parsed.get("keep") or []
    if not isinstance(keep, list):
        return jobs, False
    score_by_id = {}
    reason_by_id = {}
    for row in keep:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id", "")).strip()
        if not rid:
            continue
        score_by_id[rid] = int(row.get("score", 0))
        reason_by_id[rid] = str(row.get("reason", "")).strip()
    if not score_by_id:
        return jobs, False
    pruned = []
    for j in shortlist:
        rid = j.get("id", "")
        if rid not in score_by_id:
            continue
        j["aiScore"] = score_by_id[rid]
        j["aiReason"] = reason_by_id.get(rid, "")
        j["score"] = int(j.get("score", 0)) + (score_by_id[rid] // 5)
        pruned.append(j)
    pruned.sort(key=lambda x: (x.get("aiScore", 0), x.get("score", 0)), reverse=True)
    return pruned, True


def parse_multipart_resume(content_type, body):
    match = re.search(r"boundary=(.+)", content_type)
    if not match:
        return None
    boundary = match.group(1).encode("utf-8")
    parts = body.split(b"--" + boundary)
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        if b'name="resume"' not in part:
            continue
        filename = "resume.pdf"
        header_blob = part.split(b"\r\n\r\n", 1)[0]
        fnm = re.search(br'filename="([^"]+)"', header_blob)
        if fnm:
            try:
                filename = fnm.group(1).decode("utf-8", errors="ignore")
            except Exception:
                filename = "resume.pdf"
        split = part.split(b"\r\n\r\n", 1)
        if len(split) != 2:
            continue
        return split[1].rstrip(b"\r\n-"), filename
    return None, None


def extract_pdf_text(pdf_bytes):
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return None, "Install pypdf: pip install pypdf"

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return None, "No text could be extracted from this PDF"
        return text, None
    except Exception as exc:
        return None, f"PDF parsing failed: {exc}"


def save_uploaded_resume(pdf_bytes, original_name="resume.pdf"):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name)[:80] or "resume.pdf"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = UPLOADS_DIR / f"{ts}_{safe}"
    out.write_bytes(pdf_bytes)

    # Also upload to Supabase Storage (so the path survives server restarts)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            storage_path = f"resumes/{ts}_{safe}"
            url = f"{SUPABASE_URL}/storage/v1/object/{storage_path}"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/pdf",
                "x-upsert": "true",
            }
            import urllib.request
            req = urllib.request.Request(url, data=pdf_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status in (200, 201):
                    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{storage_path}"
                    return public_url
        except Exception:
            pass  # fallback to local path below

    return str(out)


def heuristic_tailor(resume_text, role, job):
    desc = f"{job.get('title', '')} {job.get('description', '')}".lower()
    words = [w for w in split_keywords(desc) if len(w) >= 4]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    top = [w for w, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:6]]
    keywords = ", ".join(top[:4]) if top else "execution, ownership, impact"

    summary = (
        f"Candidate aligned for {job.get('title', role)} at {job.get('company', 'the company')}, "
        f"with emphasis on {keywords}."
    )
    intro = (
        f"Hi {job.get('company', 'team')}, I am interested in {job.get('title', role)} and my background aligns "
        f"with your requirements around {keywords}."
    )
    return {
        "summary": summary,
        "intro": intro,
        "resume_excerpt": (resume_text or "")[:900],
    }


def groq_tailor(api_key, resume_text, role, job):
    prompt = (
        "Generate concise JSON with keys summary,intro for a job application. "
        "Match resume to role and company. Keep each under 70 words.\\n"
        f"Role: {role}\\n"
        f"Company: {job.get('company', '')}\\n"
        f"Job Title: {job.get('title', '')}\\n"
        f"Job Description: {job.get('description', '')[:1200]}\\n"
        f"Resume: {resume_text[:2000]}"
    )
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Return valid JSON only with keys summary and intro."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "JobMint/2.0 (+local-app)",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    text = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
    obj = json.loads(text)
    summary = str(obj.get("summary", "")).strip()
    intro = str(obj.get("intro", "")).strip()
    if not summary or not intro:
        raise ValueError("Groq response missing summary/intro")
    return {"summary": summary, "intro": intro}


def tailor_for_job(profile, job):
    ai_mode = (profile.get("aiMode") or "heuristic").lower()
    api_key = get_groq_api_key()
    if ai_mode == "groq" and api_key:
        try:
            return groq_tailor(api_key, profile.get("resumeText", ""), profile.get("role", ""), job)
        except Exception:
            pass
    return heuristic_tailor(profile.get("resumeText", ""), profile.get("role", ""), job)


def normalize_full_name(raw_name):
    name = (raw_name or "").strip()
    if not name:
        return ""
    name = re.sub(r"\s+", " ", name)
    name = re.split(r"[|,/]", name)[0].strip()
    words = [w for w in name.split(" ") if w]
    if len(words) > 5:
        words = words[:5]
    return " ".join(words)


def prepare_profile_for_apply(profile, user_key=None):
    p = dict(profile or {})
    resume_text = (p.get("resumeText") or "").strip()
    inferred = heuristic_profile_suggest(resume_text) if resume_text else {}

    full_name = normalize_full_name(p.get("fullName") or inferred.get("fullName", ""))
    p["fullName"] = full_name
    p["email"] = (p.get("email") or inferred.get("email", "")).strip()
    p["phone"] = (p.get("phone") or inferred.get("phone", "")).strip()
    p["autoSubmit"] = True

    # Per-user browser profile: isolates each user's login sessions
    if user_key and not p.get("profileDir"):
        safe_key = re.sub(r"[^a-zA-Z0-9_-]", "", user_key)[:64]
        p["profileDir"] = f"automation/.pw-profiles/{safe_key}"

    return p


def build_apply_input(profile, job):
    tailored = tailor_for_job(profile, job)
    auto_submit = True
    if "autoSubmit" in profile:
        auto_submit = bool(profile.get("autoSubmit"))
    return ApplyInput(
        url=job.get("applyUrl", ""),
        role=profile.get("role", "") or job.get("title", ""),
        location=profile.get("location", "") or job.get("location", ""),
        job_type=profile.get("jobType", "all"),
        resume_text=profile.get("resumeText", ""),
        preferred_platform=(job.get("platform") or "").strip().lower(),
        full_name=profile.get("fullName", ""),
        email=profile.get("email", ""),
        phone=profile.get("phone", ""),
        resume_path=profile.get("resumePath", ""),
        tailored_summary=tailored["summary"],
        tailored_intro=tailored["intro"],
        auto_submit=auto_submit,
        profile_dir=profile.get("profileDir", ""),
        linkedin_email=profile.get("linkedinEmail", ""),
        linkedin_password=profile.get("linkedinPassword", ""),
    )


def build_manual_handoff(profile, job, url, platform, message):
    prefill = {
        "fullName": profile.get("fullName", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "resumePath": profile.get("resumePath", ""),
    }
    try:
        t = tailor_for_job(profile, job)
        prefill["summary"] = t.get("summary", "")
        prefill["intro"] = t.get("intro", "")
    except Exception:
        prefill["summary"] = ""
        prefill["intro"] = ""
    return {
        "url": url or "",
        "platform": platform or "unknown",
        "status": "needs_human",
        "message": message,
        "evidence": {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "continueUrl": url or "",
            "prefill": prefill,
        },
    }


def heuristic_profile_suggest(resume_text):
    text = resume_text or ""
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    phone_match = re.search(r"(\+?\d[\d\-\s()]{8,}\d)", text)
    role_guess = ""
    role_patterns = [
        r"(software engineer|data engineer|frontend engineer|backend engineer|full stack developer|product manager|designer)",
    ]
    low = text.lower()
    for pat in role_patterns:
        m = re.search(pat, low)
        if m:
            role_guess = m.group(1).title()
            break
    return {
        "fullName": first_line[:80] if len(first_line.split()) <= 6 else "",
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0) if phone_match else "",
        "role": role_guess,
        "location": "Remote" if "remote" in low else "",
    }


def groq_profile_suggest(api_key, resume_text):
    prompt = (
        "Extract profile fields from resume text and return JSON only with keys: "
        "fullName,email,phone,role,location. Keep values concise and empty string if unknown.\n\n"
        f"Resume:\n{resume_text[:4000]}"
    )
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "JobMint/2.0 (+local-app)",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    text = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
    data = json.loads(text)
    return {
        "fullName": str(data.get("fullName", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "role": str(data.get("role", "")).strip(),
        "location": str(data.get("location", "")).strip(),
    }


def groq_full_tailor(api_key, resume_text, role, job):
    title = job.get("title", role or "the role")
    company = job.get("company", "the company")
    desc = (job.get("description") or "")[:2200]
    prompt = f"""You are an expert career coach and resume writer. Generate a tailored application kit.

Job Title: {title}
Company: {company}
Job Description: {desc}
Candidate's Target Role: {role}
Candidate's Resume:
{resume_text[:3000]}

Return ONLY valid JSON with exactly these keys:

"summary": 2-3 sentences positioning the candidate specifically for {title} at {company}. Reference the company by name. Be concrete, not generic.

"coverLetter": A compelling cover letter, 230-270 words, 3 paragraphs:
  - Para 1 (Hook): Start with something specific about {company}'s product, mission, or impact — NOT "I am excited to apply". Show you actually know the company.
  - Para 2 (Value): Pick 2-3 achievements from the resume that directly solve the problems described in the job description. Tell a story — do NOT list bullets. No sentence should begin with "I have X years of".
  - Para 3 (Close): One forward-looking sentence + a clear, confident call to action.
  RULES: Do NOT copy resume sentences verbatim. Do NOT use the phrases "I am excited", "passionate about", "perfect fit", or "dream job". Be conversational and human.

"resumeTweaks": Array of exactly 3 objects, each with keys "original", "improved", "reason".
  - Take 3 existing phrases or bullets from the resume and make minor keyword/impact improvements.
  - Do NOT rewrite completely — just optimize phrasing and add 1-2 job-relevant keywords.
  - "reason" must be one short sentence explaining the change.

"keywords": Array of 6-8 top keywords from the job description that should appear in the application."""

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "Return valid JSON only. No markdown, no explanation outside JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "JobMint/4.0",
        },
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
    data = json.loads(content)
    return {
        "summary": str(data.get("summary", "")).strip(),
        "coverLetter": str(data.get("coverLetter", "")).strip(),
        "resumeTweaks": data.get("resumeTweaks") or [],
        "keywords": data.get("keywords") or [],
        "aiUsed": True,
    }


def heuristic_full_tailor(resume_text, role, job):
    title = job.get("title", role or "the role")
    company = job.get("company", "the company")
    desc = f"{title} {job.get('description', '')}".lower()
    words = [w for w in re.findall(r"[a-z0-9+#.]+", desc) if len(w) >= 4]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    stop = {"this", "that", "with", "your", "will", "have", "from", "team", "role", "work", "years"}
    keywords = [w for w, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True) if w not in stop][:8]
    kw_str = ", ".join(keywords[:4]) or "technical excellence"

    summary = (
        f"Experienced {role or title} with a track record of delivering results in "
        f"{keywords[0] if keywords else 'engineering'} and {keywords[1] if len(keywords) > 1 else 'system design'}. "
        f"Applying to {title} at {company} where my background directly maps to the team's priorities around {kw_str}."
    )
    cover_letter = (
        f"Dear {company} Hiring Team,\n\n"
        f"{company}'s work on {keywords[0] if keywords else 'this problem space'} represents exactly the kind of "
        f"high-impact engineering challenge I seek. The {title} role aligns with the direction I've been building toward.\n\n"
        f"In recent projects, I've delivered measurable results across {kw_str}. My approach combines "
        f"technical depth with a strong sense of ownership — I focus on shipping reliable, scalable solutions "
        f"that move metrics the business cares about. The problems described in this role are ones I've navigated before.\n\n"
        f"I'd welcome the chance to discuss how my experience can contribute to {company}'s goals. "
        f"Thank you for your time and consideration.\n\nBest regards"
    )

    lines = [ln.strip() for ln in (resume_text or "").splitlines() if ln.strip() and len(ln.strip()) > 20]
    sample_bullets = lines[5:8] if len(lines) >= 8 else lines[:3]
    tweaks = []
    for bullet in sample_bullets:
        improved = bullet
        for kw in keywords[:3]:
            if kw not in improved.lower():
                improved = improved.rstrip(".") + f", leveraging {kw}."
                break
        tweaks.append({"original": bullet[:120], "improved": improved[:140], "reason": f"Added '{kw}' to align with job requirements."})

    return {
        "summary": summary,
        "coverLetter": cover_letter,
        "resumeTweaks": tweaks[:3],
        "keywords": keywords[:8],
        "aiUsed": False,
    }


def automation_status():
    status = {
        "playwright": False,
        "pypdf": False,
        "groq_configured": bool(get_groq_api_key()),
        "auth_profile_ready": False,
    }
    try:
        import playwright  # noqa: F401

        status["playwright"] = True
    except Exception:
        pass

    try:
        import pypdf  # noqa: F401

        status["pypdf"] = True
    except Exception:
        pass
    try:
        profile_dir = Path(os.environ.get("AUTOAPPLY_PROFILE_DIR", "automation/.pw-profile"))
        status["auth_profile_ready"] = profile_dir.exists()
    except Exception:
        pass
    return status


class JobMintHandler(SimpleHTTPRequestHandler):
    engine = AutomationEngine()
    orchestrator = PipelineOrchestrator()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/health":
            return json_response(self, {"ok": True, "automation": automation_status()})

        if parsed.path == "/api/jobs":
            qs = urllib.parse.parse_qs(parsed.query)
            role = qs.get("role", [""])[0].strip()
            location = qs.get("location", [""])[0].strip()
            job_type = qs.get("job_type", ["all"])[0].strip() or "all"
            resume_text = qs.get("resume_text", [""])[0]
            limit = int(qs.get("limit", ["250"])[0])
            ranked, errors, ai_used = self._fetch_rank_jobs(role, location, job_type, resume_text, limit, "heuristic", "")
            ready_count = sum(1 for j in ranked if j.get("autoApplyReady"))
            return json_response(
                self,
                {
                    "jobs": ranked,
                    "count": len(ranked),
                    "autoApplyReadyCount": ready_count,
                    "sources": ["Arbeitnow", "Remotive", "RemoteOK"],
                    "errors": errors,
                    "aiUsed": ai_used,
                },
            )

        if parsed.path == "/api/pipeline/status":
            qs = urllib.parse.parse_qs(parsed.query)
            task_id = qs.get("task_id", [""])[0].strip()
            if not task_id:
                return json_response(self, {"error": "task_id is required"}, status=400)
            task = self.orchestrator.get(task_id)
            if not task:
                return json_response(self, {"error": "task not found"}, status=404)
            return json_response(self, task.to_response())

        if parsed.path == "/api/auto-apply/status":
            qs = urllib.parse.parse_qs(parsed.query)
            task_id = qs.get("task_id", [""])[0].strip()
            if not task_id:
                return json_response(self, {"error": "task_id is required"}, status=400)
            with TASKS_LOCK:
                task = TASKS.get(task_id)
            if not task:
                return json_response(self, {"error": "task not found"}, status=404)
            return json_response(self, task)

        if parsed.path == "/api/learning/status":
            return json_response(self, {"ok": True, "learning": self.engine.learning_summary()})

        if parsed.path == "/api/learning/selectors":
            return json_response(self, {"ok": True, "selectors": selector_learning_summary()})

        if parsed.path == "/api/application-answers":
            qs = urllib.parse.parse_qs(parsed.query)
            url = (qs.get("url", [""])[0] or "").strip()
            platform = (qs.get("platform", ["unknown"])[0] or "unknown").strip().lower()
            if not url:
                return json_response(self, {"error": "url is required"}, status=400)
            return json_response(self, {"ok": True, "answers": get_answers(url=url, platform=platform)})

        if parsed.path == "/api/assisted/resolve":
            qs = urllib.parse.parse_qs(parsed.query)
            url = (qs.get("url", [""])[0] or "").strip()
            if not url:
                return json_response(self, {"error": "url is required"}, status=400)
            resolved = resolve_direct_apply_url(url)
            return json_response(self, {"ok": True, "resolvedUrl": resolved})

        if parsed.path == "/api/yc/companies":
            qs = urllib.parse.parse_qs(parsed.query)
            batch = qs.get("batch", ["W25,S24,W24"])[0]
            limit = int(qs.get("limit", ["50"])[0])
            companies = fetch_yc_companies(batch, limit)
            return json_response(self, {"companies": companies, "count": len(companies)})

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/pipeline/start":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            # Accept either roles[] array or single role string
            roles_raw = payload.get("roles") or []
            if isinstance(roles_raw, str):
                roles_raw = [r.strip() for r in roles_raw.split(",") if r.strip()]
            roles = [r.strip() for r in roles_raw if r.strip()]
            # Fallback to legacy single-role field
            single_role = (payload.get("role") or "").strip()
            if not roles and single_role:
                roles = [single_role]
            effective_role = " ".join(roles) if roles else single_role
            location = (payload.get("location") or "").strip()
            resume_text = (payload.get("resumeText") or "").strip()
            if not roles or not resume_text:
                return json_response(self, {"error": "roles and resumeText are required"}, status=400)
            req = PipelineRequest(
                role=effective_role,
                roles=roles,
                location=location,
                resume_text=resume_text,
                ai_mode=(payload.get("aiMode") or "groq").strip().lower(),
                job_type=(payload.get("jobType") or "all").strip() or "all",
            )
            task_id = self.orchestrator.start(req)
            return json_response(self, {"taskId": task_id})

        if parsed.path == "/api/auto-apply/start":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)

            user_key = (payload.get("userKey") or "").strip()
            profile = prepare_profile_for_apply(payload.get("profile") or {}, user_key=user_key)
            jobs = payload.get("jobs") or []
            if not isinstance(profile, dict) or not isinstance(jobs, list):
                return json_response(self, {"error": "Invalid payload format"}, status=400)
            if not jobs:
                return json_response(self, {"error": "No jobs provided"}, status=400)

            task_id = uuid.uuid4().hex
            set_task(
                task_id,
                taskId=task_id,
                status="running",
                stage="queued",
                percent=2,
                logs=[],
                processed=0,
                successCount=0,
                manualCount=0,
                success=[],
                manual=[],
            )
            append_task_log(task_id, "Auto-apply task created")
            append_task_log(
                task_id,
                "Profile prepared: "
                f"name={'yes' if profile.get('fullName') else 'no'}, "
                f"email={'yes' if profile.get('email') else 'no'}, "
                f"resume_path={'yes' if profile.get('resumePath') else 'no'}, "
                f"auto_submit={'on' if profile.get('autoSubmit') else 'off'}",
            )
            thread = threading.Thread(target=self._run_auto_apply_task, args=(task_id, profile, jobs), daemon=True)
            thread.start()
            return json_response(self, {"taskId": task_id})

        if parsed.path == "/api/learning/feedback":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            url = (payload.get("url") or "").strip()
            platform = (payload.get("platform") or "unknown").strip().lower()
            completed = bool(payload.get("completed", True))
            if not url:
                return json_response(self, {"error": "url is required"}, status=400)
            if completed:
                self.engine.record_human_completion(url=url, platform=platform)
            return json_response(self, {"ok": True})

        if parsed.path == "/api/application-answers":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            url = (payload.get("url") or "").strip()
            platform = (payload.get("platform") or "unknown").strip().lower()
            answers = payload.get("answers") or []
            if not url:
                return json_response(self, {"error": "url is required"}, status=400)
            if not isinstance(answers, list):
                return json_response(self, {"error": "answers must be a list"}, status=400)
            save_answers(url=url, platform=platform, answers=answers)
            return json_response(self, {"ok": True, "saved": len(answers)})

        if parsed.path == "/api/jobs-search":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            profile = payload.get("profile") or {}
            if not isinstance(profile, dict):
                return json_response(self, {"error": "profile must be object"}, status=400)

            role = (profile.get("role") or "").strip()
            location = (profile.get("location") or "").strip()
            job_type = (profile.get("jobType") or "all").strip() or "all"
            resume_text = profile.get("resumeText") or ""
            ai_mode = (profile.get("aiMode") or "heuristic").strip().lower()
            ai_key = get_groq_api_key()
            limit = int(payload.get("limit", 260))

            ranked, errors, ai_used = self._fetch_rank_jobs(role, location, job_type, resume_text, limit, ai_mode, ai_key)
            ready_count = sum(1 for j in ranked if j.get("autoApplyReady"))
            return json_response(
                self,
                {
                    "jobs": ranked,
                    "count": len(ranked),
                    "autoApplyReadyCount": ready_count,
                    "sources": ["Arbeitnow", "Remotive", "RemoteOK"],
                    "errors": errors,
                    "aiUsed": ai_used,
                },
            )

        if parsed.path == "/api/extract-resume":
            content_length = int(self.headers.get("Content-Length", "0"))
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)
            pdf_data, filename = parse_multipart_resume(content_type, body)
            if not pdf_data:
                return json_response(self, {"error": "Missing resume PDF file"}, status=400)

            text, error = extract_pdf_text(pdf_data)
            if error:
                return json_response(self, {"error": error}, status=400)
            saved_path = save_uploaded_resume(pdf_data, filename or "resume.pdf")
            return json_response(self, {"text": text, "filePath": saved_path})

        if parsed.path == "/api/auto-apply":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)

            user_key = (payload.get("userKey") or "").strip()
            profile = prepare_profile_for_apply(payload.get("profile") or {}, user_key=user_key)
            jobs = payload.get("jobs") or []
            if not isinstance(profile, dict) or not isinstance(jobs, list):
                return json_response(self, {"error": "Invalid payload format"}, status=400)
            if not jobs:
                return json_response(self, {"error": "No jobs provided"}, status=400)

            success = []
            manual = []

            ready_jobs = []
            for j in jobs:
                url = j.get("applyUrl") or ""
                if not url:
                    manual.append(build_manual_handoff(profile, j, "", "unknown", "Missing apply URL"))
                    continue
                platform = (j.get("platform") or detect_platform(url, "")[0].platform).lower()
                is_ready = platform in SUPPORTED_AUTOMATION_PLATFORMS
                if not is_ready:
                    manual.append(
                        build_manual_handoff(
                            profile,
                            j,
                            url,
                            platform,
                            "Platform not supported for auto-submit yet; manual apply required",
                        )
                    )
                    continue
                ready_jobs.append(j)

            apply_inputs = [build_apply_input(profile, j) for j in ready_jobs]
            if not apply_inputs and not manual:
                return json_response(self, {"error": "No auto-apply-ready jobs in selection"}, status=400)

            import asyncio

            if apply_inputs:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    results = loop.run_until_complete(self.engine.run_batch(apply_inputs))
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
            else:
                results = []
            for r, src in zip(results, apply_inputs):
                row = {
                    "url": src.url,
                    "platform": r.platform,
                    "status": r.status.value,
                    "message": r.message,
                    "evidence": r.evidence,
                }
                if r.status.value == "applied":
                    success.append(row)
                else:
                    manual.append(row)

            return json_response(
                self,
                {
                    "processed": len(results) + len(manual),
                    "attemptedAutoApply": len(results),
                    "successCount": len(success),
                    "manualCount": len(manual),
                    "success": success,
                    "manual": manual,
                    "reportFiles": {
                        "history": "automation/apply_history.json",
                        "success": "automation/applied_successfully.json",
                        "manual": "automation/manual_required.json",
                    },
                },
            )

        if parsed.path == "/api/profile-suggest":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            resume_text = (payload.get("resumeText") or "").strip()
            if not resume_text:
                return json_response(self, {"error": "resumeText is required"}, status=400)

            ai_mode = (payload.get("aiMode") or "heuristic").lower()
            api_key = get_groq_api_key()
            profile = heuristic_profile_suggest(resume_text)
            mode_used = "heuristic"
            if ai_mode == "groq" and api_key:
                try:
                    profile = groq_profile_suggest(api_key, resume_text)
                    mode_used = "groq"
                except Exception:
                    mode_used = "heuristic-fallback"

            return json_response(self, {"profile": profile, "modeUsed": mode_used})

        if parsed.path == "/api/tailor":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            resume_text = (payload.get("resumeText") or "").strip()
            role = (payload.get("role") or "").strip()
            job = payload.get("job") or {}
            if not resume_text or not job:
                return json_response(self, {"error": "resumeText and job are required"}, status=400)
            api_key = get_groq_api_key()
            result = groq_full_tailor(api_key, resume_text, role, job) if api_key else heuristic_full_tailor(resume_text, role, job)
            return json_response(self, result)

        if parsed.path == "/api/yc/pitch":
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                return json_response(self, {"error": "Invalid JSON"}, status=400)
            company = payload.get("company") or {}
            resume_text = (payload.get("resumeText") or "").strip()
            if not company.get("name"):
                return json_response(self, {"error": "company.name is required"}, status=400)
            pitch = generate_yc_pitch(company, resume_text)
            return json_response(self, pitch)

        return json_response(self, {"error": "Not found"}, status=404)

    def _fetch_rank_jobs(self, role, location, job_type, resume_text, limit, ai_mode, ai_key):
        jobs = []
        errors = []
        fns = [
            ("Arbeitnow", lambda: fetch_arbeitnow()),
            ("Remotive", lambda: fetch_remotive(role)),
            ("RemoteOK", lambda: fetch_remoteok()),
        ]
        with ThreadPoolExecutor(max_workers=3) as ex:
            future_map = {ex.submit(fn): name for name, fn in fns}
            for fut, name in [(f, future_map[f]) for f in future_map]:
                try:
                    jobs.extend(fut.result())
                except Exception as exc:
                    errors.append(f"{name}: {exc}")

        ranked = filter_and_rank_jobs(jobs, role, location, job_type, resume_text=resume_text, limit=limit)
        ai_used = False
        if ai_mode == "groq" and ai_key and ranked:
            try:
                reranked, ai_used = ai_rerank_jobs_with_groq(ai_key, ranked, role, location, job_type, resume_text)
                ranked = reranked[:limit]
            except Exception as exc:
                errors.append(f"Groq rerank failed: {exc}")
                ai_used = False

        ranked = enrich_platform_apply_urls(ranked, max_checks=min(60, len(ranked)))
        # Prefer automation-ready jobs first while preserving relevance score ordering.
        ranked.sort(key=lambda j: (1 if j.get("autoApplyReady") else 0, int(j.get("score", 0))), reverse=True)

        if not ranked:
            ranked = [annotate_apply_capability(r) for r in fallback_jobs()]
        return ranked, errors, ai_used

    def _run_auto_apply_task(self, task_id, profile, jobs):
        try:
            profile = prepare_profile_for_apply(profile)
            ready_jobs = []
            success = []
            manual = []
            total = len(jobs)

            append_task_log(task_id, f"Received {total} jobs")
            set_task(task_id, stage="profile_preflight", percent=8)
            if not profile.get("email"):
                append_task_log(task_id, "Warning: email missing; some sites may fail")
            if not profile.get("fullName"):
                append_task_log(task_id, "Warning: full name missing; some sites may fail")
            if not profile.get("resumePath"):
                append_task_log(task_id, "Warning: resume file path missing; upload field may fail")

            set_task(task_id, stage="screening", percent=16)
            for j in jobs:
                url = j.get("applyUrl") or ""
                if not url:
                    manual.append(build_manual_handoff(profile, j, "", "unknown", "Missing apply URL"))
                    continue
                platform = (j.get("platform") or detect_platform(url, "")[0].platform).lower()
                is_ready = platform in SUPPORTED_AUTOMATION_PLATFORMS
                if not is_ready:
                    manual.append(
                        build_manual_handoff(
                            profile,
                            j,
                            url,
                            platform,
                            "Platform not supported for auto-submit yet; manual apply required",
                        )
                    )
                    continue
                ready_jobs.append(j)

            append_task_log(task_id, f"Auto-ready jobs: {len(ready_jobs)} | Manual-only: {len(manual)}")
            set_task(task_id, stage="applying", percent=24)

            import asyncio

            done = 0
            for j in ready_jobs:
                apply_input = build_apply_input(profile, j)
                append_task_log(
                    task_id,
                    f"Applying via {j.get('platform','unknown')} -> {j.get('title','')[:70]}",
                )
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(self.engine.run(apply_input))
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
                row = {
                    "url": apply_input.url,
                    "platform": result.platform,
                    "status": result.status.value,
                    "message": result.message,
                    "evidence": result.evidence,
                }
                if result.status.value == "applied":
                    success.append(row)
                    append_task_log(task_id, f"Applied: {j.get('company','')} | {j.get('title','')[:50]}")
                else:
                    manual.append(row)
                    append_task_log(task_id, f"Manual: {row['message'][:90]}")
                done += 1
                pct = 24 + int((done / max(1, len(ready_jobs))) * 72)
                set_task(
                    task_id,
                    percent=min(pct, 96),
                    processed=done + (len(jobs) - len(ready_jobs)),
                    successCount=len(success),
                    manualCount=len(manual),
                    success=success[-50:],
                    manual=manual[-80:],
                )
                append_task_log(task_id, f"Processed {done}/{len(ready_jobs)} ready jobs")

            self.engine.store.write_reports()
            set_task(
                task_id,
                status="completed",
                stage="completed",
                percent=100,
                processed=len(jobs),
                successCount=len(success),
                manualCount=len(manual),
                success=success,
                manual=manual,
                reportFiles={
                    "history": "automation/apply_history.json",
                    "success": "automation/applied_successfully.json",
                    "manual": "automation/manual_required.json",
                },
            )
            append_task_log(task_id, "Auto-apply completed")
        except Exception as exc:
            set_task(task_id, status="failed", stage="failed", percent=100, error=str(exc))
            append_task_log(task_id, f"Auto-apply failed: {exc}")

    def _run_pipeline_task(self, task_id, payload):
        try:
            role = (payload.get("role") or "").strip()
            location = (payload.get("location") or "").strip()
            resume_text = (payload.get("resumeText") or "").strip()
            ai_mode = (payload.get("aiMode") or "groq").strip().lower()
            ai_key = get_groq_api_key()
            job_type = (payload.get("jobType") or "all").strip() or "all"

            set_task(task_id, stage="parsing_resume", percent=12)
            append_task_log(task_id, "Parsing resume and inferring profile")
            inferred = heuristic_profile_suggest(resume_text)
            mode_used = "heuristic"
            if ai_mode == "groq" and ai_key:
                try:
                    inferred = groq_profile_suggest(ai_key, resume_text)
                    mode_used = "groq"
                except Exception:
                    mode_used = "heuristic-fallback"
            set_task(task_id, inferredProfile=inferred, profileMode=mode_used, percent=30)

            set_task(task_id, stage="fetching_jobs", percent=50)
            append_task_log(task_id, "Fetching jobs from all sources")
            ranked, errors, ai_used = self._fetch_rank_jobs(
                role=role,
                location=location,
                job_type=job_type,
                resume_text=resume_text,
                limit=260,
                ai_mode=ai_mode,
                ai_key=ai_key,
            )
            ready_count = sum(1 for j in ranked if j.get("autoApplyReady"))
            append_task_log(task_id, f"Fetched {len(ranked)} jobs, auto-ready {ready_count}")
            if ai_used:
                append_task_log(task_id, "AI reranking enabled for job quality")
            if errors:
                append_task_log(task_id, "Some sources failed but pipeline recovered")

            set_task(
                task_id,
                status="completed",
                stage="completed",
                percent=100,
                jobs=ranked,
                aiUsed=ai_used,
                errors=errors,
                autoApplyReadyCount=ready_count,
            )
        except Exception as exc:
            set_task(task_id, status="failed", stage="failed", percent=100, error=str(exc))
            append_task_log(task_id, f"Pipeline failed: {exc}")


def fetch_yc_companies(batch="W25,S24,W24", limit=60):
    """Fetch YC companies via their public Algolia search."""
    batches = [b.strip() for b in batch.split(",")]
    algolia_url = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
    filters = " OR ".join(f"batch:{b}" for b in batches)
    payload = json.dumps({
        "requests": [{
            "indexName": "ycombinator_companies",
            "params": f"hitsPerPage={limit}&filters={urllib.parse.quote(filters)}&attributesToRetrieve=name,one_liner,website,batch,team_size,status,tags,long_description"
        }]
    }).encode("utf-8")
    req = urllib.request.Request(
        algolia_url,
        data=payload,
        headers={
            "x-algolia-application-id": "45BWZJ1SGC",
            "x-algolia-api-key": "Zjk5ZmVlMWUwNDliNjA1OTgzZmE3OWUwZDFhMThiZTYwNDNlODcyYTczZjMwMmFlMDMzMTQ3Mzg4NWVjNzJjNGE1YTkzNGI2NWI1MjIzOTA4ZDJmYTFlOWMy",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = (data.get("results") or [{}])[0]
        hits = results.get("hits") or []
        return [
            {
                "name": h.get("name") or "",
                "oneLiner": h.get("one_liner") or "",
                "website": h.get("website") or "",
                "batch": h.get("batch") or "",
                "teamSize": h.get("team_size") or "",
                "status": h.get("status") or "",
                "tags": h.get("tags") or [],
                "description": h.get("long_description") or h.get("one_liner") or "",
            }
            for h in hits
        ]
    except Exception:
        return [
            {"name": "Harvey AI", "oneLiner": "AI for lawyers", "website": "https://harvey.ai", "batch": "W23", "tags": ["legaltech", "ai"]},
            {"name": "Cognition (Devin)", "oneLiner": "AI software engineer", "website": "https://cognition.ai", "batch": "W24", "tags": ["devtools", "ai"]},
            {"name": "Factory", "oneLiner": "Autonomous software development", "website": "https://factory.ai", "batch": "S23", "tags": ["devtools", "ai"]},
        ]


def generate_yc_pitch(company, resume_text=""):
    api_key = get_groq_api_key()
    name = company.get("name", "")
    description = company.get("description") or company.get("oneLiner") or ""
    website = company.get("website") or ""
    tags = ", ".join(company.get("tags") or [])

    if api_key:
        try:
            prompt = f"""You are a strategic advisor helping a talented engineer approach a startup creatively.

Company: {name}
What they do: {description}
Website: {website}
Tags: {tags}
Candidate resume snippet: {resume_text[:1500]}

Generate JSON with these keys:

"productAnalysis": 2-3 sentences analyzing what the product does well and what gap/opportunity exists
"valueAdd": 1-2 specific, concrete enhancements or features the candidate could build/improve (be technical and specific, reference the product)
"email": A short outreach email (120-160 words) to the founder. Structure:
  - Line 1: One specific observation about their product (show you've studied it)
  - Line 2-3: Specific enhancement/feature idea that would add real value (technical specificity wins)
  - Line 4-5: 1-2 relevant achievements from the resume that prove you can build it
  - Close: "Happy to prototype this in a weekend if you're interested."
  RULES: No generic phrases. Do NOT say "I'm a big fan" or "I love what you're building". Be direct, specific, and brief. Subject line included.
"subjectLine": Email subject line (compelling, specific, <60 chars)"""

            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
            }
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "JobMint/4.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
            data = json.loads(content)
            return {
                "productAnalysis": str(data.get("productAnalysis", "")),
                "valueAdd": str(data.get("valueAdd", "")),
                "email": str(data.get("email", "")),
                "subjectLine": str(data.get("subjectLine", "")),
                "aiUsed": True,
            }
        except Exception:
            pass

    return {
        "productAnalysis": f"{name} is working on {description}. There may be opportunities to improve {tags.split(',')[0].strip() if tags else 'core functionality'}.",
        "valueAdd": f"A feature to enhance {name}'s core offering around {tags.split(',')[0].strip() if tags else 'the product'} could be valuable.",
        "email": f"Subject: Quick idea for {name}\n\nHi,\n\nI was exploring {name}'s product and noticed an opportunity to improve {tags.split(',')[0].strip() if tags else 'the user experience'}.\n\nI'd love to discuss a specific feature that could add real value. Happy to prototype in a weekend.\n\nBest",
        "subjectLine": f"Quick idea for {name}",
        "aiUsed": False,
    }


def main():
    os.chdir(BASE_DIR)
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), JobMintHandler)
    print(f"JobMint server running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
