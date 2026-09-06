import { useCallback, useEffect, useRef, useState } from "react";
import { api, groupsLabel, Run, windowLabel } from "../api";

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

function progressPct(run: Run): number {
  if (run.status && run.status !== "running") return 100;
  const n = run.note ?? "";
  const d = n.match(/descriptions (\d+)\/(\d+)/);
  const s = n.match(/searching (\d+)\/(\d+)/);
  if (d) return 50 + (50 * +d[1]) / Math.max(1, +d[2]);
  if (s) return (50 * +s[1]) / Math.max(1, +s[2]);
  return 3;
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
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number>();

  const refresh = useCallback(async () => {
    try {
      setRuns(await api.runs(50));
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
            <th>Unique / New</th>
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
                {r.n_unique} / <b>{r.n_new}</b>
                {r.status === "running" && (
                  <div className="progress" style={{ marginTop: 4 }}>
                    <div style={{ width: `${progressPct(r)}%` }} />
                  </div>
                )}
                {r.note && (
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
                ) : (
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
