from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class PipelineRequest:
    role: str
    location: str
    resume_text: str
    ai_mode: str = "groq"
    job_type: str = "all"
    # Multi-role: if provided, overrides `role` with a joined string for ranking
    roles: list = field(default_factory=list)


@dataclass
class JobRecord:
    id: str
    title: str
    company: str
    location: str
    source: str
    remote: bool
    apply_url: str
    description: str = ""
    posted_at: str = ""
    platform: str = "generic"
    auto_apply_ready: bool = False
    score: int = 0
    score_reason: dict[str, Any] = field(default_factory=dict)
    ai_score: int = 0
    ai_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["applyUrl"] = out.pop("apply_url")
        out["postedAt"] = out.pop("posted_at")
        out["autoApplyReady"] = out.pop("auto_apply_ready")
        out["scoreReason"] = out.pop("score_reason")
        out["aiScore"] = out.pop("ai_score")
        out["aiReason"] = out.pop("ai_reason")
        return out


@dataclass
class PipelineState:
    task_id: str
    status: str = "running"
    stage: str = "queued"
    percent: int = 0
    logs: list[str] = field(default_factory=list)
    role: str = ""
    location: str = ""
    inferred_profile: dict[str, Any] = field(default_factory=dict)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ai_used: bool = False
    auto_apply_ready_count: int = 0

    def append_log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        self.logs = self.logs[-120:]

    def to_response(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "logs": self.logs,
            "role": self.role,
            "location": self.location,
            "inferredProfile": self.inferred_profile,
            "jobs": self.jobs,
            "errors": self.errors,
            "aiUsed": self.ai_used,
            "autoApplyReadyCount": self.auto_apply_ready_count,
        }
