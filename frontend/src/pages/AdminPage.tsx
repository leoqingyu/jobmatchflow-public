import { useEffect, useMemo, useState } from "react";
import {
  fetchAccount,
  fetchAdminOverview,
  fetchAdminUserQuota,
  fetchAdminUserStats,
  fetchAdminUsers,
  fetchPublicSettings,
  fetchTracking,
  fetchUserJobs,
  postPipelineRun,
  putAdminUserQuota,
  type Account,
  type AdminOverview,
  type AdminQuota,
  type AdminUser,
  type AdminUserStats,
  type JobsListResponse,
  type PublicSettings,
  type TrackingRow,
} from "../api";

function StatTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-canvas p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-ink">{value}</p>
    </div>
  );
}

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [jobs, setJobs] = useState<JobsListResponse | null>(null);
  const [applications, setApplications] = useState<TrackingRow[]>([]);
  const [userStats, setUserStats] = useState<AdminUserStats | null>(null);
  const [settings, setSettings] = useState<PublicSettings | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [quota, setQuota] = useState<AdminQuota | null>(null);
  const [quotaBusy, setQuotaBusy] = useState(false);
  const [quotaMessage, setQuotaMessage] = useState<string | null>(null);
  const selected = useMemo(() => users.find((user) => user.id === selectedId) || null, [users, selectedId]);

  useEffect(() => {
    void Promise.all([fetchAdminUsers(), fetchPublicSettings(), fetchAdminOverview()]).then(
      ([nextUsers, nextSettings, nextOverview]) => {
        setUsers(nextUsers);
        setSettings(nextSettings);
        setOverview(nextOverview);
        setSelectedId(nextUsers[0]?.id ?? null);
      }
    );
  }, []);

  useEffect(() => {
    if (selectedId == null) return;
    void Promise.all([
      fetchAccount(selectedId),
      fetchUserJobs(selectedId),
      fetchTracking(selectedId),
      fetchAdminUserStats(selectedId),
    ])
      .then(([nextAccount, nextJobs, nextTracking, nextStats]) => {
        setAccount(nextAccount);
        setJobs(nextJobs);
        setApplications(nextTracking.rows);
        setUserStats(nextStats);
      })
      .catch((e: unknown) => setMessage(e instanceof Error ? e.message : "Unable to load user data"));
    setQuotaMessage(null);
    void fetchAdminUserQuota(selectedId).then(setQuota);
  }, [selectedId]);

  const saveQuota = () => {
    if (selectedId == null || quota == null) return;
    setQuotaBusy(true);
    setQuotaMessage(null);
    void putAdminUserQuota(selectedId, {
      max_matched_jobs: quota.max_matched_jobs,
      max_generated_resumes: quota.max_generated_resumes,
      max_generated_cover_letters: quota.max_generated_cover_letters,
      allowed_countries: quota.allowed_countries,
    })
      .then(() => setQuotaMessage("Quota saved."))
      .catch((e) => setQuotaMessage(e instanceof Error ? e.message : "Failed to save quota"))
      .finally(() => setQuotaBusy(false));
  };

  const toggleQuotaCountry = (code: string) => {
    if (!quota) return;
    const has = quota.allowed_countries.includes(code);
    setQuota({
      ...quota,
      allowed_countries: has ? quota.allowed_countries.filter((c) => c !== code) : [...quota.allowed_countries, code],
    });
  };

  const runPipeline = () => {
    if (selectedId == null) return;
    setBusy(true);
    setMessage(null);
    void postPipelineRun(selectedId)
      .then(() => setMessage("Pipeline completed."))
      .catch((e) => setMessage(e instanceof Error ? e.message : "Pipeline failed"))
      .finally(() => setBusy(false));
  };

  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Developer area</p>
      <h1 className="mt-1 font-display text-3xl font-semibold text-ink">Admin</h1>
      <p className="mt-2 text-sm text-muted">Manage users, inspect user activity and control system settings.</p>

      {overview && (
        <section className="mt-8 rounded-xl border border-line bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-ink">Overview</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Users" value={overview.total_users} />
            <StatTile label="Matches" value={overview.total_matches} />
            <StatTile label="Resumes" value={overview.total_resumes} />
            <StatTile label="Cover letters" value={overview.total_cover_letters} />
            <StatTile label="Est. LLM cost" value={`$${overview.estimated_cost_usd.toFixed(2)}`} />
            <StatTile label="Tokens" value={overview.total_tokens.toLocaleString()} />
          </div>
        </section>
      )}

      <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-ink">Users</h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Email</th>
                <th className="py-2 pr-4">Role</th>
                <th className="py-2 pr-4">Verified</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Plan</th>
                <th className="py-2 pr-4">Created</th>
                <th className="py-2 pr-4">Last login</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr
                  key={user.id}
                  onClick={() => setSelectedId(user.id)}
                  className={`cursor-pointer border-b border-line/60 hover:bg-canvas ${user.id === selectedId ? "bg-canvas" : ""}`}
                >
                  <td className="py-2 pr-4 font-medium text-ink">{user.name}</td>
                  <td className="py-2 pr-4 text-muted">{user.email}</td>
                  <td className="py-2 pr-4">{user.role}</td>
                  <td className="py-2 pr-4">{user.email_verified ? "✓" : "—"}</td>
                  <td className="py-2 pr-4">{user.status}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${user.quota_overridden_by_admin ? "bg-accent/10 text-accent" : "bg-canvas text-muted"}`}
                    >
                      {user.quota_overridden_by_admin ? "Custom" : "Free tier"}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-muted">{new Date(user.created_at).toLocaleDateString()}</td>
                  <td className="py-2 pr-4 text-muted">
                    {user.last_login_at ? new Date(user.last_login_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && account && (
          <div className="mt-6 grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <div className="rounded-lg bg-canvas p-4">
              <p className="text-xs text-muted">Account</p>
              <p className="mt-1 font-medium text-ink">{account.name}</p>
              <p className="text-sm text-muted">{account.email}</p>
            </div>
            <StatTile label="Available matches" value={jobs?.jobs.length ?? 0} />
            <StatTile label="Applications" value={applications.length} />
            {userStats && (
              <>
                <StatTile label="Scored matches" value={userStats.match_count} />
                <StatTile label="Resumes generated" value={userStats.resume_count} />
                <StatTile label="Est. cost" value={`$${userStats.estimated_cost_usd.toFixed(2)}`} />
              </>
            )}
          </div>
        )}
      </section>

      {selected && (
        <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-ink">User activity</h2>
            <button
              type="button"
              disabled={busy}
              onClick={runPipeline}
              className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy ? "Running…" : "Run pipeline for selected user"}
            </button>
          </div>
          <div className="mt-4 divide-y divide-line">
            {applications.slice(0, 10).map((application) => (
              <div key={application.tracking_id} className="flex items-center justify-between py-3 text-sm">
                <span className="font-medium text-ink">
                  {application.title} · {application.company}
                </span>
                <span className="text-muted">{application.application_status}</span>
              </div>
            ))}
            {!applications.length && <p className="py-3 text-sm text-muted">No applications for this user.</p>}
          </div>
          {message && <p className="mt-3 text-sm text-muted">{message}</p>}
        </section>
      )}

      {selected && quota && (
        <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-ink">Quota — {selected.name}</h2>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${quota.quota_overridden_by_admin ? "bg-accent/10 text-accent" : "bg-canvas text-muted"}`}
            >
              {quota.quota_overridden_by_admin ? "Manually set" : "Free-tier default"}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">Leave a limit blank for unlimited. Saving marks this user as manually managed.</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <label className="text-sm font-medium text-ink">
              Max matched jobs
              <input
                type="number"
                min={0}
                value={quota.max_matched_jobs ?? ""}
                onChange={(e) =>
                  setQuota({ ...quota, max_matched_jobs: e.target.value === "" ? null : Number(e.target.value) })
                }
                className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal"
              />
            </label>
            <label className="text-sm font-medium text-ink">
              Max resumes generated
              <input
                type="number"
                min={0}
                value={quota.max_generated_resumes ?? ""}
                onChange={(e) =>
                  setQuota({
                    ...quota,
                    max_generated_resumes: e.target.value === "" ? null : Number(e.target.value),
                  })
                }
                className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal"
              />
            </label>
            <label className="text-sm font-medium text-ink">
              Max cover letters generated
              <input
                type="number"
                min={0}
                value={quota.max_generated_cover_letters ?? ""}
                onChange={(e) =>
                  setQuota({
                    ...quota,
                    max_generated_cover_letters: e.target.value === "" ? null : Number(e.target.value),
                  })
                }
                className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal"
              />
            </label>
          </div>
          <p className="mt-4 text-sm font-medium text-ink">Allowed countries</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {(settings?.job_markets ?? []).map((m) => (
              <label
                key={m.code}
                className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${quota.allowed_countries.includes(m.code) ? "border-accent bg-accent/10 text-accent" : "border-line text-muted"}`}
              >
                <input
                  type="checkbox"
                  className="mr-2"
                  checked={quota.allowed_countries.includes(m.code)}
                  onChange={() => toggleQuotaCountry(m.code)}
                />
                {m.code}
              </label>
            ))}
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              disabled={quotaBusy}
              onClick={saveQuota}
              className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {quotaBusy ? "Saving…" : "Save quota"}
            </button>
            {quotaMessage && <p className="text-sm text-muted">{quotaMessage}</p>}
          </div>
        </section>
      )}

      {settings && (
        <section className="mt-6 rounded-xl border border-line bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-ink">System settings</h2>
          <pre className="mt-4 max-h-96 overflow-auto rounded-lg bg-canvas p-4 text-xs">
            {JSON.stringify(settings, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}
