import { NavLink, Outlet } from "react-router-dom";
import { useEffect, useState } from "react";
import { fetchAdminUsers, fetchSearchProfile, type AdminUser } from "../api";
import { readAdminViewUserId, writeAdminViewUserId, type AuthSession } from "../config";
import OnboardingModal from "../components/OnboardingModal";

const userNav = [
  { to: "/jobs", label: "Jobs", emoji: "▣" },
  { to: "/applications", label: "Applications", emoji: "▤" },
  { to: "/experience", label: "Experience", emoji: "◎" },
  { to: "/settings", label: "Settings", emoji: "⚙" },
];

export default function AppShell({ session, onLogout }: { session: AuthSession; onLogout: () => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [viewUserId, setViewUserId] = useState<number | null>(session.role === "admin" ? readAdminViewUserId() : session.id);
  useEffect(() => { if (session.role === "admin") void fetchAdminUsers().then((next) => { setUsers(next); if (viewUserId == null && next[0]) { setViewUserId(next[0].id); writeAdminViewUserId(next[0].id); } }); }, [session.role, viewUserId]);
  const viewUser = users.find((user) => user.id === viewUserId);

  const [onboarding, setOnboarding] = useState<{ locked: boolean; allowedCountries: string[] } | null>(null);
  useEffect(() => {
    if (session.role === "admin") return;
    void fetchSearchProfile(session.id).then((sp) =>
      setOnboarding({ locked: sp.country_locked, allowedCountries: sp.allowed_countries })
    );
  }, [session.role, session.id]);

  const adminSwitcher = session.role === "admin" && (
    <select
      value={viewUserId ?? ""}
      onChange={(event) => { const id = Number(event.target.value); setViewUserId(id); writeAdminViewUserId(id); window.location.reload(); }}
      className="w-full rounded-md border border-white/10 bg-sidebar px-2 py-2 text-xs text-white"
    >
      {users.map((user) => <option key={user.id} value={user.id}>{user.name} · {user.email}</option>)}
    </select>
  );

  return (
    <div className="flex min-h-screen bg-canvas">
      {/* Desktop sidebar (hidden on mobile — replaced by the top bar + bottom tab bar below) */}
      <aside className="hidden w-60 shrink-0 flex-col bg-sidebar px-4 py-6 md:flex">
        <h1 className="text-lg font-semibold tracking-tight text-white">
          🎯 JobMatchFlow
        </h1>
        <p className="mt-1 text-xs text-slate-400">Daily job inbox</p>
        <hr className="my-4 border-white/10" />
        <nav className="flex flex-col gap-0.5" aria-label="Primary navigation">
          {userNav.map(({ to, label, emoji }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                [
                  "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-white shadow-sm"
                    : "text-slate-300 hover:bg-white/5 hover:text-white",
                ].join(" ")
              }
            >
              <span className="mr-2">{emoji}</span>
              {label}
            </NavLink>
          ))}
          {session.role === "admin" && <NavLink to="/admin" className={({ isActive }) => ["rounded-md px-3 py-2 text-sm font-medium transition-colors", isActive ? "bg-accent text-white shadow-sm" : "text-slate-300 hover:bg-white/5 hover:text-white"].join(" ")}><span className="mr-2">⚙</span>Admin</NavLink>}
        </nav>
        {session.role === "admin" && <div className="mt-6 rounded-lg border border-white/10 bg-white/5 p-3"><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-300">Viewing as</p><div className="mt-2">{adminSwitcher}</div>{viewUser && <p className="mt-2 truncate text-[11px] text-slate-400">User workspace preview</p>}</div>}
        <div className="mt-8 border-t border-white/10 pt-4">
          <p className="text-xs font-medium text-white">{session.name}</p>
          <p className="mt-1 truncate text-xs text-slate-400">{session.email}</p>
          <button type="button" onClick={onLogout} className="mt-3 text-xs font-medium text-slate-400 hover:text-white">Sign out</button>
        </div>
        <a
          href="mailto:leo_jiangq@gmail.com"
          className="mt-auto flex items-center gap-2 pt-6 text-xs text-slate-400 hover:text-white"
        >
          <span>✉️</span> Questions or feedback? Email Leo
        </a>
      </aside>

      {/* Mobile top bar: brand + (admin only) workspace switcher + sign out */}
      <header className="fixed inset-x-0 top-0 z-20 flex items-center justify-between gap-3 bg-sidebar px-4 py-3 md:hidden">
        <h1 className="shrink-0 text-sm font-semibold tracking-tight text-white">🎯 JobMatchFlow</h1>
        <div className="flex min-w-0 items-center gap-3">
          {session.role === "admin" && <div className="w-36">{adminSwitcher}</div>}
          <a href="mailto:leo_jiangq@gmail.com" title="Questions or feedback? Email Leo" className="shrink-0 text-base">✉️</a>
          <button type="button" onClick={onLogout} className="shrink-0 text-xs font-medium text-slate-300 hover:text-white">Sign out</button>
        </div>
      </header>

      <main className="min-w-0 flex-1 overflow-auto pb-20 pt-14 md:pb-0 md:pt-0">
        <div className="mx-auto max-w-5xl px-4 py-6 md:px-6 md:py-8">
          {session.role === "admin" && viewUserId == null ? <p className="text-sm text-muted">Loading user workspaces…</p> : <Outlet />}
        </div>
      </main>

      {/* Mobile bottom tab bar: the primary navigation on phones — always visible, thumb-reachable */}
      <nav className="fixed inset-x-0 bottom-0 z-20 flex border-t border-line bg-white md:hidden" aria-label="Primary navigation">
        {userNav.map(({ to, label, emoji }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              [
                "flex flex-1 flex-col items-center gap-0.5 py-2.5 text-[11px] font-medium transition-colors",
                isActive ? "text-accent" : "text-muted",
              ].join(" ")
            }
          >
            <span className="text-lg leading-none">{emoji}</span>
            {label}
          </NavLink>
        ))}
        {session.role === "admin" && (
          <NavLink
            to="/admin"
            className={({ isActive }) =>
              [
                "flex flex-1 flex-col items-center gap-0.5 py-2.5 text-[11px] font-medium transition-colors",
                isActive ? "text-accent" : "text-muted",
              ].join(" ")
            }
          >
            <span className="text-lg leading-none">⚙</span>
            Admin
          </NavLink>
        )}
      </nav>

      {onboarding && !onboarding.locked && (
        <OnboardingModal
          userId={session.id}
          allowedCountries={onboarding.allowedCountries}
          onDone={() => setOnboarding({ ...onboarding, locked: true })}
        />
      )}
    </div>
  );
}
