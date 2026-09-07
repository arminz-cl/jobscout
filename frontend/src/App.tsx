import { useEffect, useState } from "react";
import { api, AppConfig, seedGroupTitles, windowLabel } from "./api";
import { Dashboard } from "./components/Dashboard";
import { Postings } from "./components/Postings";
import { Runs } from "./components/Runs";
import { Companies } from "./components/Companies";
import { Resumes } from "./components/Resumes";

type Tab = "dashboard" | "runs" | "companies" | "postings" | "assessments" | "resumes";
const TABS: Tab[] = ["dashboard", "runs", "companies", "postings", "assessments", "resumes"];
const hashTab = (): Tab => {
  const h = window.location.hash.replace("#", "") as Tab;
  return TABS.includes(h) ? h : "dashboard";
};

export function App() {
  const [tab, setTabState] = useState<Tab>(hashTab);
  const setTab = (t: Tab) => {
    window.location.hash = t;
    setTabState(t);
  };
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runFilter, setRunFilter] = useState<number | null>(null);
  const [companyFilter, setCompanyFilter] = useState<string | null>(null);

  useEffect(() => {
    api.config().then(setCfg).catch((e) => setErr(String(e.message)));
    api.queryGroups().then(seedGroupTitles).catch(() => {});
  }, []);

  const openRun = (id: number) => {
    setCompanyFilter(null);
    setRunFilter(id);
    setTab("postings");
  };
  const openCompany = (name: string) => {
    setRunFilter(null);
    setCompanyFilter(name);
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
          {(["dashboard", "runs", "companies", "postings", "assessments"] as Tab[]).map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => {
                setTab(t);
                if (t !== "postings") {
                  setRunFilter(null);
                  setCompanyFilter(null);
                }
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
        {tab === "companies" && <Companies onOpenCompany={openCompany} />}
        {tab === "postings" && (
          <Postings
            mode="all"
            runId={runFilter}
            initialCompany={companyFilter}
            onClearRun={() => setRunFilter(null)}
          />
        )}
        {tab === "assessments" && <Postings mode="assessed" />}
        {tab === "resumes" && <Resumes />}
      </main>
    </>
  );
}
