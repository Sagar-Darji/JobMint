from automation import ApplyInput, AutomationEngine

from ..models import JobRecord


class ApplyAgent:
    def __init__(self):
        self.engine = AutomationEngine()

    def apply_jobs(self, profile: dict, jobs: list[JobRecord]) -> dict:
        inputs = []
        for j in jobs:
            if not j.auto_apply_ready or not j.apply_url:
                continue
            inputs.append(
                ApplyInput(
                    url=j.apply_url,
                    role=profile.get("role", "") or j.title,
                    location=profile.get("location", "") or j.location,
                    job_type=profile.get("jobType", "all"),
                    resume_text=profile.get("resumeText", ""),
                    full_name=profile.get("fullName", ""),
                    email=profile.get("email", ""),
                    phone=profile.get("phone", ""),
                    resume_path=profile.get("resumePath", ""),
                    tailored_summary=profile.get("tailoredSummary", ""),
                    tailored_intro=profile.get("tailoredIntro", ""),
                    auto_submit=bool(profile.get("autoSubmit", False)),
                )
            )

        import asyncio

        results = asyncio.run(self.engine.run_batch(inputs)) if inputs else []
        success = []
        manual = []
        for r, src in zip(results, inputs):
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
        return {
            "processed": len(results),
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
        }
