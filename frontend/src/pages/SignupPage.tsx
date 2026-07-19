import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { postSignup } from "../api";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postSignup(email, password, name);
      navigate(`/verify-email?email=${encodeURIComponent(email)}`, { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to sign up");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <form onSubmit={submit} className="w-full max-w-md rounded-2xl border border-line bg-white p-8 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">JobMatchFlow</p>
        <h1 className="mt-2 font-display text-3xl font-semibold text-ink">Create your account</h1>
        <p className="mt-2 text-sm text-muted">We'll email you a verification code.</p>
        {error && <p className="mt-5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
        <label className="mt-6 block text-sm font-medium text-ink">
          Name
          <input required value={name} onChange={(e) => setName(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" />
        </label>
        <label className="mt-4 block text-sm font-medium text-ink">
          Email
          <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" />
        </label>
        <label className="mt-4 block text-sm font-medium text-ink">
          Password
          <input type="password" required minLength={8} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" />
        </label>
        <button type="submit" disabled={busy} className="mt-6 w-full rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">
          {busy ? "Creating account…" : "Create account"}
        </button>
        <p className="mt-4 text-center text-sm text-muted">
          Already have an account? <Link to="/" className="font-medium text-accent">Sign in</Link>
        </p>
      </form>
    </main>
  );
}
