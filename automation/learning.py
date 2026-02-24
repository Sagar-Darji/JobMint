import json
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat()


class LearningStore:
    def __init__(self, path: str = "automation/learning_state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default(self):
        return {"platform": {}, "domains": {}}

    def _load(self):
        if not self.path.exists():
            return self._default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self._default()
            data.setdefault("platform", {})
            data.setdefault("domains", {})
            return data
        except Exception:
            return self._default()

    def _save(self, data):
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _touch(self, bucket: dict, key: str):
        row = bucket.get(key) or {"attempts": 0, "auto_success": 0, "human_completed": 0, "updatedAt": _now()}
        bucket[key] = row
        return row

    def record_auto(self, platform: str, domain: str, success: bool):
        data = self._load()
        p = self._touch(data["platform"], platform or "unknown")
        p["attempts"] += 1
        if success:
            p["auto_success"] += 1
        p["updatedAt"] = _now()

        if domain:
            d = self._touch(data["domains"], domain)
            d["attempts"] += 1
            if success:
                d["auto_success"] += 1
            d["updatedAt"] = _now()
        self._save(data)

    def record_human_completion(self, platform: str, domain: str):
        data = self._load()
        p = self._touch(data["platform"], platform or "unknown")
        p["human_completed"] += 1
        p["updatedAt"] = _now()
        if domain:
            d = self._touch(data["domains"], domain)
            d["human_completed"] += 1
            d["updatedAt"] = _now()
        self._save(data)

    def prior(self, platform: str, domain: str = "") -> float:
        data = self._load()
        p = data["platform"].get(platform or "unknown", {})
        attempts = int(p.get("attempts", 0))
        auto_success = int(p.get("auto_success", 0))
        human_completed = int(p.get("human_completed", 0))

        # Smoothed prior in [0.35, 1.10], with small boost for repeated human completions.
        platform_rate = (auto_success + 2) / (attempts + 4) if attempts >= 0 else 0.5
        platform_boost = min(0.15, human_completed * 0.01)
        base = 0.35 + (platform_rate * 0.6) + platform_boost

        if not domain:
            return max(0.35, min(1.10, base))

        d = data["domains"].get(domain, {})
        da = int(d.get("attempts", 0))
        ds = int(d.get("auto_success", 0))
        dh = int(d.get("human_completed", 0))
        domain_rate = (ds + 1) / (da + 2) if da >= 0 else 0.5
        domain_boost = min(0.08, dh * 0.005)
        score = base * (0.8 + (domain_rate * 0.4) + domain_boost)
        return max(0.35, min(1.10, score))

    def summary(self):
        data = self._load()
        return {
            "platforms": data.get("platform", {}),
            "domains": data.get("domains", {}),
        }
