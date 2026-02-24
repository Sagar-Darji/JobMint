-- ─── JobhireAI — Supabase Schema ───
-- Run this entire file in: Supabase Dashboard → SQL Editor → New Query → Run

-- ── 1. User Profile ──────────────────────────────────────────────────────────
-- One row per device (identified by a UUID stored in the browser's localStorage)
CREATE TABLE IF NOT EXISTS user_profile (
  id          uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_key    text        UNIQUE NOT NULL,
  full_name   text        DEFAULT '',
  email       text        DEFAULT '',
  phone       text        DEFAULT '',
  roles       text[]      DEFAULT '{}',
  locations   text[]      DEFAULT '{}',
  job_type    text        DEFAULT 'all',
  resume_text text        DEFAULT '',
  resume_path text        DEFAULT '',
  updated_at  timestamptz DEFAULT now()
);

-- ── 2. Job Tracker ────────────────────────────────────────────────────────────
-- Application tracker rows — one per (device, job)
CREATE TABLE IF NOT EXISTS job_tracker (
  id         uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_key   text        NOT NULL,
  job_id     text        NOT NULL,
  title      text        DEFAULT '',
  company    text        DEFAULT '',
  location   text        DEFAULT '',
  apply_url  text        DEFAULT '',
  source     text        DEFAULT '',
  status     text        DEFAULT 'Saved',
  updated_at timestamptz DEFAULT now(),
  UNIQUE (user_key, job_id)
);

-- ── 3. Row-Level Security ─────────────────────────────────────────────────────
-- Allow the anonymous browser key to read/write its own rows only.
ALTER TABLE user_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_tracker  ENABLE ROW LEVEL SECURITY;

-- Policies: anon can do everything (app uses user_key as identity, not auth)
DROP POLICY IF EXISTS "anon_all_profile" ON user_profile;
CREATE POLICY "anon_all_profile"
  ON user_profile FOR ALL
  USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_all_tracker" ON job_tracker;
CREATE POLICY "anon_all_tracker"
  ON job_tracker FOR ALL
  USING (true) WITH CHECK (true);

-- ── 4. Auto-update updated_at ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

DROP TRIGGER IF EXISTS trg_profile_updated_at ON user_profile;
CREATE TRIGGER trg_profile_updated_at
  BEFORE UPDATE ON user_profile
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS trg_tracker_updated_at ON job_tracker;
CREATE TRIGGER trg_tracker_updated_at
  BEFORE UPDATE ON job_tracker
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();
