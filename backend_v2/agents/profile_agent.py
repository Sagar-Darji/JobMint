import json
import re
import urllib.request

from ..config import get_groq_api_key


class ProfileAgent:
    def infer(self, resume_text: str, ai_mode: str = "groq") -> tuple[dict, str]:
        basic = self._heuristic(resume_text)
        if ai_mode != "groq":
            return basic, "heuristic"
        key = get_groq_api_key()
        if not key:
            return basic, "heuristic-no-key"
        try:
            return self._groq(resume_text, key), "groq"
        except Exception:
            return basic, "heuristic-fallback"

    def _heuristic(self, text: str) -> dict:
        first = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
        email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or "")
        phone = re.search(r"(\+?\d[\d\-\s()]{8,}\d)", text or "")
        role = ""
        low = (text or "").lower()
        for pat in [
            r"data engineer",
            r"software engineer",
            r"backend engineer",
            r"frontend engineer",
            r"full stack",
            r"data scientist",
            r"ml engineer",
        ]:
            if re.search(pat, low):
                role = pat.title()
                break
        return {
            "fullName": first if len(first.split()) <= 6 else "",
            "email": email.group(0) if email else "",
            "phone": phone.group(0) if phone else "",
            "role": role,
            "location": "Remote" if "remote" in low else "",
        }

    def _groq(self, resume_text: str, key: str) -> dict:
        prompt = (
            "Extract candidate profile from resume. Return JSON only with keys fullName,email,phone,role,location.\n"
            f"Resume:\n{(resume_text or '')[:5000]}"
        )
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
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
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content")) or "{}"
        data = json.loads(content)
        return {
            "fullName": str(data.get("fullName", "")).strip(),
            "email": str(data.get("email", "")).strip(),
            "phone": str(data.get("phone", "")).strip(),
            "role": str(data.get("role", "")).strip(),
            "location": str(data.get("location", "")).strip(),
        }
