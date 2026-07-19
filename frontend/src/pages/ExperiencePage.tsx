import { useCallback, useEffect, useState } from "react";
import {
  SCORING_PREFERENCES_MAX_CHARS,
  deleteDirection,
  deleteExperienceUnit,
  fetchCandidateFacts,
  fetchDirections,
  fetchEmploymentTypePreference,
  fetchExperienceUnits,
  fetchMasterCvJson,
  fetchScoringPreferences,
  postCreateDirection,
  postCreateExperienceUnit,
  postStartJobSearch,
  putCandidateFacts,
  putDirection,
  putEmploymentTypePreference,
  putExperienceUnit,
  putMasterCvJson,
  putScoringPreferences,
  type CandidateFacts,
  type EmploymentTypePreference,
  type ExperienceUnit,
  type ExperienceUnitInput,
  type JobDirection,
  type StartJobSearchResult,
} from "../api";
import { activeUserId, readAuthSession } from "../config";

type EducationEntry = { degree: string; major: string; institution: string; date_range: string; location: string; bullets: string };
type LinkEntry = { label: string; url: string };
type BasicInfo = {
  full_name: string;
  location: string;
  phone: string;
  email: string;
  visa: string;
  linkedin: LinkEntry;
  github: LinkEntry;
  profile_summary: string;
  education: EducationEntry[];
};

const EMPTY_BASIC_INFO: BasicInfo = {
  full_name: "", location: "", phone: "", email: "", visa: "",
  linkedin: { label: "", url: "" }, github: { label: "", url: "" },
  profile_summary: "", education: [],
};

/** master_cv_json stores linkedin/github as {label,url} objects (see renderer/docx_render.py);
 * older data may have a bare string URL — normalize both into the object shape the form edits. */
function readLink(value: unknown): LinkEntry {
  if (value && typeof value === "object") {
    const v = value as Record<string, unknown>;
    return { label: String(v.label ?? ""), url: String(v.url ?? "") };
  }
  return { label: "", url: value ? String(value) : "" };
}

const EMPLOYMENT_TYPE_OPTIONS: { value: EmploymentTypePreference; label: string; hint: string }[] = [
  { value: "both", label: "Both", hint: "No filtering — internships and full-time roles are both matched." },
  { value: "internship_only", label: "Internships only", hint: "Only internships, trainee and graduate programs are matched; full-time roles are excluded." },
  { value: "full_time_only", label: "Full-time only", hint: "Internships are excluded; only full-time roles are matched." },
];

const TIERS: { value: string; label: string }[] = [
  { value: "flagship", label: "Flagship" },
  { value: "solid", label: "Solid" },
  { value: "filler", label: "Filler" },
];

function tierClass(tier: string | null): string {
  if (tier === "flagship") return "bg-emerald-100 text-emerald-800";
  if (tier === "solid") return "bg-sky-100 text-sky-800";
  if (tier === "filler") return "bg-slate-200 text-slate-700";
  return "bg-canvas text-muted";
}

function emptyUnitDraft(): ExperienceUnitInput {
  return { title: "", employer: "", background: "", actions: "", results: "", domain: "", technologies: [], ownership: null, raw_date_text: "", tier: "solid" };
}

export default function ExperiencePage() {
  const userId = activeUserId(readAuthSession());

  // Employment type preference (internship / full-time / both) — the frontmost filter in the
  // matching pipeline (services/scoring_service.py Step 0, before Step 3's expensive per-job LLM
  // matching even runs). Locked/read-only until "Edit", same pattern as Basic info.
  const [employmentTypePreference, setEmploymentTypePreference] = useState<EmploymentTypePreference>("both");
  const [employmentTypePreferenceDraft, setEmploymentTypePreferenceDraft] = useState<EmploymentTypePreference>("both");
  const [employmentTypePreferenceEditing, setEmploymentTypePreferenceEditing] = useState(false);
  const [employmentTypePreferenceBusy, setEmploymentTypePreferenceBusy] = useState(false);
  const [employmentTypePreferenceMsg, setEmploymentTypePreferenceMsg] = useState<string | null>(null);

  // Basic info — locked/read-only by default; "Edit" opens a draft copy, "Cancel" discards it,
  // "Save" is the only path that commits. basicInfo itself only ever holds the last-saved value.
  const [basicInfo, setBasicInfo] = useState<BasicInfo>(EMPTY_BASIC_INFO);
  const [basicInfoDraft, setBasicInfoDraft] = useState<BasicInfo>(EMPTY_BASIC_INFO);
  const [basicInfoEditing, setBasicInfoEditing] = useState(false);
  // Full master_cv_json as last loaded — PUT replaces the whole record, so fields this form
  // doesn't manage (e.g. legacy experience/skills used for the scoring-context HTML render)
  // must be preserved by merging onto this rather than sending the form fields alone.
  const [rawMasterCv, setRawMasterCv] = useState<Record<string, unknown>>({});
  const [basicInfoBusy, setBasicInfoBusy] = useState(false);
  const [basicInfoMsg, setBasicInfoMsg] = useState<string | null>(null);
  const [factsSummary, setFactsSummary] = useState<string | null>(null);

  // Candidate facts (atoms) — skill + education atoms editable here (chip add/remove,
  // shared save path via saveFactsAtoms); other atom types (language/certification/...)
  // still only come from the one-time master-CV bootstrap extraction, no UI for those yet.
  // total_years_experience also lives on this same record — Step 4's seniority-mismatch gate
  // reads it, so without a way to set it here that gate can never fire for manually-entered profiles.
  const [facts, setFacts] = useState<CandidateFacts>({ atoms: [], total_years_experience: null, source: null, confirmed: false });
  const [newSkillLabel, setNewSkillLabel] = useState("");
  // Experience level (total years) — locked/read-only until "Edit", same pattern as Basic info:
  // totalYearsInput only holds the draft while editing, facts.total_years_experience is the
  // committed value shown in the locked view.
  const [totalYearsInput, setTotalYearsInput] = useState("");
  const [experienceLevelEditing, setExperienceLevelEditing] = useState(false);
  const [factsBusy, setFactsBusy] = useState(false);

  // Directions
  const [directions, setDirections] = useState<JobDirection[]>([]);
  const [newDirectionTitle, setNewDirectionTitle] = useState("");
  const [directionBusy, setDirectionBusy] = useState(false);

  // Scoring preferences — moved here from Settings; free text folded into the AI scoring bonus.
  // Locked/read-only until "Edit", same pattern as Basic info: scoringPreferencesDraft only
  // holds the draft while editing, scoringPreferences is the committed value.
  const [scoringPreferences, setScoringPreferences] = useState("");
  const [scoringPreferencesDraft, setScoringPreferencesDraft] = useState("");
  const [scoringPreferencesEditing, setScoringPreferencesEditing] = useState(false);
  const [scoringPreferencesBusy, setScoringPreferencesBusy] = useState(false);
  const [scoringPreferencesMsg, setScoringPreferencesMsg] = useState<string | null>(null);

  // Experience units
  const [units, setUnits] = useState<ExperienceUnit[]>([]);
  const [expandedUnitId, setExpandedUnitId] = useState<number | "new" | null>(null);
  const [unitDraft, setUnitDraft] = useState<ExperienceUnitInput>(emptyUnitDraft());
  const [unitBusy, setUnitBusy] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Start job search — one-time (but safe to re-run) trigger: scores every unscored job
  // for this user right now instead of waiting for the nightly batch, then generates resume
  // + cover letter for anything that clears the high-score bar.
  const [startBusy, setStartBusy] = useState(false);
  const [startResult, setStartResult] = useState<StartJobSearchResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cv, factsResult, dirs, unitRows, scoring, employmentType] = await Promise.all([
        fetchMasterCvJson(userId),
        fetchCandidateFacts(userId),
        fetchDirections(userId),
        fetchExperienceUnits(userId),
        fetchScoringPreferences(userId),
        fetchEmploymentTypePreference(userId),
      ]);
      setEmploymentTypePreference(employmentType.employment_type_preference);
      const raw = cv.master_cv_json || {};
      setRawMasterCv(raw);
      setBasicInfo({
        full_name: String(raw.full_name ?? ""),
        location: String(raw.location ?? ""),
        phone: String(raw.phone ?? ""),
        email: String(raw.email ?? ""),
        visa: String(raw.visa ?? ""),
        linkedin: readLink(raw.linkedin),
        github: readLink(raw.github),
        profile_summary: String(raw.profile_summary ?? ""),
        education: Array.isArray(raw.education)
          ? (raw.education as Record<string, unknown>[]).map((e) => ({
              degree: String(e.degree ?? ""),
              major: String(e.major ?? ""),
              institution: String(e.institution ?? ""),
              date_range: String(e.date_range ?? ""),
              location: String(e.location ?? ""),
              bullets: Array.isArray(e.bullets) ? (e.bullets as string[]).join("\n") : "",
            }))
          : [],
      });
      setFactsSummary(
        factsResult.atoms.length || factsResult.total_years_experience != null
          ? `${factsResult.atoms.length} facts extracted${factsResult.total_years_experience != null ? ` · ${factsResult.total_years_experience} years total experience` : ""}`
          : null
      );
      setFacts(factsResult);
      setTotalYearsInput(factsResult.total_years_experience != null ? String(factsResult.total_years_experience) : "");
      setDirections(dirs);
      setUnits(unitRows);
      setScoringPreferences(scoring.scoring_preferences);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load experience library");
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { void load(); }, [load]);

  const startEditEmploymentTypePreference = () => {
    setEmploymentTypePreferenceDraft(employmentTypePreference);
    setEmploymentTypePreferenceEditing(true);
    setEmploymentTypePreferenceMsg(null);
  };

  const cancelEditEmploymentTypePreference = () => {
    setEmploymentTypePreferenceEditing(false);
    setEmploymentTypePreferenceMsg(null);
  };

  const saveEmploymentTypePreference = async () => {
    setEmploymentTypePreferenceBusy(true);
    setEmploymentTypePreferenceMsg(null);
    try {
      await putEmploymentTypePreference(userId, employmentTypePreferenceDraft);
      setEmploymentTypePreference(employmentTypePreferenceDraft);
      setEmploymentTypePreferenceEditing(false);
      setEmploymentTypePreferenceMsg("Saved.");
    } catch (e) {
      setEmploymentTypePreferenceMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setEmploymentTypePreferenceBusy(false);
    }
  };

  const startEditBasicInfo = () => {
    setBasicInfoDraft(basicInfo);
    setBasicInfoEditing(true);
    setBasicInfoMsg(null);
  };

  const cancelEditBasicInfo = () => {
    setBasicInfoEditing(false);
    setBasicInfoMsg(null);
  };

  const saveBasicInfo = async () => {
    setBasicInfoBusy(true);
    setBasicInfoMsg(null);
    try {
      await putMasterCvJson(userId, {
        ...rawMasterCv,
        ...basicInfoDraft,
        linkedin: basicInfoDraft.linkedin.url.trim() ? { label: basicInfoDraft.linkedin.label.trim() || "LinkedIn", url: basicInfoDraft.linkedin.url.trim() } : null,
        github: basicInfoDraft.github.url.trim() ? { label: basicInfoDraft.github.label.trim() || "GitHub", url: basicInfoDraft.github.url.trim() } : null,
        education: basicInfoDraft.education.map((e) => ({ ...e, bullets: e.bullets.split("\n").map((b) => b.trim()).filter(Boolean) })),
      });
      setBasicInfo(basicInfoDraft);
      setBasicInfoEditing(false);
      setBasicInfoMsg("Basic info saved.");
    } catch (e) {
      setBasicInfoMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBasicInfoBusy(false);
    }
  };

  const saveFactsAtoms = async (atoms: typeof facts.atoms, totalYears: number | null, errorFallback: string): Promise<boolean> => {
    setFactsBusy(true);
    try {
      await putCandidateFacts(userId, { atoms, total_years_experience: totalYears });
      await load();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : errorFallback);
      return false;
    } finally {
      setFactsBusy(false);
    }
  };

  const addSkill = async () => {
    const label = newSkillLabel.trim();
    if (!label) return;
    await saveFactsAtoms(
      [...facts.atoms, { id: "", type: "skill", label, detail: {} }],
      facts.total_years_experience,
      "Unable to add skill"
    );
    setNewSkillLabel("");
  };

  const removeSkill = async (atomId: string) => {
    await saveFactsAtoms(
      facts.atoms.filter((a) => a.id !== atomId),
      facts.total_years_experience,
      "Unable to remove skill"
    );
  };

  const startEditExperienceLevel = () => {
    setTotalYearsInput(facts.total_years_experience != null ? String(facts.total_years_experience) : "");
    setExperienceLevelEditing(true);
  };

  const cancelEditExperienceLevel = () => {
    setExperienceLevelEditing(false);
  };

  const saveTotalYears = async () => {
    const trimmed = totalYearsInput.trim();
    const parsed = trimmed === "" ? null : Number(trimmed);
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) {
      setError("Total years of experience must be a non-negative number");
      return;
    }
    const ok = await saveFactsAtoms(facts.atoms, parsed, "Unable to save total years of experience");
    if (ok) setExperienceLevelEditing(false);
  };

  const startEditScoringPreferences = () => {
    setScoringPreferencesDraft(scoringPreferences);
    setScoringPreferencesEditing(true);
    setScoringPreferencesMsg(null);
  };

  const cancelEditScoringPreferences = () => {
    setScoringPreferencesEditing(false);
    setScoringPreferencesMsg(null);
  };

  const saveScoringPreferences = async () => {
    setScoringPreferencesBusy(true);
    setScoringPreferencesMsg(null);
    try {
      await putScoringPreferences(userId, scoringPreferencesDraft);
      setScoringPreferences(scoringPreferencesDraft);
      setScoringPreferencesEditing(false);
      setScoringPreferencesMsg("Scoring preferences saved.");
    } catch (e) {
      setScoringPreferencesMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setScoringPreferencesBusy(false);
    }
  };

  const addDirection = async () => {
    if (!newDirectionTitle.trim()) return;
    setDirectionBusy(true);
    try {
      await postCreateDirection(userId, newDirectionTitle.trim());
      setNewDirectionTitle("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to add direction");
    } finally {
      setDirectionBusy(false);
    }
  };

  const toggleDirection = async (direction: JobDirection) => {
    setDirectionBusy(true);
    try {
      await putDirection(userId, direction.id, { is_active: !direction.is_active });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to update direction");
    } finally {
      setDirectionBusy(false);
    }
  };

  const removeDirection = async (direction: JobDirection) => {
    setDirectionBusy(true);
    try {
      await deleteDirection(userId, direction.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to delete direction");
    } finally {
      setDirectionBusy(false);
    }
  };

  const openUnit = (unit: ExperienceUnit) => {
    setExpandedUnitId(unit.id);
    setUnitDraft({
      title: unit.title ?? "", employer: unit.employer ?? "", background: unit.background ?? "",
      actions: unit.actions ?? "", results: unit.results ?? "", domain: unit.domain ?? "",
      technologies: unit.technologies, ownership: unit.ownership, raw_date_text: unit.raw_date_text ?? "",
      tier: unit.tier ?? "solid",
    });
  };

  const openNewUnit = () => {
    setExpandedUnitId("new");
    setUnitDraft(emptyUnitDraft());
  };

  const saveUnit = async () => {
    setUnitBusy(true);
    try {
      if (expandedUnitId === "new") await postCreateExperienceUnit(userId, unitDraft);
      else if (expandedUnitId != null) await putExperienceUnit(userId, expandedUnitId, unitDraft);
      setExpandedUnitId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to save experience");
    } finally {
      setUnitBusy(false);
    }
  };

  const setTier = async (unit: ExperienceUnit, tier: string) => {
    setUnitBusy(true);
    try {
      await putExperienceUnit(userId, unit.id, { tier });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to update priority");
    } finally {
      setUnitBusy(false);
    }
  };

  const startJobSearch = async () => {
    setStartBusy(true);
    setStartResult(null);
    try {
      const result = await postStartJobSearch(userId);
      setStartResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start job search");
    } finally {
      setStartBusy(false);
    }
  };

  const removeUnit = async (unit: ExperienceUnit) => {
    if (!window.confirm(`Delete "${unit.title || "this experience"}"?`)) return;
    setUnitBusy(true);
    try {
      await deleteExperienceUnit(userId, unit.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to delete experience");
    } finally {
      setUnitBusy(false);
    }
  };

  const renderUnitForm = () => (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <input value={unitDraft.title ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, title: e.target.value })} placeholder="Title (e.g. Backend Engineer)" className="rounded-lg border border-line px-3 py-2 text-sm" />
        <input value={unitDraft.employer ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, employer: e.target.value })} placeholder="Employer / project" className="rounded-lg border border-line px-3 py-2 text-sm" />
        <input value={unitDraft.raw_date_text ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, raw_date_text: e.target.value })} placeholder="Dates (e.g. 2021 – Present)" className="rounded-lg border border-line px-3 py-2 text-sm" />
        <input value={unitDraft.domain ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, domain: e.target.value })} placeholder="Domain (e.g. fintech)" className="rounded-lg border border-line px-3 py-2 text-sm" />
      </div>
      <textarea value={unitDraft.background ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, background: e.target.value })} placeholder="Background — what was the situation?" rows={2} className="w-full rounded-lg border border-line px-3 py-2 text-sm" />
      <textarea value={unitDraft.actions ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, actions: e.target.value })} placeholder="Actions — what did you do?" rows={2} className="w-full rounded-lg border border-line px-3 py-2 text-sm" />
      <textarea value={unitDraft.results ?? ""} onChange={(e) => setUnitDraft({ ...unitDraft, results: e.target.value })} placeholder="Results — what happened?" rows={2} className="w-full rounded-lg border border-line px-3 py-2 text-sm" />
      <input
        value={(unitDraft.technologies ?? []).join(", ")}
        onChange={(e) => setUnitDraft({ ...unitDraft, technologies: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
        placeholder="Technologies, comma separated"
        className="w-full rounded-lg border border-line px-3 py-2 text-sm"
      />
      <div className="flex items-center gap-4 text-sm text-ink">
        <span className="font-medium">Ownership</span>
        <label className="flex items-center gap-1.5"><input type="radio" checked={unitDraft.ownership === "independent"} onChange={() => setUnitDraft({ ...unitDraft, ownership: "independent" })} />Independently delivered</label>
        <label className="flex items-center gap-1.5"><input type="radio" checked={unitDraft.ownership === "participant"} onChange={() => setUnitDraft({ ...unitDraft, ownership: "participant" })} />Contributed as part of a team</label>
      </div>
      <div className="flex items-center gap-4">
        <span className="text-sm font-medium text-ink">Priority</span>
        {TIERS.map((t) => (
          <label key={t.value} className="flex items-center gap-1.5 text-sm text-muted"><input type="radio" checked={unitDraft.tier === t.value} onChange={() => setUnitDraft({ ...unitDraft, tier: t.value })} />{t.label}</label>
        ))}
      </div>
      <div className="flex gap-2 border-t border-line pt-3">
        <button type="button" disabled={unitBusy} onClick={() => void saveUnit()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{unitBusy ? "Saving…" : "Save"}</button>
        <button type="button" onClick={() => setExpandedUnitId(null)} className="rounded-lg border border-line px-4 py-2 text-sm font-medium hover:bg-canvas">Cancel</button>
      </div>
    </div>
  );

  return (
    <div className="max-w-3xl">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Your material</p>
      <h1 className="mt-1 text-3xl font-semibold text-ink">Experience</h1>
      <p className="mt-2 text-sm text-muted">Keep your background, target roles and priorities up to date — this feeds every resume and cover letter JobMatchFlow generates for you.</p>

      {error && <p className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
      {loading && <p className="mt-8 text-sm text-muted">Loading…</p>}

      {!loading && (
        <>
          {/* Employment type preference — the frontmost filter: applied in the matching pipeline
              before any per-job LLM matching runs (see services/scoring_service.py Step 0). */}
          <section className="mt-8 rounded-xl border border-line bg-white p-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-ink">Job type</h2>
              {!employmentTypePreferenceEditing && (
                <button type="button" onClick={startEditEmploymentTypePreference} className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-canvas">Edit</button>
              )}
            </div>
            <p className="mt-1 text-sm text-muted">What kind of roles should JobMatchFlow match you against? Applied before anything else — the most fundamental filter.</p>

            {!employmentTypePreferenceEditing ? (
              <p className="mt-4 text-sm text-ink">
                {EMPLOYMENT_TYPE_OPTIONS.find((o) => o.value === employmentTypePreference)?.label ?? "Both"}
              </p>
            ) : (
              <>
                <div className="mt-4 space-y-2">
                  {EMPLOYMENT_TYPE_OPTIONS.map((opt) => (
                    <label key={opt.value} className="flex items-start gap-2 text-sm text-ink">
                      <input
                        type="radio"
                        className="mt-1"
                        checked={employmentTypePreferenceDraft === opt.value}
                        onChange={() => setEmploymentTypePreferenceDraft(opt.value)}
                      />
                      <span>
                        <span className="font-medium">{opt.label}</span>
                        <span className="block text-xs text-muted">{opt.hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <div className="mt-4 flex gap-2 border-t border-line pt-4">
                  <button type="button" disabled={employmentTypePreferenceBusy} onClick={() => void saveEmploymentTypePreference()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{employmentTypePreferenceBusy ? "Saving…" : "Save"}</button>
                  <button type="button" disabled={employmentTypePreferenceBusy} onClick={cancelEditEmploymentTypePreference} className="rounded-lg border border-line px-4 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">Cancel</button>
                </div>
              </>
            )}
            {employmentTypePreferenceMsg && <p className="mt-4 text-sm text-accent">{employmentTypePreferenceMsg}</p>}
          </section>

          {/* Basic info — locked/read-only until you click Edit; typing never touches the
              saved copy, only Save commits it, only Cancel discards it. */}
          <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-ink">Basic info</h2>
              {!basicInfoEditing && (
                <button type="button" onClick={startEditBasicInfo} className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-canvas">Edit</button>
              )}
            </div>
            {factsSummary && <p className="mt-1 text-xs text-muted">{factsSummary}</p>}

            {!basicInfoEditing ? (
              <div className="mt-5 space-y-4 text-sm">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">Full name</p><p className="mt-1 text-ink">{basicInfo.full_name || "—"}</p></div>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">Location</p><p className="mt-1 text-ink">{basicInfo.location || "—"}</p></div>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">Phone</p><p className="mt-1 text-ink">{basicInfo.phone || "—"}</p></div>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">Email</p><p className="mt-1 text-ink">{basicInfo.email || "—"}</p></div>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">Visa / work authorization</p><p className="mt-1 text-ink">{basicInfo.visa || "—"}</p></div>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">LinkedIn</p><p className="mt-1 text-ink">{basicInfo.linkedin.url || "—"}</p></div>
                  <div><p className="text-xs font-medium uppercase tracking-wide text-muted">GitHub</p><p className="mt-1 text-ink">{basicInfo.github.url || "—"}</p></div>
                </div>
                <div><p className="text-xs font-medium uppercase tracking-wide text-muted">Profile summary</p><p className="mt-1 whitespace-pre-wrap text-ink">{basicInfo.profile_summary || "—"}</p></div>
                <div className="border-t border-line pt-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted">Education</p>
                  {basicInfo.education.length === 0 && <p className="mt-1 text-ink">—</p>}
                  <div className="mt-2 space-y-3">
                    {basicInfo.education.map((edu, i) => (
                      <div key={i}>
                        <p className="font-medium text-ink">{edu.degree || "Untitled degree"}{edu.major ? ` · ${edu.major}` : ""}{edu.institution ? ` · ${edu.institution}` : ""}</p>
                        <p className="text-xs text-muted">{edu.date_range}{edu.date_range && edu.location ? " · " : ""}{edu.location}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="mt-5 grid gap-4 sm:grid-cols-2">
                  <label className="text-sm font-medium text-ink">Full name<input value={basicInfoDraft.full_name} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, full_name: e.target.value })} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                  <label className="text-sm font-medium text-ink">Location<input value={basicInfoDraft.location} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, location: e.target.value })} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                  <label className="text-sm font-medium text-ink">Phone<input value={basicInfoDraft.phone} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, phone: e.target.value })} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                  <label className="text-sm font-medium text-ink">Email<input value={basicInfoDraft.email} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, email: e.target.value })} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                  <label className="text-sm font-medium text-ink">Visa / work authorization<input value={basicInfoDraft.visa} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, visa: e.target.value })} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                  <label className="text-sm font-medium text-ink">LinkedIn URL<input value={basicInfoDraft.linkedin.url} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, linkedin: { ...basicInfoDraft.linkedin, url: e.target.value } })} placeholder="linkedin.com/in/…" className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                  <label className="text-sm font-medium text-ink">GitHub URL<input value={basicInfoDraft.github.url} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, github: { ...basicInfoDraft.github, url: e.target.value } })} placeholder="github.com/…" className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
                </div>
                <label className="mt-4 block text-sm font-medium text-ink">Profile summary<textarea value={basicInfoDraft.profile_summary} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, profile_summary: e.target.value })} rows={3} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>

                <div className="mt-5 border-t border-line pt-5">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-ink">Education</h3>
                    <button type="button" onClick={() => setBasicInfoDraft({ ...basicInfoDraft, education: [...basicInfoDraft.education, { degree: "", major: "", institution: "", date_range: "", location: "", bullets: "" }] })} className="text-sm font-medium text-accent hover:underline">+ Add entry</button>
                  </div>
                  <div className="mt-3 space-y-4">
                    {basicInfoDraft.education.map((edu, i) => (
                      <div key={i} className="rounded-lg border border-line p-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                          <input value={edu.degree} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.map((x, j) => j === i ? { ...x, degree: e.target.value } : x) })} placeholder="Degree" className="rounded-lg border border-line px-3 py-2 text-sm" />
                          <input value={edu.major} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.map((x, j) => j === i ? { ...x, major: e.target.value } : x) })} placeholder="Major / field of study" className="rounded-lg border border-line px-3 py-2 text-sm" />
                          <input value={edu.institution} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.map((x, j) => j === i ? { ...x, institution: e.target.value } : x) })} placeholder="Institution" className="rounded-lg border border-line px-3 py-2 text-sm" />
                          <input value={edu.date_range} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.map((x, j) => j === i ? { ...x, date_range: e.target.value } : x) })} placeholder="Dates" className="rounded-lg border border-line px-3 py-2 text-sm" />
                          <input value={edu.location} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.map((x, j) => j === i ? { ...x, location: e.target.value } : x) })} placeholder="Location" className="rounded-lg border border-line px-3 py-2 text-sm" />
                        </div>
                        <textarea value={edu.bullets} onChange={(e) => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.map((x, j) => j === i ? { ...x, bullets: e.target.value } : x) })} placeholder="Highlights, one per line (keep to 2 lines — the resume renders every line you add, and more may push the layout to a second page)" rows={2} className="mt-2 w-full rounded-lg border border-line px-3 py-2 text-sm" />
                        <button type="button" onClick={() => setBasicInfoDraft({ ...basicInfoDraft, education: basicInfoDraft.education.filter((_, j) => j !== i) })} className="mt-2 text-xs font-medium text-red-700 hover:underline">Remove</button>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="mt-5 flex gap-2 border-t border-line pt-5">
                  <button type="button" disabled={basicInfoBusy} onClick={() => void saveBasicInfo()} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{basicInfoBusy ? "Saving…" : "Save basic info"}</button>
                  <button type="button" disabled={basicInfoBusy} onClick={cancelEditBasicInfo} className="rounded-lg border border-line px-4 py-2.5 text-sm font-medium hover:bg-canvas disabled:opacity-50">Cancel</button>
                </div>
              </>
            )}
            {basicInfoMsg && <p className="mt-4 text-sm text-accent">{basicInfoMsg}</p>}
          </section>

          {/* Skills */}
          <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-ink">Skills</h2>
            <p className="mt-1 text-sm text-muted">Named technologies/tools JobMatchFlow should treat as canonical when matching you against a job's requirements.</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {facts.atoms.filter((a) => a.type === "skill").map((a) => (
                <span key={a.id} className="flex items-center gap-1.5 rounded-full bg-canvas px-3 py-1.5 text-sm text-ink">
                  {a.label}
                  <button type="button" disabled={factsBusy} onClick={() => void removeSkill(a.id)} className="text-muted hover:text-red-700" aria-label={`Remove ${a.label}`}>×</button>
                </span>
              ))}
              {facts.atoms.filter((a) => a.type === "skill").length === 0 && <p className="text-sm text-muted">No skills yet — add one below.</p>}
            </div>
            <div className="mt-4 flex gap-2">
              <input value={newSkillLabel} onChange={(e) => setNewSkillLabel(e.target.value)} placeholder="e.g. PostgreSQL" className="flex-1 rounded-lg border border-line px-3 py-2 text-sm" />
              <button type="button" disabled={factsBusy || !newSkillLabel.trim()} onClick={() => void addSkill()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{factsBusy ? "Saving…" : "Add"}</button>
            </div>
          </section>

          {/* total_years_experience feeds Step 3/4 scoring directly (build_candidate_context_plain /
              seniority-mismatch gate); without it the seniority gate can never fire. Education for
              scoring is read server-side from Basic info's education, not entered here. */}
          <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-ink">Experience level</h2>
              {!experienceLevelEditing && (
                <button type="button" onClick={startEditExperienceLevel} className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-canvas">Edit</button>
              )}
            </div>
            <p className="mt-1 text-sm text-muted">Used for job matching only. Education for matching now comes from the Education entries under Basic info — no separate copy here.</p>

            {!experienceLevelEditing ? (
              <div className="mt-4 text-sm">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">Total years of professional experience</p>
                <p className="mt-1 text-ink">{facts.total_years_experience != null ? facts.total_years_experience : "—"}</p>
              </div>
            ) : (
              <>
                <label className="mt-4 block text-sm font-medium text-ink">
                  Total years of professional experience
                  <input
                    type="number"
                    min={0}
                    step="0.5"
                    value={totalYearsInput}
                    onChange={(e) => setTotalYearsInput(e.target.value)}
                    placeholder="e.g. 3"
                    className="mt-2 w-32 rounded-lg border border-line px-3 py-2 text-sm font-normal"
                  />
                </label>
                <div className="mt-4 flex gap-2 border-t border-line pt-4">
                  <button type="button" disabled={factsBusy} onClick={() => void saveTotalYears()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{factsBusy ? "Saving…" : "Save"}</button>
                  <button type="button" disabled={factsBusy} onClick={cancelEditExperienceLevel} className="rounded-lg border border-line px-4 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">Cancel</button>
                </div>
              </>
            )}
          </section>

          {/* Job directions + scoring preferences */}
          <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-ink">Roles you want to apply for</h2>
            <p className="mt-1 text-sm text-muted">Every role added here is reused for free across all future job matching.</p>
            <div className="mt-4 space-y-2">
              {directions.map((d) => (
                <div key={d.id} className="flex items-center justify-between rounded-lg border border-line px-3 py-2">
                  <span className={`text-sm ${d.is_active ? "text-ink" : "text-muted line-through"}`}>{d.title}</span>
                  <span className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={d.is_active} disabled={directionBusy} onChange={() => void toggleDirection(d)} />Active</label>
                    <button type="button" disabled={directionBusy} onClick={() => void removeDirection(d)} className="text-xs font-medium text-red-700 hover:underline">Remove</button>
                  </span>
                </div>
              ))}
              {directions.length === 0 && <p className="text-sm text-muted">No target roles yet — add one below.</p>}
            </div>
            <div className="mt-4 flex gap-2">
              <input value={newDirectionTitle} onChange={(e) => setNewDirectionTitle(e.target.value)} placeholder="e.g. Senior Backend Engineer" className="flex-1 rounded-lg border border-line px-3 py-2 text-sm" />
              <button type="button" disabled={directionBusy || !newDirectionTitle.trim()} onClick={() => void addDirection()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{directionBusy ? "Adding…" : "Add"}</button>
            </div>

            <div className="mt-6 border-t border-line pt-5">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-ink">Scoring preferences</h3>
                {!scoringPreferencesEditing && (
                  <button type="button" onClick={startEditScoringPreferences} className="rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-canvas">Edit</button>
                )}
              </div>
              {!scoringPreferencesEditing ? (
                <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{scoringPreferences || "—"}</p>
              ) : (
                <>
                  <textarea
                    value={scoringPreferencesDraft}
                    onChange={(e) => setScoringPreferencesDraft(e.target.value.slice(0, SCORING_PREFERENCES_MAX_CHARS))}
                    rows={3}
                    placeholder="e.g. prefer remote-friendly teams, avoid on-call heavy roles"
                    className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal"
                  />
                  <p className="mt-1 text-xs text-muted">{scoringPreferencesDraft.length}/{SCORING_PREFERENCES_MAX_CHARS}</p>
                  <div className="mt-3 flex gap-2">
                    <button type="button" disabled={scoringPreferencesBusy} onClick={() => void saveScoringPreferences()} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">
                      {scoringPreferencesBusy ? "Saving…" : "Save scoring preferences"}
                    </button>
                    <button type="button" disabled={scoringPreferencesBusy} onClick={cancelEditScoringPreferences} className="rounded-lg border border-line px-4 py-2.5 text-sm font-medium hover:bg-canvas disabled:opacity-50">Cancel</button>
                  </div>
                </>
              )}
              {scoringPreferencesMsg && <p className="mt-3 text-sm text-accent">{scoringPreferencesMsg}</p>}
            </div>
          </section>

          {/* Experience units */}
          <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-ink">Experience library</h2>
              <button type="button" onClick={openNewUnit} className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent-hover">+ Add experience</button>
            </div>
            <p className="mt-1 text-sm text-muted">Priority controls which experiences JobMatchFlow reaches for first when tailoring a resume.</p>

            <div className="mt-4 space-y-3">
              {units.map((unit) => (
                <div key={unit.id} className="rounded-lg border border-line">
                  <div className="flex items-center justify-between gap-3 px-4 py-3">
                    <button type="button" onClick={() => (expandedUnitId === unit.id ? setExpandedUnitId(null) : openUnit(unit))} className="min-w-0 flex-1 text-left">
                      <p className="truncate text-sm font-semibold text-ink">{unit.title || "Untitled"}</p>
                      <p className="truncate text-xs text-muted">{unit.employer}{unit.employer && unit.raw_date_text ? " · " : ""}{unit.raw_date_text}{unit.domain ? ` · ${unit.domain}` : ""}</p>
                    </button>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {TIERS.map((t) => (
                        <button key={t.value} type="button" disabled={unitBusy} onClick={() => void setTier(unit, t.value)} className={`rounded-full px-2.5 py-1 text-xs font-medium ${unit.tier === t.value ? tierClass(t.value) : "text-muted hover:bg-canvas"}`}>{t.label}</button>
                      ))}
                      <button type="button" disabled={unitBusy} onClick={() => void removeUnit(unit)} className="ml-1 text-xs font-medium text-red-700 hover:underline">Delete</button>
                    </div>
                  </div>
                  {expandedUnitId === unit.id && (
                    <div className="border-t border-line p-4">{renderUnitForm()}</div>
                  )}
                </div>
              ))}
              {units.length === 0 && <p className="text-sm text-muted">No experiences yet — add your first one below.</p>}
              {expandedUnitId === "new" && (
                <div className="rounded-lg border border-accent/30 bg-accent-soft/20 p-4">{renderUnitForm()}</div>
              )}
            </div>
          </section>

          {/* Start job search */}
          <section className="mt-6 rounded-xl border border-accent/30 bg-accent-soft/20 p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-ink">Start your job search</h2>
            <p className="mt-1 text-sm text-muted">
              Once your basics, roles and experience look right, run this to match you against every open job
              right now — no need to wait for tonight's automatic run. Safe to click again later: it only
              processes jobs it hasn't seen for you yet.
            </p>
            <button
              type="button"
              disabled={startBusy}
              onClick={() => void startJobSearch()}
              className="mt-4 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
            >
              {startBusy ? "Matching…" : "Start matching"}
            </button>
            {startResult && (
              <p className="mt-4 text-sm text-ink">
                Scored {startResult.scoring.scored} new job(s) ({startResult.scoring.rejected} filtered out as
                off-direction, {startResult.scoring.skipped} skipped) — resume and cover letter are generated
                automatically for any that clear the bar, check the Jobs page for results.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
