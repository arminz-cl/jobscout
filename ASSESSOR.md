# The assessor — design (for review)

Status: **not built.** This is the plan for `src/jobscout/assess.py` (Phase 3) and the eval
harness (Phase 4). Review and mark it up before I implement.

---

## What it does

One job posting in → one structured verdict out.

```
inputs:  profile pack  +  rubric  +  one JD (title, company, location, description)
             │
             ▼
        Claude API call  (structured output, one JD per call)
             │
             ▼
output:  { area, target_quality, chance, verdict, overall, gaps_hit,
           comp_vs_baseline, keep_de_titles }   → stored in the `assessments` table
```

The verdict schema mirrors `assessment-guide.md` in the private career repo — the assessor is
that rubric, executed.

---

## The three inputs

### 1. Profile pack (built once per run, cached)

A condensed digest assembled from the private career repo (`profile_dir`):

| Source file | What's pulled |
|---|---|
| `experience-raw.md` | roles + skills inventory (the "what I've done" side) |
| `gaps.md` | the JD-tech gaps to detect (Kubernetes, Terraform, Flink, Go/Scala, recent LLM…) |
| `target-areas.md` | the A/B/C/D taxonomy + the "read the JD, not the title" rule |
| `evaluating-offers.md` | the trajectory bar + comp baseline |
| `intention-modes.md` | current mode (casual / intentional / aggressive) — changes the verdict rule |

Assembled into one text block (~3–5k tokens), built once at the start of a run and reused for
every JD. **Not** re-read per posting.

### 2. Rubric

`jds/assessment-guide.md` verbatim (~1.5k tokens) — the scoring process, the `target_quality` /
`chance` scales, and the verdict rule.

### 3. One JD

`title · company · location · description` for the single posting being assessed. ~1–3k tokens.

---

## The LLM call — pluggable provider

`assess.py` calls a thin `LLMClient` interface (`assess(system, user, schema) -> dict`) with three
implementations, chosen by `config.yaml` → `assessor.provider`:

| provider | for | model example | notes |
|---|---|---|---|
| **`openai_compat`** | **now — free** | `llama-3.3-70b-versatile` via Groq | Groq / Gemini / OpenRouter free tiers, OpenAI-style `/chat/completions`. Free key in `.env` as `ASSESSOR_API_KEY`. **Default while developing.** |
| `claude` | the eventual target | `claude-sonnet-5` / `claude-haiku-4-5` | `anthropic` SDK, forced tool call + `strict: true`, prompt caching on the profile-pack prefix (~90% off). Paid (~$0.05–0.15/100). Anthropic doesn't train on API data. |
| `ollama` | fully local | `qwen2.5:7b` | `http://localhost:11434`, no key, offline. Weaker (7–8B) — kept as an option, not the plan. |

Decision: **start on Groq (`openai_compat`, Llama 3.3 70B).** Free, 70B ≈ good judgment on the
rubric, no local setup. The profile pack (personal career data) does leave the machine — accepted.
Flip to `claude` later; the eval will quantify what that buys.

- **Structured output:** `response_format: {type: "json_object"}` + the schema spelled out in the
  system prompt + Pydantic validation + one retry on a parse/validation failure. Works across all
  three providers. (The `claude` path can additionally use a forced tool call for a hard guarantee.)
- **Determinism:** approximate. `temperature: 0` where the provider accepts it (Groq/Ollama do;
  Sonnet 5 / Opus 5 reject sampling params — 400). The rubric + rigid schema + the eval harness are
  what keep verdicts stable; the eval catches drift on any prompt or model change.
- **Prompt caching:** only meaningful on `claude` (identical `profile pack + rubric` prefix →
  `cache_control: {type: "ephemeral"}`). The free providers re-send the prefix each call — fine
  when it's free.
- **One JD per call.** Debuggable, cheap, parallelizable.

### `config.yaml`

```yaml
assessor:
  provider: openai_compat
  model: "llama-3.3-70b-versatile"
  base_url: "https://api.groq.com/openai/v1"
  temperature: 0
# .env:  ASSESSOR_API_KEY=gsk_...      (Groq key; ANTHROPIC_API_KEY when provider: claude)
```

### Output schema

```json
{
  "area":             "A | B | C | D",
  "area_reason":      "one clause — why this area, per read-the-JD",
  "target_quality":   "high | med | low",
  "chance":           "high | med | low",
  "verdict":          "pursue | maybe | skip",
  "overall":          "one line capturing the reasoning",
  "gaps_hit":         ["kubernetes", "terraform", ...],
  "comp_vs_baseline": "above | within | below | unknown",
  "keep_de_titles":   true
}
```

`keep_de_titles` = the rubric's "for this application, keep the literal 'Data Engineer' titles on
the résumé" signal (true when the JD values DE experience positively).

---

## Cost (rough, per 100 assessments)

Per call ≈ 6k tokens in (profile pack + rubric, cached after the first) + 2k JD (uncached) + 0.3k out.

| Model | ~$ / 100 assessments (with prefix caching) |
|---|---|
| `claude-haiku-4-5` | ~$0.05 |
| `claude-sonnet-5` | ~$0.15 |
| `claude-opus-5` | ~$0.40 |

Batch API (`/v1/messages/batches`) halves it again and is the right tool for a large first
backfill — async, results keyed by `custom_id`. Live/interactive assessment (the "assess now"
button) uses a normal call.

---

## Where it runs

| Trigger | Function | Scope |
|---|---|---|
| After a fetch run (opt-in) | `assess_new(conn, cfg, run_id)` | every posting with a description and no assessment |
| "Assess" button on a posting | `assess_posting(conn, cfg, posting_id)` | that one |
| Eval | `eval.py` | the labeled JD set |

- **Needs a description.** Postings without a JD are skipped (or the description is fetched first —
  open question below).
- **Re-assess** overwrites the row (`assessments` has `UNIQUE(posting_id)`).

---

## Storage

```
assessments(
  posting_id, area, area_reason, target_quality, chance, verdict, overall,
  keep_de_titles, gaps_hit(json), comp_vs_baseline, model, assessed_at
)
```

- Add `area_reason` (migration).
- **`model` is the full identifier** — `groq/llama-3.3-70b-versatile`, `claude-sonnet-5`,
  `ollama/qwen2.5:7b`. Surfaced in the UI as a **tag/pill** on every assessed row (Assessments
  table + posting drawer) so you can always see what produced a verdict and spot stale ones after
  switching models.

---

## Eval harness (Phase 4)

`eval.py` — the "does it actually work" measure.

- Reads the labeled set from `eval_dir` (`assessments.csv` + `processed/*.md`).
- Runs `assess()` on each JD, compares `verdict` and `area` to the hand labels.
- Reports:
  - verdict exact-match %
  - pursue / maybe / skip confusion matrix
  - area accuracy
  - a per-JD diff (which ones flipped, and the model's `overall` for each)
- **Bar:** ≥ 12/15 verdicts match, **zero pursue↔skip flips** (med↔pursue drift tolerated).
- Re-run on every prompt change (profile-pack wording, rubric emphasis, model).

This is the project's rigor story — an LLM app with a real, versioned eval.

---

## Decisions

- **Provider:** `openai_compat` → Groq, Llama 3.3 70B. Free. (settled)
- **Model tag:** stored as the full id, shown as a pill in the UI. (settled)
- **Trigger:** opt-in "+ assess" checkbox next to Run, **plus** a standalone "Assess N pending"
  button — mirrors the cards/descriptions split. Never auto-assesses a big backlog silently.
- **No description:** skip. `assess_pending` only touches postings that have a JD. (Fetch/backfill
  the description first; the drawer's "assess" button fetches it inline if missing.)
- **Batch:** sequential for now — Groq is free and fast, and the Batch API is Anthropic-only.
- **Mode:** read from jobscout's own `config.yaml` `mode:` field (already there); the private
  `intention-modes.md` is the source of truth the user mirrors into it.
- **Re-assess:** on demand only — a "re-assess" button per posting, and a "re-assess all" action.
  Never automatic on a prompt change (would silently churn cost/verdicts).
- **Profile pack:** rebuilt every run from `profile_dir` (always current); cached in-process for
  the duration of one run so it's read once, not per JD.

---

## Build steps

1. `assess.py`: `build_profile_pack()` + `assess_posting()` + `assess_new()`; schema + cache_control;
   store. Run on ~5 real postings, eyeball.
2. `POST /api/postings/{id}/assess` → wire to `assess_posting` (currently 501).
3. `POST /api/runs` `phase: "assess"` (or a `assess: true` flag) → `assess_new` after a fetch.
4. `eval.py` + `jobscout eval` CLI; tune the prompt until it clears the bar; commit `eval-results.md`.
5. Assessments tab: real data, verdict grouping, the `area_reason` on hover.
