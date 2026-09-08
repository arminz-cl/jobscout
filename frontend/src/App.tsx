import { useEffect, useRef, useState } from "react";
import { api, RunnerState, seedGroupTitles } from "./api";
import { Dashboard } from "./components/Dashboard";
import { Postings } from "./components/Postings";
import { Runs } from "./components/Runs";
import { Companies } from "./components/Companies";
import { Resumes } from "./components/Resumes";
import { Info } from "./components/Info";
import { ResumeBuilder } from "./components/ResumeBuilder";

type Tab = "dashboard" | "runs" | "companies" | "postings" | "assessments" | "resumes" | "info";
const TABS: Tab[] = ["dashboard", "runs", "companies", "postings", "assessments", "resumes", "info"];
const hashTab = (): Tab => {
  const h = window.location.hash.replace("#", "") as Tab;
  return TABS.includes(h) ? h : "dashboard";
};
const hashBuilder = (): number | null => {
  const m = window.location.hash.match(/^#builder\/(\d+)$/);
  return m ? Number(m[1]) : null;
};

export function App() {
  const [tab, setTabState] = useState<Tab>(hashTab);
  const [builder, setBuilder] = useState<number | null>(hashBuilder);
  const setTab = (t: Tab) => {
    window.location.hash = t;
    setTabState(t);
  };

  useEffect(() => {
    const onHash = () => {
      setBuilder(hashBuilder());
      setTabState(hashTab());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const [err, setErr] = useState<string | null>(null);
  const [runFilter, setRunFilter] = useState<number | null>(null);
  const [companyFilter, setCompanyFilter] = useState<string | null>(null);
  const [rs, setRs] = useState<RunnerState | null>(null);
  const poll = useRef<number>();

  useEffect(() => {
    api.queryGroups().then(seedGroupTitles).catch(() => setErr("failed to load"));
    const tick = () => api.runner().then(setRs).catch(() => {});
    tick();
    poll.current = window.setInterval(tick, 2500);
    return () => window.clearInterval(poll.current);
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
        <ActivityPill rs={rs} />
        <nav className="tabs">
          {TABS.map((t) => (
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
        {builder != null ? (
          <ResumeBuilder
            postingId={builder}
            onClose={() => {
              window.location.hash = "postings";
            }}
          />
        ) : (
          <>
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
        {tab === "info" && <Info />}
          </>
        )}
      </main>
    </>
  );
}

function ActivityPill({ rs }: { rs: RunnerState | null }) {
  const [, setTick] = useState(0);
  const recvRef = useRef<{ key: string; at: number }>({ key: "", at: 0 });
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);

  if (!rs) return null;
  let a = rs.activity || "idle";
  const failed = /failed/i.test(a);
  const stopped = a === "stopped";

  const key = `${a}|${rs.activity_age}`;
  if (recvRef.current.key !== key) recvRef.current = { key, at: Date.now() };

  const m = a.match(/next call in (\d+)s/);
  const rateLimited = !!m || /rate-limited/i.test(a);
  if (m) {
    const localElapsed = (Date.now() - recvRef.current.at) / 1000;
    const left = Math.max(0, Math.round(+m[1] - (rs.activity_age ?? 0) - localElapsed));
    a = a.replace(/next call in \d+s/, `next call in ${left}s`);
  }

  const active = rs.busy || (a !== "idle" && !stopped && !failed);
  const color = failed
    ? "var(--danger)"
    : rateLimited
      ? "var(--maybe)"
      : active
        ? "var(--accent)"
        : "var(--muted)";

  return (
    <span
      className="pill"
      title={rs.activity_age != null ? `set ${rs.activity_age}s ago` : ""}
      style={{ color, marginLeft: 14, display: "inline-flex", gap: 6, alignItems: "center" }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: color,
          animation: active && !rateLimited ? "pulse 1.2s ease-in-out infinite" : "none",
        }}
      />
      {rs.busy && rs.current_run_id ? `#${rs.current_run_id} · ` : ""}
      {a}
    </span>
  );
}
