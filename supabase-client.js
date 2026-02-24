/* ─── JobMint — Supabase Client ─────────────────────────────────────────── */

const SUPABASE_URL = "https://pknxuepcmauglgncnynf.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBrbnh1ZXBjbWF1Z2xnbmNueW5mIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE4OTEwMjQsImV4cCI6MjA4NzQ2NzAyNH0.KpUIJgArLsla2vRgRLb6i7AlTVW6dmmU1hREthALAgw";

// Initialise once — supabase global is loaded by the CDN script tag
const sb = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

/* ─── Device identity (no login required) ─── */
function getUserKey() {
  let k = localStorage.getItem("jobmint_user_key");
  if (!k) {
    k = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem("jobmint_user_key", k);
  }
  return k;
}

/* ─── Profile ─── */
async function sbSaveProfile(fields) {
  try {
    const { error } = await sb.from("user_profile").upsert(
      { user_key: getUserKey(), ...fields },
      { onConflict: "user_key" }
    );
    return !error;
  } catch { return false; }
}

async function sbLoadProfile() {
  try {
    const { data, error } = await sb
      .from("user_profile")
      .select("*")
      .eq("user_key", getUserKey())
      .maybeSingle();
    return error ? null : data;
  } catch { return null; }
}

/* ─── Tracker ─── */
async function sbSaveTracker(tracking) {
  if (!Array.isArray(tracking) || !tracking.length) return true;
  const userKey = getUserKey();
  const rows = tracking.map(t => ({
    user_key:   userKey,
    job_id:     t.jobId,
    title:      t.title      || "",
    company:    t.company    || "",
    location:   t.location   || "",
    apply_url:  t.applyUrl   || "",
    source:     t.source     || "",
    status:     t.status     || "Saved",
    updated_at: t.updatedAt  || new Date().toISOString(),
  }));
  try {
    const { error } = await sb.from("job_tracker").upsert(rows, { onConflict: "user_key,job_id" });
    return !error;
  } catch { return false; }
}

async function sbLoadTracker() {
  try {
    const { data, error } = await sb
      .from("job_tracker")
      .select("*")
      .eq("user_key", getUserKey())
      .order("updated_at", { ascending: false });
    if (error || !data) return [];
    return data.map(r => ({
      jobId:     r.job_id,
      title:     r.title,
      company:   r.company,
      location:  r.location,
      applyUrl:  r.apply_url,
      source:    r.source,
      status:    r.status,
      updatedAt: r.updated_at,
    }));
  } catch { return []; }
}

async function sbDeleteTrackerItem(jobId) {
  try {
    await sb.from("job_tracker").delete().eq("user_key", getUserKey()).eq("job_id", jobId);
    return true;
  } catch { return false; }
}

async function sbClearTracker() {
  try {
    await sb.from("job_tracker").delete().eq("user_key", getUserKey());
    return true;
  } catch { return false; }
}

/* ─── Resume Storage ─── */
// Uploads a PDF File object, returns the public URL (or null on failure)
async function sbUploadResume(file) {
  try {
    const userKey = getUserKey();
    const ext = file.name.split(".").pop() || "pdf";
    const path = `${userKey}/resume_${Date.now()}.${ext}`;
    const { error } = await sb.storage.from("resumes").upload(path, file, {
      upsert: true,
      contentType: file.type || "application/pdf",
    });
    if (error) return null;
    const { data: { publicUrl } } = sb.storage.from("resumes").getPublicUrl(path);
    return publicUrl;
  } catch { return null; }
}

/* ─── Sync status helper ─── */
function setSyncBadge(state) {
  const el = document.getElementById("syncBadge");
  if (!el) return;
  const map = {
    syncing: { text: "↻ Syncing…",  cls: "sync-syncing" },
    ok:      { text: "☁ Synced",    cls: "sync-ok"      },
    error:   { text: "⚠ Sync err",  cls: "sync-error"   },
    offline: { text: "— Offline",   cls: "sync-offline"  },
  };
  const m = map[state] || map.offline;
  el.textContent = m.text;
  el.className = `sync-badge ${m.cls}`;
}
