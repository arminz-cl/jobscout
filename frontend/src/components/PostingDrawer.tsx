import { useEffect, useState } from "react";
import { api, AREA_NAMES, Posting } from "../api";

const STATES = ["new", "shortlisted", "applied", "passed", "ignored"];

export function PostingDrawer({
  id,
  onClose,
  onChanged,
}: {
  id: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [p, setP] = useState<Posting | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.posting(id).then(setP).catch((e) => setErr(e.message));
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const setStatus = async (state: string) => {
    await api.setStatus(id, state);
    setP((prev) => (prev ? { ...prev, status_state: state } : prev));
    onChanged();
  };

  const toggleStar = async () => {
    if (!p) return;
    const next = !p.starred;
    await api.setStarred(id, next);
    setP({ ...p, starred: next ? 1 : 0 });
    onChanged();
  };

  const assess = async () => {
    setBusy(true);
    setErr(null);
    try {
      setP(await api.assess(id));
      onChanged();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const gaps = Array.isArray(p?.gaps_hit) ? p?.gaps_hit : undefined;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer">
        <button onClick={onClose} style={{ float: "right" }}>
          close ✕
        </button>
        {err && <div className="error">{err}</div>}
        {!p ? (
          <div className="empty">Loading…</div>
        ) : (
          <>
            <h1>
              <button
                onClick={toggleStar}
                title="star"
                style={{
                  border: "none",
                  background: "none",
                  padding: 0,
                  marginRight: 8,
                  fontSize: 20,
                  color: p.starred ? "var(--maybe)" : "var(--border)",
                }}
              >
                {p.starred ? "★" : "☆"}
              </button>
              {p.title}
            </h1>
            <div className="sub">
              {p.company} · {p.location}
              {p.workplace_type ? ` · ${p.workplace_type}` : ""}
              {p.posted_at ? ` · posted ${p.posted_at}` : ""}
              {p.first_run_id ? ` · run #${p.first_run_id}` : ""}
              {p.seen_at ? ` · seen` : " · unseen"}
            </div>

            <dl className="kv">
              <dt>Status</dt>
              <dd>
                {STATES.map((s) => (
                  <button
                    key={s}
                    onClick={() => setStatus(s)}
                    className={p.status_state === s ? "primary" : ""}
                    style={{ marginRight: 4, marginBottom: 4, padding: "2px 8px", fontSize: 12 }}
                  >
                    {s}
                  </button>
                ))}
              </dd>

              <dt>Link</dt>
              <dd>
                <a href={p.url} target="_blank" rel="noreferrer">
                  open on LinkedIn ↗
                </a>
              </dd>

              <dt>Matched query</dt>
              <dd style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
                {p.matched_query}
              </dd>

              {p.comp_raw && (
                <>
                  <dt>Comp (sniffed)</dt>
                  <dd>{p.comp_raw}</dd>
                </>
              )}
            </dl>

            <div className="panel" style={{ background: "var(--panel-2)" }}>
              <h2>Assessment</h2>
              {p.verdict ? (
                <>
                  <div style={{ marginBottom: 8 }}>
                    <span className={"pill " + p.verdict}>{p.verdict}</span>{" "}
                    <span className="pill area">
                      area {p.area}
                      {p.area && AREA_NAMES[p.area] ? ` · ${AREA_NAMES[p.area]}` : ""}
                    </span>{" "}
                    <span className="muted">
                      quality {p.target_quality} · chance {p.chance} · comp {p.comp_vs_baseline}
                    </span>
                  </div>
                  <p style={{ margin: "8px 0" }}>{p.overall}</p>
                  {gaps && gaps.length > 0 && (
                    <div className="muted" style={{ fontSize: 12 }}>
                      gaps: {gaps.join(", ")}
                    </div>
                  )}
                  <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                    assessed {p.assessed_at}
                  </div>
                </>
              ) : (
                <div className="muted">Not assessed yet.</div>
              )}
              <button onClick={assess} disabled={busy} style={{ marginTop: 10 }}>
                {busy ? "assessing…" : p.verdict ? "re-assess" : "assess now"}
              </button>
            </div>

            <div className="jd">{p.description || "(no description fetched)"}</div>
          </>
        )}
      </aside>
    </>
  );
}
