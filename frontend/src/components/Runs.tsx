import { useCallback, useEffect, useRef, useState } from "react";
import { api, groupsLabel, Run, RunnerState, windowLabel } from "../api";

const statusColor = (s: string | null) =>
  s === "ok"
    ? "var(--pursue)"
    : s === "failed"
      ? "var(--danger)"
      : s === "partial"
        ? "var(--maybe)"
        : s === "running"
          ? "var(--accent)"
          : "var(--muted)";

const isAssessKind = (k: string | null) => k === "triage" || k === "deep";

// the runner notes all look like "<phase> N/M ..." — just read the N/M
export function progressPct(run: Run): number {
  if (run.status && run.status !== "running") return 100;
  const m = (run.note ?? "").match(/(\d+)\s*\/\s*(\d+)/);
  if (m) return Math.max(3, Math.min(100, (100 * +m[1]) / Math.max(1, +m[2])));
  return 4;
}

function duration(run: Run): string {
  const start = new Date(run.started_at).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  const s = Math.round((end - start) / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function Runs({ onOpenRun }: { onOpenRun: (id: number) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [rs, setRs] = useState<RunnerState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number>();

  const refresh = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.runs(50), api.runner().catch(() => null)]);
      setRuns(r);
      setRs(s);
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  const stop = async (id: number) => {
    try {
      await api.stopRun(id);
      await refresh();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const active = runs.some((r) => r.status === "running" || !r.finished_at);
    window.clearInterval(timer.current);
    if (active) timer.current = window.setInterval(refresh, 2000);
    return () => window.clearInterval(timer.current);
  }, [runs, refresh]);

  return (
    <div className="panel">
      <h2>Runs — collection processes</h2>
      {err && <div className="error">{err}</div>}
      {runs.length === 0 && <div className="empty">No runs yet.</div>}
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Kind</th>
            <th>Groups</th>
            <th>Window</th>
            <th>Started</th>
            <th>Duration</th>
            <th>Result</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>
                <b>{r.id}</b>
              </td>
              <td className="muted">{r.kind ?? "fetch"}</td>
              <td>
                <span className="pill">{groupsLabel(r.groups) }</span>
              </td>
              <td className="muted">{windowLabel(r.time_posted_used)}</td>
              <td className="muted">{new Date(r.started_at).toLocaleString()}</td>
              <td className="muted">{duration(r)}</td>
              <td>
                {r.kind === "triage" ? (
                  <><b>{r.n_assessed}</b> triaged</>
                ) : r.kind === "deep" ? (
                  <><b>{r.n_assessed}</b> deep-assessed</>
                ) : r.kind === "backfill" ? (
                  <><b>{r.n_new}</b> descriptions</>
                ) : (
                  <>{r.n_unique} seen · <b>{r.n_new}</b> new</>
                )}
                {r.status === "running" && (
                  <div className="progress" style={{ marginTop: 4 }}>
                    <div style={{ width: `${progressPct(r)}%` }} />
                  </div>
                )}
                {r.status === "running" && rs?.current_run_id === r.id && (
                  <div className="muted" style={{ fontSize: 11 }}>
                    {(rs.wait_remaining ?? 0) > 0 ? (
                      <span style={{ color: "var(--maybe)" }}>⏳ retry in {rs.wait_remaining}s</span>
                    ) : (
                      rs.activity
                    )}
                  </div>
                )}
                {r.note && (r.status !== "running" || rs?.current_run_id !== r.id) && (
                  <div className="muted" style={{ fontSize: 11 }}>
                    {r.note}
                  </div>
                )}
              </td>
              <td>
                <span className="pill" style={{ color: statusColor(r.status) }}>
                  {r.status ?? "…"}
                </span>
              </td>
              <td style={{ whiteSpace: "nowrap" }}>
                {r.status === "running" ? (
                  <button
                    style={{ fontSize: 12, padding: "2px 8px", color: "var(--danger)" }}
                    onClick={() => stop(r.id)}
                  >
                    ■ stop
                  </button>
                ) : isAssessKind(r.kind) ? null : (
                  <button
                    style={{ fontSize: 12, padding: "2px 8px" }}
                    onClick={() => onOpenRun(r.id)}
                    disabled={!r.n_new}
                  >
                    {r.n_new} postings →
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
