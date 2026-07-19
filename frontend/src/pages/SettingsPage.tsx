import { useEffect, useState } from "react";
import {
  deleteAccount,
  downloadUserData,
  fetchAccount,
  fetchGenerationModel,
  fetchPublicSettings,
  fetchResumeSlotsBundle,
  fetchResumeTailoringMode,
  fetchSearchProfile,
  postProfilePhoto,
  postResumeSlotUploadByNumber,
  profilePhotoUrl,
  putAccount,
  putGenerationModel,
  putResumeTailoringMode,
  putSearchProfile,
  type GenerationModel,
  type PublicSettings,
  type ResumeSlotsBundle,
  type ResumeTailoringMode,
} from "../api";
import { activeUserId, readAuthSession } from "../config";

type NotificationState = {
  dailyDigest: boolean;
  highMatchAlerts: boolean;
  applicationReminders: boolean;
};

const NOTIFICATION_KEY = "jobmatchflow_notifications";
const LANGUAGE_KEY = "jobmatchflow_language";
const SELECTABLE_COUNTRIES = new Set(["Switzerland", "Luxembourg"]);

function readNotifications(): NotificationState {
  try {
    const value = JSON.parse(localStorage.getItem(NOTIFICATION_KEY) || "null");
    if (value) return value as NotificationState;
  } catch { /* use defaults */ }
  return { dailyDigest: true, highMatchAlerts: true, applicationReminders: true };
}

export default function SettingsPage() {
  const userId = activeUserId(readAuthSession());
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [notifications, setNotifications] = useState<NotificationState>(readNotifications);
  const [language, setLanguage] = useState(() => localStorage.getItem(LANGUAGE_KEY) || "English");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoVersion, setPhotoVersion] = useState(0);
  const [hasPhoto, setHasPhoto] = useState(true);
  const [resumeSlots, setResumeSlots] = useState<ResumeSlotsBundle | null>(null);
  const [resumeFile1, setResumeFile1] = useState<File | null>(null);
  const [resumeFile2, setResumeFile2] = useState<File | null>(null);
  const [jobMarkets, setJobMarkets] = useState<PublicSettings["job_markets"]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [generationModelOptions, setGenerationModelOptions] = useState<PublicSettings["generation_model_options"]>([]);
  const [generationModel, setGenerationModel] = useState<GenerationModel>("gemini");
  const [tailoringModeOptions, setTailoringModeOptions] = useState<PublicSettings["resume_tailoring_mode_options"]>([]);
  const [tailoringMode, setTailoringMode] = useState<ResumeTailoringMode>("honest");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const loadResumeSlots = () =>
    fetchResumeSlotsBundle(userId)
      .then(setResumeSlots)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Unable to load resumes"));

  useEffect(() => {
    void fetchAccount(userId).then((account) => {
      setName(account.name);
      setEmail(account.email);
    }).catch((e: unknown) => setError(e instanceof Error ? e.message : "Unable to load account"));
    void Promise.all([fetchPublicSettings(), fetchSearchProfile(userId)])
      .then(([settings, searchProfile]) => {
        setJobMarkets(settings.job_markets);
        setGenerationModelOptions(settings.generation_model_options);
        setTailoringModeOptions(settings.resume_tailoring_mode_options);
        setCountries(searchProfile.countries.filter((c) => SELECTABLE_COUNTRIES.has(c)));
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Unable to load job matching preferences"));
    void fetchGenerationModel(userId)
      .then((res) => setGenerationModel(res.generation_model))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Unable to load generation model preference"));
    void fetchResumeTailoringMode(userId)
      .then((res) => setTailoringMode(res.resume_tailoring_mode))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Unable to load resume tailoring mode"));
    void loadResumeSlots();
  }, [userId]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setMessage(null); setError(null);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong"); }
    finally { setBusy(null); }
  };

  const saveNotifications = (next: NotificationState) => {
    setNotifications(next);
    localStorage.setItem(NOTIFICATION_KEY, JSON.stringify(next));
  };

  const toggleCountry = (code: string) => {
    if (!SELECTABLE_COUNTRIES.has(code)) return;
    setCountries((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));
  };

  return <div className="max-w-3xl">
    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Workspace</p>
    <h1 className="mt-1 text-3xl font-semibold text-ink">Settings</h1>
    <p className="mt-2 text-sm text-muted">Manage your account and how JobMatchFlow keeps you informed.</p>

    {error && <p className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
    {message && <p className="mt-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">{message}</p>}

    <section className="mt-8 rounded-xl border border-line bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-ink">Account</h2>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="text-sm font-medium text-ink">Name<input value={name} onChange={(e) => setName(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
        <label className="text-sm font-medium text-ink">Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label>
      </div>
      <div className="mt-5 border-t border-line pt-5">
        <label className="text-sm font-medium text-ink">Profile photo<input type="file" accept=".jpg,.jpeg,image/jpeg" onChange={(e) => setPhoto(e.target.files?.[0] || null)} className="mt-2 block w-full text-sm font-normal text-muted" /></label>
        <div className="mt-3 flex items-center gap-4">
          {hasPhoto && (
            <img
              src={profilePhotoUrl(userId, photoVersion)}
              alt="Profile"
              onError={() => setHasPhoto(false)}
              className="h-16 w-16 rounded-full border border-line object-cover"
            />
          )}
          <button type="button" disabled={!photo || busy !== null} onClick={() => void run("photo", async () => { if (photo) { await postProfilePhoto(userId, photo); setPhoto(null); setHasPhoto(true); setPhotoVersion((v) => v + 1); setMessage("Profile photo updated."); } })} className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50">{busy === "photo" ? "Uploading…" : "Upload photo"}</button>
        </div>
      </div>
      <div className="mt-5 border-t border-line pt-5">
        <p className="text-sm font-medium text-ink">Resumes</p>
        <p className="mt-1 text-xs text-muted">Upload up to two PDF resumes. Re-uploading to a slot replaces its contents.</p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          {([1, 2] as const).map((slot) => {
            const item = resumeSlots?.items[slot - 1];
            const file = slot === 1 ? resumeFile1 : resumeFile2;
            const setFile = slot === 1 ? setResumeFile1 : setResumeFile2;
            const key = `resume${slot}`;
            return (
              <div key={slot} className="rounded-lg border border-line p-3">
                <p className="text-sm font-medium text-ink">Resume {slot}</p>
                <p className="mt-1 truncate text-xs text-muted">{item ? item.cv_name : "Not uploaded"}</p>
                <input type="file" accept=".pdf,application/pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} className="mt-2 block w-full text-sm font-normal text-muted" />
                <button
                  type="button"
                  disabled={!file || busy !== null}
                  onClick={() => void run(key, async () => {
                    if (file) {
                      await postResumeSlotUploadByNumber(userId, slot, file);
                      setFile(null);
                      await loadResumeSlots();
                      setMessage(`Resume ${slot} updated.`);
                    }
                  })}
                  className="mt-2 rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50"
                >
                  {busy === key ? "Uploading…" : "Upload"}
                </button>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-5 border-t border-line pt-5">
        <p className="text-sm font-medium text-ink">Resume &amp; cover letter generation model</p>
        <p className="mt-1 text-xs text-muted">Which LLM writes your tailored resumes and cover letters.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {generationModelOptions.map((opt) => (
            <button
              key={opt.id}
              type="button"
              disabled={busy !== null}
              onClick={() => void run("generation-model", async () => {
                await putGenerationModel(userId, opt.id as GenerationModel);
                setGenerationModel(opt.id as GenerationModel);
                setMessage("Generation model saved.");
              })}
              className={`rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 ${generationModel === opt.id ? "bg-ink text-white" : "border border-line text-muted hover:text-ink"}`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-5 border-t border-line pt-5">
        <p className="text-sm font-medium text-ink">Resume tailoring mode</p>
        <p className="mt-1 text-xs text-muted">
          {tailoringMode === "jd_aligned"
            ? "JD-aligned: confident framing and JD terminology, with reasonable inference on tools/skills you likely have. Numbers and employer names always come straight from your experience — never invented. Review before you send."
            : "Honest: bullets only reorganize and rephrase what's already in your experience entries, no inference beyond the literal text."}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {tailoringModeOptions.map((opt) => (
            <button
              key={opt.id}
              type="button"
              disabled={busy !== null}
              onClick={() => void run("tailoring-mode", async () => {
                await putResumeTailoringMode(userId, opt.id as ResumeTailoringMode);
                setTailoringMode(opt.id as ResumeTailoringMode);
                setMessage("Resume tailoring mode saved.");
              })}
              className={`rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 ${tailoringMode === opt.id ? "bg-ink text-white" : "border border-line text-muted hover:text-ink"}`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <button type="button" disabled={busy !== null || !name.trim() || !email.trim()} onClick={() => void run("account", async () => { await putAccount(userId, name.trim(), email.trim()); setMessage("Account details saved."); })} className="mt-5 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{busy === "account" ? "Saving…" : "Save changes"}</button>
    </section>

    <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-ink">Job matching</h2>
      <p className="mt-1 text-sm text-muted">Where to look for roles. Scoring preferences and target roles live on the Experience page.</p>
      <div className="mt-4">
        <p className="text-sm font-medium text-ink">Target countries</p>
        <p className="mt-1 text-xs text-muted">Currently open to Switzerland and Luxembourg — other markets are shown for reference only.</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {jobMarkets.map((m) => {
            const selectable = SELECTABLE_COUNTRIES.has(m.code);
            const selected = countries.includes(m.code);
            return (
              <button
                key={m.code}
                type="button"
                disabled={!selectable}
                onClick={() => toggleCountry(m.code)}
                className={`rounded-full px-3 py-1.5 text-sm ${
                  !selectable ? "cursor-not-allowed border border-line text-line" : selected ? "bg-ink text-white" : "border border-line text-muted hover:text-ink"
                }`}
              >
                {m.code}
              </button>
            );
          })}
        </div>
      </div>
      <button
        type="button"
        disabled={busy !== null}
        onClick={() => void run("job-matching", async () => {
          await putSearchProfile(userId, countries);
          setMessage("Job matching preferences saved.");
        })}
        className="mt-4 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50"
      >
        {busy === "job-matching" ? "Saving…" : "Save job matching preferences"}
      </button>
    </section>

    <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-ink">Notifications</h2>
      <div className="mt-4 divide-y divide-line">
        {([["dailyDigest", "Daily job digest"], ["highMatchAlerts", "High-match alerts"], ["applicationReminders", "Application reminders"]] as const).map(([key, label]) => <label key={key} className="flex cursor-pointer items-center justify-between py-4 text-sm text-ink"><span>{label}</span><input type="checkbox" checked={notifications[key]} onChange={(e) => saveNotifications({ ...notifications, [key]: e.target.checked })} className="h-4 w-4 accent-accent" /></label>)}
      </div>
    </section>

    <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-ink">Language</h2>
      <div className="mt-4 flex flex-wrap gap-2">{["English", "Chinese"].map((option) => <button key={option} type="button" onClick={() => { setLanguage(option); localStorage.setItem(LANGUAGE_KEY, option); }} className={`rounded-lg px-4 py-2 text-sm font-medium ${language === option ? "bg-ink text-white" : "border border-line text-muted hover:text-ink"}`}>{option}</button>)}</div>
    </section>

    <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-ink">Privacy and data</h2>
      <div className="mt-5 flex flex-wrap gap-3"><button type="button" disabled={busy !== null} onClick={() => void run("download", async () => { await downloadUserData(userId); setMessage("Your data download is ready."); })} className="rounded-lg border border-line px-4 py-2.5 text-sm font-medium hover:bg-canvas disabled:opacity-50">Download my data</button><button type="button" disabled={busy !== null} onClick={() => { if (window.confirm("Delete your account and personal data? This cannot be undone.")) void run("delete", async () => { await deleteAccount(userId); setMessage("Your account has been deleted."); }); }} className="rounded-lg border border-red-200 px-4 py-2.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50">Delete account</button></div>
    </section>
  </div>;
}
