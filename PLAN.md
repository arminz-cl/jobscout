# Jobscout — MVP plan

A tool I run on demand that pulls fresh job postings matching my profile, scores each one with the [[../../jds/assessment-guide]] rubric, and drops a ranked shortlist into this repo.

Doubles as **Portfolio Project 1** ([[../../gaps]] #1, #5): real source integration + LLM app + eval harness + a CLI.

## Repo split

- **Code → new public repo `jobscout`** (proposed: `~/Desktop/jobscout`, `arminz-cl`, start private → flip public once README + eval look good).
- **This (private) repo keeps:** this PLAN, the real profile docs, the daily output (`jds/daily/`), the real eval labels (`jds/assessments/`).
- **Link:** jobscout's `config.yaml` (gitignored) sets `profile_dir` + `output_dir` to point into this repo on my machine. It reads the real profile from here and writes the daily list back to `jds/daily/` — neither touches the public repo.
- **Public repo ships:** `src/`, `README.md` (neutral), `config.example.yaml`, `profile.example/` (sanitized), optional `eval/` fixture (JD text + verdict labels, company names → "Company A").
- `.gitignore` (public): `.env`, `jobscout.db`, `config.yaml`.

---

## MVP scope

**In:**
- Pull postings from **one** job source, for a fixed set of profile-derived queries.
- Dedupe against what's already been seen.
- Score each new posting with Claude against my profile + the rubric → `area / target_quality / chance / verdict / one-line reasoning`.
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
- Ship two adapters: **`linkedin_guest`** (the one I actually want) and **`jsearch`** (clean fallback / public-demo mode).
- **Run locally on cron** (not GitHub Actions) so the guest endpoint isn't hitting a blocked cloud IP. Polite delay (2–4s) between requests; cache descriptions; cap queries/run.
- Frame the project around the assessor + eval (the real value), not the scraper — the source is one swappable module.

"Matching my profile" in the MVP = a fixed query list derived from [[../../target-areas]], not a personalized LinkedIn feed.

---

## Architecture

```
jobscout run   (I trigger it manually when I want a fresh list)
  └─ fetch.py      queries → Source adapter → raw postings + descriptions (JSON)
  └─ store.py      dedupe by (source, external_id); upsert into SQLite
  └─ assess.py     for each NEW posting: Claude API (profile pack + rubric + JD) → verdict JSON → store
  └─ report.py     query DB → render <output_dir>/jds/daily/<date>.md
  └─ (I review, then git commit the daily file in the private repo)
```

- **Language:** Python.
- **State:** SQLite (`jobscout.db`), gitignored.
- **Source:** pluggable `Source` interface; adapters `linkedin_guest` (primary) and `jsearch` (fallback/demo).
- **LLM:** Claude API (`claude-sonnet-5`), structured output (tool/JSON schema), temperature 0.
- **Config:** `config.yaml` — queries, locations, active source, model, thresholds, request delay.
- **Secrets:** `.env` (`ANTHROPIC_API_KEY`, `RAPIDAPI_KEY` if using jsearch).

### Queries (MVP, from target-areas)
```
"data platform engineer"    | Toronto, Remote Canada
"software engineer data"     | Toronto, Remote Canada
"data infrastructure engineer"
"backend software engineer"  (filter later on data/systems signal)
"AI engineer" / "ML platform engineer" / "machine learning engineer"
```

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

**Profile pack** (assembled once per run, passed in the prompt): condensed pull from
[[../../experience-raw]] (roles + skills), [[../../gaps]] (#8 tech gaps), [[../../target-areas]] (A/B/C/D + read-the-JD),
[[../../evaluating-offers]] (trajectory bar + comp baseline), [[../../intention-modes]] (current mode).

**Prompt:** profile pack + the full [[../../jds/assessment-guide]] rubric + one JD → returns:
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

**MVP: manual trigger.** `jobscout run` (or `make run`) whenever I want a fresh list — no scheduler to build or babysit, and it sidesteps the cloud-IP block on the guest source. Add `--dry-run` (fetch + assess, don't write) and `--source jsearch` overrides.

**Later (optional):** local `cron` / `launchd` for a daily auto-run; or GitHub Actions **only** with the `jsearch` source (guest endpoint is blocked from cloud IPs).

---

## Eval (the "does it work" measure)

I have **15 hand-scored JDs** in `jds/assessments/` + `assessments.csv` — a labeled set.

- `eval.py` runs the assessor over those 15 JDs, compares `verdict` and `area` to my labels.
- Metrics: verdict exact-match %, pursue/skip confusion matrix, area accuracy. Target for MVP: **≥12/15 verdicts match, no pursue↔skip flips** (med↔pursue drift is tolerable).
- Re-run on any prompt change. This is the portfolio artifact's rigor story ([[../../gaps]] #5).

---

## Build order (~1 week of evenings)

1. **Day 1** — repo skeleton, `config.yaml`, SQLite schema, `Source` interface + `linkedin_guest.fetch()` for one query, print results.
2. **Day 2** — `linkedin_guest.fetch_description()` (the N+1); `store.py` dedupe/upsert; all queries; polite delay; normalize fields.
3. **Day 3** — `assess.py`: profile-pack builder + Claude call + JSON schema + store. Run on 5 real postings, eyeball.
4. **Day 4** — `eval.py` against the 15 labeled JDs; tune the prompt until it passes the bar.
5. **Day 5** — `report.py` → daily markdown; `status` overrides so handled jobs don't recur.
6. **Day 6** — `jobscout run` CLI entrypoint (`--dry-run`, `--source`); `jsearch` adapter as fallback; end-to-end run on real queries.
7. **Day 7** — README with architecture diagram + eval results; `profile.example/`; tests for `store` (dedupe) and `report` (rendering); tidy.

---

## Out of scope for MVP (parking lot)

- Personalized LinkedIn feed / logged-in scraping
- Multi-source + cross-source dedupe (same job on LinkedIn + Greenhouse)
- Feedback loop: I mark good/bad → few-shot examples or threshold tuning
- Auto-generate a tailored resume draft for `pursue` hits (ties into `resumes/_template`)
- Web dashboard
- Comp inference when the posting omits it
- Company-health / Glassdoor signal in the trajectory score
