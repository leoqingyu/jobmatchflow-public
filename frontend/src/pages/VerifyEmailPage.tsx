import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { postVerifyEmail } from "../api";

export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postVerifyEmail(email, code.trim());
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid or expired code");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <form onSubmit={submit} className="w-full max-w-md rounded-2xl border border-line bg-white p-8 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">JobMatchFlow</p>
        <h1 className="mt-2 font-display text-3xl font-semibold text-ink">Verify your email</h1>
        <p className="mt-2 text-sm text-muted">Enter the 6-digit code we emailed you.</p>
        {error && <p className="mt-5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
        {done ? (
          <div className="mt-5">
            <p className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
              Email verified. You can sign in now.
            </p>
            <button type="button" onClick={() => navigate("/", { replace: true })} className="mt-6 w-full rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white hover:bg-accent-hover">
              Go to sign in
            </button>
          </div>
        ) : (
          <>
            <label className="mt-6 block text-sm font-medium text-ink">
              Email
              <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" />
            </label>
            <label className="mt-4 block text-sm font-medium text-ink">
              Verification code
              <input required minLength={6} maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal tracking-[0.3em]" />
            </label>
            <button type="submit" disabled={busy} className="mt-6 w-full rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">
              {busy ? "Verifying…" : "Verify"}
            </button>
            <p className="mt-4 text-center text-sm text-muted">
              <Link to="/" className="font-medium text-accent">Back to sign in</Link>
            </p>
          </>
        )}
      </form>
    </main>
  );
}
