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

  const getDescription = async () => {
    setBusy(true);
    setErr(null);
    try {
      setP(await api.fetchDescription(id));
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
              <h2>
                Assessment ·{" "}
                {p.assess_method === "single"
                  ? "level 2 · deep"
                  : p.assess_method === "batch"
                    ? "level 1 · triaged"
                    : "level 0 · none"}
              </h2>
              {p.score_overall != null && (
                <div style={{ display: "flex", gap: 16, marginBottom: 8 }}>
                  <Score label="overall" v={p.score_overall} />
                  <Score label="chance" v={p.score_chance} />
                  <Score label="quality" v={p.score_quality} />
                </div>
              )}
              {p.verdict ? (
                <>
                  <div style={{ marginBottom: 8 }}>
                    <span className={"pill " + p.verdict}>{p.verdict}</span>{" "}
                    <span className="pill area">
                      area {p.area}
                      {p.area && AREA_NAMES[p.area] ? ` · ${AREA_NAMES[p.area]}` : ""}
                    </span>{" "}
                    {p.assess_method === "single" && (
                      <span className="muted">
                        quality {p.target_quality} · chance {p.chance} · comp {p.comp_vs_baseline}
                      </span>
                    )}
                  </div>
                  {p.area_reason && (
                    <p style={{ margin: "6px 0", fontSize: 13 }} className="muted">
                      area: {p.area_reason}
                    </p>
                  )}
                  <p style={{ margin: "8px 0" }}>{p.overall}</p>
                  {gaps && gaps.length > 0 && (
                    <div className="muted" style={{ fontSize: 12 }}>
                      gaps: {gaps.join(", ")}
                    </div>
                  )}
                  <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                    <span className="pill">{p.assessed_model ?? "?"}</span> · {p.assessed_at}
                  </div>
                </>
              ) : (
                <div className="muted">Not assessed yet.</div>
              )}
              <button onClick={assess} disabled={busy} style={{ marginTop: 10 }}>
                {busy
                  ? "assessing…"
                  : p.assess_method === "single"
                    ? "re-assess (deep)"
                    : "deep-assess this one"}
              </button>
            </div>

            <div className="jd">
              {p.description || "(no description fetched yet)"}
              {!p.description && (
                <div style={{ marginTop: 10 }}>
                  <button onClick={getDescription} disabled={busy}>
                    {busy ? "fetching…" : "fetch description"}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </aside>
    </>
  );
}

function Score({ label, v }: { label: string; v: number | null }) {
  const color = v == null ? "var(--muted)" : v >= 70 ? "var(--pursue)" : v >= 45 ? "var(--maybe)" : "var(--skip)";
  return (
    <div>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>{v ?? "—"}</div>
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
    </div>
  );
}
