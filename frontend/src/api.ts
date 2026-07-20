const apiPrefix = "/api/v1";

export type LoginResult = { id: number; email: string; name: string; role: "user" | "admin" };

export async function postLogin(email: string, password: string): Promise<LoginResult> {
  const r = await fetch(`${apiPrefix}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postSignup(email: string, password: string, name: string): Promise<{ ok: boolean; user_id: number; message: string }> {
  const r = await fetch(`${apiPrefix}/auth/signup`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password, name }) });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postVerifyEmail(email: string, code: string): Promise<{ ok: boolean }> {
  const r = await fetch(`${apiPrefix}/auth/verify-email`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, code }) });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postLogout(): Promise<void> {
  await fetch(`${apiPrefix}/auth/logout`, { method: "POST" });
}

/** Restores the session from the httpOnly cookie on app boot; the cookie is
 * invisible to JS, so this is the only way to know whether we're logged in
 * after a hard refresh. Returns null if not authenticated. */
export async function fetchMe(): Promise<LoginResult | null> {
  const r = await fetch(`${apiPrefix}/auth/me`);
  if (!r.ok) return null;
  return r.json();
}

export type AdminUser = {
  id: number;
  name: string;
  email: string;
  status: string;
  role: string;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
  quota_overridden_by_admin: boolean;
};

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  const r = await fetch(`${apiPrefix}/admin/users`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type AdminOverview = {
  total_users: number;
  total_matches: number;
  total_resumes: number;
  total_cover_letters: number;
  estimated_cost_usd: number;
  total_tokens: number;
};

export async function fetchAdminOverview(): Promise<AdminOverview> {
  const r = await fetch(`${apiPrefix}/admin/overview`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type AdminUserStats = {
  user_id: number;
  match_count: number;
  resume_count: number;
  cover_letter_count: number;
  estimated_cost_usd: number;
  total_tokens: number;
  last_active_at: string | null;
};

export async function fetchAdminUserStats(userId: number): Promise<AdminUserStats> {
  const r = await fetch(`${apiPrefix}/admin/users/${userId}/stats`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type AdminQuota = {
  user_id: number;
  max_matched_jobs: number | null;
  max_generated_resumes: number | null;
  max_generated_cover_letters: number | null;
  allowed_countries: string[];
  quota_overridden_by_admin: boolean;
};

export async function fetchAdminUserQuota(userId: number): Promise<AdminQuota> {
  const r = await fetch(`${apiPrefix}/admin/users/${userId}/quota`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putAdminUserQuota(
  userId: number,
  quota: {
    max_matched_jobs: number | null;
    max_generated_resumes: number | null;
    max_generated_cover_letters: number | null;
    allowed_countries: string[];
  }
): Promise<void> {
  const r = await fetch(`${apiPrefix}/admin/users/${userId}/quota`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(quota),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export type RequirementMatch = {
  id: string;
  text: string | null;
  category: string | null;
  importance: "must" | "nice" | null;
  match_level: "exceeds" | "full" | "strong" | "partial" | "weak" | "none" | null;
  reason: string | null;
  confidence: number | null;
};

export type JobRow = {
  id: number;
  title: string;
  company: string;
  country: string;
  source: string | null;
  source_label: string;
  date_posted: string | null;
  created_at: string | null;
  url: string | null;
  description_clean: string | null;
  description_raw: string | null;
  has_jd: boolean;
  score: number | null;
  decision: string | null;
  reason: string | null;
  vector_similarity: number | null;
  requirement_matches: RequirementMatch[];
  hard_constraints_hit: string[];
  job_seniority: "junior" | "mid" | "senior" | null;
  seniority_mismatch: boolean;
  cap_applied: boolean;
  preference_bonus: number | null;
  preference_bonus_reason: string | null;
  processing_status: "unscored" | "vector_filtered" | "employment_type_filtered" | "scored";
  has_resume_asset: boolean;
  has_cover_letter_asset: boolean;
  has_tailored_resume: boolean;
  recommended_cv_id: number | null;
  recommended_cv_name: string | null;
  in_application: boolean;
  is_saved: boolean;
  /** 打分模型对比测试用（见 jobs_list_model_comparison），非开发环境恒为 null/[] */
  compare_model: string | null;
  compare_score: number | null;
  compare_decision: string | null;
  compare_reason: string | null;
  compare_requirement_matches: RequirementMatch[];
};

export type SavedJobRow = {
  job_id: number;
  title: string;
  company: string | null;
  country: string | null;
  score: number | null;
  decision: string | null;
  saved_at: string | null;
};

export type JobsListResponse = {
  user_id: number;
  jobs_list_debug_show_all: boolean;
  jobs_list_diagnostics: boolean;
  jobs_list_model_comparison: boolean;
  compare_model_name: string | null;
  score_threshold_review: number;
  score_threshold_generate: number;
  jobs: JobRow[];
};

async function errText(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (j.detail) return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
  } catch {
    /* ignore */
  }
  return await r.text().catch(() => r.statusText);
}

export async function fetchUserProfile(userId: number): Promise<{
  user_id: number;
  has_profile_photo: boolean;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/profile`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function fetchUserJobs(userId: number): Promise<JobsListResponse> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type ResumeChoice = "slot_1" | "slot_2" | "tailored";

export async function postMarkApplied(userId: number, jobId: number, resumeChoice: ResumeChoice): Promise<void> {
  const r = await fetch(
    `${apiPrefix}/users/${userId}/jobs/${jobId}/mark-applied`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_choice: resumeChoice }),
    }
  );
  if (!r.ok) throw new Error(await errText(r));
}

export async function postSaveJob(userId: number, jobId: number): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs/${jobId}/save`, { method: "POST" });
  if (!r.ok) throw new Error(await errText(r));
}

export async function postUnsaveJob(userId: number, jobId: number): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs/${jobId}/save`, { method: "DELETE" });
  if (!r.ok) throw new Error(await errText(r));
}

export async function fetchSavedJobs(userId: number): Promise<{ user_id: number; jobs: SavedJobRow[] }> {
  const r = await fetch(`${apiPrefix}/users/${userId}/saved-jobs`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

/** 重组简历（占位 API，后续接 LLM） */
export async function postReorganizeResume(
  userId: number,
  jobId: number
): Promise<{ message?: string }> {
  const r = await fetch(
    `${apiPrefix}/users/${userId}/jobs/${jobId}/reorganize-resume`,
    { method: "POST" }
  );
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

// —— Dashboard / Settings / Tracking / Assets ——

export type DashboardMetrics = {
  total_jobs: number;
  total_scores: number;
  high_score_count: number;
  score_threshold_generate: number;
  total_assets: number;
  last_pipeline_run: {
    status: string;
    started_at: string | null;
    jobs_fetched: number;
    jobs_scored: number;
    jobs_generated: number;
    jobs_notified: number;
  } | null;
};

export async function fetchDashboardMetrics(): Promise<DashboardMetrics> {
  const r = await fetch(`${apiPrefix}/dashboard/metrics`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type PublicSettings = {
  database_url_preview: string;
  gemini_model_name: string;
  score_threshold_review: number;
  score_threshold_generate: number;
  scheduler_interval_hours: number;
  storage_provider: string;
  notification_email_to: string;
  job_markets: { label_zh: string; code: string }[];
  generation_model_options: { id: string; label: string }[];
  resume_tailoring_mode_options: { id: string; label: string }[];
  pipeline_llm_model_name: string;
  claude_model_name: string;
};

export async function fetchPublicSettings(): Promise<PublicSettings> {
  const r = await fetch(`${apiPrefix}/settings/public`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

/** 生成策略 JSON（占位，当前不覆盖单次生成所选模型）；GET/PUT 与后端 config_examples/generation_policy.json 同步 */
export async function fetchGenerationPolicy(): Promise<Record<string, unknown>> {
  const r = await fetch(`${apiPrefix}/settings/generation-policy`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putGenerationPolicy(
  body: Record<string, unknown>
): Promise<void> {
  const r = await fetch(`${apiPrefix}/settings/generation-policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export type Account = { id: number; name: string; email: string; has_profile_photo: boolean };

export async function fetchAccount(userId: number): Promise<Account> {
  const r = await fetch(`${apiPrefix}/users/${userId}/account`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putAccount(userId: number, name: string, email: string): Promise<Account> {
  const r = await fetch(`${apiPrefix}/users/${userId}/account`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, email }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function deleteAccount(userId: number): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/account`, { method: "DELETE" });
  if (!r.ok) throw new Error(await errText(r));
}

export async function downloadUserData(userId: number): Promise<void> {
  const endpoints = ["profile", "search-profile", "scoring-preferences", "resume-slots", "tracking"];
  const data: Record<string, unknown> = {};
  for (const endpoint of endpoints) {
    const r = await fetch(`${apiPrefix}/users/${userId}/${endpoint}`);
    if (r.ok) data[endpoint] = await r.json();
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = "jobmatchflow-data.json"; a.click();
  URL.revokeObjectURL(url);
}

export type SearchProfile = {
  countries: string[];
  allowed_countries: string[];
  country_locked: boolean;
};

export async function fetchSearchProfile(userId: number): Promise<SearchProfile> {
  const r = await fetch(`${apiPrefix}/users/${userId}/search-profile`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postChooseCountry(userId: number, country: string): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/onboarding/choose-country`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ country }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export async function putSearchProfile(
  userId: number,
  countries: string[]
): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/search-profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ countries }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export const SCORING_PREFERENCES_MAX_CHARS = 300;

export async function fetchScoringPreferences(userId: number): Promise<{
  user_id: number;
  scoring_preferences: string;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/scoring-preferences`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putScoringPreferences(
  userId: number,
  scoring_preferences: string
): Promise<void> {
  if (scoring_preferences.length > SCORING_PREFERENCES_MAX_CHARS) {
    throw new Error(`偏好文本最多 ${SCORING_PREFERENCES_MAX_CHARS} 字`);
  }
  const r = await fetch(`${apiPrefix}/users/${userId}/scoring-preferences`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scoring_preferences }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export type EmploymentTypePreference = "internship_only" | "full_time_only" | "both";

export async function fetchEmploymentTypePreference(userId: number): Promise<{
  user_id: number;
  employment_type_preference: EmploymentTypePreference;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/employment-type-preference`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putEmploymentTypePreference(
  userId: number,
  employment_type_preference: EmploymentTypePreference
): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/employment-type-preference`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ employment_type_preference }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export type GenerationModel = "gemini" | "claude";

export async function fetchGenerationModel(userId: number): Promise<{
  user_id: number;
  generation_model: GenerationModel;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/generation-model`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putGenerationModel(
  userId: number,
  generation_model: GenerationModel
): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/generation-model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ generation_model }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export type ResumeTailoringMode = "honest" | "jd_aligned";

export async function fetchResumeTailoringMode(userId: number): Promise<{
  user_id: number;
  resume_tailoring_mode: ResumeTailoringMode;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/resume-tailoring-mode`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putResumeTailoringMode(
  userId: number,
  resume_tailoring_mode: ResumeTailoringMode
): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/resume-tailoring-mode`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume_tailoring_mode }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export async function fetchMasterCv(
  userId: number,
  includeFull = false
): Promise<{
  id: number | null;
  cv_name: string | null;
  char_count: number;
  preview: string;
  full_text?: string;
}> {
  const q = includeFull ? "?include_full=true" : "";
  const r = await fetch(`${apiPrefix}/users/${userId}/master-cv${q}`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putMasterCv(
  userId: number,
  cvName: string,
  text: string
): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/master-cv`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cv_name: cvName, text }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export async function fetchMasterCvJson(userId: number): Promise<{
  user_id: number;
  master_cv_json: Record<string, unknown>;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/master-cv/json`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putMasterCvJson(
  userId: number,
  masterCvJson: Record<string, unknown>
): Promise<{ html_char_count: number; json_keys: number }> {
  const r = await fetch(`${apiPrefix}/users/${userId}/master-cv/json`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ master_cv_json: masterCvJson }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type ResumeSlotItem = {
  id: number;
  cv_name: string;
  char_count: number;
  has_source_file: boolean;
  is_pdf?: boolean;
};

export type ResumeSlotsBundle = {
  user_id: number;
  max_slots: number;
  items: ResumeSlotItem[];
  master_cv_html_char_count?: number;
  master_cv_updated_at?: string | null;
  material_library_char_count: number;
  material_library_updated_at: string | null;
};

export async function fetchResumeSlotsBundle(
  userId: number
): Promise<ResumeSlotsBundle> {
  const r = await fetch(`${apiPrefix}/users/${userId}/resume-slots`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function deleteResumeSlot(
  userId: number,
  cvId: number
): Promise<void> {
  const r = await fetch(
    `${apiPrefix}/users/${userId}/resume-slots/${cvId}`,
    { method: "DELETE" }
  );
  if (!r.ok) throw new Error(await errText(r));
}

/** 从简历库 PDF 抽取文本 + CV 模板生成 Master CV HTML（流水线 Gemini） */
export async function postRebuildMasterCvFromPdfs(userId: number): Promise<{
  char_count: number;
}> {
  const r = await fetch(
    `${apiPrefix}/users/${userId}/master-cv/rebuild-from-pdfs`,
    { method: "POST" }
  );
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

/** @deprecated 请使用 postRebuildMasterCvFromPdfs */
export async function postRebuildMaterialLibrary(userId: number): Promise<{
  char_count: number;
}> {
  return postRebuildMasterCvFromPdfs(userId);
}

export function masterCvPreviewUrl(userId: number): string {
  return `${apiPrefix}/users/${userId}/master-cv/preview-html`;
}

export async function postResumeSlotUpload(
  userId: number,
  file: File,
  cvName?: string
): Promise<{ cv_id: number }> {
  const fd = new FormData();
  fd.append("file", file);
  const q = cvName?.trim()
    ? `?cv_name=${encodeURIComponent(cvName.trim())}`
    : "";
  const r = await fetch(
    `${apiPrefix}/users/${userId}/resume-slots/upload${q}`,
    { method: "POST", body: fd }
  );
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postResumeSlotUploadByNumber(
  userId: number,
  slotNumber: 1 | 2,
  file: File,
  cvName?: string
): Promise<{ cv_id: number; slot_number: number }> {
  const fd = new FormData();
  fd.append("file", file);
  const q = cvName?.trim()
    ? `?cv_name=${encodeURIComponent(cvName.trim())}`
    : "";
  const r = await fetch(
    `${apiPrefix}/users/${userId}/resume-slots/${slotNumber}/upload${q}`,
    { method: "POST", body: fd }
  );
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export function resumeSlotDownloadUrl(userId: number, cvId: number): string {
  return `${apiPrefix}/users/${userId}/resume-slots/${cvId}/download`;
}

export async function postProfilePhoto(userId: number, file: File): Promise<void> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${apiPrefix}/users/${userId}/profile-photo`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) throw new Error(await errText(r));
}

export function profilePhotoUrl(userId: number, cacheBust?: number): string {
  const q = cacheBust ? `?v=${cacheBust}` : "";
  return `${apiPrefix}/users/${userId}/profile-photo${q}`;
}

export async function postPipelineRun(triggerUserId: number): Promise<{ result: unknown }> {
  const r = await fetch(`${apiPrefix}/pipeline/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trigger_user_id: triggerUserId }),
    signal: AbortSignal.timeout(3_600_000),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type AppliedMaterialsSnapshot = {
  resume_source: "library" | "tailored" | null;
  master_cv_id: number | null;
  master_cv_name: string | null;
  resume_asset_id: number | null;
  cover_letter_asset_id: number | null;
  resume_snapshot?: Record<string, unknown> | null;
  cover_letter_snapshot?: Record<string, unknown> | null;
  resume_file_path?: string | null;
  cover_letter_file_path?: string | null;
};

export type TrackingRow = {
  tracking_id: number;
  job_id: number;
  application_status: string;
  updated_at: string | null;
  applied_at: string | null;
  title: string;
  company: string;
  description_clean: string | null;
  description_raw: string | null;
  url: string | null;
  applied_materials: AppliedMaterialsSnapshot;
  jd_snapshot_text?: string | null;
  score_snapshot?: { score?: number | null; reason_summary?: string | null; requirement_matches?: unknown[] } | null;
  status_history?: { status: string; at: string }[];
};

export type FunnelMetrics = { matched: number; applied: number; interviewed: number; offer: number; rejected: number };

export async function fetchTracking(userId: number): Promise<{
  rows: TrackingRow[];
  assets_by_job_id: Record<string, Record<string, { id: number; file_path: string | null }>>;
  metrics: { tracked: number; applied: number; high_match: number; interviews: number; offers: number; applied_last_7_days: number };
  funnel: FunnelMetrics;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/tracking`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postTrackingStage(
  userId: number,
  jobId: number,
  stage: "interview" | "offer" | "rejected"
): Promise<void> {
  const r = await fetch(
    `${apiPrefix}/users/${userId}/tracking/jobs/${jobId}/stage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage }),
    }
  );
  if (!r.ok) throw new Error(await errText(r));
}

export async function deleteTrackingRecord(userId: number, jobId: number): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/tracking/jobs/${jobId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await errText(r));
}

export function trackingDownloadUrl(
  userId: number,
  jobId: number,
  kind: "jd_txt" | "resume_docx" | "letter_docx"
): string {
  const q = new URLSearchParams({ kind });
  return `${apiPrefix}/users/${userId}/jobs/${jobId}/downloads?${q}`;
}

export type AssetRow = {
  id: number;
  user_id: number;
  job_id: number;
  asset_type: string;
  content_json: Record<string, unknown>;
  content_text: string | null;
  file_path: string | null;
  job_title: string;
  company: string;
  is_tailored_resume: boolean;
};

export async function fetchAssetsList(userId: number): Promise<{
  assets: AssetRow[];
  excluded_resume_letter_in_tracking_count?: number;
}> {
  const r = await fetch(`${apiPrefix}/users/${userId}/assets`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export function assetPreviewUrl(userId: number, assetId: number): string {
  return `${apiPrefix}/users/${userId}/assets/${assetId}/preview-html`;
}

export async function putAssetContent(
  userId: number,
  assetId: number,
  contentJson: Record<string, unknown>
): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/assets/${assetId}/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_json: contentJson }),
  });
  if (!r.ok) throw new Error(await errText(r));
}

export type AssetPreviewThumbnail = {
  pages: number | null;
  thumbnail_png_base64: string | null;
};

export async function postPreviewThumbnail(
  userId: number,
  assetId: number,
  contentJson: Record<string, unknown>
): Promise<AssetPreviewThumbnail> {
  const r = await fetch(`${apiPrefix}/users/${userId}/assets/${assetId}/preview-thumbnail`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_json: contentJson }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export function assetExportFileUrl(userId: number, assetId: number): string {
  return `${apiPrefix}/users/${userId}/assets/${assetId}/export-file`;
}

// —— Experience library: directions / candidate facts / experience units ——

export type JobDirection = {
  id: number;
  title: string;
  expanded_text: string | null;
  is_active: boolean;
  embed_model: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export async function fetchDirections(userId: number): Promise<JobDirection[]> {
  const r = await fetch(`${apiPrefix}/users/${userId}/directions`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postCreateDirection(userId: number, title: string): Promise<JobDirection> {
  const r = await fetch(`${apiPrefix}/users/${userId}/directions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putDirection(
  userId: number,
  directionId: number,
  body: { title?: string; is_active?: boolean }
): Promise<JobDirection> {
  const r = await fetch(`${apiPrefix}/users/${userId}/directions/${directionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function deleteDirection(userId: number, directionId: number): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/directions/${directionId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await errText(r));
}

export type FactAtom = { id: string; type: string; label: string; detail?: Record<string, unknown> };

export type CandidateFacts = {
  atoms: FactAtom[];
  total_years_experience: number | null;
  source: string | null;
  confirmed: boolean;
};

export async function fetchCandidateFacts(userId: number): Promise<CandidateFacts> {
  const r = await fetch(`${apiPrefix}/users/${userId}/candidate-facts`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putCandidateFacts(
  userId: number,
  body: { atoms: FactAtom[]; total_years_experience: number | null }
): Promise<CandidateFacts> {
  const r = await fetch(`${apiPrefix}/users/${userId}/candidate-facts`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type ExperienceUnit = {
  id: number;
  title: string | null;
  employer: string | null;
  background: string | null;
  actions: string | null;
  technologies: string[];
  ownership: string | null;
  results: string | null;
  domain: string | null;
  start_date: string | null;
  end_date: string | null;
  raw_date_text: string | null;
  raw_text: string | null;
  order_index: number;
  tier: string | null;
  source: string | null;
  confirmed: boolean;
};

export async function fetchExperienceUnits(userId: number): Promise<ExperienceUnit[]> {
  const r = await fetch(`${apiPrefix}/users/${userId}/experience-units`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type ExperienceUnitInput = Partial<Omit<ExperienceUnit, "id" | "source" | "confirmed">>;

export async function postCreateExperienceUnit(
  userId: number,
  body: ExperienceUnitInput
): Promise<ExperienceUnit> {
  const r = await fetch(`${apiPrefix}/users/${userId}/experience-units`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putExperienceUnit(
  userId: number,
  unitId: number,
  body: ExperienceUnitInput
): Promise<ExperienceUnit> {
  const r = await fetch(`${apiPrefix}/users/${userId}/experience-units/${unitId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function deleteExperienceUnit(userId: number, unitId: number): Promise<void> {
  const r = await fetch(`${apiPrefix}/users/${userId}/experience-units/${unitId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await errText(r));
}

export async function postExtractExperienceFromMasterCv(userId: number): Promise<Record<string, unknown>> {
  const r = await fetch(`${apiPrefix}/users/${userId}/experience-library/extract-from-master-cv`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export type StartJobSearchResult = {
  scoring: { scored: number; skipped: number; rejected: number };
  notified: { sent: number; skipped?: number };
};

export async function postStartJobSearch(userId: number): Promise<StartJobSearchResult> {
  const r = await fetch(`${apiPrefix}/users/${userId}/experience-library/start-job-search`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

// —— Materials editor (resume + cover letter for a single job, opened in a new tab) ——

export type MaterialAsset = {
  id: number;
  job_id: number;
  content: Record<string, unknown>;
  has_file: boolean;
  llm_model: string | null;
  updated_at: string | null;
};

export async function fetchJobMaterials(
  userId: number,
  jobId: number
): Promise<{ job: { id: number; title: string; company: string }; resume: MaterialAsset | null; cover_letter: MaterialAsset | null }> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs/${jobId}/materials`);
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postGenerateResume(userId: number, jobId: number): Promise<MaterialAsset> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs/${jobId}/resume/generate`, { method: "POST" });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function putResumeSelection(
  userId: number,
  jobId: number,
  unitIdsInOrder: number[]
): Promise<MaterialAsset> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs/${jobId}/resume/selection`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ unit_ids_in_order: unitIdsInOrder }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function postGenerateCoverLetter(userId: number, jobId: number): Promise<MaterialAsset> {
  const r = await fetch(`${apiPrefix}/users/${userId}/jobs/${jobId}/cover-letter/generate`, { method: "POST" });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}
