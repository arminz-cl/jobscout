import { useCallback, useEffect, useState } from "react";
import { api, Resume, ResumeListItem } from "../api";

export function Resumes() {
  const [list, setList] = useState<ResumeListItem[]>([]);
  const [sel, setSel] = useState<Resume | null>(null);
  const [tab, setTab] = useState<"resume" | "notes">("resume");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setList(await api.resumes());
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const open = async (id: number) => {
    setSel(await api.resume(id));
    setTab("resume");
  };

  const del = async (id: number) => {
    await api.deleteResume(id);
    if (sel?.id === id) setSel(null);
    load();
  };

  return (
    <div className="grid cols-2">
      <div className="panel">
        <h2>Generated resumes ({list.length})</h2>
        {err && <div className="error">{err}</div>}
        {list.length === 0 && (
          <div className="empty">
            None yet. Open a posting and hit <b>generate resume</b> in the drawer.
          </div>
        )}
        {list.map((r) => (
          <div
            key={r.id}
            className="group"
            style={{ cursor: "pointer", background: sel?.id === r.id ? "var(--panel)" : undefined }}
            onClick={() => open(r.id)}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <b>{r.title}</b>
              <span className="pill">base {r.base ?? "?"}</span>
              {r.verdict && <span className={"pill " + r.verdict}>{r.verdict}</span>}
              {r.score_overall != null && <span className="muted">{r.score_overall}</span>}
              <button
                style={{ marginLeft: "auto", fontSize: 11, padding: "1px 6px" }}
                onClick={(e) => {
                  e.stopPropagation();
                  del(r.id);
                }}
              >
                delete
              </button>
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              {r.company} · {r.model} · {new Date(r.created_at).toLocaleString()}
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        {!sel ? (
          <div className="empty">Select a resume.</div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <button className={tab === "resume" ? "primary" : ""} onClick={() => setTab("resume")}>
                Resume
              </button>
              <button className={tab === "notes" ? "primary" : ""} onClick={() => setTab("notes")}>
                Tailoring notes
              </button>
              <button
                style={{ marginLeft: "auto" }}
                onClick={() => navigator.clipboard.writeText(sel.content)}
              >
                copy markdown
              </button>
              {sel.url && (
                <a href={sel.url} target="_blank" rel="noreferrer">
                  <button>open JD ↗</button>
                </a>
              )}
            </div>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6 }}>
              {tab === "resume" ? sel.content : sel.notes || "(no notes)"}
            </pre>
          </>
        )}
      </div>
    </div>
  );
}
