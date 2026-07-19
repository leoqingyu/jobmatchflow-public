import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./layout/AppShell";
import MatchesPage from "./pages/MatchesPage";
import ExperiencePage from "./pages/ExperiencePage";
import SettingsPage from "./pages/SettingsPage";
import TrackingPage from "./pages/TrackingPage";
import MaterialsEditorPage from "./pages/MaterialsEditorPage";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";
import AdminPage from "./pages/AdminPage";
import { readAuthSession, writeAuthSession, clearAuthSession, type AuthSession } from "./config";
import { fetchMe, postLogout } from "./api";
import { useEffect, useState } from "react";

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(readAuthSession);
  const [checking, setChecking] = useState(true);

  // The httpOnly session cookie is invisible to JS and is the real source of
  // truth — reconcile the cached localStorage session against it once at boot,
  // so a stale/expired cookie (or a ban) doesn't leave the UI stuck logged in.
  useEffect(() => {
    void fetchMe().then((me) => {
      if (me) writeAuthSession(me);
      else clearAuthSession();
      setSession(me);
      setChecking(false);
    });
  }, []);

  if (checking) return null;

  if (!session) {
    return (
      <Routes>
        <Route path="signup" element={<SignupPage />} />
        <Route path="verify-email" element={<VerifyEmailPage />} />
        <Route path="*" element={<LoginPage onLogin={() => setSession(readAuthSession())} />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="materials/:jobId" element={<MaterialsEditorPage />} />
      <Route
        element={
          <AppShell
            session={session}
            onLogout={() => {
              void postLogout();
              clearAuthSession();
              setSession(null);
            }}
          />
        }
      >
        <Route index element={<Navigate to={session.role === "admin" ? "/admin" : "/jobs"} replace />} />
        <Route path="jobs" element={<MatchesPage />} />
        <Route path="applications" element={<TrackingPage />} />
        <Route path="experience" element={<ExperiencePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="admin" element={session.role === "admin" ? <AdminPage /> : <Navigate to="/jobs" replace />} />
        <Route path="*" element={<Navigate to="/jobs" replace />} />
      </Route>
    </Routes>
  );
}
