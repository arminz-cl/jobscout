# jobscout

A personal CLI + local web app that fetches job postings matching a profile, scores each with an
LLM against a rubric, and writes a ranked daily shortlist. See `PLAN.md` for the MVP plan.

## Context lives in a separate (private) repo

The **profile, the assessment rubric, and the eval labels** are personal and live outside this repo,
in a private "career repo" on the user's machine:

`~/path/to/career-repo/`  (real path set in the gitignored `config.yaml`)

Files the assessor reads for context (names may vary):
- `experience-raw.md` — full work history + skills (the profile source)
- `gaps.md` — skill gaps to detect in a JD
- `target-areas.md` — the role taxonomy (A/B/C/D) + "read the JD, not the title"
- `evaluating-offers.md` — the trajectory bar + comp baseline
- `intention-modes.md` — current job-search mode
- `jds/assessment-guide.md` — **the scoring rubric the assessor implements**
- `jds/assessments.csv` + `jds/processed/*.md` — hand-scored JDs = the eval set

That repo is **private — never copy its content into this repo.** jobscout reads it at runtime via a
gitignored `config.yaml` (`profile_dir`, `eval_dir`, `output_dir`). For anything committed here
(README, `config.example.yaml`, `profile.example/`, `eval/` fixtures) use **sanitized placeholders**
— fake company names, no real comp numbers, no personal commentary.

## Layout

- `src/jobscout/` — the engine: `config`, `sources/` (pluggable job sources), `db` (SQLite),
  `fetch` (orchestration), `models`, `cli`
- `src/jobscout/web/` — FastAPI backend + in-process run runner; serves the built SPA
- `frontend/` — React + Vite SPA (`npm run build` → `src/jobscout/web/static/`)
- `config.example.yaml` — copy to `config.yaml` (gitignored) and fill in local paths + secrets

## Status

Fetch pipeline + local web app (Dashboard / Runs / Companies / Postings) working. The LLM assessor
(`assess.py`) is not built yet — `/assess` endpoints return 501.
