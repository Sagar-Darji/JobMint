import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import ApplyInput, ApplyResult, ApplyStatus


class ApplyStore:
    def __init__(self, path: str = "automation/apply_history.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, rows: list[dict]) -> None:
        self.path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    def record(self, apply_input: ApplyInput, result: ApplyResult) -> None:
        rows = self._load()
        rows.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "input": asdict(apply_input),
                "result": {
                    "platform": result.platform,
                    "status": result.status.value,
                    "message": result.message,
                    "evidence": result.evidence,
                },
            }
        )
        self._save(rows)

    def summary(self) -> dict:
        rows = self._load()
        success = [r for r in rows if r.get("result", {}).get("status") == ApplyStatus.APPLIED.value]
        manual = [
            r
            for r in rows
            if r.get("result", {}).get("status")
            in (ApplyStatus.NEEDS_HUMAN.value, ApplyStatus.BLOCKED.value, ApplyStatus.FAILED.value)
        ]
        return {"total": len(rows), "success": success, "manual": manual}

    def write_reports(
        self,
        success_path: str = "automation/applied_successfully.json",
        manual_path: str = "automation/manual_required.json",
    ) -> None:
        s = self.summary()
        Path(success_path).write_text(json.dumps(s["success"], indent=2), encoding="utf-8")
        Path(manual_path).write_text(json.dumps(s["manual"], indent=2), encoding="utf-8")
