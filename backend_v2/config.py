import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GROQ_API_KEY = ""
SUPPORTED_AUTOMATION_PLATFORMS = {"greenhouse", "lever", "workday", "linkedin", "indeed"}
SUPPORTED_PLATFORM_DOMAINS = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "linkedin.com",
    "indeed.com",
)


def load_env_file() -> None:
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


def get_groq_api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip() or DEFAULT_GROQ_API_KEY
