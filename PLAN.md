# jobscout — MVP plan

A tool run on demand that pulls fresh job postings matching a profile, scores each one with an
LLM against a rubric (`assessment-guide.md` in the private career repo), and drops a ranked
shortlist into the output directory.

Also a portfolio project: real source integration + LLM app + eval harness + a CLI + a local web app.

## Repo split

- **This repo (`jobscout`) is public and ships only code:** `src/`, `frontend/`, `README.md`,
  `config.example.yaml`, `profile.example/` (sanitized), optional `eval/` fixture (JD text +
  verdict labels, company names → "Company A").
- **A separate private "career repo"** holds the real profile docs, the real eval labels, and the
  daily output. jobscout's `config.yaml` (gitignored) points `profile_dir` / `eval_dir` /
  `output_dir` into it. Nothing personal is committed here.
- `.gitignore`: `.env`, `jobscout.db`, `config.yaml`.

---

## MVP scope

**In:**
- Pull postings from **one** job source, for a fixed set of profile-derived queries.
- Dedupe against what's already been seen.
- Score each new posting with an LLM against the profile + the rubric → `area / target_quality / chance / verdict / one-line reasoning`.
- Write `jds/daily/YYYY-MM-DD.md` — pursue + maybe grouped and ranked, skips collapsed to a count.
- Run automatically once a day; commit the file.

**Out (later):** web UI, LinkedIn personalized feed, multi-source dedupe, feedback learning loop, auto-tailored resumes, application autofill, notifications beyond the committed file.

---

## The LinkedIn problem (decide up front)

LinkedIn has **no usable public jobs API** (Talent Solutions is partner-gated, for *posting* not searching). Options, ranked by pain:

| Path | Notes |
|---|---|
| **Guest endpoint** `linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=…&location=…&start=N` | No login. Returns HTML job *cards* only (title/company/location/url/date). ~1000-result cap/search, rate-limited (429s), against User Agreement §8.2. Full description = a 2nd request per job to `jobs/view/<id>` (N+1). Works from residential IP, **blocked from cloud IPs** (so: run locally). |
| **Hosted scraper** (Apify LinkedIn Jobs actor) | Pay-per-run, structured JSON, they handle proxies/blocking. Reliable. Still ToS-grey. |
| **Authenticated** (my `li_at` cookie → Voyager API) | Personalized recs, but most against ToS, account-restriction risk, brittle. **Skip.** |
| **Aggregators** (JSearch/SerpAPI = Google for Jobs; Adzuna; ATS boards) | Fully clean. Partial LinkedIn coverage (Google's LinkedIn indexing has shrunk). Works from any IP → cloud-deployable. |

Cautionary tale: Proxycurl (paid LinkedIn data API) was shut down late 2024 under LinkedIn legal pressure — that's the risk profile for anything *commercial* built on this. A personal daily script is a different story.

**Decision for MVP:**
- Build a **pluggable `Source` interface** (`fetch(query, location) -> list[Posting]`, `fetch_description(posting) -> str`).
- Ship two adapters: **`linkedin_guest`** (primary) and **`jsearch`** (clean fallback / public-demo mode).
- **Run locally on cron** (not GitHub Actions) so the guest endpoint isn't hitting a blocked cloud IP. Polite delay (2–4s) between requests; cache descriptions; cap queries/run.
- Frame the project around the assessor + eval (the real value), not the scraper — the source is one swappable module.

"Matching the profile" in the MVP = a fixed query list derived from the role taxonomy, not a personalized LinkedIn feed.

---

## Architecture

```
jobscout run   (manual trigger, or the local web app's Run buttons)
  └─ fetch.py      queries → Source adapter → raw postings + descriptions
  └─ db.py         dedupe by (source, external_id); upsert into SQLite
  └─ assess.py     for each NEW posting: LLM (profile pack + rubric + JD) → verdict JSON → store
  └─ report.py     query DB → render <output_dir>/jds/daily/<date>.md
  └─ (review, then git commit the daily file in the private repo)
```

- **Language:** Python; frontend in React + Vite.
- **State:** SQLite (`jobscout.db`), gitignored.
- **Source:** pluggable `Source` interface; adapters `linkedin_guest` (primary) and `jsearch` (fallback/demo).
- **LLM:** Claude API, structured output (tool/JSON schema), temperature 0.
- **Config:** `config.yaml` — query groups, locations, active source, model, request delay.
- **Secrets:** `.env` (`ANTHROPIC_API_KEY`, `RAPIDAPI_KEY` if using jsearch).

### Query groups (from the role taxonomy)
Grouped by target area (A/B/C/D), each group a small set of `keywords` strings.
See `config.example.yaml` for the shipped defaults.

---

## Data model (SQLite)

```
postings(
  id, source, external_id, url, title, company, location, remote,
  description, comp_raw, posted_at, first_seen_at, last_seen_at,
  raw_json
)
assessments(
  posting_id, area, target_quality, chance, verdict,
  overall, keep_de_titles(bool), gaps_hit(json), model, assessed_at
)
status(            -- my manual overrides, so the tool doesn't re-surface handled ones
  posting_id, state,           -- new | shortlisted | applied | passed | ignored
  note, updated_at
)
```

---

## The assessor

**Profile pack** (assembled once per run, passed in the prompt): condensed pull from the private
career repo — roles + skills, tech gaps to detect, the role taxonomy (A/B/C/D + read-the-JD), the
trajectory bar + comp baseline, the current job-search mode.

**Prompt:** profile pack + the full `assessment-guide.md` rubric + one JD → returns:
```json
{
  "area": "A|B|C|D",
  "target_quality": "high|med|low",
  "chance": "high|med|low",
  "verdict": "pursue|maybe|skip",
  "overall": "one line",
  "keep_de_titles": true,
  "gaps_hit": ["kubernetes", "flink", ...],
  "comp_vs_baseline": "above|within|below|unknown"
}
```
Deterministic (temp 0), JSON schema enforced. One JD per call (keeps it debuggable + cheap).

---

## Output — `jds/daily/YYYY-MM-DD.md`

```markdown
# Jobscout — 2026-09-10   (new: 14 · pursue: 2 · maybe: 3 · skip: 9)

## 🟢 Pursue
### Company — Role  ·  A · quality High · chance Med
<one-line reasoning> · <comp> · <location> · [link]

## 🟡 Maybe
...

## Skipped (9) — <company/role · one-clause reason each, collapsed>
```

Committed to the repo → shows up in my normal review flow, diffable day to day.

---

## Running it

**Manual trigger.** Either:
- `jobscout serve` → the local web app (Dashboard has per-group Run buttons, live progress).
- `jobscout fetch` / (later) `jobscout run` on the CLI.

No scheduler to babysit, and it sidesteps the cloud-IP block on the guest source (which needs a
residential IP).

**Later (optional):** local `cron` / `launchd` for a daily auto-run; or GitHub Actions **only**
with the `jsearch` source (guest endpoint is blocked from cloud IPs).

---

## Eval (the "does it work" measure)

A set of **hand-scored JDs** (`assessments.csv` + `processed/*.md` in the private career repo) is
the labeled set.

- `eval.py` runs the assessor over them, compares `verdict` and `area` to the labels.
- Metrics: verdict exact-match %, pursue/skip confusion matrix, area accuracy. Target for MVP: **≥12/15 verdicts match, no pursue↔skip flips** (med↔pursue drift is tolerable).
- Re-run on any prompt change. This is the project's rigor story.

---

## Roadmap

The overall timeline. Each step is small enough to finish in a sitting; the **current step gets
its own detailed plan file** (e.g. `ASSESSOR.md`), which this file links to.

| # | Step | State |
|---|---|---|
| 1 | Skeleton, `config.yaml`, SQLite schema, `Source` interface, `linkedin_guest.fetch()` | ✅ done |
| 2 | `fetch_description()` (N+1); dedupe/upsert; all query groups; polite delay; `runs` tracking | ✅ done |
| — | *(ahead of plan)* local web app — FastAPI + React SPA: Dashboard / Runs / Companies / Postings, per-group runs, cards/descriptions phase split, filters, star/seen/status | ✅ done |
| 3 | **The assessor** — `assess.py`: profile-pack builder + Claude call + schema + store → **see `ASSESSOR.md`** | ⏳ current |
| 4 | `eval.py` against the labeled JDs; tune the prompt until it clears the bar; commit `eval-results.md` | next |
| 5 | `report.py` → daily markdown; wire `status` overrides into the report | next |
| 6 | `jobscout run` end-to-end (fetch → assess → report); `jsearch` adapter as the cloud-safe fallback | next |
| 7 | README (architecture diagram + eval numbers); `profile.example/`; `eval/` fixture; tests (`db` dedupe, `report` rendering, `config`); `launchd` example | next |

Open review items carried forward (from a code-review pass):
- No cross-process lock — a CLI `jobscout fetch` and a web-triggered run can hit LinkedIn at once.
- A partial-group run currently advances the global "last run" window; should be per-group.
- No tests yet.

---

## Out of scope for MVP (parking lot)

- Personalized LinkedIn feed / logged-in scraping
- Multi-source + cross-source dedupe (same job on LinkedIn + Greenhouse)
- Feedback loop: mark good/bad → few-shot examples or threshold tuning
- Auto-generate a tailored resume draft for `pursue` hits
- Comp inference when the posting omits it
- Company-health / Glassdoor signal in the trajectory score
- Per-company rating that actually feeds the assessor (the `companies.rating` column exists; unused)
