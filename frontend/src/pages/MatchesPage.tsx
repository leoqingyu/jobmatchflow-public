import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchResumeSlotsBundle,
  fetchSavedJobs,
  fetchUserJobs,
  postGenerateCoverLetter,
  postGenerateResume,
  postMarkApplied,
  postSaveJob,
  postUnsaveJob,
  type JobRow,
  type JobsListResponse,
  type RequirementMatch,
  type ResumeChoice,
  type ResumeSlotItem,
  type SavedJobRow,
} from "../api";
import { activeUserId, appPath, readAuthSession } from "../config";
import Modal from "../components/Modal";

type InboxFilter = "new" | "recent" | "preparing" | "dismissed" | "unscored" | "vector_filtered" | "employment_type_filtered";

/** Jobs list / New jobs tabs: scored and cleared the review threshold (score >= 60, i.e. decision is "review" or "generate"). */
function passedReviewThreshold(job: JobRow): boolean {
  return job.decision === "review" || job.decision === "generate";
}

const RECENT_WINDOW_DAYS = 7;

function userLabel(job: JobRow): string {
  if (job.decision === "generate") return "Strong match";
  if (job.decision === "review") return "Worth reviewing";
  if (job.decision === "discard") return "Dismissed";
  return "Jobs list";
}

function isRecent(job: JobRow): boolean {
  const posted = job.date_posted ?? job.created_at;
  if (!posted) return false;
  const postedMs = new Date(posted).getTime();
  if (Number.isNaN(postedMs)) return false;
  return Date.now() - postedMs <= RECENT_WINDOW_DAYS * 24 * 60 * 60 * 1000;
}

function matchesFilter(job: JobRow, filter: InboxFilter): boolean {
  // 打了分但没过 60 分线——不含被向量/实习全职硬过滤拦掉的（那两类各自有自己的 tab，见下）
  if (filter === "dismissed") return job.processing_status === "scored" && job.decision === "discard";
  if (filter === "preparing") {
    return !job.in_application && (job.has_resume_asset || job.has_cover_letter_asset);
  }
  if (filter === "unscored") return job.processing_status === "unscored";
  if (filter === "vector_filtered") return job.processing_status === "vector_filtered";
  if (filter === "employment_type_filtered") return job.processing_status === "employment_type_filtered";
  if (filter === "recent") return passedReviewThreshold(job) && isRecent(job);
  return passedReviewThreshold(job);
}

function scoreClass(
  score: number | null,
  reviewThreshold = 60,
  generateThreshold = 80,
): string {
  if (score == null) return "bg-canvas text-muted";
  if (score >= generateThreshold) return "bg-emerald-100 text-emerald-800";
  if (score >= reviewThreshold) return "bg-amber-100 text-amber-800";
  return "bg-canvas text-muted";
}

function matchLevelClass(level: string | null): string {
  if (level === "exceeds" || level === "full") return "bg-emerald-100 text-emerald-800";
  if (level === "strong" || level === "partial") return "bg-amber-100 text-amber-800";
  if (level === "weak" || level === "none") return "bg-red-100 text-red-800";
  return "bg-canvas text-muted";
}

const _CATEGORY_LABELS: Record<string, string> = {
  skill: "Skills",
  experience: "Experience",
  capability: "Capabilities",
  education: "Education",
  domain: "Domain",
  language: "Language",
  work_authorization: "Work authorization",
  certification: "Certification",
};

function categoryLabel(category: string | null): string {
  return (category && _CATEGORY_LABELS[category]) || "Other";
}

export default function MatchesPage() {
  const userId = activeUserId(readAuthSession());
  const [data, setData] = useState<JobsListResponse | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filter, setFilter] = useState<InboxFilter>("new");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<"resume" | "letter" | "apply" | "save" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedJobs, setSavedJobs] = useState<SavedJobRow[]>([]);
  const [savedPanelOpen, setSavedPanelOpen] = useState(true);
  const [showApplyModal, setShowApplyModal] = useState(false);
  const [resumeChoice, setResumeChoice] = useState<ResumeChoice | null>(null);
  const [resumeSlots, setResumeSlots] = useState<ResumeSlotItem[]>([]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const next = await fetchUserJobs(userId);
      setData(next);
      setSelectedId((current) => current ?? next.jobs[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load matches");
    }
  }, [userId]);

  const loadSaved = useCallback(async () => {
    try {
      const next = await fetchSavedJobs(userId);
      setSavedJobs(next.jobs);
    } catch {
      /* saved-jobs panel is a convenience, ignore load failures silently */
    }
  }, [userId]);

  const loadResumeSlots = useCallback(async () => {
    try {
      const next = await fetchResumeSlotsBundle(userId);
      setResumeSlots(next.items);
    } catch {
      /* apply modal falls back to generic "Resume N" labels, ignore load failures silently */
    }
  }, [userId]);

  useEffect(() => { void load(); void loadSaved(); void loadResumeSlots(); }, [load, loadSaved, loadResumeSlots]);

  useEffect(() => { setMessage(null); }, [selectedId]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.jobs ?? []).filter((job) => {
      if (!matchesFilter(job, filter)) return false;
      if (!needle) return true;
      return `${job.title} ${job.company} ${job.country} ${job.source_label}`.toLowerCase().includes(needle);
    });
  }, [data, filter, query]);

  const selected =
    (data?.jobs ?? []).find((job) => job.id === selectedId) ?? visible[0] ?? null;
  const diagnostics = Boolean(data?.jobs_list_diagnostics);
  const modelComparison = Boolean(data?.jobs_list_model_comparison);

  const groupedRequirements = useMemo(() => {
    const compareById = new Map<string, RequirementMatch>();
    for (const rm of selected?.compare_requirement_matches ?? []) compareById.set(rm.id, rm);
    const groups = new Map<string, { primary: RequirementMatch; compare: RequirementMatch | null }[]>();
    for (const rm of selected?.requirement_matches ?? []) {
      const key = rm.category || "other";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push({ primary: rm, compare: compareById.get(rm.id) ?? null });
    }
    return Array.from(groups.entries());
  }, [selected]);

  const generateResume = async () => {
    if (!selected) return;
    setBusy("resume");
    setMessage(null);
    try {
      await postGenerateResume(userId, selected.id);
      setMessage("Resume is ready.");
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Resume generation failed");
    } finally {
      setBusy(null);
    }
  };

  const generateCoverLetter = async () => {
    if (!selected) return;
    setBusy("letter");
    setMessage(null);
    try {
      await postGenerateCoverLetter(userId, selected.id);
      setMessage("Cover letter is ready.");
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Cover letter generation failed");
    } finally {
      setBusy(null);
    }
  };

  const apply = async (choice: ResumeChoice) => {
    if (!selected) return;
    setBusy("apply");
    setMessage(null);
    try {
      await postMarkApplied(userId, selected.id, choice);
      setMessage("Saved to Applications with a snapshot of today's materials.");
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const openApplyModal = () => {
    if (!selected) return;
    setResumeChoice(null);
    setShowApplyModal(true);
  };

  const confirmApply = () => {
    if (!resumeChoice) return;
    setShowApplyModal(false);
    void apply(resumeChoice);
  };

  const toggleSave = async () => {
    if (!selected) return;
    setBusy("save");
    setMessage(null);
    try {
      if (selected.is_saved) {
        await postUnsaveJob(userId, selected.id);
      } else {
        await postSaveJob(userId, selected.id);
      }
      await Promise.all([load(), loadSaved()]);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const openEditor = (jobId: number) => {
    window.open(appPath(`materials/${jobId}`), "_blank", "noopener");
  };

  return (
    <div className="-mx-2 sm:-mx-4">
      <div className="flex flex-col gap-4 border-b border-line px-2 pb-5 sm:flex-row sm:items-end sm:justify-between sm:px-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Daily job inbox</p>
          <h1 className="mt-1 text-3xl font-semibold text-ink">Jobs</h1>
          <p className="mt-1 text-sm text-muted">Focus on the roles worth a decision today.</p>
        </div>
        <div className="flex gap-2">
          <input aria-label="Search jobs" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search roles or companies" className="w-52 rounded-lg border border-line bg-white px-3 py-2 text-sm" />
          <button type="button" onClick={() => void load()} className="rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium hover:bg-canvas">Refresh</button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 px-2 py-4 sm:px-4">
        {(["new", "recent", "preparing", "dismissed", ...(diagnostics ? ["unscored", "vector_filtered", "employment_type_filtered"] : [])] as InboxFilter[]).map((item) => {
          const labels: Record<InboxFilter, string> = { new: "Jobs list", recent: "New jobs", preparing: "Preparing", dismissed: "Dismissed", unscored: "Unscored", vector_filtered: "Vector filtered", employment_type_filtered: "Employment type filtered" };
          return <button key={item} type="button" onClick={() => setFilter(item)} className={`rounded-full px-3 py-1.5 text-sm ${filter === item ? "bg-ink text-white" : "bg-white text-muted ring-1 ring-line hover:text-ink"}`}>{labels[item]}</button>;
        })}
      </div>

      {error && <p className="mx-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:mx-4">{error}</p>}
      {!data && !error && <p className="px-2 text-sm text-muted sm:px-4">Loading matches…</p>}
      {data && (
        <div className="grid min-h-[620px] gap-0 overflow-hidden rounded-xl border border-line bg-white shadow-sm lg:grid-cols-[minmax(280px,34%)_1fr]">
          <section className="border-b border-line bg-canvas/60 lg:border-b-0 lg:border-r">
            {savedJobs.length > 0 && (
              <div className="border-b border-line bg-white">
                <button type="button" onClick={() => setSavedPanelOpen((v) => !v)} className="flex w-full items-center justify-between px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink">
                  <span>★ Saved ({savedJobs.length})</span>
                  <span className="text-muted">{savedPanelOpen ? "▾" : "▸"}</span>
                </button>
                {savedPanelOpen && (
                  <div className="max-h-64 overflow-y-auto border-t border-line">
                    {savedJobs.map((job) => (
                      <button key={job.job_id} type="button" onClick={() => setSelectedId(job.job_id)} className={`block w-full border-b border-line px-4 py-2.5 text-left transition ${selected?.id === job.job_id ? "bg-accent-soft/40" : "hover:bg-canvas"}`}>
                        <p className="truncate text-sm font-medium text-ink">{job.title}</p>
                        <p className="mt-0.5 truncate text-xs text-muted">{job.company || "—"}</p>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div className="border-b border-line px-4 py-3 text-xs text-muted">{visible.length} roles for you</div>
            <div className="max-h-[720px] overflow-y-auto">
              {visible.map((job) => (
                <button key={job.id} type="button" onClick={() => setSelectedId(job.id)} className={`block w-full border-b border-line px-4 py-4 text-left transition ${selected?.id === job.id ? "border-l-2 border-accent bg-white" : "hover:bg-white/70"} ${job.in_application ? "opacity-50" : ""}`}>
                  <div className="flex items-start gap-3">
                    <div className="flex shrink-0 flex-col items-center gap-1">
                      <span className={`rounded-md px-2 py-1 text-xs font-bold ${scoreClass(job.score, data.score_threshold_review, data.score_threshold_generate)}`}>{job.score == null ? "—" : Math.round(job.score)}</span>
                      {modelComparison && job.compare_score != null && (
                        <span className="rounded-md bg-violet-100 px-2 py-0.5 text-[10px] font-bold text-violet-800" title={job.compare_model ?? undefined}>{Math.round(job.compare_score)}</span>
                      )}
                    </div>
                    <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-ink">{job.title}{job.in_application && <span className="ml-2 rounded-full bg-canvas px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">Applied</span>}</p><p className="mt-0.5 truncate text-sm text-muted">{job.company}</p><p className="mt-2 truncate text-xs text-muted">{job.country || "Location not specified"} · {userLabel(job)}{diagnostics && job.vector_similarity != null ? ` · vec ${job.vector_similarity.toFixed(3)}` : ""}</p></div>
                  </div>
                </button>
              ))}
              {visible.length === 0 && <p className="px-4 py-8 text-sm text-muted">No roles in this view.</p>}
            </div>
          </section>

          <section className="min-w-0 p-5 sm:p-8">
            {!selected ? <p className="text-sm text-muted">Select a role to see why it may be worth your time.</p> : <>
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div><p className="text-sm text-muted">{selected.source_label} · {selected.country || "Location not specified"}</p><h2 className="mt-1 text-2xl font-semibold text-ink">{selected.title}</h2><p className="mt-1 text-sm text-muted">{selected.company}</p>{diagnostics && <p className="mt-2 text-xs text-muted">处理状态：{selected.processing_status}{selected.vector_similarity != null ? ` · 最高向量相似度：${selected.vector_similarity.toFixed(4)}` : ""}</p>}</div>
                <div className="flex items-start gap-2">
                  {selected.score != null && <div className={`rounded-xl px-4 py-3 text-center ${scoreClass(selected.score, data.score_threshold_review, data.score_threshold_generate)}`}><p className="text-2xl font-bold">{Math.round(selected.score)}</p><p className="text-xs font-medium">{userLabel(selected)}</p></div>}
                  {modelComparison && selected.compare_score != null && (
                    <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-center text-violet-800">
                      <p className="text-2xl font-bold">{Math.round(selected.compare_score)}</p>
                      <p className="text-xs font-medium">{selected.compare_model} · {selected.compare_decision}</p>
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-6 rounded-xl border border-accent/20 bg-accent-soft/40 p-4">
                <h3 className="font-semibold text-ink">Why this may fit</h3>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink">{selected.reason || "We are still preparing the match explanation."}</p>
                {selected.preference_bonus != null && selected.preference_bonus > 0 && (
                  <p className="mt-2 text-xs font-medium text-accent">+{selected.preference_bonus} bonus{selected.preference_bonus_reason ? ` — ${selected.preference_bonus_reason}` : ""}</p>
                )}
                {modelComparison && selected.compare_reason && (
                  <p className="mt-3 whitespace-pre-wrap border-t border-accent/20 pt-3 text-sm leading-relaxed text-violet-800"><span className="font-semibold">{selected.compare_model}: </span>{selected.compare_reason}</p>
                )}
              </div>

              {(selected.hard_constraints_hit.length > 0 || selected.seniority_mismatch) && (
                <div className="mt-4 rounded-xl border border-red-200 bg-red-50 p-4">
                  <p className="text-sm font-semibold text-red-800">
                    {selected.hard_constraints_hit.length > 0 && selected.seniority_mismatch
                      ? "This role has an unmet hard requirement and looks like a seniority mismatch."
                      : selected.hard_constraints_hit.length > 0
                      ? "This role has a hard requirement (language / work authorization / certification) that doesn't look met."
                      : selected.job_seniority === "senior"
                      ? "This looks like a senior-level role relative to your current experience."
                      : "This looks like a junior-level role relative to your current experience."}
                  </p>
                </div>
              )}

              {selected.requirement_matches.length > 0 && (
                <details className="mt-4 rounded-xl border border-line bg-white p-4">
                  <summary className="cursor-pointer text-sm font-semibold text-ink">View requirement-by-requirement match ({selected.requirement_matches.length})</summary>
                  <div className="mt-3 space-y-4">
                    {groupedRequirements.map(([category, items]) => (
                      <div key={category}>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted">{categoryLabel(category)}</p>
                        <div className="mt-2 space-y-2">
                          {items.map(({ primary, compare }) => (
                            <div key={primary.id} className="rounded-lg bg-canvas px-3 py-2">
                              <div className="flex items-start justify-between gap-3">
                                <div><p className="text-sm text-ink">{primary.text || primary.id}</p>{primary.reason && <p className="mt-1 text-xs text-muted">{primary.reason}</p>}</div>
                                <span className={`shrink-0 rounded-full px-2 py-1 text-xs font-bold ${matchLevelClass(primary.match_level)}`}>{primary.match_level || "unknown"}</span>
                              </div>
                              {modelComparison && compare && (
                                <div className="mt-2 flex items-start justify-between gap-3 border-t border-line pt-2">
                                  <p className="text-xs text-violet-800">{compare.reason || "—"}</p>
                                  <span className={`shrink-0 rounded-full px-2 py-1 text-xs font-bold ${matchLevelClass(compare.match_level)}`}>{compare.match_level || "unknown"}</span>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              )}

              <div className="mt-6 border-t border-line pt-6">
                {/* Primary actions — the ones used most often day-to-day, kept large and
                    high-contrast so they're easy to hit on a phone. */}
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void toggleSave()}
                    className={[
                      "rounded-lg px-4 py-3.5 text-sm font-semibold shadow-sm disabled:opacity-50",
                      selected.is_saved
                        ? "bg-amber-400 text-white hover:bg-amber-500"
                        : "border-2 border-amber-400 text-amber-600 hover:bg-amber-50",
                    ].join(" ")}
                  >
                    {busy === "save" ? "…" : selected.is_saved ? "★ Saved" : "☆ Save job"}
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={openApplyModal}
                    className={[
                      "rounded-lg px-4 py-3.5 text-sm font-semibold shadow-sm disabled:opacity-50",
                      selected.in_application
                        ? "border-2 border-stage-applied text-stage-applied hover:bg-sky-50"
                        : "bg-stage-applied text-white hover:bg-sky-600",
                    ].join(" ")}
                  >
                    {busy === "apply" ? "Saving…" : selected.in_application ? "✓ Applied — update" : "Mark as applied"}
                  </button>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <button type="button" disabled={busy !== null} onClick={() => void generateResume()} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">
                    {busy === "resume" ? "Preparing…" : selected.has_resume_asset ? "Regenerate resume" : "Generate resume"}
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null || !selected.has_resume_asset}
                    title={selected.has_resume_asset ? undefined : "Generate the resume first — the cover letter reuses its selected bullets"}
                    onClick={() => void generateCoverLetter()}
                    className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
                  >
                    {busy === "letter" ? "Preparing…" : selected.has_cover_letter_asset ? "Regenerate cover letter" : "Generate cover letter"}
                  </button>
                  {(selected.has_resume_asset || selected.has_cover_letter_asset) && (
                    <button type="button" onClick={() => openEditor(selected.id)} className="rounded-lg border border-line bg-white px-3 py-2.5 text-sm font-medium text-muted hover:text-ink">
                      Edit ↗
                    </button>
                  )}
                  {message && <span className="text-sm text-accent">{message}</span>}
                </div>
              </div>

              <Modal open={showApplyModal} title="Which resume did you use?" onClose={() => setShowApplyModal(false)}>
                <div className="space-y-2">
                  {([["slot_1", resumeSlots[0]], ["slot_2", resumeSlots[1]]] as [ResumeChoice, ResumeSlotItem | undefined][]).map(([choice, slot]) => (
                    <label key={choice} className={`flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm ${slot ? "cursor-pointer text-ink hover:bg-canvas" : "cursor-not-allowed text-line"}`}>
                      <input type="radio" name="resume-choice" className="h-4 w-4 accent-accent" checked={resumeChoice === choice} disabled={!slot} onChange={() => setResumeChoice(choice)} />
                      {slot ? slot.cv_name : `Resume ${choice === "slot_1" ? "1" : "2"} (not uploaded)`}
                    </label>
                  ))}
                  <label className={`flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm ${selected.has_resume_asset ? "cursor-pointer text-ink hover:bg-canvas" : "cursor-not-allowed text-line"}`}>
                    <input type="radio" name="resume-choice" className="h-4 w-4 accent-accent" checked={resumeChoice === "tailored"} disabled={!selected.has_resume_asset} onChange={() => setResumeChoice("tailored")} />
                    Generated resume{!selected.has_resume_asset && " (not generated yet)"}
                  </label>
                </div>
                <div className="mt-5 flex justify-end gap-2">
                  <button type="button" onClick={() => setShowApplyModal(false)} className="rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium text-ink hover:bg-canvas">Cancel</button>
                  <button type="button" disabled={!resumeChoice} onClick={confirmApply} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">Confirm</button>
                </div>
              </Modal>

              <div className="mt-6 flex flex-wrap gap-2">
                <a href={selected.url ?? "#"} target="_blank" rel="noreferrer" className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas">Open original posting</a>
              </div>

              <details className="mt-6 border-t border-line pt-5"><summary className="cursor-pointer text-sm font-semibold text-ink">Job description</summary><div className="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-muted">{selected.description_clean || selected.description_raw || "We found this position and are retrieving the full description."}</div></details>
            </>}
          </section>
        </div>
      )}
    </div>
  );
}
