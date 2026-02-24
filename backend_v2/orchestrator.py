import threading
import uuid
from dataclasses import asdict

from .agents.link_resolver_agent import LinkResolverAgent
from .agents.profile_agent import ProfileAgent
from .agents.relevance_agent import RelevanceAgent
from .agents.source_agent import SourceAgent
from .models import PipelineRequest, PipelineState


class PipelineOrchestrator:
    def __init__(self):
        self.profile_agent = ProfileAgent()
        self.source_agent = SourceAgent()
        self.relevance_agent = RelevanceAgent()
        self.link_agent = LinkResolverAgent()
        self._lock = threading.Lock()
        self._tasks: dict[str, PipelineState] = {}

    def start(self, request: PipelineRequest) -> str:
        task_id = uuid.uuid4().hex
        state = PipelineState(task_id=task_id, role=request.role, location=request.location, percent=2)
        state.append_log("Pipeline created")
        with self._lock:
            self._tasks[task_id] = state
        thread = threading.Thread(target=self._run, args=(task_id, request), daemon=True)
        thread.start()
        return task_id

    def get(self, task_id: str) -> PipelineState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _update(self, task_id: str, **kwargs):
        with self._lock:
            st = self._tasks.get(task_id)
            if not st:
                return
            for k, v in kwargs.items():
                setattr(st, k, v)

    def _log(self, task_id: str, msg: str):
        with self._lock:
            st = self._tasks.get(task_id)
            if st:
                st.append_log(msg)

    def _run(self, task_id: str, req: PipelineRequest):
        try:
            # Support multi-role: join all roles for unified ranking
            roles = [r.strip() for r in (req.roles or []) if r.strip()]
            effective_role = " ".join(roles) if roles else req.role

            self._update(task_id, stage="parsing_profile", percent=18)
            self._log(task_id, "Inferring profile from resume")
            profile, mode_used = self.profile_agent.infer(req.resume_text, ai_mode=req.ai_mode)
            self._update(task_id, inferred_profile=profile)
            self._log(task_id, f"Profile inference mode: {mode_used}")

            self._update(task_id, stage="fetching_sources", percent=42)
            role_label = ", ".join(roles) if roles else req.role
            self._log(task_id, f"Fetching jobs for roles: {role_label}")
            raw_jobs, errors = self.source_agent.fetch(effective_role)
            self._log(task_id, f"Fetched {len(raw_jobs)} raw jobs from all sources")

            self._update(task_id, stage="ranking", percent=65)
            ranked, ai_used = self.relevance_agent.rank(
                raw_jobs,
                role=effective_role,
                location=req.location,
                job_type=req.job_type,
                resume_text=req.resume_text,
                ai_mode=req.ai_mode,
            )
            self._log(task_id, f"Ranked {len(ranked)} relevant jobs (AI rerank: {ai_used})")

            self._update(task_id, stage="resolving_links", percent=82)
            resolved = self.link_agent.resolve(ranked, max_checks=min(80, len(ranked)))
            final_jobs = resolved[:300]
            ready = sum(1 for j in final_jobs if j.auto_apply_ready)
            self._log(task_id, f"Resolved links — auto-apply-ready: {ready}")

            self._update(
                task_id,
                status="completed",
                stage="completed",
                percent=100,
                ai_used=ai_used,
                errors=errors,
                auto_apply_ready_count=ready,
                jobs=[j.to_dict() for j in final_jobs],
            )
        except Exception as exc:
            self._update(task_id, status="failed", stage="failed", percent=100)
            self._log(task_id, f"Pipeline failed: {exc}")
