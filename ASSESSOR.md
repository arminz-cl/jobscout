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

## The API call

- **Model:** `claude-sonnet-5` (from `config.yaml`). Sonnet 5 is strong at the nuanced
  "SWE-on-data vs data-mover" judgment the rubric needs. Alternatives to validate against the
  eval: `claude-opus-5` (better judgment, 2.5× the price), `claude-haiku-4-5` (⅕ the price,
  worth trying for a rubric this structured).
- **Structured output:** a forced tool call — `tool_choice: {type: "tool", name: "record_assessment"}`
  with `strict: true` on the tool, so `tool_use.input` validates against the schema exactly.
- **No `temperature`.** Sonnet 5 / Opus 5 reject sampling params (400). Determinism is therefore
  *approximate* — same JD usually → same verdict, but not guaranteed. The rubric + rigid schema +
  the eval harness are what keep it stable; the eval catches drift on any prompt change.
- **Prompt caching:** `profile pack + rubric` is an identical prefix across every JD call in a
  run. Mark it `cache_control: {type: "ephemeral"}` → ~90% cheaper on that prefix after the
  first call. This is the single biggest cost lever for batch assessment.
- **One JD per call.** Keeps each call debuggable, cheap, cache-friendly, and parallelizable.

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

Already in the schema:

```
assessments(
  posting_id, area, target_quality, chance, verdict, overall,
  keep_de_titles, gaps_hit(json), comp_vs_baseline, model, assessed_at
)
```

Add `area_reason` to that table alongside `overall`.

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

## Open questions (for you)

1. **Auto-assess after every fetch, or only on an explicit action?**
   Leaning: opt-in per run (a checkbox next to "Run"), plus a standalone "Assess N pending" button —
   mirrors the descriptions split. Assessing 600 postings on the first run is a real spend.
2. **Model** — ship on `sonnet-5`, or eval `haiku-4-5` first and use it if it clears the bar?
   The rubric is structured enough that Haiku might be fine at ⅕ the cost.
3. **No description yet** — auto-fetch the JD before assessing, or skip and leave it for a backfill?
4. **Batch vs sequential** for `assess_new` — Batch API (50% cost, async, minutes of latency) for
   big backlogs, normal calls for small/interactive. Worth the two code paths?
5. **Re-assess policy** — when the rubric or profile changes, re-assess everything automatically,
   or only on demand (a "re-assess all" button)?
6. **`intention-modes.md` mode** — the verdict rule shifts by mode. Read the mode from the private
   repo at run time, or from jobscout's own `config.yaml` `mode:` field (which already exists)?
7. **Profile-pack freshness** — rebuild it every run (always current), or cache to disk and only
   rebuild when the source files change?

---

## Build steps

1. `assess.py`: `build_profile_pack()` + `assess_posting()` + `assess_new()`; schema + cache_control;
   store. Run on ~5 real postings, eyeball.
2. `POST /api/postings/{id}/assess` → wire to `assess_posting` (currently 501).
3. `POST /api/runs` `phase: "assess"` (or a `assess: true` flag) → `assess_new` after a fetch.
4. `eval.py` + `jobscout eval` CLI; tune the prompt until it clears the bar; commit `eval-results.md`.
5. Assessments tab: real data, verdict grouping, the `area_reason` on hover.
