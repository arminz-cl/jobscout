# jobscout

**Pulls fresh job postings, scores each one against a personal profile + rubric with an LLM, and hands you a ranked shortlist** — so triaging a few hundred jobs takes minutes instead of an evening.

A personal CLI + local web app. Runs on your machine; nothing is hosted.

---

## The workflow

```
   FETCH                TRIAGE                 DEEP                   YOU
   ─────                ──────                 ────                   ───
LinkedIn guest    →   batch-score all    →   full rubric on    →   review the
search, per          postings 0–100          the top ~15,          shortlist,
intent group        (overall/chance/         one call each         mark applied
                     quality) + area
  ~600 postings         ~600 scored          ~15 deep verdicts        ~5 to apply
```

- **Fetch** — a small set of saved searches, grouped by target area (A: SWE-on-data, B: AI engineer, …). Dedupes against what it's seen; pulls the full JD for anything new.
- **Triage** — one cheap batched LLM pass over everything: three 0–100 scores + a coarse verdict. Enough to rank.
- **Deep-assess** — the full scoring rubric on the highest-scoring postings, one focused call each: verdict, area with reasoning, skill gaps, comp read.
- **Review** — sort by score, open a posting, read the JD + verdict side by side, mark it shortlisted / applied / passed.

The profile, the rubric, and the hand-labelled eval set live in a **separate private repo** — this repo is just the engine.

---

## The app

`jobscout serve` → `http://127.0.0.1:8000`

### Dashboard — run everything from here

Two sections, same shape: **Fetch** (top) collects postings, **Assess** (bottom) scores them. Each query group has its own Run button; runs show live progress and can be stopped.

![Dashboard](docs/img/dashboard.png)

### Postings — everything collected

Filter by intent group, company, work type, seen/unseen, has-JD. Click a row for the full JD, the assessment, and status buttons.

![Postings](docs/img/postings.png)

### Assessments — the scored shortlist

Every assessed posting with its verdict, area, and which model produced it. Sort by verdict or score.

![Assessments](docs/img/assessments.png)

### Companies — one row per employer

Posting counts, how many were `pursue`, a manual 0–5 rating. Click through to that company's postings.

![Companies](docs/img/companies.png)

### Runs — every collection / scoring job

Full history: what each run covered, how long it took, how many new postings or assessments it produced.

![Runs](docs/img/runs.png)

---

## Running it

```bash
make install          # python venv + deps
cp config.example.yaml config.yaml     # then edit: paths to your profile repo, search queries
echo 'ASSESSOR_API_KEY=...' > .env     # a free Groq key (console.groq.com) drives the assessor
make build            # build the React SPA into the package
make serve            # API + UI at http://127.0.0.1:8000
```

CLI equivalents: `jobscout config`, `jobscout fetch`, `jobscout search "..."`, `jobscout serve`.

---

## How it's built

| | |
|---|---|
| Engine | Python — `config`, pluggable `sources/`, SQLite (`db`), `fetch` orchestration, `assess` |
| Source | LinkedIn guest endpoint (run locally — cloud IPs are blocked); `jsearch` fallback planned |
| Assessor | pluggable LLM provider — Groq / Gemini / OpenRouter (OpenAI-compatible) or Claude; batch triage + single-JD deep pass |
| Backend | FastAPI, in-process background run runner (one job at a time, cancellable) |
| Frontend | React + Vite SPA, built into the Python package and served by the backend |
| State | one SQLite file, gitignored |

## Status

Fetch pipeline and the web app are working. The assessor runs (two-tier triage + deep). Still to come: the eval harness that scores the assessor against the hand-labelled JD set, the daily-markdown report, and tests. See [`PLAN.md`](PLAN.md) for the roadmap and [`ASSESSOR.md`](ASSESSOR.md) for the current step.
