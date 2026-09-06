import { useEffect, useState } from "react";
import { api, AppConfig, windowLabel } from "./api";
import { Dashboard } from "./components/Dashboard";
import { Postings } from "./components/Postings";
import { Runs } from "./components/Runs";

type Tab = "dashboard" | "runs" | "postings" | "assessments";

export function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runFilter, setRunFilter] = useState<number | null>(null);

  useEffect(() => {
    api.config().then(setCfg).catch((e) => setErr(String(e.message)));
  }, []);

  const openRun = (id: number) => {
    setRunFilter(id);
    setTab("postings");
  };

  return (
    <>
      <header className="topbar">
        <span className="brand">jobscout</span>
        {cfg && (
          <span className="meta">
            mode <b>{cfg.mode}</b> &nbsp;·&nbsp; {cfg.source} &nbsp;·&nbsp; {cfg.model} &nbsp;·&nbsp;
            next window <b>{windowLabel(cfg.resolved_window)}</b> &nbsp;·&nbsp;
            last run {cfg.last_run ? new Date(cfg.last_run).toLocaleString() : "never"}
          </span>
        )}
        <nav className="tabs">
          {(["dashboard", "runs", "postings", "assessments"] as Tab[]).map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => {
                setTab(t);
                if (t !== "postings") setRunFilter(null);
              }}
            >
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {err && <div className="error">{err}</div>}
        {tab === "dashboard" && <Dashboard />}
        {tab === "runs" && <Runs onOpenRun={openRun} />}
        {tab === "postings" && (
          <Postings mode="all" runId={runFilter} onClearRun={() => setRunFilter(null)} />
        )}
        {tab === "assessments" && <Postings mode="assessed" />}
      </main>
    </>
  );
}
