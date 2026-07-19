import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  assetExportFileUrl,
  fetchExperienceUnits,
  fetchJobMaterials,
  postGenerateCoverLetter,
  postGenerateResume,
  postPreviewThumbnail,
  putAssetContent,
  putResumeSelection,
  type ExperienceUnit,
  type MaterialAsset,
} from "../api";
import { activeUserId, readAuthSession } from "../config";
import { postDownloadBlob } from "../lib/download";

type ResumeExperienceEntry = { _unit_id: number; title: string; company: string; location: string; date_range: string; bullets: string[] };
type SkillCategory = { label: string; skills: string[] };

const TIER_ORDER = ["flagship", "solid", "filler"];
const TIER_LABEL: Record<string, string> = { flagship: "Flagship", solid: "Solid", filler: "Filler" };

export default function MaterialsEditorPage() {
  const { jobId: jobIdParam } = useParams();
  const jobId = Number(jobIdParam);
  const userId = activeUserId(readAuthSession());

  const [tab, setTab] = useState<"resume" | "cover_letter">("resume");
  const [job, setJob] = useState<{ id: number; title: string; company: string } | null>(null);
  const [resumeAsset, setResumeAsset] = useState<MaterialAsset | null>(null);
  const [letterAsset, setLetterAsset] = useState<MaterialAsset | null>(null);
  const [units, setUnits] = useState<ExperienceUnit[]>([]);

  const [includedOrder, setIncludedOrder] = useState<number[]>([]);
  const [entryByUnitId, setEntryByUnitId] = useState<Record<number, ResumeExperienceEntry>>({});
  const [skillCategories, setSkillCategories] = useState<SkillCategory[]>([]);
  const [newSkillByCategory, setNewSkillByCategory] = useState<Record<number, string>>({});
  const [languages, setLanguages] = useState<string[]>([]);
  const [newLanguage, setNewLanguage] = useState("");
  const [letterText, setLetterText] = useState("");
  const [greeting, setGreeting] = useState("");
  const [closing, setClosing] = useState("");
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [selectionDirty, setSelectionDirty] = useState(false);

  // 缩略图预览：草稿一变（经历/skills）就防抖发一次预览请求，不用等 Save 才看到排版效果
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [previewPages, setPreviewPages] = useState<number | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewUnavailable, setPreviewUnavailable] = useState(false);
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 求职信预览跟简历预览是两条独立的防抖/状态，互不影响（切 tab 时各自保留上次结果）。
  const [letterPreviewImage, setLetterPreviewImage] = useState<string | null>(null);
  const [letterPreviewPages, setLetterPreviewPages] = useState<number | null>(null);
  const [letterPreviewLoading, setLetterPreviewLoading] = useState(false);
  const [letterPreviewUnavailable, setLetterPreviewUnavailable] = useState(false);
  const letterPreviewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const applyResumeAsset = useCallback((asset: MaterialAsset | null) => {
    setResumeAsset(asset);
    const content = (asset?.content ?? {}) as Record<string, unknown>;
    const experience = (content.experience as ResumeExperienceEntry[] | undefined) ?? [];
    const byId: Record<number, ResumeExperienceEntry> = {};
    const order: number[] = [];
    for (const entry of experience) {
      if (entry._unit_id == null) continue;
      byId[entry._unit_id] = entry;
      order.push(entry._unit_id);
    }
    setEntryByUnitId(byId);
    setIncludedOrder(order);
    const categories = content.skill_categories as SkillCategory[] | undefined;
    if (categories && categories.length) {
      setSkillCategories(categories.map((c) => ({ label: c.label, skills: [...(c.skills || [])] })));
    } else {
      // 没有分类结果（比如旧数据，或者上次是走扁平编辑退化的）——用一个不分类的
      // "Skills" 分组兜底展示，用户还是能照常增删，下次真正生成/调整选材时会重新分类。
      const flat = (content.skills as string[] | undefined) ?? [];
      setSkillCategories(flat.length ? [{ label: "Skills", skills: [...flat] }] : []);
    }
    setNewSkillByCategory({});
    setLanguages([...((content.languages as string[] | undefined) ?? [])]);
    setNewLanguage("");
    setSelectionDirty(false);
  }, []);

  const applyLetterAsset = useCallback((asset: MaterialAsset | null) => {
    setLetterAsset(asset);
    const content = (asset?.content ?? {}) as Record<string, unknown>;
    const paragraphs = (content.paragraphs as string[] | undefined) ?? [];
    setLetterText(paragraphs.join("\n\n"));
    setGreeting((content.greeting as string | undefined) ?? "Dear Hiring Team,");
    setClosing((content.closing as string | undefined) ?? "Sincerely,");
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [materials, unitRows] = await Promise.all([
        fetchJobMaterials(userId, jobId),
        fetchExperienceUnits(userId),
      ]);
      setJob(materials.job);
      applyResumeAsset(materials.resume);
      applyLetterAsset(materials.cover_letter);
      setUnits(unitRows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load materials");
    } finally {
      setLoading(false);
    }
  }, [userId, jobId, applyResumeAsset, applyLetterAsset]);

  useEffect(() => { void load(); }, [load]);

  // 草稿一变就防抖 800ms 发一次预览请求——不用等 Save 才看到排版效果，也不会每次按键都
  // 触发一次 LibreOffice 转换。只看内容轮廓，不追求实时到每个字符。
  useEffect(() => {
    if (!resumeAsset) return;
    if (previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => {
      const experience = includedOrder.map((id) => entryByUnitId[id]).filter(Boolean);
      const skills = skillCategories.flatMap((c) => c.skills);
      setPreviewLoading(true);
      setPreviewUnavailable(false);
      postPreviewThumbnail(userId, resumeAsset.id, { ...resumeAsset.content, experience, skills, skill_categories: skillCategories, languages })
        .then((r) => {
          setPreviewPages(r.pages);
          if (r.thumbnail_png_base64) {
            setPreviewImage(`data:image/png;base64,${r.thumbnail_png_base64}`);
          } else {
            setPreviewImage(null);
            setPreviewUnavailable(true);
          }
        })
        .catch(() => setPreviewUnavailable(true))
        .finally(() => setPreviewLoading(false));
    }, 800);
    return () => { if (previewTimer.current) clearTimeout(previewTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeAsset, includedOrder, entryByUnitId, skillCategories, languages, userId]);

  useEffect(() => {
    if (!letterAsset) return;
    if (letterPreviewTimer.current) clearTimeout(letterPreviewTimer.current);
    letterPreviewTimer.current = setTimeout(() => {
      const paragraphs = letterText.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
      setLetterPreviewLoading(true);
      setLetterPreviewUnavailable(false);
      postPreviewThumbnail(userId, letterAsset.id, { ...letterAsset.content, paragraphs, greeting, closing })
        .then((r) => {
          setLetterPreviewPages(r.pages);
          if (r.thumbnail_png_base64) {
            setLetterPreviewImage(`data:image/png;base64,${r.thumbnail_png_base64}`);
          } else {
            setLetterPreviewImage(null);
            setLetterPreviewUnavailable(true);
          }
        })
        .catch(() => setLetterPreviewUnavailable(true))
        .finally(() => setLetterPreviewLoading(false));
    }, 800);
    return () => { if (letterPreviewTimer.current) clearTimeout(letterPreviewTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [letterAsset, letterText, greeting, closing, userId]);

  const unitById = useMemo(() => Object.fromEntries(units.map((u) => [u.id, u])), [units]);
  const availableUnits = useMemo(() => units.filter((u) => !includedOrder.includes(u.id)), [units, includedOrder]);

  const moveIntoIncluded = (unitId: number, beforeId: number | null) => {
    setIncludedOrder((prev) => {
      const withoutMoved = prev.filter((id) => id !== unitId);
      if (beforeId == null) return [...withoutMoved, unitId];
      const idx = withoutMoved.indexOf(beforeId);
      if (idx === -1) return [...withoutMoved, unitId];
      return [...withoutMoved.slice(0, idx), unitId, ...withoutMoved.slice(idx)];
    });
    setSelectionDirty(true);
  };

  const removeFromIncluded = (unitId: number) => {
    setIncludedOrder((prev) => prev.filter((id) => id !== unitId));
    setSelectionDirty(true);
  };

  const generateResume = async () => {
    setBusy("generate-resume");
    setError(null);
    try {
      const asset = await postGenerateResume(userId, jobId);
      applyResumeAsset(asset);
      setMessage("Resume generated.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Resume generation failed");
    } finally {
      setBusy(null);
    }
  };

  const applySelection = async () => {
    if (includedOrder.length === 0) { setError("Include at least one experience."); return; }
    setBusy("apply-selection");
    setError(null);
    try {
      const asset = await putResumeSelection(userId, jobId, includedOrder);
      applyResumeAsset(asset);
      setMessage("Experience selection applied — bullets were rewritten for the new set.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to apply selection change");
    } finally {
      setBusy(null);
    }
  };

  const saveResumeText = async () => {
    if (!resumeAsset) return;
    setBusy("save-resume");
    setError(null);
    try {
      const experience = includedOrder.map((id) => entryByUnitId[id]).filter(Boolean);
      const skills = skillCategories.flatMap((c) => c.skills);
      await putAssetContent(userId, resumeAsset.id, { ...resumeAsset.content, experience, skills, skill_categories: skillCategories, languages });
      setMessage("Text edits saved and resume re-rendered.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(null);
    }
  };

  const addSkillToCategory = (categoryIndex: number) => {
    const value = (newSkillByCategory[categoryIndex] ?? "").trim();
    if (!value) return;
    setSkillCategories((prev) =>
      prev.map((c, i) => (i === categoryIndex && !c.skills.includes(value) ? { ...c, skills: [...c.skills, value] } : c))
    );
    setNewSkillByCategory((prev) => ({ ...prev, [categoryIndex]: "" }));
  };

  const removeSkillFromCategory = (categoryIndex: number, skill: string) => {
    setSkillCategories((prev) =>
      prev.map((c, i) => (i === categoryIndex ? { ...c, skills: c.skills.filter((s) => s !== skill) } : c))
    );
  };

  const renameCategory = (categoryIndex: number, label: string) => {
    setSkillCategories((prev) => prev.map((c, i) => (i === categoryIndex ? { ...c, label } : c)));
  };

  const addCategory = () => {
    setSkillCategories((prev) => [...prev, { label: "New group", skills: [] }]);
  };

  const removeCategory = (categoryIndex: number) => {
    setSkillCategories((prev) => prev.filter((_, i) => i !== categoryIndex));
    setNewSkillByCategory((prev) => {
      const next = { ...prev };
      delete next[categoryIndex];
      return next;
    });
  };

  // Languages 是独立于 skill_categories 的一行（渲染成 "Languages: English (fluent), ..."），
  // 逻辑比技能分组简单——没有分组概念，就是一个可增删的标签列表。
  const addLanguage = () => {
    const value = newLanguage.trim();
    if (!value || languages.includes(value)) return;
    setLanguages((prev) => [...prev, value]);
    setNewLanguage("");
  };

  const removeLanguage = (lang: string) => {
    setLanguages((prev) => prev.filter((l) => l !== lang));
  };

  const downloadResume = async () => {
    if (!resumeAsset) return;
    setBusy("download-resume");
    try {
      await postDownloadBlob(assetExportFileUrl(userId, resumeAsset.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  };

  const generateLetter = async () => {
    setBusy("generate-letter");
    setError(null);
    try {
      const asset = await postGenerateCoverLetter(userId, jobId);
      applyLetterAsset(asset);
      setMessage("Cover letter generated.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cover letter generation failed");
    } finally {
      setBusy(null);
    }
  };

  const saveLetterText = async () => {
    if (!letterAsset) return;
    setBusy("save-letter");
    setError(null);
    try {
      const paragraphs = letterText.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
      await putAssetContent(userId, letterAsset.id, { ...letterAsset.content, paragraphs, greeting, closing });
      setMessage("Cover letter saved and re-rendered.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(null);
    }
  };

  const downloadLetter = async () => {
    if (!letterAsset) return;
    setBusy("download-letter");
    try {
      await postDownloadBlob(assetExportFileUrl(userId, letterAsset.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  };

  const updateEntry = (unitId: number, patch: Partial<ResumeExperienceEntry>) => {
    setEntryByUnitId((prev) => ({ ...prev, [unitId]: { ...prev[unitId], ...patch } }));
  };

  const updateBullet = (unitId: number, index: number, text: string) => {
    const entry = entryByUnitId[unitId];
    if (!entry) return;
    const bullets = entry.bullets.map((b, i) => (i === index ? text : b));
    updateEntry(unitId, { bullets });
  };

  const addBullet = (unitId: number) => {
    const entry = entryByUnitId[unitId];
    if (!entry) return;
    updateEntry(unitId, { bullets: [...entry.bullets, ""] });
  };

  const removeBullet = (unitId: number, index: number) => {
    const entry = entryByUnitId[unitId];
    if (!entry) return;
    updateEntry(unitId, { bullets: entry.bullets.filter((_, i) => i !== index) });
  };

  const content = resumeAsset?.content ?? {};

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-line bg-white px-6 py-4">
        <div className="mx-auto flex max-w-4xl items-center justify-between">
          <div>
            <p className="text-xs text-muted">{job ? job.company : "Loading…"}</p>
            <h1 className="text-lg font-semibold text-ink">{job ? job.title : "Materials editor"}</h1>
          </div>
          <p className="text-xs text-muted">You can close this tab when you're done — changes save on the buttons below.</p>
        </div>
      </header>

      <div className="mx-auto max-w-4xl px-6 py-8">
        {error && <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
        {message && <p className="mb-4 rounded-lg border border-accent/20 bg-accent-soft/40 px-4 py-3 text-sm text-accent">{message}</p>}
        {loading && <p className="text-sm text-muted">Loading…</p>}

        {!loading && (
          <>
            <div className="flex gap-2 border-b border-line">
              <button type="button" onClick={() => setTab("resume")} className={`px-4 py-2 text-sm font-medium ${tab === "resume" ? "border-b-2 border-accent text-accent" : "text-muted"}`}>Resume</button>
              <button type="button" onClick={() => setTab("cover_letter")} className={`px-4 py-2 text-sm font-medium ${tab === "cover_letter" ? "border-b-2 border-accent text-accent" : "text-muted"}`}>Cover letter</button>
            </div>

            {tab === "resume" && (
              !resumeAsset ? (
                <div className="mt-8 rounded-xl border border-line bg-white p-6 text-center shadow-sm">
                  <p className="text-sm text-muted">No resume generated yet for this role.</p>
                  <button type="button" disabled={busy !== null} onClick={() => void generateResume()} className="mt-4 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{busy === "generate-resume" ? "Generating…" : "Generate resume"}</button>
                </div>
              ) : (
                <div className="mt-6 space-y-6">
                  <section className="rounded-xl border border-line bg-white p-5 shadow-sm">
                    <h2 className="text-sm font-semibold text-ink">Basic info</h2>
                    <p className="mt-1 text-sm text-muted">{String(content.full_name ?? "")} · {String(content.location ?? "")} · {String(content.email ?? "")}</p>
                    <p className="mt-1 text-xs text-muted">Edit contact details and education on the Experience page.</p>
                  </section>

                  <div className="grid gap-6 sm:grid-cols-[1fr_260px]">
                    <section className="rounded-xl border border-line bg-white p-5 shadow-sm">
                      <div className="flex items-center justify-between">
                        <h2 className="text-sm font-semibold text-ink">Included experiences</h2>
                        {selectionDirty && <span className="text-xs font-medium text-amber-700">Selection changed — apply to rewrite bullets</span>}
                      </div>
                      <div
                        className="mt-3 space-y-3"
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => { if (draggingId != null) moveIntoIncluded(draggingId, null); setDraggingId(null); }}
                      >
                        {includedOrder.map((unitId) => {
                          const entry = entryByUnitId[unitId];
                          const unit = unitById[unitId];
                          return (
                            <div
                              key={unitId}
                              draggable
                              onDragStart={() => setDraggingId(unitId)}
                              onDragOver={(e) => e.preventDefault()}
                              onDrop={(e) => { e.stopPropagation(); if (draggingId != null) moveIntoIncluded(draggingId, unitId); setDraggingId(null); }}
                              className="cursor-move rounded-lg border border-line p-3"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                  {entry ? (
                                    <>
                                      <input value={entry.title} onChange={(e) => updateEntry(unitId, { title: e.target.value })} className="w-full rounded border-0 bg-transparent text-sm font-semibold text-ink focus:bg-canvas focus:outline-none" />
                                      <div className="mt-0.5 flex gap-2 text-xs text-muted">
                                        <input value={entry.company} onChange={(e) => updateEntry(unitId, { company: e.target.value })} className="rounded border-0 bg-transparent focus:bg-canvas focus:outline-none" />
                                        <span>·</span>
                                        <input value={entry.date_range} onChange={(e) => updateEntry(unitId, { date_range: e.target.value })} className="rounded border-0 bg-transparent focus:bg-canvas focus:outline-none" />
                                      </div>
                                    </>
                                  ) : (
                                    <p className="text-sm text-muted">{unit?.title || "Untitled"} — not yet generated; apply the selection to write tailored bullets.</p>
                                  )}
                                </div>
                                <button type="button" onClick={() => removeFromIncluded(unitId)} className="shrink-0 text-xs font-medium text-red-700 hover:underline">Remove</button>
                              </div>
                              {entry && (
                                <ul className="mt-3 space-y-2">
                                  {entry.bullets.map((bullet, i) => (
                                    <li key={i} className="flex items-start gap-2">
                                      <textarea value={bullet} onChange={(e) => updateBullet(unitId, i, e.target.value)} rows={2} className="flex-1 rounded-lg border border-line px-2 py-1.5 text-sm" />
                                      <button type="button" onClick={() => removeBullet(unitId, i)} className="mt-1 text-xs font-medium text-red-700 hover:underline">✕</button>
                                    </li>
                                  ))}
                                  <button type="button" onClick={() => addBullet(unitId)} className="text-xs font-medium text-accent hover:underline">+ Add bullet</button>
                                </ul>
                              )}
                            </div>
                          );
                        })}
                        {includedOrder.length === 0 && <p className="text-sm text-muted">Drag experiences in from the right to include them.</p>}
                      </div>

                      <div className="mt-5 border-t border-line pt-4">
                        <div className="flex items-center justify-between">
                          <p className="text-sm font-medium text-ink">Skills</p>
                          <button type="button" onClick={addCategory} className="text-xs font-medium text-accent hover:underline">+ Add group</button>
                        </div>
                        <div className="mt-3 space-y-4">
                          {skillCategories.map((cat, ci) => (
                            <div key={ci} className="rounded-lg border border-line p-2.5">
                              <div className="flex items-center gap-2">
                                <input
                                  value={cat.label}
                                  onChange={(e) => renameCategory(ci, e.target.value)}
                                  placeholder="Group name"
                                  className="flex-1 rounded border-0 bg-transparent text-xs font-semibold uppercase tracking-wide text-muted focus:bg-canvas focus:outline-none"
                                />
                                <button type="button" onClick={() => removeCategory(ci)} className="shrink-0 text-xs font-medium text-red-700 hover:underline">Remove group</button>
                              </div>
                              <div className="mt-1.5 flex flex-wrap gap-1.5">
                                {cat.skills.map((s) => (
                                  <span key={s} className="flex items-center gap-1 rounded-full bg-canvas px-2.5 py-1 text-xs text-ink">
                                    {s}
                                    <button type="button" onClick={() => removeSkillFromCategory(ci, s)} className="text-muted hover:text-red-700" aria-label={`Remove ${s}`}>×</button>
                                  </span>
                                ))}
                                {cat.skills.length === 0 && <span className="text-xs text-muted">No skills in this group yet.</span>}
                              </div>
                              <div className="mt-1.5 flex gap-1.5">
                                <input
                                  value={newSkillByCategory[ci] ?? ""}
                                  onChange={(e) => setNewSkillByCategory({ ...newSkillByCategory, [ci]: e.target.value })}
                                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSkillToCategory(ci); } }}
                                  placeholder={`Add to ${cat.label || "this group"}`}
                                  className="flex-1 rounded-lg border border-line px-2 py-1 text-xs"
                                />
                                <button type="button" onClick={() => addSkillToCategory(ci)} className="rounded-lg border border-line px-2.5 py-1 text-xs font-medium hover:bg-canvas">Add</button>
                              </div>
                            </div>
                          ))}
                          {skillCategories.length === 0 && <p className="text-sm text-muted">No skills yet — add a group above.</p>}
                        </div>
                      </div>

                      <div className="mt-5 border-t border-line pt-4">
                        <p className="text-sm font-medium text-ink">Languages</p>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {languages.map((l) => (
                            <span key={l} className="flex items-center gap-1 rounded-full bg-canvas px-2.5 py-1 text-xs text-ink">
                              {l}
                              <button type="button" onClick={() => removeLanguage(l)} className="text-muted hover:text-red-700" aria-label={`Remove ${l}`}>×</button>
                            </span>
                          ))}
                          {languages.length === 0 && <span className="text-xs text-muted">No languages yet.</span>}
                        </div>
                        <div className="mt-1.5 flex gap-1.5">
                          <input
                            value={newLanguage}
                            onChange={(e) => setNewLanguage(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addLanguage(); } }}
                            placeholder="e.g. German (B2)"
                            className="flex-1 rounded-lg border border-line px-2 py-1 text-xs"
                          />
                          <button type="button" onClick={addLanguage} className="rounded-lg border border-line px-2.5 py-1 text-xs font-medium hover:bg-canvas">Add</button>
                        </div>
                      </div>

                      <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
                        <button type="button" disabled={busy !== null || !selectionDirty} onClick={() => void applySelection()} className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{busy === "apply-selection" ? "Rewriting…" : "Apply selection change"}</button>
                        <button type="button" disabled={busy !== null} onClick={() => void saveResumeText()} className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">{busy === "save-resume" ? "Saving…" : "Save text edits"}</button>
                        <button type="button" disabled={busy !== null} onClick={() => void downloadResume()} className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">Download .docx</button>
                      </div>
                    </section>

                    <div className="flex flex-col gap-6">
                      <section
                        className="h-fit rounded-xl border border-line bg-white p-4 shadow-sm"
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => { if (draggingId != null) removeFromIncluded(draggingId); setDraggingId(null); }}
                      >
                        <h2 className="text-sm font-semibold text-ink">Available experiences</h2>
                        <p className="mt-1 text-xs text-muted">Drag into the resume to include.</p>
                        {TIER_ORDER.map((tier) => {
                          const tierUnits = availableUnits.filter((u) => (u.tier ?? "solid") === tier);
                          if (!tierUnits.length) return null;
                          return (
                            <div key={tier} className="mt-3">
                              <p className="text-xs font-semibold uppercase tracking-wide text-muted">{TIER_LABEL[tier]}</p>
                              <div className="mt-1 space-y-1.5">
                                {tierUnits.map((u) => (
                                  <div key={u.id} draggable onDragStart={() => setDraggingId(u.id)} className="cursor-move rounded-lg border border-line px-2.5 py-2 text-sm text-ink hover:bg-canvas">
                                    {u.title || "Untitled"}
                                    <button type="button" onClick={() => moveIntoIncluded(u.id, null)} className="ml-2 text-xs font-medium text-accent hover:underline">+ include</button>
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                        {availableUnits.length === 0 && <p className="mt-3 text-sm text-muted">All experiences are included.</p>}
                      </section>

                      <section className="h-fit rounded-xl border border-line bg-white p-4 shadow-sm">
                        <div className="flex items-center justify-between">
                          <h2 className="text-sm font-semibold text-ink">Preview</h2>
                          {previewPages != null && (
                            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${previewPages > 1 ? "bg-red-100 text-red-800" : "bg-emerald-100 text-emerald-800"}`}>
                              {previewPages} page{previewPages === 1 ? "" : "s"}
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-xs text-muted">Rough layout only — updates automatically as you edit, not meant to be readable.</p>
                        <div className="mt-3 flex items-center justify-center rounded-lg border border-line bg-canvas p-2" style={{ minHeight: 200 }}>
                          {previewImage ? (
                            <img src={previewImage} alt="Resume layout preview" className="max-w-full rounded shadow-sm" />
                          ) : previewLoading ? (
                            <p className="text-xs text-muted">Rendering preview…</p>
                          ) : previewUnavailable ? (
                            <p className="px-2 text-center text-xs text-muted">Preview unavailable on this server (needs LibreOffice + PyMuPDF).</p>
                          ) : (
                            <p className="text-xs text-muted">No preview yet.</p>
                          )}
                        </div>
                      </section>
                    </div>
                  </div>
                </div>
              )
            )}

            {tab === "cover_letter" && (
              !letterAsset ? (
                <div className="mt-8 rounded-xl border border-line bg-white p-6 text-center shadow-sm">
                  <p className="text-sm text-muted">{resumeAsset ? "No cover letter generated yet for this role." : "Generate a resume first — cover letters reuse its selected experiences."}</p>
                  <button type="button" disabled={busy !== null || !resumeAsset} onClick={() => void generateLetter()} className="mt-4 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{busy === "generate-letter" ? "Generating…" : "Generate cover letter"}</button>
                </div>
              ) : (
                <div className="mt-6 grid gap-6 sm:grid-cols-[1fr_260px]">
                  <section className="rounded-xl border border-line bg-white p-5 shadow-sm">
                    <p className="text-sm text-muted">{String(letterAsset.content.company ?? "")} · {String(letterAsset.content.job_title ?? "")}</p>
                    <div className="mt-4 space-y-3">
                      <input
                        value={greeting}
                        onChange={(e) => setGreeting(e.target.value)}
                        placeholder="Dear Hiring Team,"
                        className="w-full rounded-lg border border-line px-3 py-2 text-sm font-medium"
                      />
                      <textarea
                        value={letterText}
                        onChange={(e) => setLetterText(e.target.value)}
                        rows={16}
                        placeholder="Write your cover letter here. Separate paragraphs with a blank line."
                        className="w-full rounded-lg border border-line px-3 py-2 text-sm leading-relaxed"
                      />
                      <input
                        value={closing}
                        onChange={(e) => setClosing(e.target.value)}
                        placeholder="Sincerely,"
                        className="w-full rounded-lg border border-line px-3 py-2 text-sm font-medium"
                      />
                    </div>
                    <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
                      <button type="button" disabled={busy !== null} onClick={() => void saveLetterText()} className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{busy === "save-letter" ? "Saving…" : "Save"}</button>
                      <button type="button" disabled={busy !== null} onClick={() => void generateLetter()} className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">{busy === "generate-letter" ? "Regenerating…" : "Regenerate"}</button>
                      <button type="button" disabled={busy !== null} onClick={() => void downloadLetter()} className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">Download .docx</button>
                    </div>
                  </section>

                  <section className="h-fit rounded-xl border border-line bg-white p-4 shadow-sm">
                    <div className="flex items-center justify-between">
                      <h2 className="text-sm font-semibold text-ink">Preview</h2>
                      {letterPreviewPages != null && (
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${letterPreviewPages > 1 ? "bg-red-100 text-red-800" : "bg-emerald-100 text-emerald-800"}`}>
                          {letterPreviewPages} page{letterPreviewPages === 1 ? "" : "s"}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted">Rough layout only — updates automatically as you edit, not meant to be readable.</p>
                    <div className="mt-3 flex items-center justify-center rounded-lg border border-line bg-canvas p-2" style={{ minHeight: 200 }}>
                      {letterPreviewImage ? (
                        <img src={letterPreviewImage} alt="Cover letter layout preview" className="max-w-full rounded shadow-sm" />
                      ) : letterPreviewLoading ? (
                        <p className="text-xs text-muted">Rendering preview…</p>
                      ) : letterPreviewUnavailable ? (
                        <p className="px-2 text-center text-xs text-muted">Preview unavailable on this server (needs LibreOffice + PyMuPDF).</p>
                      ) : (
                        <p className="text-xs text-muted">No preview yet.</p>
                      )}
                    </div>
                  </section>
                </div>
              )
            )}
          </>
        )}
      </div>
    </div>
  );
}
