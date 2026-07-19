import { useState } from "react";
import { postLogin } from "../api";
import { writeAuthSession } from "../config";
import { useNavigate, Link } from "react-router-dom";

export default function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const submit = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); setError(null); try { const session = await postLogin(email, password); writeAuthSession(session); onLogin(); navigate(session.role === "admin" ? "/admin" : "/jobs", { replace: true }); } catch (e) { setError(e instanceof Error ? e.message : "Unable to sign in"); } finally { setBusy(false); } };
  return <main className="flex min-h-screen items-center justify-center bg-canvas px-4"><form onSubmit={submit} className="w-full max-w-md rounded-2xl border border-line bg-white p-8 shadow-sm"><p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">JobMatchFlow</p><h1 className="mt-2 font-display text-3xl font-semibold text-ink">Welcome back</h1><p className="mt-2 text-sm text-muted">Sign in to continue your job search.</p>{error && <p className="mt-5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}<label className="mt-6 block text-sm font-medium text-ink">Email<input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label><label className="mt-4 block text-sm font-medium text-ink">Password<input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-2 w-full rounded-lg border border-line px-3 py-2 font-normal" /></label><button type="submit" disabled={busy} className="mt-6 w-full rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white hover:bg-accent-hover disabled:opacity-50">{busy ? "Signing in…" : "Sign in"}</button><p className="mt-4 text-center text-sm text-muted">New here? <Link to="/signup" className="font-medium text-accent">Create an account</Link></p></form></main>;
}
