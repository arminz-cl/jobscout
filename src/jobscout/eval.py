"""Run the deep assessor over the hand-labelled JD set and score it.

Labels: <eval_dir>/assessments.csv  ·  JD text: <eval_dir>/processed/<file>
Reports verdict accuracy, the pursue/maybe/skip confusion matrix, area accuracy,
and a per-JD diff. This is the "is the assessor any good" measure — re-run it
after every prompt/model change.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .assess import AssessError, deep_one
from .config import Config, load

_VERDICTS = ["pursue", "maybe", "skip"]
# human chance/quality use hyphenated in-betweens; fold to 3 buckets
_TIER = {
    "high": "high", "med-high": "high",
    "med": "med", "low-med": "med",
    "low": "low", "very-low": "low",
}
# model 0-100 score -> tier, for comparison with the human high/med/low
def _score_tier(n: int | None) -> str:
    if n is None:
        return "?"
    return "high" if n >= 70 else "med" if n >= 45 else "low"


def _load_labels(eval_dir: Path) -> list[dict]:
    csv_path = eval_dir / "assessments.csv"
    if not csv_path.exists():
        raise AssessError(f"no assessments.csv in {eval_dir}")
    rows = []
    with csv_path.open() as f:
        for r in csv.DictReader(f):
            jd = eval_dir / "processed" / r["file"]
            if not jd.exists():
                print(f"  ! missing JD: {r['file']}")
                continue
            rows.append({**r, "jd_text": jd.read_text()})
    return rows


def run_eval(
    cfg: Config,
    *,
    limit: int | None = None,
    verbose: bool = True,
    progress=None,
    cancel=None,
) -> dict:
    labels = _load_labels(cfg.eval_dir)
    if limit:
        labels = labels[:limit]

    results = []
    confusion = {v: {w: 0 for w in _VERDICTS} for v in _VERDICTS}
    verdict_hits = area_hits = quality_hits = chance_hits = 0
    flips = 0  # pursue<->skip

    for done, lab in enumerate(labels):
        if cancel and cancel.is_set():
            break
        if progress:
            acc = verdict_hits / done if done else 0
            progress(done, len(labels), f"eval {done}/{len(labels)} · verdict {acc:.0%}")
        try:
            got = deep_one(
                cfg,
                title=lab["role"],
                company=lab["company"],
                location="",
                description=lab["jd_text"],
            )
        except AssessError as e:
            print(f"  ! {lab['file']}: {e}")
            continue

        human_v = lab["verdict"].strip()
        got_v = got["verdict"]
        confusion[human_v][got_v] += 1
        v_ok = got_v == human_v
        verdict_hits += v_ok
        if {human_v, got_v} == {"pursue", "skip"}:
            flips += 1

        human_areas = {a.strip() for a in lab["area"].replace("/", " ").split()}
        a_ok = got["area"] in human_areas
        area_hits += a_ok

        got_q = got.get("target_quality") or _score_tier(got["score_quality"])
        got_c = got.get("chance") or _score_tier(got["score_chance"])
        q_ok = got_q == _TIER.get(lab["target_quality"].strip(), "?")
        c_ok = got_c == _TIER.get(lab["chance"].strip(), "?")
        quality_hits += q_ok
        chance_hits += c_ok

        results.append(
            {
                "file": lab["file"],
                "human": f"{lab['area']:4} q:{lab['target_quality']:8} c:{lab['chance']:9} {human_v}",
                "model": f"{got['area']:4} q:{got_q:5} c:{got_c:5} {got_v}",
                "v_ok": v_ok, "a_ok": a_ok, "q_ok": q_ok, "c_ok": c_ok,
                "overall": got["overall"],
            }
        )
        if verbose:
            mark = "OK " if v_ok else "XX "
            print(f"  {mark}{lab['file'][:38]:38}  human {human_v:6} -> model {got_v:6}  "
                  f"area {'ok' if a_ok else 'MISS'}")

    n = len(results)
    summary = {
        "n": n,
        "verdict_acc": round(verdict_hits / n, 3) if n else 0,
        "area_acc": round(area_hits / n, 3) if n else 0,
        "quality_acc": round(quality_hits / n, 3) if n else 0,
        "chance_acc": round(chance_hits / n, 3) if n else 0,
        "pursue_skip_flips": flips,
        "confusion": confusion,
        "results": results,
        "model": cfg.assessor.model_tag if cfg.assessor else "?",
    }

    if verbose:
        print(f"\n  model: {summary['model']}   n={n}")
        print(f"  verdict {summary['verdict_acc']:.0%}   area {summary['area_acc']:.0%}   "
              f"quality {summary['quality_acc']:.0%}   chance {summary['chance_acc']:.0%}   "
              f"pursue<->skip flips: {flips}")
        print("\n  confusion (rows = human, cols = model):")
        print("            " + "  ".join(f"{v:>6}" for v in _VERDICTS))
        for hv in _VERDICTS:
            print(f"    {hv:8}" + "  ".join(f"{confusion[hv][mv]:>6}" for mv in _VERDICTS))
        print(f"\n  bar: verdict >= 80%, zero pursue<->skip flips  "
              f"-> {'PASS' if summary['verdict_acc'] >= 0.8 and flips == 0 else 'FAIL'}")

    return summary


def _connect_marker(cfg: Config) -> sqlite3.Connection:  # kept for symmetry; eval is DB-free
    from . import db

    return db.connect(cfg.db_path)


if __name__ == "__main__":
    run_eval(load())
