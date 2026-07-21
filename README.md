# JobMatchFlow

### A speed-first job scanner and matcher for the Swiss & Luxembourg market.

In markets like Switzerland, getting hired is a game of **coverage and speed**, not
polish. Good roles attract hundreds of applicants within days and are often closed
early — so the person who *sees the posting first* has a real edge. JobMatchFlow
continuously scans postings across sources, filters out the ones that don't fit (wrong
language, wrong work-authorization, wrong seniority) before they ever reach you, and
surfaces the handful worth acting on — so your time goes to networking and referrals
instead of refreshing job boards.

This repo is the full source: a FastAPI backend, an ingestion/scraping pipeline, the
LLM-based scoring and matching engine, resume/cover-letter generation, and a React
frontend.

[Live Demo](https://www.jobmatchflow.com) · [Architecture](#architecture) · [Report an Issue](https://github.com/leoqingyu/jobmatchflow-public/issues)

![CI](https://github.com/leoqingyu/jobmatchflow-public/actions/workflows/ci.yml/badge.svg)
![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Node 20+](https://img.shields.io/badge/node-20%2B-blue)

---

![JobMatchFlow dashboard showing matched jobs and application status](docs/images/match-analysis.png)

## The problem this is built around

Most AI job tools optimize the *wrong* variable. They assume you already have a short
list of jobs and just need to tailor a better resume to raise your hit rate. That's the
US large-company model. It doesn't match how hiring actually works in Switzerland and
Luxembourg, where the binding constraints are different:

- **Coverage** — relevant roles are scattered across many boards and company sites; no
  single search catches them all.
- **Speed** — the window between a posting going live and being buried (or closed) is
  measured in days, sometimes hours.
- **Hard blockers** — a posting can be a perfect skills match and still be a dead end
  because of a C1-German requirement or a work-authorization constraint. Finding that
  out *after* applying is wasted effort.

JobMatchFlow is designed around those three constraints specifically, rather than
around "make my resume look better."

## Architecture

```
                 ┌─────────────┐
                 │  Scheduler  │  keyword-driven, per-market daily scan
                 └──────┬──────┘
                        ▼
   Sources ──▶ Scraper ──▶ Normalize ──▶ De-dup ──▶ Job store (Postgres)
  (JobSpy /                    │            │
   provider interface)         │            └─ embedding-based near-duplicate collapse
                               ▼
                        Matching worker
                        ├─ hard-requirement gate  (language / work auth / certs)
                        ├─ seniority reconciliation
                        ├─ LLM scoring + explanation (per user CV)
                        └─ material generation (resume / cover letter, on demand)
                               ▼
                     FastAPI  ◀──▶  React SPA
                               ▼
                        Notifier (daily digest email)
```

The design goal throughout is to **push expensive work as late as possible and share it
as widely as possible.** Job discovery, normalization and de-duplication happen once per
posting and are amortized across all users; only the final CV-specific scoring and
material generation are per-user.

## Engineering notes

The parts I spent the most time on, and the tradeoffs behind them:

### Ingestion & scan cadence
The scan scheduler is keyword- and market-driven, currently running on a **daily**
cadence per market. The central tension here is **freshness vs. cost and rate-limits**:
fresher scans catch roles sooner — which is the entire point of the product — but every
scan spends requests against sources that rate-limit and actively detect scraping. A
finer-grained approach on the roadmap is **tiered refresh**: scanning a user's
high-priority criteria (specific target companies, exact role keywords) more frequently
than broad, catch-all queries, so freshness is spent where it actually matters instead
of uniformly across the whole market.

The bundled `scraper/providers/jobspy_provider.py` is a **reference implementation** on
top of the open-source [`python-jobspy`](https://pypi.org/project/python-jobspy/)
library — it's here to make the pipeline runnable end-to-end, not a description of how
the production service sources data. Providers are swappable via the interface in
`scraper/base.py`.

### De-duplication
The same role frequently appears across multiple boards with slightly different titles
and descriptions. Exact matching misses these, so the pipeline uses embedding-based
near-duplicate collapse (optional, see `requirements-embed.txt`) to avoid scoring and
showing the same job more than once.

### Matching: gate before you score
Running an LLM over every posting is expensive and, worse, wastes the user's attention.
Matching therefore runs a **hard-requirement gate first** — language level, work
authorization, certifications — and only postings that clear it get the full LLM scoring
pass. Seniority is reconciled separately, so a junior posting padded with a wishful
"5+ years" line doesn't auto-reject a good candidate, and a genuine senior role doesn't
get inflated. The output is an explainable, requirement-by-requirement breakdown rather
than a single black-box number.

### Pluggable by design
Storage sits behind an interface (local disk today, S3-ready) and the LLM layer supports
multiple providers (Gemini / Claude / DeepSeek / Qwen), so cost and capability can be
tuned per task — cheaper models for high-volume scoring, stronger ones where the output
quality matters most.

## Core capabilities

- **Job discovery** — continuous multi-source scanning across the target markets, so
  you're not limited to a single keyword search.
- **Hard-requirement filtering** — language, work authorization and certification
  blockers are checked *before* scoring, surfacing dead-ends immediately.
- **Explainable CV–job matching** — a requirement-by-requirement fit breakdown, not a
  black-box score.
- **Tailored materials** — job-specific resume content and cover letters from a master
  career profile (a secondary feature, generated on demand).
- **Application tracking** — saved jobs, status, generated documents, deadlines and
  follow-ups in one pipeline.

## Product preview

| Job discovery | Match analysis | Application tracking |
| --- | --- | --- |
| ![Job discovery](docs/images/job-discovery.png) | ![Match analysis](docs/images/match-analysis.png) | ![Application tracking](docs/images/application-tracking.png) |

## Technology

| Layer | Stack |
| --- | --- |
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI |
| Database | PostgreSQL (SQLAlchemy + Alembic) |
| AI | Pluggable LLM providers (Gemini / Claude / DeepSeek / Qwen) |
| Docs | HTML → PDF/DOCX rendering |

The production architecture may evolve independently from this public demo repo.

## Project structure

```
ai/         LLM clients, scoring, JD extraction, resume rewriting
api/        FastAPI apps (user_app.py is the main entrypoint) and route modules
core/       Settings, logging, shared constants and utilities
db/         SQLAlchemy models and Alembic migrations
notifier/   Email notifications
renderer/   HTML → PDF/DOCX rendering
scraper/    Job board connectors (JobSpy reference provider)
services/   Business logic (ingestion, scoring, tracking, generation, ...)
storage/    Pluggable file storage (local disk today, S3-ready interface)
tasks/      Scheduled/background task definitions
scripts/    Standalone entrypoints for workers and maintenance scripts
frontend/   React + TypeScript + Vite single-page app
```

## Local development

### Requirements
- Python 3.11+
- PostgreSQL 14+
- Node.js 20+ and npm
- At least one LLM API key (Gemini, Claude, DeepSeek, or Qwen)
- Optional: Playwright browsers (chromium PDF engine), LibreOffice (`soffice`, only used
  to sanity-check DOCX page counts — degrades gracefully if absent)

### 1. Clone and install the backend
```bash
git clone https://github.com/leoqingyu/jobmatchflow-public.git
cd jobmatchflow-public

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-embed.txt   # optional: embedding-based de-dup
playwright install chromium              # only for the default chromium PDF engine
```

### 2. Configure environment
```bash
cp .env.example .env
```
Fill in at minimum `DATABASE_URL` and one LLM API key. Every variable is documented
inline in `.env.example` and maps 1:1 to a field on `Settings` in `core/config.py`.

### 3. Set up the database
```bash
createdb jobmatchflow
alembic upgrade head
```

### 4. Run the API
```bash
uvicorn api.user_app:app --host 0.0.0.0 --port 8000 --reload
```
Swagger UI at `/docs`. Once the frontend is built, `/` redirects to the SPA at `/app/`.

### 5. Run the frontend
```bash
cd frontend
npm install
npm run dev
```
Dev mode proxies `/api/*` to `http://127.0.0.1:8000` (see `frontend/vite.config.ts`).
For a production build served by FastAPI: `npm run build` (outputs to `frontend/dist`,
auto-served at `/app`).

### 6. Optional background workers
```bash
python scripts/run_scheduler.py          # scheduled, keyword-driven scraping
python scripts/run_matching_worker.py    # per-job scoring + asset generation
```
Use `systemd` / `supervisor` / `tmux` for anything long-lived. Never commit real API
keys, credentials, cookies, or production URLs — local, gitignored `.env` only.

## Scope & markets

Live today in **Switzerland & Luxembourg**, focused on **tech (software & data
engineering)** and **finance & banking (compliance, risk, analyst)** roles. Next markets
on the roadmap: **Germany** (Stuttgart, Frankfurt, Munich) and the **Netherlands**
(Amsterdam, Rotterdam, Utrecht) — chosen for high role density and English-friendliness
for international candidates, not for breadth.

**Not included, for obvious reasons:** real user data, production credentials, and any
`.env` file. See `.env.example` for the full config surface.

## License

Licensed under [AGPL-3.0](LICENSE). You're free to use, modify and self-host — including
commercially — provided any modified version you run as a network service is also made
available under AGPL-3.0, including to users interacting with it only over a network.

---

**JobMatchFlow — See the right roles first. Spend your time where it counts.**


<div align="center">

**JobMatchFlow — Discover better matches. Prepare stronger applications.**

</div>
