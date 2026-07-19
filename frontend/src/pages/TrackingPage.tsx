import { useCallback, useEffect, useMemo, useState } from "react";
import { deleteTrackingRecord, fetchTracking, postTrackingStage, trackingDownloadUrl, type AppliedMaterialsSnapshot, type FunnelMetrics, type TrackingRow } from "../api";
import { activeUserId, readAuthSession } from "../config";
import { downloadFromApi } from "../lib/download";
import FunnelChart from "../components/FunnelChart";

const STATUS: Record<string, string> = { applied: "Applied", interview: "Interview", offer: "Offer", rejected: "Rejected" };
const FILTERS = [{ key: "applied", label: "Applied", values: ["applied"] }, { key: "interviews", label: "Interviews", values: ["interview"] }, { key: "offer", label: "Offers", values: ["offer"] }, { key: "rejected", label: "Rejected", values: ["rejected"] }];
// applied/interview 单向不可回溯，只列出"下一步"；offer/rejected 互为对方的合法下一步
// （点错/事后变化可以互改），但都不能倒退回 interview/applied。
// rejected 的合法下一步是路径相关的：直接从 applied 拒的只能纠正成 interview（没面试过，
// 不能直接给 offer）；面试之后拒的只能纠正成 offer（已经过了面试阶段，不倒退回 interview）。
// 见 services/tracking_service.py::_allowed_next_statuses，这里跟后端保持同一套判断逻辑。
const STATIC_NEXT_STEPS: Record<string, { value: "interview" | "offer" | "rejected"; label: string }[]> = {
  applied: [{ value: "interview", label: "Mark interview" }, { value: "rejected", label: "Mark rejected" }],
  interview: [{ value: "offer", label: "Mark offer" }, { value: "rejected", label: "Mark rejected" }],
  offer: [{ value: "rejected", label: "Mark rejected" }],
};

function nextStepsFor(row: TrackingRow): { value: "interview" | "offer" | "rejected"; label: string }[] {
  if (row.application_status === "rejected") {
    const wentThroughInterview = (row.status_history || []).some((h) => h.status === "interview");
    return wentThroughInterview
      ? [{ value: "offer", label: "Mark offer" }]
      : [{ value: "interview", label: "Mark interview" }];
  }
  return STATIC_NEXT_STEPS[row.application_status] || [];
}
const EMPTY: AppliedMaterialsSnapshot = { resume_source: null, master_cv_id: null, master_cv_name: null, resume_asset_id: null, cover_letter_asset_id: null };
const EMPTY_FUNNEL: FunnelMetrics = { matched: 0, applied: 0, interviewed: 0, offer: 0, rejected: 0 };

function formatDate(value: string | null) { return value ? value.replace("T", " ").slice(0, 16) : "—"; }

export default function TrackingPage() {
  const userId = activeUserId(readAuthSession());
  const [rows, setRows] = useState<TrackingRow[]>([]);
  const [funnel, setFunnel] = useState<FunnelMetrics>(EMPTY_FUNNEL);
  const [filters, setFilters] = useState<Record<string, boolean>>({ applied: false, interviews: false, offer: false, rejected: false });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => { setLoading(true); setError(null); try { const data = await fetchTracking(userId); setRows(data.rows); setFunnel(data.funnel); } catch (e) { setError(e instanceof Error ? e.message : "Unable to load applications"); } finally { setLoading(false); } }, [userId]);
  useEffect(() => { void load(); }, [load]);
  const visible = useMemo(() => { const active = FILTERS.filter((f) => filters[f.key]).flatMap((f) => f.values); return active.length ? rows.filter((row) => active.includes(row.application_status)) : rows; }, [rows, filters]);
  const updateStage = async (jobId: number, stage: "interview" | "offer" | "rejected") => { setBusy(String(jobId)); try { await postTrackingStage(userId, jobId, stage); await load(); } catch (e) { setError(e instanceof Error ? e.message : "Unable to update application"); } finally { setBusy(null); } };
  const removeTracking = async (jobId: number, title: string) => {
    if (!window.confirm(`Remove "${title}" from Applications? It will go back to the Jobs list as not-applied.`)) return;
    setBusy(String(jobId));
    try {
      await deleteTrackingRecord(userId, jobId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to remove application");
    } finally {
      setBusy(null);
    }
  };
  const download = async (jobId: number, kind: "jd_txt" | "resume_docx" | "letter_docx") => { setBusy(`${jobId}-${kind}`); try { await downloadFromApi(trackingDownloadUrl(userId, jobId, kind)); } catch (e) { setError(e instanceof Error ? e.message : "Download failed"); } finally { setBusy(null); } };

  const funnelStages = [
    { label: "Matched", value: funnel.matched, color: "#4f46e5" },
    { label: "Applied", value: funnel.applied, color: "#0ea5e9" },
    { label: "Interviewed", value: funnel.interviewed, color: "#f59e0b" },
    { label: "Offer", value: funnel.offer, color: "#10b981" },
  ];

  return <div>
    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Application workspace</p><h1 className="mt-1 text-3xl font-semibold text-ink">Applications</h1><p className="mt-2 text-sm text-muted">Keep every application, material snapshot and next step in one place.</p>

    <div className="mt-6 grid gap-4 rounded-xl border border-line bg-white p-5 shadow-sm sm:grid-cols-[1fr_180px]">
      <div>
        <p className="text-sm font-semibold text-ink">Your funnel so far</p>
        <div className="mt-3">
          <FunnelChart stages={funnelStages} />
        </div>
      </div>
      <div className="flex flex-col justify-center rounded-xl bg-stage-rejected/10 p-4 text-center sm:mt-9">
        <p className="text-3xl font-bold text-stage-rejected">{funnel.rejected}</p>
        <p className="mt-1 text-xs font-medium text-stage-rejected">Rejected</p>
      </div>
    </div>

    <div className="mt-8 flex flex-wrap items-center gap-3"><span className="text-sm font-medium text-ink">Filter</span>{FILTERS.map((f) => <label key={f.key} className="flex items-center gap-2 text-sm text-muted"><input type="checkbox" checked={filters[f.key]} onChange={(e) => setFilters({ ...filters, [f.key]: e.target.checked })} />{f.label}</label>)}<button type="button" onClick={() => void load()} className="rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium hover:bg-canvas">Refresh</button></div>
    {error && <p className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}{loading && <p className="mt-8 text-sm text-muted">Loading applications…</p>}{!loading && !error && !rows.length && <p className="mt-8 text-sm text-muted">No applications yet. Start from Jobs.</p>}
    <div className="mt-8 space-y-5">{visible.map((row) => { const materials = row.applied_materials ?? EMPTY; return <article key={row.tracking_id} className="rounded-xl border border-line bg-white p-5 shadow-sm"><div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div><p className="text-xs text-muted">{row.company} · {formatDate(row.applied_at)}</p><h2 className="mt-1 text-lg font-semibold text-ink">{row.title}</h2><p className="mt-1 text-sm text-muted">Last updated {formatDate(row.updated_at)}</p></div><div className="flex items-center gap-2"><span className="rounded-full bg-canvas px-3 py-1.5 text-sm font-medium text-ink">{STATUS[row.application_status] || row.application_status}</span>{nextStepsFor(row).map((step) => <button key={step.value} type="button" disabled={busy !== null} onClick={() => void updateStage(row.job_id, step.value)} className="rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">{step.label}</button>)}<button type="button" disabled={busy !== null} onClick={() => void removeTracking(row.job_id, row.title)} className="rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50">Remove</button></div></div>
      <div className="mt-5 grid gap-4 border-t border-line pt-5 md:grid-cols-2"><div><h3 className="text-sm font-semibold text-ink">Application materials</h3><div className="mt-2 space-y-1 text-sm text-muted"><p>Resume: {materials.resume_source === "library" && materials.master_cv_id != null ? materials.master_cv_name || `Resume ${materials.master_cv_id}` : materials.resume_source === "tailored" ? "Tailored snapshot" : "Not recorded"}</p><p>Cover letter: {materials.cover_letter_asset_id != null ? "Snapshot available" : "Not recorded"}</p></div></div><div><h3 className="text-sm font-semibold text-ink">Quick downloads</h3><div className="mt-2 flex flex-wrap gap-2"><button type="button" disabled={busy !== null} onClick={() => void download(row.job_id, "resume_docx")} className="rounded-lg border border-line px-2.5 py-1.5 text-xs hover:bg-canvas">Resume</button><button type="button" disabled={busy !== null} onClick={() => void download(row.job_id, "letter_docx")} className="rounded-lg border border-line px-2.5 py-1.5 text-xs hover:bg-canvas">Cover letter</button><button type="button" disabled={busy !== null} onClick={() => void download(row.job_id, "jd_txt")} className="rounded-lg border border-line px-2.5 py-1.5 text-xs hover:bg-canvas">Job description</button>{row.url && <a href={row.url} target="_blank" rel="noreferrer" className="rounded-lg border border-line px-2.5 py-1.5 text-xs hover:bg-canvas">Open original</a>}</div></div></div>
      <details className="mt-5 border-t border-line pt-4"><summary className="cursor-pointer text-sm font-semibold text-ink">Interview notes</summary><div className="mt-3 space-y-3 text-sm leading-relaxed text-muted"><p><strong className="text-ink">Job description:</strong> {row.jd_snapshot_text || "No snapshot available."}</p>{row.score_snapshot?.reason_summary && <p><strong className="text-ink">Match summary:</strong> {row.score_snapshot.reason_summary}</p>}<p><strong className="text-ink">Timeline:</strong> {(row.status_history || []).map((item) => `${STATUS[item.status] || item.status} ${item.at.slice(0, 10)}`).join(" → ") || "No updates yet."}</p></div></details></article>; })}</div>
  </div>;
}
