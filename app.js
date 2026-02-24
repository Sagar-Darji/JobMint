/* ─── STATE ─── */
const PAGE_SIZE = 20;
const state = {
  roles: [],          // string[]
  locations: [],      // string[]
  jobType: "all",     // "all" | "remote" | "onsite"
  resumeText: "",
  resumePath: "",
  fullName: "", email: "", phone: "",
  jobs: [],
  selectedJob: null,
  tracking: [],       // {jobId, title, company, applyUrl, status, updatedAt}
  pipelineTaskId: "",
  pipelineTimer: null,
  backendOk: false,
  kitGenerating: false,
  page: 1,
};

const STATUSES = ["Saved", "Applied", "Interview", "Offer", "Rejected"];

/* ─── ELEMENT REFS ─── */
const $ = id => document.getElementById(id);

/* ─── UTIL ─── */
function esc(v) {
  return String(v || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#039;");
}

function saveState() {
  // 1. Instant localStorage save (sync, no latency)
  const s = {
    roles: state.roles, locations: state.locations, jobType: state.jobType,
    fullName: state.fullName, email: state.email, phone: state.phone,
    resumeText: state.resumeText, resumePath: state.resumePath,
    tracking: state.tracking,
  };
  try { localStorage.setItem("jobmint_state_v4", JSON.stringify(s)); } catch {}

  // 2. Background Supabase sync (async, fire-and-forget)
  setSyncBadge("syncing");
  Promise.all([
    sbSaveProfile({
      full_name:   state.fullName,
      email:       state.email,
      phone:       state.phone,
      roles:       state.roles,
      locations:   state.locations,
      job_type:    state.jobType,
      resume_text: state.resumeText,
      resume_path: state.resumePath,
    }),
    sbSaveTracker(state.tracking),
  ]).then(([p, t]) => setSyncBadge(p && t ? "ok" : "error"))
    .catch(() => setSyncBadge("error"));
}

async function loadState() {
  // Try Supabase first (cross-device persistence)
  setSyncBadge("syncing");
  try {
    const [profile, tracking] = await Promise.all([sbLoadProfile(), sbLoadTracker()]);
    if (profile) {
      state.roles      = profile.roles      || [];
      state.locations  = profile.locations  || [];
      state.jobType    = profile.job_type   || "all";
      state.fullName   = profile.full_name  || "";
      state.email      = profile.email      || "";
      state.phone      = profile.phone      || "";
      state.resumeText = profile.resume_text || "";
      state.resumePath = profile.resume_path || "";
    }
    if (tracking && tracking.length) state.tracking = tracking;
    if (profile || tracking.length) { setSyncBadge("ok"); return; }
  } catch { setSyncBadge("error"); }

  // Fallback: localStorage (offline or first launch)
  try {
    const raw = localStorage.getItem("jobmint_state_v4");
    if (!raw) return;
    const s = JSON.parse(raw);
    state.roles      = s.roles      || [];
    state.locations  = s.locations  || [];
    state.jobType    = s.jobType    || "all";
    state.fullName   = s.fullName   || "";
    state.email      = s.email      || "";
    state.phone      = s.phone      || "";
    state.resumeText = s.resumeText || "";
    state.resumePath = s.resumePath || "";
    state.tracking   = s.tracking   || [];
  } catch {}
}

/* ─── CHIP INPUTS ─── */
function addChip(arr, value, renderFn) {
  const v = value.trim();
  if (!v || arr.includes(v)) return;
  arr.push(v);
  renderFn();
  saveState();
}

function removeChip(arr, value, renderFn) {
  const idx = arr.indexOf(value);
  if (idx !== -1) arr.splice(idx, 1);
  renderFn();
  saveState();
}

function renderRoleChips() {
  const container = $("roleChips");
  container.innerHTML = state.roles.map(r =>
    `<span class="chip">${esc(r)}<button class="chip-remove" data-role="${esc(r)}" title="Remove">×</button></span>`
  ).join("");
  container.querySelectorAll(".chip-remove").forEach(btn => {
    btn.onclick = () => removeChip(state.roles, btn.dataset.role, renderRoleChips);
  });
}

function renderLocationChips() {
  const container = $("locationChips");
  container.innerHTML = state.locations.map(l =>
    `<span class="chip">${esc(l)}<button class="chip-remove" data-loc="${esc(l)}" title="Remove">×</button></span>`
  ).join("");
  container.querySelectorAll(".chip-remove").forEach(btn => {
    btn.onclick = () => removeChip(state.locations, btn.dataset.loc, renderLocationChips);
  });
}

function initChipInput(inputId, arr, renderFn) {
  const input = $(inputId);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      const val = input.value.replace(/,$/, "").trim();
      if (val) { addChip(arr, val, renderFn); input.value = ""; }
    }
    if (e.key === "Backspace" && !input.value && arr.length) {
      arr.pop(); renderFn(); saveState();
    }
  });
  // Click on container focuses input
  const wrapper = input.closest(".chip-input");
  if (wrapper) wrapper.addEventListener("click", () => input.focus());
}

/* ─── WORK TYPE TOGGLE ─── */
function initWorkTypeToggle() {
  $("workTypeGroup").querySelectorAll(".toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $("workTypeGroup").querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.jobType = btn.dataset.type;
      saveState();
    });
  });
}

function syncWorkTypeUI() {
  $("workTypeGroup").querySelectorAll(".toggle-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.type === state.jobType);
  });
}

/* ─── RESUME UPLOAD ─── */
function initUpload() {
  const zone = $("uploadZone");
  const input = $("resumeFile");

  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", e => {
    e.preventDefault(); zone.classList.remove("dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file && file.type === "application/pdf") processResume(file);
    else setResumeStatus("Please drop a PDF file.", "error");
  });
  input.addEventListener("change", () => {
    if (input.files?.[0]) processResume(input.files[0]);
  });
}

function setResumeStatus(msg, type="") {
  const el = $("resumeExtractStatus");
  el.textContent = msg;
  el.style.color = type === "error" ? "var(--red)" : type === "ok" ? "var(--green)" : "var(--text-dim)";
}

async function processResume(file) {
  $("uploadLabel").textContent = file.name;
  setResumeStatus("Extracting text…");
  if (!state.backendOk) { setResumeStatus("Backend not available — start server.py first.", "error"); return; }
  try {
    const form = new FormData();
    form.append("resume", file);
    const res = await fetch("/api/extract-resume", { method: "POST", body: form });
    const json = await res.json();
    if (!res.ok || !json.text) throw new Error(json.error || "Extraction failed");
    state.resumeText = json.text;
    state.resumePath = json.filePath || "";
    $("resumeText").value = state.resumeText;
    $("resumePathInput").value = state.resumePath;
    setResumeStatus(`✓ Extracted ${state.resumeText.length.toLocaleString()} chars`, "ok");
    saveState();
    // Auto-suggest profile
    autoSuggestProfile();
  } catch (err) {
    setResumeStatus(`Error: ${err.message}`, "error");
  }
}

async function autoSuggestProfile() {
  if (!state.resumeText || !state.backendOk) return;
  try {
    const res = await fetch("/api/profile-suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resumeText: state.resumeText, aiMode: "groq" }),
    });
    const json = await res.json();
    if (!res.ok || !json.profile) return;
    const p = json.profile;
    if (p.fullName && !$("fullNameInput").value) { $("fullNameInput").value = p.fullName; state.fullName = p.fullName; }
    if (p.email    && !$("emailInput").value)    { $("emailInput").value = p.email;       state.email = p.email; }
    if (p.phone    && !$("phoneInput").value)    { $("phoneInput").value = p.phone;       state.phone = p.phone; }
    // Auto-add inferred role if not already set
    if (p.role && !state.roles.includes(p.role)) { addChip(state.roles, p.role, renderRoleChips); }
    saveState();
  } catch {}
}

/* ─── BACKEND HEALTH ─── */
async function checkBackend() {
  try {
    const res = await fetch("/api/health", { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      state.backendOk = true;
      const badge = $("backendBadge");
      badge.textContent = "● Backend Online";
      badge.className = "badge-pill status-online";
    }
  } catch {
    state.backendOk = false;
    const badge = $("backendBadge");
    badge.textContent = "● Backend Offline";
    badge.className = "badge-pill status-offline";
    $("setupNote").textContent = "Start the backend: python server.py";
  }
}

/* ─── FIND JOBS ─── */
async function findJobs() {
  if (!state.roles.length) { $("setupNote").textContent = "Add at least one target role."; return; }
  if (!state.resumeText)   { $("setupNote").textContent = "Upload your resume PDF first."; return; }
  if (!state.backendOk)    { $("setupNote").textContent = "Backend is offline. Start server.py."; return; }

  $("findJobsBtn").disabled = true;
  $("findJobsBtnLabel").textContent = "Searching…";
  $("setupNote").textContent = "";
  $("progressSection").classList.remove("hidden");
  $("progressBar").style.width = "2%";
  $("progressLogs").textContent = "Starting pipeline…";

  const location = state.locations.join(", ");

  try {
    if (state.pipelineTimer) { clearTimeout(state.pipelineTimer); state.pipelineTimer = null; }
    const res = await fetch("/api/pipeline/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        roles: state.roles,
        location,
        resumeText: state.resumeText,
        aiMode: "groq",
        jobType: state.jobType,
      }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Pipeline start failed");
    state.pipelineTaskId = json.taskId;
    pollPipeline();
  } catch (err) {
    $("setupNote").textContent = `Error: ${err.message}`;
    resetFindBtn();
  }
}

const STAGE_LABELS = {
  queued: "Queued", parsing_profile: "Parsing resume", parsing_resume: "Parsing resume",
  fetching_sources: "Sourcing jobs from 160+ boards", fetching_jobs: "Sourcing jobs",
  ranking: "AI ranking by match quality", resolving_links: "Resolving apply links",
  completed: "Done!", failed: "Failed",
};

async function pollPipeline() {
  if (!state.pipelineTaskId) return;
  try {
    const res = await fetch(`/api/pipeline/status?task_id=${encodeURIComponent(state.pipelineTaskId)}`);
    const task = await res.json();
    if (!res.ok) throw new Error(task.error || "Poll failed");

    const pct = task.percent || 0;
    $("progressBar").style.width = `${pct}%`;
    $("progressPct").textContent = `${pct}%`;
    $("progressStageLabel").textContent = STAGE_LABELS[task.stage] || task.stage || "Working…";
    if (Array.isArray(task.logs) && task.logs.length) {
      $("progressLogs").innerHTML = task.logs.slice(-5).map(l => esc(l)).join("<br>");
    }

    if (task.status === "completed") {
      state.jobs = Array.isArray(task.jobs) ? task.jobs : [];
      state.pipelineTaskId = "";

      // Auto-fill contact if inferred
      const p = task.inferredProfile || {};
      if (p.fullName && !$("fullNameInput").value) { $("fullNameInput").value = p.fullName; state.fullName = p.fullName; }
      if (p.email    && !$("emailInput").value)    { $("emailInput").value = p.email;       state.email = p.email; }
      if (p.phone    && !$("phoneInput").value)    { $("phoneInput").value = p.phone;       state.phone = p.phone; }

      const ready = task.autoApplyReadyCount || 0;
      $("setupNote").textContent = `Found ${state.jobs.length} jobs — ${ready} auto-ready.`;
      renderJobs();
      renderSourceOptions();
      resetFindBtn();
      return;
    }
    if (task.status === "failed") throw new Error("Pipeline failed");
    state.pipelineTimer = setTimeout(pollPipeline, 1200);
  } catch (err) {
    $("setupNote").textContent = `Search error: ${err.message}`;
    state.pipelineTaskId = "";
    resetFindBtn();
  }
}

function resetFindBtn() {
  $("findJobsBtn").disabled = false;
  $("findJobsBtnLabel").textContent = "🔍 Find Jobs";
}

/* ─── JOB RENDERING ─── */
function getFilteredJobs() {
  const needle = $("searchFilter").value.trim().toLowerCase();
  const source = $("sourceFilter").value;
  const type   = $("typeFilter").value;
  return state.jobs.filter(j => {
    if (needle && !j.title.toLowerCase().includes(needle) && !j.company.toLowerCase().includes(needle)) return false;
    if (source !== "all" && j.source !== source) return false;
    if (type === "remote" && !j.remote) return false;
    if (type === "onsite" && j.remote)  return false;
    return true;
  });
}

function renderSourceOptions() {
  const sources = [...new Set(state.jobs.map(j => j.source))];
  const sel = $("sourceFilter");
  sel.innerHTML = '<option value="all">All Sources</option>' +
    sources.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
}

function renderJobs() {
  const filtered = getFilteredJobs();
  const autoReady = filtered.filter(j => j.autoApplyReady).length;
  const manual    = filtered.length - autoReady;

  $("jobCount").textContent = filtered.length;
  $("autoReadyBadge").textContent = `${autoReady} auto-ready`;
  $("manualBadge").textContent = `${manual} manual`;

  if (!filtered.length) {
    $("jobsList").innerHTML = `<div class="empty-state">${
      state.jobs.length ? "No jobs match current filters." : "Run <strong>Find Jobs</strong> to see results."
    }</div>`;
    const pbar = $("paginationBar");
    if (pbar) pbar.classList.add("hidden");
    return;
  }

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;
  const start = (state.page - 1) * PAGE_SIZE;
  const pageJobs = filtered.slice(start, start + PAGE_SIZE);

  $("jobsList").innerHTML = pageJobs.map(job => {
    const tracked = state.tracking.find(t => t.jobId === job.id);
    const score = Number(job.aiScore || job.score || 0);
    const scoreClass = score >= 60 ? "score-hi" : score >= 30 ? "score-mid" : "score-lo";
    const scoreLabel = score >= 60 ? "High match" : score >= 30 ? "Good match" : "Low match";
    const reason = job.aiReason || "";
    return `
    <article class="job-card${state.selectedJob?.id === job.id ? " selected" : ""}" data-id="${esc(job.id)}">
      <div class="job-card-title">${esc(job.title)}</div>
      <div class="job-card-meta">${esc(job.company)} · ${esc(job.location)}</div>
      <div class="job-tags">
        <span class="tag source">${esc(job.source)}</span>
        <span class="tag ${job.remote ? "remote" : "onsite"}">${job.remote ? "Remote" : "On-site"}</span>
        <span class="tag ${scoreClass}">${scoreLabel}</span>
        ${job.autoApplyReady ? '<span class="tag auto-ready">Auto-Ready</span>' : ""}
        ${tracked ? `<span class="tag tracked">${esc(tracked.status)}</span>` : ""}
      </div>
      ${reason ? `<div class="job-card-reason">${esc(reason)}</div>` : ""}
    </article>`;
  }).join("");

  $("jobsList").querySelectorAll(".job-card").forEach(card => {
    card.addEventListener("click", () => {
      const job = state.jobs.find(j => j.id === card.dataset.id);
      if (job) selectJob(job);
    });
  });

  // Update pagination bar
  const pbar = $("paginationBar");
  if (pbar) {
    if (totalPages > 1) {
      pbar.classList.remove("hidden");
      $("pageInfo").textContent = `Page ${state.page} of ${totalPages}`;
      $("prevPageBtn").disabled = state.page <= 1;
      $("nextPageBtn").disabled = state.page >= totalPages;
    } else {
      pbar.classList.add("hidden");
    }
  }
}

/* ─── JOB SELECTION + AUTO-TAILOR ─── */
function selectJob(job) {
  state.selectedJob = job;
  renderJobs();

  // Ensure tracker
  if (!state.tracking.find(t => t.jobId === job.id)) {
    state.tracking.push({ jobId: job.id, title: job.title, company: job.company,
      location: job.location, applyUrl: job.applyUrl, source: job.source,
      status: "Saved", updatedAt: new Date().toISOString() });
    saveState(); renderTracker();
  }

  // Show apply panel
  $("applyEmpty").classList.add("hidden");
  $("applyKit").classList.remove("hidden");

  const autoBtn = $("autoApplyBtn");
  if (autoBtn) {
    if (job.autoApplyReady) {
      autoBtn.classList.remove("hidden");
    } else {
      autoBtn.classList.add("hidden");
    }
    $("autoApplyProgress").classList.add("hidden");
  }

  // Render selected job card
  $("selectedJobCard").innerHTML = `
    <div class="selected-job-title">${esc(job.title)}</div>
    <div class="selected-job-meta">${esc(job.company)} · ${esc(job.location)}</div>
    <div style="margin-top:5px">
      <span class="tag source">${esc(job.source)}</span>
      ${job.remote ? '<span class="tag remote" style="margin-left:4px">Remote</span>' : ""}
    </div>`;

  // Reset kit
  $("kitContent").classList.add("hidden");
  $("kitGenerating").classList.remove("hidden");

  // Fetch AI kit
  fetchTailorKit(job);
}

async function fetchTailorKit(job) {
  if (!state.resumeText || !state.backendOk) {
    $("kitGenerating").classList.add("hidden");
    $("kitContent").classList.remove("hidden");
    renderHeuristicKit(job);
    return;
  }
  try {
    const res = await fetch("/api/tailor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resumeText: state.resumeText,
        role: state.roles.join(", "),
        job: { title: job.title, company: job.company, description: job.description || "", location: job.location },
      }),
    });
    const kit = await res.json();
    if (!res.ok) throw new Error(kit.error || "Tailor failed");
    renderKit(kit);
  } catch {
    renderHeuristicKit(job);
  } finally {
    $("kitGenerating").classList.add("hidden");
    $("kitContent").classList.remove("hidden");
  }
}

function renderKit(kit) {
  // Summary
  $("summaryText").textContent = kit.summary || "";

  // Cover letter
  $("coverLetterText").textContent = kit.coverLetter || "";

  // Resume tweaks
  const tweaks = kit.resumeTweaks || [];
  $("resumeTweaksContainer").innerHTML = tweaks.map(t => `
    <div class="tweak-item">
      <div class="tweak-original">${esc(t.original || "")}</div>
      <div class="tweak-improved">${esc(t.improved || "")}</div>
      <div class="tweak-reason">→ ${esc(t.reason || "")}</div>
    </div>`).join("") || '<p style="color:var(--text-dim);font-size:12px">No tweaks generated.</p>';
  // Hidden copy target
  $("resumeTweaksText").textContent = tweaks.map(t =>
    `ORIGINAL: ${t.original}\nIMPROVED: ${t.improved}\nWHY: ${t.reason}`
  ).join("\n\n---\n\n");

  // Keywords
  const kws = kit.keywords || [];
  $("keywordsContainer").innerHTML = kws.map(k => `<span class="keyword-chip">${esc(k)}</span>`).join("");
}

function renderHeuristicKit(job) {
  const desc = `${job.title} ${job.description || ""}`.toLowerCase();
  const words = desc.match(/[a-z0-9+#.]{4,}/g) || [];
  const freq = {};
  words.forEach(w => { freq[w] = (freq[w] || 0) + 1; });
  const stop = new Set(["this","that","with","your","will","have","from","team","role","work","years","experience"]);
  const kws = Object.entries(freq).filter(([w]) => !stop.has(w)).sort((a,b) => b[1]-a[1]).slice(0,6).map(([w]) => w);
  const kwStr = kws.slice(0,3).join(", ") || "technical skills";

  renderKit({
    summary: `Experienced ${state.roles[0] || "professional"} applying for ${job.title} at ${job.company}. Strong background in ${kwStr}, with a focus on delivering measurable results.`,
    coverLetter: `Dear ${job.company} Hiring Team,\n\n${job.company}'s focus on ${kws[0] || "innovation"} and ${kws[1] || "engineering"} aligns closely with the direction of my career. The ${job.title} role is an exciting opportunity to apply my experience in ${kwStr}.\n\nI have consistently delivered high-impact work in these areas, driving real outcomes for the teams I've been part of. My background maps directly to the skills and challenges described in this role.\n\nI'd welcome the chance to connect and discuss how I can contribute to ${job.company}'s goals. Thank you for your consideration.\n\nBest regards`,
    resumeTweaks: [],
    keywords: kws,
    aiUsed: false,
  });
}

/* ─── COPY BUTTONS ─── */
function initCopyButtons() {
  document.querySelectorAll(".btn-copy[data-copy]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const target = $(btn.dataset.copy);
      if (!target) return;
      const text = target.textContent || target.value || "";
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied!";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1800);
      } catch {
        btn.textContent = "Failed";
        setTimeout(() => { btn.textContent = "Copy"; }, 1500);
      }
    });
  });
}

/* ─── APPLY NOW ─── */
async function applyNow() {
  const job = state.selectedJob;
  if (!job?.applyUrl) { $("applyStatusNote").textContent = "No apply URL for this job."; return; }

  // Open job URL in new tab
  window.open(job.applyUrl, "_blank", "noopener,noreferrer");

  // Build full kit text and copy to clipboard
  const name = $("fullNameInput").value || state.fullName || "";
  const email = $("emailInput").value || state.email || "";
  const phone = $("phoneInput").value || state.phone || "";
  const summary = $("summaryText").textContent || "";
  const letter  = $("coverLetterText").textContent || "";
  const tweaks  = $("resumeTweaksText").textContent || "";
  const kwText  = Array.from($("keywordsContainer").querySelectorAll(".keyword-chip")).map(el => el.textContent).join(", ");

  const kit = [
    `=== JobMint Apply Kit ===`,
    `Job: ${job.title} @ ${job.company}`,
    `URL: ${job.applyUrl}`,
    ``,
    `--- Contact ---`,
    `Name:  ${name}`,
    `Email: ${email}`,
    `Phone: ${phone}`,
    ``,
    `--- Targeted Summary ---`,
    summary,
    ``,
    `--- Cover Letter ---`,
    letter,
    ``,
    `--- Resume Tweaks ---`,
    tweaks || "(no tweaks)",
    ``,
    `--- Keywords ---`,
    kwText,
  ].join("\n");

  try {
    await navigator.clipboard.writeText(kit);
    $("applyStatusNote").textContent = "✓ Kit copied to clipboard. The job page has opened in a new tab.";
  } catch {
    $("applyStatusNote").textContent = "Job page opened. (Could not auto-copy kit — copy sections manually.)";
  }

  // Mark as Saved in tracker (user can upgrade to Applied)
  updateTrackingStatus(job.id, "Saved");
}

function markApplied() {
  if (!state.selectedJob) return;
  updateTrackingStatus(state.selectedJob.id, "Applied");
  $("applyStatusNote").textContent = "✓ Marked as Applied.";
}

/* ─── TRACKER ─── */
function updateTrackingStatus(jobId, status) {
  const item = state.tracking.find(t => t.jobId === jobId);
  if (item) { item.status = status; item.updatedAt = new Date().toISOString(); }
  saveState(); renderTracker(); renderJobs();
}

function renderTracker() {
  $("trackerCount").textContent = state.tracking.length;
  if (!state.tracking.length) {
    $("trackerBoard").innerHTML = '<div class="empty-state">No tracked applications yet.</div>';
    return;
  }
  const sorted = [...state.tracking].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  $("trackerBoard").innerHTML = sorted.map(item => `
    <div class="tracker-card">
      <div class="tracker-card-title">${esc(item.title)}</div>
      <div class="tracker-card-meta">${esc(item.company)} · ${esc(item.location || "")}</div>
      <div class="tracker-card-actions">
        <select class="tracker-status-select" data-job-id="${esc(item.jobId)}">
          ${STATUSES.map(s => `<option${s === item.status ? " selected" : ""}>${s}</option>`).join("")}
        </select>
        <a class="tracker-link" href="${esc(item.applyUrl)}" target="_blank" rel="noopener">Open ↗</a>
      </div>
    </div>`).join("");

  $("trackerBoard").querySelectorAll(".tracker-status-select").forEach(sel => {
    sel.addEventListener("change", () => updateTrackingStatus(sel.dataset.jobId, sel.value));
  });
}

/* ─── SAVE PROFILE ─── */
function syncContactState() {
  state.fullName = $("fullNameInput").value.trim();
  state.email    = $("emailInput").value.trim();
  state.phone    = $("phoneInput").value.trim();
}

/* ─── FILTERS ─── */
function initFilters() {
  $("searchFilter").addEventListener("input", () => { state.page = 1; renderJobs(); });
  $("sourceFilter").addEventListener("change", () => { state.page = 1; renderJobs(); });
  $("typeFilter").addEventListener("change", () => { state.page = 1; renderJobs(); });
}

/* ─── QUICK LOCATION TAGS ─── */
function initQuickLocs() {
  document.querySelectorAll(".quick-tag[data-loc]").forEach(btn => {
    btn.addEventListener("click", () => addChip(state.locations, btn.dataset.loc, renderLocationChips));
  });
}

/* ─── COLLAPSIBLES ─── */
function initCollapsibles() {
  document.querySelectorAll(".kit-toggle").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const targetId = btn.dataset.target;
      const body = $(targetId);
      if (!body) return;
      const collapsed = body.classList.toggle("hidden");
      btn.textContent = collapsed ? "▸" : "▾";
      btn.classList.toggle("collapsed", collapsed);
    });
  });
  document.querySelectorAll(".collapsible-head").forEach(head => {
    head.addEventListener("click", e => {
      if (e.target.classList.contains("btn-copy") || e.target.classList.contains("kit-toggle")) return;
      const btn = head.querySelector(".kit-toggle");
      if (btn) btn.click();
    });
  });
}

/* ─── AUTO-APPLY ─── */
async function autoApply() {
  const job = state.selectedJob;
  if (!job) return;
  if (!job.autoApplyReady) { $("applyStatusNote").textContent = "This job is not auto-apply ready (unsupported platform)."; return; }
  const btn = $("autoApplyBtn");
  btn.disabled = true;
  btn.textContent = "⚡ Applying…";
  $("autoApplyProgress").classList.remove("hidden");
  $("autoApplyBar").style.width = "5%";
  $("autoApplyStage").textContent = "Starting automation…";
  $("autoApplyLogs").textContent = "";

  const profile = {
    fullName: $("fullNameInput").value || state.fullName,
    email: $("emailInput").value || state.email,
    phone: $("phoneInput").value || state.phone,
    resumeText: state.resumeText,
    resumePath: state.resumePath,
    role: state.roles.join(", "),
    location: state.locations.join(", "),
    jobType: state.jobType,
    autoSubmit: true,
    // LinkedIn credentials (only sent if provided, never stored by server)
    linkedinEmail: ($("linkedinEmailInput")?.value || "").trim(),
    linkedinPassword: $("linkedinPasswordInput")?.value || "",
  };

  try {
    const res = await fetch("/api/auto-apply/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile, jobs: [job], userKey: getUserKey() }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Failed to start");
    pollAutoApply(json.taskId, btn);
  } catch (err) {
    $("applyStatusNote").textContent = `Auto-apply error: ${err.message}`;
    btn.disabled = false;
    btn.textContent = "⚡ Auto-Apply (Playwright)";
  }
}

async function pollAutoApply(taskId, btn) {
  try {
    const res = await fetch(`/api/auto-apply/status?task_id=${encodeURIComponent(taskId)}`);
    const task = await res.json();
    if (!res.ok) throw new Error(task.error || "Poll failed");

    const pct = task.percent || 0;
    $("autoApplyBar").style.width = `${pct}%`;
    $("autoApplyPct").textContent = `${pct}%`;
    $("autoApplyStage").textContent = task.stage || "Working…";
    if (Array.isArray(task.logs)) {
      $("autoApplyLogs").innerHTML = task.logs.slice(-3).map(l => esc(l)).join("<br>");
    }

    if (task.status === "completed") {
      const ok = task.successCount || 0;
      if (ok > 0) {
        $("applyStatusNote").textContent = `✓ Applied successfully via automation!`;
        updateTrackingStatus(state.selectedJob.id, "Applied");
      } else {
        const m = (task.manual || [])[0];
        $("applyStatusNote").textContent = `Manual required: ${(m?.message || "Platform needs human review").slice(0, 100)}`;
      }
      btn.disabled = false;
      btn.textContent = "⚡ Auto-Apply (Playwright)";
      return;
    }
    if (task.status === "failed") throw new Error(task.error || "Auto-apply failed");
    setTimeout(() => pollAutoApply(taskId, btn), 1500);
  } catch (err) {
    $("applyStatusNote").textContent = `Error: ${err.message}`;
    if (btn) { btn.disabled = false; btn.textContent = "⚡ Auto-Apply (Playwright)"; }
  }
}

/* ─── YC OUTREACH ─── */
const ycState = { companies: [], selectedCompany: null };

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      if (tab === "yc") {
        document.querySelector(".workspace").classList.add("hidden");
        $("trackerSection").classList.add("hidden");
        $("ycSection").classList.remove("hidden");
      } else {
        document.querySelector(".workspace").classList.remove("hidden");
        $("trackerSection").classList.remove("hidden");
        $("ycSection").classList.add("hidden");
      }
    });
  });
}

async function loadYcCompanies() {
  const btn = $("loadYcBtn");
  const batch = $("ycBatchFilter").value;
  btn.disabled = true;
  btn.textContent = "Loading…";
  $("ycCompaniesList").innerHTML = '<div class="empty-state">Fetching YC companies…</div>';
  try {
    const res = await fetch(`/api/yc/companies?batch=${encodeURIComponent(batch)}&limit=60`);
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Failed");
    ycState.companies = json.companies || [];
    renderYcCompanies();
  } catch (err) {
    $("ycCompaniesList").innerHTML = `<div class="empty-state">Error: ${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Load Companies";
  }
}

function renderYcCompanies() {
  if (!ycState.companies.length) {
    $("ycCompaniesList").innerHTML = '<div class="empty-state">No companies found.</div>';
    return;
  }
  $("ycCompaniesList").innerHTML = ycState.companies.map((c, i) => `
    <div class="yc-company-card${ycState.selectedCompany?.name === c.name ? " selected" : ""}" data-idx="${i}">
      <div class="yc-company-name">${esc(c.name)}</div>
      <div class="yc-company-liner">${esc(c.oneLiner)}</div>
      <div class="yc-company-meta">
        <span class="tag source">${esc(c.batch)}</span>
        ${(c.tags || []).slice(0, 2).map(t => `<span class="tag">${esc(t)}</span>`).join("")}
        ${c.website ? `<a href="${esc(c.website)}" target="_blank" rel="noopener" class="tag" style="color:var(--accent);border-color:rgba(99,102,241,.3)" onclick="event.stopPropagation()">Site ↗</a>` : ""}
      </div>
    </div>`).join("");

  $("ycCompaniesList").querySelectorAll(".yc-company-card").forEach(card => {
    card.addEventListener("click", () => {
      const company = ycState.companies[+card.dataset.idx];
      if (company) selectYcCompany(company);
    });
  });
}

async function selectYcCompany(company) {
  ycState.selectedCompany = company;
  renderYcCompanies();
  $("ycPitchEmpty").classList.add("hidden");
  $("ycPitchContent").classList.remove("hidden");
  $("ycSelectedCompany").innerHTML = `
    <div class="selected-job-title">${esc(company.name)}</div>
    <div class="selected-job-meta">${esc(company.oneLiner)}</div>
    <div style="margin-top:5px"><span class="tag source">${esc(company.batch)}</span></div>`;
  $("ycPitchResult").classList.add("hidden");
  $("ycPitchGenerating").classList.remove("hidden");

  try {
    const res = await fetch("/api/yc/pitch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company, resumeText: state.resumeText }),
    });
    const pitch = await res.json();
    if (!res.ok) throw new Error(pitch.error || "Pitch failed");
    $("ycAnalysisText").textContent = pitch.productAnalysis || "";
    $("ycValueAddText").textContent = pitch.valueAdd || "";
    $("ycEmailText").textContent = pitch.email || "";
    $("ycPitchResult").classList.remove("hidden");
  } catch (err) {
    $("ycAnalysisText").textContent = "";
    $("ycValueAddText").textContent = "";
    $("ycEmailText").textContent = `Error generating pitch: ${err.message}`;
    $("ycPitchResult").classList.remove("hidden");
  } finally {
    $("ycPitchGenerating").classList.add("hidden");
  }
}

/* ─── INIT ─── */
async function init() {
  // Load from Supabase (cross-device) or localStorage (offline fallback)
  await loadState();

  // Restore UI state
  renderRoleChips();
  renderLocationChips();
  syncWorkTypeUI();
  $("fullNameInput").value = state.fullName;
  $("emailInput").value    = state.email;
  $("phoneInput").value    = state.phone;
  if (state.resumeText) {
    $("uploadLabel").textContent = "Resume loaded from session";
    $("resumeText").value = state.resumeText;
    setResumeStatus(`✓ ${state.resumeText.length.toLocaleString()} chars in memory`, "ok");
  }
  renderTracker();

  // Wire up chip inputs
  initChipInput("roleInputBox", state.roles, renderRoleChips);
  initChipInput("locationInputBox", state.locations, renderLocationChips);

  // Work type
  initWorkTypeToggle();

  // Upload
  initUpload();

  // Filters
  initFilters();

  // Quick location buttons
  initQuickLocs();

  // Copy buttons
  initCopyButtons();

  // Collapsibles
  initCollapsibles();

  // Find Jobs
  $("findJobsBtn").addEventListener("click", findJobs);

  // Apply Now & Mark Applied & Auto-Apply
  $("applyNowBtn").addEventListener("click", applyNow);
  $("markAppliedBtn").addEventListener("click", markApplied);
  $("autoApplyBtn").addEventListener("click", autoApply);

  // Pagination
  $("prevPageBtn").addEventListener("click", () => { state.page--; renderJobs(); });
  $("nextPageBtn").addEventListener("click", () => { state.page++; renderJobs(); });

  // Save Profile
  $("saveProfileBtn").addEventListener("click", () => {
    syncContactState();
    saveState();
    $("saveProfileBtn").textContent = "Saved!";
    setTimeout(() => { $("saveProfileBtn").textContent = "Save Profile"; }, 1500);
  });

  // Clear tracker — also clears Supabase
  $("clearTrackerBtn").addEventListener("click", () => {
    if (confirm("Clear all tracked applications?")) {
      state.tracking = [];
      sbClearTracker().catch(() => {});
      saveState(); renderTracker();
    }
  });

  // Sync contact fields on change
  ["fullNameInput", "emailInput", "phoneInput"].forEach(id => {
    $(id).addEventListener("input", syncContactState);
  });

  // Check backend
  checkBackend();
  setInterval(checkBackend, 30000);

  // Tabs
  initTabs();

  // YC Outreach
  $("loadYcBtn").addEventListener("click", loadYcCompanies);
}

document.addEventListener("DOMContentLoaded", init);
