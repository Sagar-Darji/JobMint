from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApplyStatus(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    NEEDS_HUMAN = "needs_human"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class ApplyInput:
    url: str
    role: str
    location: str
    job_type: str
    resume_text: str
    preferred_platform: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    resume_path: str = ""
    tailored_summary: str = ""
    tailored_intro: str = ""
    cover_letter: str = ""   # Full AI-generated cover letter
    auto_submit: bool = False
    profile_dir: str = ""           # Per-user browser profile directory
    linkedin_email: str = ""        # Optional: for LinkedIn session bootstrap
    linkedin_password: str = ""     # Optional: for LinkedIn session bootstrap


@dataclass
class DetectionCandidate:
    platform: str
    confidence: float
    reason: str


@dataclass
class ApplyResult:
    platform: str
    status: ApplyStatus
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
