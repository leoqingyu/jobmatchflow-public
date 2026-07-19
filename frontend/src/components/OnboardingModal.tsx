import { useState } from "react";
import { postChooseCountry } from "../api";

const COUNTRY_LABELS: Record<string, string> = {
  Switzerland: "🇨🇭 Switzerland",
  Luxembourg: "🇱🇺 Luxembourg",
};

export default function OnboardingModal({
  userId,
  allowedCountries,
  onDone,
}: {
  userId: number;
  allowedCountries: string[];
  onDone: () => void;
}) {
  const [country, setCountry] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!country) return;
    setBusy(true);
    setError(null);
    try {
      await postChooseCountry(userId, country);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/60 px-4 py-8 sm:items-center">
      <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">Welcome to JobMatchFlow</p>
        <h1 className="mt-2 font-display text-2xl font-semibold text-ink">Hi, I'm Leo — a few things before you start</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          I built this to help people spend less time scrolling job boards and more time on
          applications that actually have a shot. A few quick things to set up first — they take a
          couple of minutes and make everything downstream a lot more accurate.
        </p>

        <div className="mt-6 space-y-5 text-sm leading-relaxed text-ink">
          <div>
            <p className="font-semibold">1. Head to Settings</p>
            <p className="mt-1 text-muted">
              Pick which AI model writes your resumes and cover letters, and upload a profile photo
              while you're there.
            </p>
          </div>

          <div>
            <p className="font-semibold">2. Choose your country</p>
            <p className="mt-1 text-muted">
              This is the market JobMatchFlow will search for you. One heads-up:{" "}
              <span className="font-medium text-ink">once you pick, it's locked</span> — this isn't
              something you can change yourself in Settings afterward. If you ever need to switch,
              just email me and I'll update it on my end.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {allowedCountries.map((code) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => setCountry(code)}
                  className={`rounded-lg border-2 px-4 py-2.5 text-sm font-semibold transition-colors ${
                    country === code ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-ink"
                  }`}
                >
                  {COUNTRY_LABELS[code] ?? code}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="font-semibold">3. Fill in your experience — thoroughly</p>
            <p className="mt-1 text-muted">
              This is the part that matters most. Everything in your Experience library feeds
              directly into how jobs get scored against you and how your resume and cover letters
              get written. The more complete and specific you are here, the more accurate the AI
              gets. Don't rush this one.
            </p>
          </div>

          <div>
            <p className="font-semibold">Then you're set</p>
            <p className="mt-1 text-muted">
              Once your experience is filled in, click "Start matching" once. After that,
              JobMatchFlow runs on its own — it checks for new jobs every day and keeps matching
              them against your profile automatically. You don't need to keep coming back and
              clicking.
            </p>
          </div>

          <div className="rounded-lg bg-canvas p-3">
            <p className="font-semibold">What you get to start</p>
            <p className="mt-1 text-muted">
              500 job matches, plus 10 resume and 10 cover letter generations, on the house.
            </p>
          </div>
        </div>

        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        <button
          type="button"
          disabled={!country || busy}
          onClick={() => void submit()}
          className="mt-6 w-full rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Saving…" : country ? `Confirm ${COUNTRY_LABELS[country] ?? country} and get started` : "Pick a country to continue"}
        </button>

        <p className="mt-5 border-t border-line pt-4 text-xs leading-relaxed text-muted">
          Questions, bugs, or ideas? Email me anytime at{" "}
          <a href="mailto:leo_jiangq@gmail.com" className="font-medium text-accent">leo_jiangq@gmail.com</a> — I read everything.
          <br />
          JobMatchFlow is open source on GitHub — run your own instance with your own API keys if
          you'd rather, or email me and we can work out usage-based pricing instead.
          <br />
          And genuinely — I hope you find something great soon and never need to open this app again.
        </p>
      </div>
    </div>
  );
}
