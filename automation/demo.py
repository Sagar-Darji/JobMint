import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import ApplyInput, AutomationEngine


async def main():
    engine = AutomationEngine()
    sample = ApplyInput(
        url="https://www.linkedin.com/jobs/view/123456",
        role="Software Engineer",
        location="Remote",
        job_type="remote",
        resume_text="Experienced software engineer...",
        full_name="Alex Candidate",
        email="alex@example.com",
        phone="+15551234567",
        resume_path="/tmp/resume.pdf",
        tailored_summary="Backend-focused engineer with distributed systems experience.",
        tailored_intro="Interested in this role and aligned with your platform goals.",
        auto_submit=False,
    )
    result = await engine.run(sample, dom_excerpt="linkedin easy apply")
    print(result)
    print(engine.store.summary())


if __name__ == "__main__":
    asyncio.run(main())
