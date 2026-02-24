import json
import urllib.request

from ..config import get_groq_api_key
from ..models import JobRecord
from ..utils import parse_iso_age_score, split_keywords


class RelevanceAgent:
    def rank(self, jobs: list[JobRecord], role: str, location: str, job_type: str, resume_text: str, ai_mode: str) -> tuple[list[JobRecord], bool]:
        role_keywords = split_keywords(role)
        resume_keywords = [w for w in split_keywords(resume_text) if len(w) >= 4][:80]

        scored: list[JobRecord] = []
        for j in jobs:
            hay = f"{j.title} {j.description} {j.company}".lower()
            role_hits = sum(1 for kw in role_keywords if kw in hay)
            title_bonus = sum(2 for kw in role_keywords[:6] if kw in j.title.lower())
            resume_overlap = sum(1 for kw in set(resume_keywords) if kw in hay)
            loc_score = 0
            if location:
                ll = j.location.lower()
                wl = location.lower()
                if wl in ll:
                    loc_score = 4
                elif wl == "remote" and j.remote:
                    loc_score = 4
                else:
                    loc_score = -1
            type_score = 0
            if job_type == "remote":
                type_score = 3 if j.remote else -3
            elif job_type == "onsite":
                type_score = 2 if not j.remote else -2
            freshness = parse_iso_age_score(j.posted_at)
            score = (role_hits * 4) + title_bonus + min(resume_overlap, 8) + loc_score + type_score + freshness
            j.score = int(score)
            j.score_reason = {
                "role_hits": role_hits,
                "title_bonus": title_bonus,
                "resume_overlap": min(resume_overlap, 8),
                "location_score": loc_score,
                "type_score": type_score,
                "freshness": freshness,
            }
            # Hard quality gate: keep only jobs with meaningful role/title match.
            if role_keywords:
                title_role_match = any(kw in j.title.lower() for kw in role_keywords[:8])
                if not title_role_match and role_hits == 0:
                    continue
            if j.score < 8:
                continue
            scored.append(j)

        scored.sort(key=lambda x: x.score, reverse=True)

        ai_used = False
        if ai_mode == "groq" and scored:
            key = get_groq_api_key()
            if key:
                try:
                    scored = self._ai_prune(scored[:140], role, location, job_type, resume_text, key)
                    ai_used = True
                except Exception:
                    ai_used = False
        return scored, ai_used

    def _ai_prune(self, jobs: list[JobRecord], role: str, location: str, job_type: str, resume_text: str, key: str) -> list[JobRecord]:
        lines = []
        for j in jobs:
            lines.append(
                f"id={j.id} | title={j.title} | company={j.company} | location={j.location} | desc={(j.description or '')[:180]}"
            )
        prompt = (
            "Return JSON only with key keep as array of objects {id, score, reason}. Keep only highly relevant jobs.\n"
            f"role={role}\nlocation={location}\njob_type={job_type}\nresume={(resume_text or '')[:1600]}\n"
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
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "JobMint/3.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
        keep = (json.loads(content).get("keep") or [])
        keep_map = {}
        for row in keep:
            rid = str((row or {}).get("id", "")).strip()
            if rid:
                keep_map[rid] = (int((row or {}).get("score", 0)), str((row or {}).get("reason", "")))
        out = []
        for j in jobs:
            if j.id not in keep_map:
                continue
            s, r = keep_map[j.id]
            j.ai_score = s
            j.ai_reason = r
            j.score = j.score + (s // 5)
            out.append(j)
        # Guardrail: if model over-prunes, blend with heuristic ranking so volume stays healthy.
        if len(out) < max(25, int(len(jobs) * 0.25)):
            selected = {j.id for j in out}
            remainder = [j for j in jobs if j.id not in selected]
            remainder.sort(key=lambda x: x.score, reverse=True)
            out.extend(remainder[: max(40, len(jobs) // 2)])
        out.sort(key=lambda x: (x.ai_score, x.score), reverse=True)
        return out
