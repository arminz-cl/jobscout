import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  Facets,
  groupsLabel,
  QueryGroup,
  Run,
  RunnerState,
  seedGroupTitles,
  windowLabel,
} from "../api";

const SINCE_OPTIONS = [
  { value: "", label: "auto (from last run)" },
  { value: "24h", label: "last 24h" },
  { value: "7d", label: "last 7 days" },
  { value: "30d", label: "last 30 days" },
];

export function Dashboard() {
  const [groups, setGroups] = useState<QueryGroup[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runner, setRunner] = useState<RunnerState | null>(null);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [since, setSince] = useState("");
  const [withDesc, setWithDesc] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number>();

  const refresh = useCallback(async () => {
    try {
      const [g, r, rn, f] = await Promise.all([
        api.queryGroups(),
        api.runs(12),
        api.runner(),
        api.facets(),
      ]);
      seedGroupTitles(g);
      setGroups(g);
      setRuns(r);
      setRunner(rn);
      setFacets(f);
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // poll while a run is active
  useEffect(() => {
    const active = runner?.busy || runs.some((r) => r.status === "running");
    window.clearInterval(timer.current);
    if (active) timer.current = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer.current);
  }, [runner?.busy, runs, refresh]);

  const start = async (groupNames: string[] | null) => {
    try {
      await api.startRun({
        groups: groupNames,
        since: since || null,
        phase: withDesc ? "full" : "cards",
      });
      await refresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const backfill = async () => {
    try {
      await api.startRun({ phase: "descriptions" });
      await refresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const busy = runner?.busy ?? false;
  const activeGroups = groups.filter((g) => g.active).map((g) => g.name);
  const missingDesc = facets ? facets.without_description : 0;

  return (
    <div className="grid cols-2">
      <div className="panel">
        <h2>Query groups</h2>
        <div className="runbar">
          <label className="muted">window</label>
          <select value={since} onChange={(e) => setSince(e.target.value)}>
            {SINCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <label className="muted" style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={withDesc}
              onChange={(e) => setWithDesc(e.target.checked)}
            />
            + descriptions
          </label>
          <button className="primary" disabled={busy} onClick={() => start(null)}>
            Run all active ({activeGroups.join(" + ")})
          </button>
        </div>
        <div className="runbar">
          <button disabled={busy || missingDesc === 0} onClick={backfill}>
            Backfill descriptions ({missingDesc} missing)
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            core attributes are cheap & dedup-safe; descriptions are one request per posting
          </span>
        </div>
        {err && <div className="error">{err}</div>}

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
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                {g.summary}
              </div>
            )}
            <ul>
              {g.queries.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
            <div className="group-actions">
              <button disabled={busy} onClick={() => start([g.name])}>
                Run
              </button>
            </div>
          </div>
        ))}
      </div>

      <div>
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2>Runs</h2>
          {runner?.error && <div className="error">last run error: {runner.error}</div>}
          {runs.length === 0 && <div className="empty">No runs yet.</div>}
          {runs.map((r) => (
            <RunRow key={r.id} run={r} />
          ))}
        </div>
      </div>
    </div>
  );
}

function RunRow({ run }: { run: Run }) {
  const running = run.status === "running";
  // rough progress: 16 searches then descriptions; use the note text
  const note = run.note ?? "";
  let pct = 0;
  const sm = note.match(/searching (\d+)\/(\d+)/);
  const dm = note.match(/descriptions (\d+)\/(\d+)/);
  if (dm) pct = 50 + (50 * +dm[1]) / Math.max(1, +dm[2]);
  else if (sm) pct = (50 * +sm[1]) / Math.max(1, +sm[2]);
  else if (!running) pct = 100;

  return (
    <div style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <b>#{run.id}</b>
        <span className="pill">{groupsLabel(run.groups)}</span>
        <span className="muted">{windowLabel(run.time_posted_used)}</span>
        <span
          className="pill"
          style={{
            color:
              run.status === "ok"
                ? "var(--pursue)"
                : run.status === "failed"
                  ? "var(--danger)"
                  : run.status === "partial"
                    ? "var(--maybe)"
                    : "var(--muted)",
          }}
        >
          {run.status ?? "…"}
        </span>
        <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
          {new Date(run.started_at).toLocaleTimeString()}
        </span>
      </div>
      {running && (
        <div className="progress">
          <div style={{ width: `${pct}%` }} />
        </div>
      )}
      <div className="muted" style={{ fontSize: 12 }}>
        {run.n_unique} unique · {run.n_new} new · {run.n_assessed} assessed
        {note && ` · ${note}`}
      </div>
    </div>
  );
}
