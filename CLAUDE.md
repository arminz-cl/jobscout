# jobscout

A personal CLI that fetches job postings matching my profile, scores each with an LLM against a
rubric, and writes a ranked daily shortlist. See `PLAN.md` for the full MVP plan.

## Context lives in a separate (private) repo

The **profile, the assessment rubric, and the eval labels** live in my private career repo:

`~/path/to/career-repo/`

Read these for context on what the assessor needs to do:
- `experience-raw.md` — my full work history + skills (the profile source)
- `gaps.md` — my skill gaps (esp. #8, the JD-tech gaps to detect)
- `target-areas.md` — the A/B/C/D role taxonomy + "read the JD not the title"
- `evaluating-offers.md` — the trajectory bar + comp baseline (the comp baseline)
- `intention-modes.md` — current job-search mode
- `jds/assessment-guide.md` — **the scoring rubric the assessor must implement**
- `jds/assessments/*.md` + `jds/assessments.csv` — 15 hand-scored JDs = the eval set
- `jds/processed/*.md` — the raw JD texts for those 15

That repo is **private — never copy its content into this repo**. This repo reads it at runtime via
a gitignored `config.yaml` (`profile_dir`, `eval_dir`, `output_dir` pointing at `~/path/to/career-repo`).
For anything committed here (README, `profile.example/`, `eval/` fixtures) use **sanitized placeholders**
— fake company names, no real comp numbers, no personal commentary.

## Status

Just initialized. Next: scaffold `src/`, `config.example.yaml`, `profile.example/` per `PLAN.md` build order.
