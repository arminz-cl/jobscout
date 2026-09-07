import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  AssessorInfo,
  Facets,
  groupsLabel,
  QueryGroup,
  Run,
  RunnerState,
  seedGroupTitles,
} from "../api";

const SINCE_OPTIONS = [
  { value: "", label: "auto (from last run)" },
  { value: "24h", label: "last 24h" },
  { value: "7d", label: "last 7 days" },
  { value: "30d", label: "last 30 days" },
];

const isAssess = (r: Run) => ["triage","deep"].includes(r.kind ?? "");

export function Dashboard() {
  const [groups, setGroups] = useState<QueryGroup[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runner, setRunner] = useState<RunnerState | null>(null);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [assessor, setAssessor] = useState<AssessorInfo | null>(null);
  const [since, setSince] = useState("");
  const [withDesc, setWithDesc] = useState(true);
  const [withAssess, setWithAssess] = useState(false);
  const [assessLimit, setAssessLimit] = useState<number | "">(20);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number>();

  const refresh = useCallback(async () => {
    try {
      const [g, r, rn, f, a] = await Promise.all([
        api.queryGroups(),
        api.runs(20),
        api.runner(),
        api.facets(),
        api.assessor(),
      ]);
      seedGroupTitles(g);
      setGroups(g);
      setRuns(r);
      setRunner(rn);
      setFacets(f);
      setAssessor(a);
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const active = runner?.busy || runs.some((r) => r.status === "running");
    window.clearInterval(timer.current);
    if (active) timer.current = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer.current);
  }, [runner?.busy, runs, refresh]);

  const call = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await refresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const limitOrNull = assessLimit === "" ? null : Number(assessLimit);

  const startFetch = (groupNames: string[] | null) =>
    call(() =>
      api.startRun({
        groups: groupNames,
        since: since || null,
        phase: withDesc ? "full" : "cards",
        then_assess: withAssess,
        assess_limit: withAssess ? limitOrNull : null,
      }),
    );

  const busy = runner?.busy ?? false;
  const activeGroups = groups.filter((g) => g.active).map((g) => g.name);
  const missingDesc = facets?.without_description ?? 0;
  const pending = assessor?.pending ?? 0;
  const fetchRuns = runs.filter((r) => !isAssess(r));
  const assessRuns = runs.filter(isAssess);

  return (
    <>
      {err && <div className="error">{err}</div>}

      {/* ── FETCH ─────────────────────────────────────────── */}
      <section className="panel" style={{ marginBottom: 20 }}>
        <h2>Fetch — collect postings</h2>
        <div className="runbar">
          <label className="muted">window</label>
          <select value={since} onChange={(e) => setSince(e.target.value)}>
            {SINCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <label className="chk">
            <input type="checkbox" checked={withDesc} onChange={(e) => setWithDesc(e.target.checked)} />
            + descriptions
          </label>
          <label className="chk">
            <input
              type="checkbox"
              checked={withAssess}
              disabled={!assessor?.configured}
              onChange={(e) => setWithAssess(e.target.checked)}
            />
            + assess
          </label>
          <button className="primary" disabled={busy} onClick={() => startFetch(null)}>
            Run all active ({activeGroups.join(" + ")})
          </button>
          <button disabled={busy || missingDesc === 0} onClick={() => call(() => api.startRun({ phase: "descriptions" }))}>
            Backfill descriptions ({missingDesc})
          </button>
        </div>

        <div className="grid cols-2">
          <div>
            {groups.map((g) => (
              <div className="group" key={g.name}>
                <div className="group-head">
                  <span className="name">{g.title}</span>
                  <span className={"badge" + (g.active ? " active" : "")}>
                    {g.active ? "in active mode" : "inactive"}
                  </span>
                  <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
                    {g.queries.length} queries
                  </span>
                </div>
                {g.summary && (
                  <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>{g.summary}</div>
                )}
                <ul>
                  {g.queries.map((q) => <li key={q}>{q}</li>)}
                </ul>
                <div className="group-actions">
                  <button disabled={busy} onClick={() => startFetch([g.name])}>Run</button>
                </div>
              </div>
            ))}
          </div>
          <RunList runs={fetchRuns} runner={runner} onRefresh={refresh} empty="No fetch runs yet." />
        </div>
      </section>

      {/* ── ASSESS ────────────────────────────────────────── */}
      <section className="panel">
        <h2>Assess — score postings against the rubric</h2>
        {!assessor?.configured ? (
          <div className="muted">No <code>assessor:</code> section in config.yaml.</div>
        ) : (
          <>
            <div className="runbar">
              <span className="pill">{assessor.model_tag}</span>
              {!assessor.has_key && (
                <span className="pill" style={{ color: "var(--danger)" }}>
                  no API key — set ASSESSOR_API_KEY in .env
                </span>
              )}
              <label className="chk">
                how many
                <input
                  type="number"
                  min={1}
                  placeholder="all"
                  value={assessLimit}
                  onChange={(e) =>
                    setAssessLimit(e.target.value === "" ? "" : Math.max(1, +e.target.value))
                  }
                  style={{ width: 64, padding: "4px 6px", font: "inherit" }}
                />
              </label>
              <button
                className="primary"
                disabled={busy || pending === 0 || !assessor.has_key}
                onClick={() =>
                  call(() => api.startRun({ phase: "triage", assess_limit: limitOrNull }))
                }
                title="batch: many JDs per request, coarse 0–100 scores"
              >
                Triage {limitOrNull === null ? `all ${pending}` : Math.min(limitOrNull, pending)}
              </button>
              <button
                disabled={busy || (assessor.deep_ready ?? 0) === 0 || !assessor.has_key}
                onClick={() =>
                  call(() => api.startRun({ phase: "deep", assess_limit: limitOrNull ?? 15 }))
                }
                title="deep: full rubric, one request per posting, highest score first"
              >
                Deep-assess top {limitOrNull ?? 15}
              </button>
            </div>
            <div className="grid cols-2">
              <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
                <b>Triage</b> batch-scores every posting with a description (0–100 overall / chance /
                quality). <b>Deep-assess</b> runs the full rubric on the top-scoring ones, one call
                each. Correct a single posting from its drawer.
                <br />
                level 0: {assessor.pending} · level 1 (triaged): {assessor.triaged} · level 2 (deep):{" "}
                {assessor.deep}
              </div>
              <RunList runs={assessRuns} runner={runner} onRefresh={refresh} empty="No assessment runs yet." />
            </div>
          </>
        )}
      </section>
    </>
  );
}

function RunList({
  runs,
  runner,
  onRefresh,
  empty,
}: {
  runs: Run[];
  runner: RunnerState | null;
  onRefresh: () => void;
  empty: string;
}) {
  const stop = async (id: number) => {
    await api.stopRun(id).catch(() => {});
    onRefresh();
  };
  if (runs.length === 0) return <div className="empty">{empty}</div>;
  return (
    <div>
      {runs.map((run) => {
        const running = run.status === "running";
        const note = run.note ?? "";
        let pct = running ? 3 : 100;
        const m =
          note.match(/descriptions (\d+)\/(\d+)/) ||
          note.match(/assessing (\d+)\/(\d+)/) ||
          note.match(/searching (\d+)\/(\d+)/);
        if (m) {
          const base = note.startsWith("descriptions") || note.startsWith("assessing") ? 0 : 0;
          pct = base + (100 * +m[1]) / Math.max(1, +m[2]);
        }
        return (
          <div key={run.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <b>#{run.id}</b>
              <span className="pill">{run.kind ?? "fetch"}</span>
              {run.groups && !["triage","deep"].includes(run.kind ?? "") && (
                <span className="muted" style={{ fontSize: 12 }}>{groupsLabel(run.groups)}</span>
              )}
              <span
                className="pill"
                style={{
                  color:
                    run.status === "ok" ? "var(--pursue)"
                    : run.status === "failed" ? "var(--danger)"
                    : run.status === "partial" || run.status === "stopped" ? "var(--maybe)"
                    : "var(--muted)",
                }}
              >
                {run.status ?? "…"}
              </span>
              {running && runner?.current_run_id === run.id && (
                <button style={{ fontSize: 11, padding: "1px 6px" }} onClick={() => stop(run.id)}>
                  ■ stop
                </button>
              )}
              <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
                {new Date(run.started_at).toLocaleTimeString()}
              </span>
            </div>
            {running && (
              <div className="progress"><div style={{ width: `${pct}%` }} /></div>
            )}
            <div className="muted" style={{ fontSize: 12 }}>
              {["triage","deep"].includes(run.kind ?? "")
                ? `${run.n_assessed} assessed`
                : `${run.n_unique} unique · ${run.n_new} new`}
              {run.n_assessed > 0 && !["triage","deep"].includes(run.kind ?? "") ? ` · ${run.n_assessed} assessed` : ""}
              {note && ` · ${note}`}
            </div>
          </div>
        );
      })}
    </div>
  );
}
