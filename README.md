# JobMint

A lightweight but professional portal to:
- upload/paste resume and target role
- choose preferences like location and job type (remote/on-site)
- fetch jobs from multiple sources through a local backend (CORS-safe)
- generate tailored resume tweaks per selected role
- auto-apply to all filtered jobs in bulk
- track job status (Saved, Applied, Interview, Offer, Rejected)

## Run (recommended)

```bash
python3 server.py
```

Open:
- `http://127.0.0.1:8080`

## Features implemented

- Multi-source job aggregation backend:
  - Arbeitnow API
  - Remotive API
  - fallback data if external sources fail
- PDF resume upload and extraction:
  - upload `.pdf`
  - click "Extract PDF Text" to populate resume textarea
- Tailored apply kit:
  - targeted summary
  - improved bullets
  - quick intro message
- Bulk auto-apply runner:
  - applies to all currently filtered jobs
  - marks each as `Applied` in tracker
  - opens apply links in new tabs
  - downloads an `auto-apply-pack` text file with per-job tailored messaging
- One-click tracker board:
  - auto-add when selecting a job
  - update status in a dropdown
  - quick apply link
- Persistent profile/tracker via browser localStorage

## Dependency for PDF extraction

Install once:

```bash
pip install pypdf
```

If `pypdf` is not installed, job fetch and tracker still work.

## Auto-apply note

This app can batch open apply links and generate tailored application content for each job.
Direct background form submission on external job platforms is generally blocked by platform security, anti-bot protections, and browser restrictions.

## Universal automation design scaffold

Added a plugin-based automation module:
- `AUTOMATION_DESIGN.md` (architecture and strategy selection model)
- `automation/` (detector + adapter registry + orchestration engine)

Run demo selector:

```bash
python3 automation/demo.py
```

What it does now:
- auto-detects likely platform from URL/DOM excerpt
- chooses best adapter automatically
- returns normalized apply outcome with fallback handling

What to implement next:
- replace placeholder adapter `apply()` methods with real Playwright flows per platform

## Batch auto-apply with result storage

Create a profile JSON (`profile.json`):

```json
{
  "full_name": "Alex Candidate",
  "email": "alex@example.com",
  "phone": "+15551234567",
  "resume_text": "...",
  "resume_path": "/absolute/path/resume.pdf",
  "role": "Software Engineer",
  "location": "Remote",
  "job_type": "remote"
}
```

Create jobs JSON (`jobs.json`):

```json
[
  {"url": "https://boards.greenhouse.io/company/jobs/1", "role": "Software Engineer", "location": "Remote"},
  {"url": "https://jobs.lever.co/company/2", "role": "Software Engineer", "location": "Remote"},
  {"url": "https://www.linkedin.com/jobs/view/3", "role": "Software Engineer", "location": "Remote"}
]
```

Run:

```bash
python3 automation/run_batch.py --profile profile.json --jobs jobs.json --auto-submit
```

Outputs:
- `automation/apply_history.json`
- `automation/applied_successfully.json`
- `automation/manual_required.json`

`manual_required.json` is the list user should open and fill manually where automation could not complete.

## New high-relevance fetch + backend auto-apply flow

The app now uses backend-only intelligence for both operations:
- `/api/jobs`:
  - aggregates from `Arbeitnow`, `Remotive`, `RemoteOK`
  - scores/ranks jobs by role keywords, resume overlap, title match, location fit, job-type fit, freshness
  - returns `score` and ranking reasons
  - marks each job with `platform` + `autoApplyReady`
- `/api/auto-apply`:
  - runs automation only for auto-apply-ready platforms
  - returns unsupported links as manual-required (instead of hanging/failing)
  - stores all outcomes in:
    - `automation/apply_history.json`
    - `automation/applied_successfully.json`
    - `automation/manual_required.json`
- `/api/profile-suggest`:
  - auto-fills profile setup fields from resume text
  - supports Groq mode with heuristic fallback

## Multi-Agent v2 backend

The `/api/pipeline/start` + `/api/pipeline/status` flow now uses `backend_v2/`:
- `ProfileAgent` (resume -> inferred profile)
- `SourceAgent` (multi-source normalized ingestion)
- `RelevanceAgent` (heuristic + Groq rerank)
- `LinkResolverAgent` (extract direct ATS links and mark `autoApplyReady`)
- `PipelineOrchestrator` (stage orchestration + realtime task status)

This is now the primary path for realtime UI progress and job discovery.

For best automation results install:

```bash
pip install playwright pypdf
playwright install
```

For Groq tailoring mode in UI (free tier friendly):
- set key in form field, or
- export env var before starting server:

```bash
export GROQ_API_KEY=gsk_...
python3 server.py
```

Project `.env` is auto-loaded at server startup, so you can also store:

```bash
GROQ_API_KEY=gsk_...
```

Resume upload behavior:
- `/api/extract-resume` now stores uploaded PDF under `uploads/` and returns `filePath`
- UI auto-fills `Resume File Path` from uploaded file automatically

Apply behavior:
- `Auto Apply Selected Job` applies only the currently selected job
- `Auto Apply Filtered Jobs` applies all auto-ready jobs in current filters
