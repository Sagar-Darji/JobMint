import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import ApplyInput, AutomationEngine  # noqa: E402


def load_json(path: str) -> dict | list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def to_apply_inputs(profile: dict, jobs: list[dict], auto_submit: bool) -> list[ApplyInput]:
    result: list[ApplyInput] = []
    for job in jobs:
        result.append(
            ApplyInput(
                url=job.get("url", ""),
                role=job.get("role", profile.get("role", "")),
                location=job.get("location", profile.get("location", "")),
                job_type=job.get("job_type", profile.get("job_type", "all")),
                resume_text=profile.get("resume_text", ""),
                full_name=profile.get("full_name", ""),
                email=profile.get("email", ""),
                phone=profile.get("phone", ""),
                resume_path=profile.get("resume_path", ""),
                tailored_summary=job.get("tailored_summary", ""),
                tailored_intro=job.get("tailored_intro", ""),
                auto_submit=auto_submit,
            )
        )
    return result


async def main_async(profile_path: str, jobs_path: str, auto_submit: bool):
    profile = load_json(profile_path)
    jobs = load_json(jobs_path)

    if not isinstance(profile, dict):
        raise ValueError("profile JSON must be an object")
    if not isinstance(jobs, list):
        raise ValueError("jobs JSON must be an array")

    engine = AutomationEngine()
    inputs = to_apply_inputs(profile, jobs, auto_submit=auto_submit)
    results = await engine.run_batch(inputs)

    success = sum(1 for r in results if r.status.value == "applied")
    manual = len(results) - success
    print(f"Processed {len(results)} jobs | applied={success} | manual_or_failed={manual}")
    print("Saved reports:")
    print("- automation/applied_successfully.json")
    print("- automation/manual_required.json")


def main():
    parser = argparse.ArgumentParser(description="Run batch job applications with auto platform selection")
    parser.add_argument("--profile", required=True, help="Path to profile JSON")
    parser.add_argument("--jobs", required=True, help="Path to jobs JSON")
    parser.add_argument("--auto-submit", action="store_true", help="Attempt submit clicks")
    args = parser.parse_args()

    asyncio.run(main_async(args.profile, args.jobs, args.auto_submit))


if __name__ == "__main__":
    main()
