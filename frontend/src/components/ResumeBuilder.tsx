import { useCallback, useEffect, useRef, useState } from "react";
import { api, BuilderSession } from "../api";

/**
 * Interactive resume builder — a chat loop that surfaces true material to close
 * the gaps a JD hits, keeping a live draft on the right. Opened from a posting.
 */
export function ResumeBuilder({
  postingId,
  onClose,
}: {
  postingId: number;
  onClose: () => void;
}) {
  const [session, setSession] = useState<BuilderSession | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [text, setText] = useState("");
  const [tab, setTab] = useState<"draft" | "notes" | "ledger">("draft");
  const [saved, setSaved] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .builder(postingId)
      .then((s) => alive && (setSession(s), setErr(null)))
      .catch(() => alive && setSession(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [postingId]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [session?.messages.length, sending]);

  const start = useCallback(async () => {
    setSending(true);
    setErr(null);
    try {
      setSession(await api.startBuilder(postingId));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setSending(false);
    }
  }, [postingId]);

  const send = async () => {
    const t = text.trim();
    if (!t || !session || sending) return;
    setSending(true);
    setErr(null);
    setText("");
    // optimistic: show the user line immediately
    setSession({
      ...session,
      messages: [
        ...session.messages,
        { role: "user", content: t, created_at: new Date().toISOString() },
      ],
    });
    try {
      setSession(await api.builderReply(session.id, t));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setSending(false);
    }
  };

  const saveResume = async () => {
    if (!session) return;
    try {
      await api.builderSave(session.id);
      setSaved(true);
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const reset = async () => {
    if (!confirm("Discard this builder session and start over?")) return;
    await api.resetBuilder(postingId).catch(() => {});
    setSession(null);
    setSaved(false);
  };

  return (
    <div className="panel" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
        <button onClick={onClose}>← postings</button>
        <h2 style={{ margin: 0 }}>
          Resume builder{session ? ` · ${session.company} — ${session.title}` : ""}
        </h2>
        {session && (
          <span className="muted" style={{ fontSize: 12 }}>
            base {session.base ?? "?"} · {session.model} · {session.status}
          </span>
        )}
        {session && (
          <button style={{ marginLeft: "auto", fontSize: 12 }} onClick={reset}>
            reset
          </button>
        )}
      </div>

      {err && <div className="error">{err}</div>}

      {loading ? (
        <div className="empty">Loading…</div>
      ) : !session ? (
        <div className="empty" style={{ display: "grid", gap: 10, justifyItems: "start" }}>
          <p style={{ margin: 0 }}>
            Start an interactive session: the assistant reads the JD and your profile, gives an
            honest read on the gaps, then asks you targeted questions to pull out real material.
            The draft on the right updates as you go.
          </p>
          <button className="primary" onClick={start} disabled={sending}>
            {sending ? "starting… (up to a minute on the free tier)" : "Start builder"}
          </button>
        </div>
      ) : (
        <div
          className="grid cols-2"
          style={{ gap: 16, alignItems: "start", minHeight: 0 }}
        >
          {/* ── chat ── */}
          <div style={{ display: "flex", flexDirection: "column", height: "72vh" }}>
            <div
              ref={scroller}
              style={{
                flex: 1,
                overflowY: "auto",
                display: "flex",
                flexDirection: "column",
                gap: 10,
                padding: "4px 2px",
              }}
            >
              {session.messages.map((m, i) => (
                <div
                  key={i}
                  style={{
                    alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                    maxWidth: "88%",
                    background: m.role === "user" ? "var(--accent)" : "var(--panel-2)",
                    color: m.role === "user" ? "#fff" : "var(--text)",
                    borderRadius: 10,
                    padding: "8px 11px",
                    whiteSpace: "pre-wrap",
                    fontSize: 13.5,
                    lineHeight: 1.55,
                  }}
                >
                  {m.content}
                </div>
              ))}
              {sending && (
                <div className="muted" style={{ fontSize: 12, alignSelf: "flex-start" }}>
                  thinking… (free tier may pause up to ~60s)
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
                }}
                placeholder="Answer the questions, or steer the draft…  (⌘/Ctrl+Enter to send)"
                rows={3}
                style={{ flex: 1, font: "inherit", padding: "8px 10px", resize: "vertical" }}
                disabled={sending}
              />
              <button className="primary" onClick={send} disabled={sending || !text.trim()}>
                send
              </button>
            </div>
          </div>

          {/* ── live draft ── */}
          <div style={{ display: "flex", flexDirection: "column", height: "72vh" }}>
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              {(["draft", "notes", "ledger"] as const).map((t) => (
                <button
                  key={t}
                  className={tab === t ? "primary" : ""}
                  onClick={() => setTab(t)}
                  style={{ fontSize: 12 }}
                >
                  {t}
                </button>
              ))}
              <button
                style={{ marginLeft: "auto", fontSize: 12 }}
                onClick={() =>
                  navigator.clipboard.writeText(
                    (tab === "notes" ? session.notes : tab === "ledger" ? session.ledger : session.draft) ||
                      "",
                  )
                }
              >
                copy
              </button>
              <button className="primary" style={{ fontSize: 12 }} onClick={saveResume}>
                {saved ? "saved ✓" : "save to Resumes"}
              </button>
            </div>
            <pre
              style={{
                flex: 1,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                fontSize: 12.5,
                lineHeight: 1.6,
                background: "var(--panel-2)",
                borderRadius: 8,
                padding: 12,
                margin: 0,
              }}
            >
              {(tab === "notes"
                ? session.notes
                : tab === "ledger"
                  ? session.ledger
                  : session.draft) || `(no ${tab} yet)`}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
