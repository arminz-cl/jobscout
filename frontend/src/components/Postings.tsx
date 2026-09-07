import { useCallback, useEffect, useState } from "react";
import { api, AREA_NAMES, Facets, groupLabel, Posting, seedGroupTitles } from "../api";
import { PostingDrawer } from "./PostingDrawer";

type Tri = "any" | "yes" | "no";
const cycle = (t: Tri): Tri => (t === "any" ? "yes" : t === "yes" ? "no" : "any");
const triParam = (t: Tri): boolean | undefined => (t === "any" ? undefined : t === "yes");

// assessment level from the stored method
const levelOf = (p: Posting): 0 | 1 | 2 =>
  p.assess_method === "single" ? 2 : p.assess_method === "batch" ? 1 : 0;
const LEVEL = [
  { label: "not assessed", short: "none", color: "var(--muted)" },
  { label: "triaged (batch score)", short: "triaged", color: "var(--maybe)" },
  { label: "deep (full rubric)", short: "deep", color: "var(--pursue)" },
] as const;

export function Postings({
  mode,
  runId,
  initialCompany,
  onClearRun,
}: {
  mode: "all" | "assessed";
  runId?: number | null;
  initialCompany?: string | null;
  onClearRun?: () => void;
}) {
  const [rows, setRows] = useState<Posting[]>([]);
  const [count, setCount] = useState(0);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [search, setSearch] = useState("");
  const [verdict, setVerdict] = useState("");
  const [group, setGroup] = useState("");
  const [company, setCompany] = useState(initialCompany ?? "");
  const [unseenOnly, setUnseenOnly] = useState(false);
  const [starredOnly, setStarredOnly] = useState(false);
  const [hasDesc, setHasDesc] = useState<Tri>("any");
  const [level, setLevel] = useState<string>("");   // "" | "0" | "1" | "2"
  const [order, setOrder] = useState(mode === "assessed" ? "score" : "first_seen_at");
  const [sel, setSel] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [res, f] = await Promise.all([
        api.postings({
          search,
          verdict: verdict || undefined,
          group: group || undefined,
          company: company || undefined,
          run_id: runId ?? undefined,
          assessed: mode === "assessed" && level === "" ? true : undefined,
          level: level === "" ? undefined : Number(level),
          seen: unseenOnly ? false : undefined,
          starred: starredOnly ? true : undefined,
          has_description: triParam(hasDesc),
          order,
          limit: 400,
        }),
        api.facets(),
      ]);
      setRows(res.results);
      setCount(res.count);
      setFacets(f);
      seedGroupTitles(f.by_group);
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }, [search, verdict, group, company, runId, unseenOnly, starredOnly, hasDesc, level, order, mode]);

  useEffect(() => {
    if (initialCompany != null) setCompany(initialCompany);
  }, [initialCompany]);

  useEffect(() => {
    const t = setTimeout(load, 180);
    return () => clearTimeout(t);
  }, [load]);

  const toggleStar = async (p: Posting, e: React.MouseEvent) => {
    e.stopPropagation();
    await api.setStarred(p.id, !p.starred);
    setRows((rs) => rs.map((r) => (r.id === p.id ? { ...r, starred: p.starred ? 0 : 1 } : r)));
  };

  return (
    <div className="panel">
      <div className="filters">
        <input
          type="search"
          placeholder="title or company…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">all intent groups</option>
          {facets?.by_group.map((g) => (
            <option key={g.name} value={g.name}>
              {g.title} ({g.count}){g.active ? " ·active" : ""}
            </option>
          ))}
        </select>
        <select value={company} onChange={(e) => setCompany(e.target.value)}>
          <option value="">all companies</option>
          {facets?.companies.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name} ({c.count})
            </option>
          ))}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)} title="assessment level">
          <option value="">any assessed state</option>
          <option value="0">not assessed{facets?.levels ? ` (${facets.levels["0"]})` : ""}</option>
          <option value="1">triaged only{facets?.levels ? ` (${facets.levels["1"]})` : ""}</option>
          <option value="2">deep-assessed{facets?.levels ? ` (${facets.levels["2"]})` : ""}</option>
        </select>
        {mode === "assessed" && (
          <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            {["", "pursue", "maybe", "skip"].map((v) => (
              <option key={v} value={v}>{v || "all verdicts"}</option>
            ))}
          </select>
        )}

        <button
          className={unseenOnly ? "primary" : ""}
          style={{ fontSize: 12, padding: "3px 10px" }}
          onClick={() => setUnseenOnly((v) => !v)}
        >
          unseen only{facets ? ` (${facets.unseen})` : ""}
        </button>
        <button
          className={starredOnly ? "primary" : ""}
          style={{ fontSize: 12, padding: "3px 10px" }}
          onClick={() => setStarredOnly((v) => !v)}
        >
          ★ starred{facets ? ` (${facets.starred})` : ""}
        </button>
        <button
          className={hasDesc !== "any" ? "primary" : ""}
          style={{ fontSize: 12, padding: "3px 10px" }}
          title="cycle: any → has JD → missing JD"
          onClick={() => setHasDesc(cycle(hasDesc))}
        >
          {hasDesc === "any"
            ? `JD: any`
            : hasDesc === "yes"
              ? `has JD${facets ? ` (${facets.with_description})` : ""}`
              : `missing JD${facets ? ` (${facets.without_description})` : ""}`}
        </button>

        <select value={order} onChange={(e) => setOrder(e.target.value)}>
          <option value="first_seen_at">newest seen</option>
          <option value="posted_at">newest posted</option>
          <option value="company">company</option>
          <option value="verdict">verdict</option>
          <option value="score">score · overall</option>
          <option value="score_chance">score · chance</option>
          <option value="score_quality">score · quality</option>
        </select>

        <span className="muted">{loading ? "…" : `${count} shown`}</span>
        {runId != null && (
          <button onClick={onClearRun} style={{ fontSize: 12, padding: "2px 8px" }}>
            run #{runId} ✕
          </button>
        )}
      </div>

      {err && <div className="error">{err}</div>}

      <div className="muted" style={{ fontSize: 12, margin: "0 0 10px", lineHeight: 1.7 }}>
        <b>Score</b> (0–100 each): <b>overall</b> fit · <b>chance</b> of landing it (profile vs. the
        JD) · <b>quality</b> of the role if landed (the trajectory bar).
        &nbsp;·&nbsp; <b>Assessed</b>: <span style={{ color: "var(--maybe)" }}>triaged</span> = quick
        batch score · <span style={{ color: "var(--pursue)" }}>deep</span> = full rubric with
        reasoning &amp; gaps.
      </div>

      {mode === "assessed" && count === 0 && !loading && (
        <div className="empty">
          No assessed postings match. Run <b>Triage</b> on the Dashboard to score them.
        </div>
      )}

      {(mode === "all" || count > 0) && (
        <table>
          <thead>
            <tr>
              <th style={{ width: 22 }}></th>
              <th>Role</th>
              <th>Intent</th>
              <th>Location</th>
              <th>Assessed</th>
              <th title="overall / chance / quality (0–100)">Score</th>
              <th>Verdict</th>
              {mode === "assessed" && <th>Area</th>}
              {mode === "assessed" && <th>Model</th>}
              <th>Posted</th>
              <th title="has description">JD</th>
              <th>Run</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr
                key={p.id}
                onClick={() => setSel(p.id)}
                style={{ cursor: "pointer", opacity: p.seen_at ? 0.62 : 1 }}
              >
                <td onClick={(e) => toggleStar(p, e)} style={{ textAlign: "center" }}>
                  <span style={{ color: p.starred ? "var(--maybe)" : "var(--border)" }}>
                    {p.starred ? "★" : "☆"}
                  </span>
                </td>
                <td>
                  {!p.seen_at && <span className="dot" title="unseen" />}
                  {p.title}
                  <br />
                  <span className="company">{p.company}</span>
                </td>
                <td>
                  {p.matched_group ? (
                    <span
                      className="pill area"
                      title={`${groupLabel(p.matched_group)}\n\nmatched query:\n${p.matched_query ?? ""}`}
                    >
                      {groupLabel(p.matched_group)}
                    </span>
                  ) : (
                    <span
                      className="muted"
                      title={p.matched_query ?? ""}
                      style={{ fontSize: 11 }}
                    >
                      (pre-groups)
                    </span>
                  )}
                </td>
                <td>{p.location}</td>
                <td>
                  <span
                    className="pill"
                    title={`level ${levelOf(p)} — ${LEVEL[levelOf(p)].label}`}
                    style={{ color: LEVEL[levelOf(p)].color }}
                  >
                    {LEVEL[levelOf(p)].short}
                  </span>
                </td>
                <td style={{ fontVariantNumeric: "tabular-nums", fontSize: 12 }}>
                  {p.score_overall == null ? (
                    <span className="muted">—</span>
                  ) : (
                    <>
                      <b>{p.score_overall}</b>
                      <span className="muted"> · {p.score_chance} · {p.score_quality}</span>
                    </>
                  )}
                </td>
                <td>{p.verdict ? <span className={"pill " + p.verdict}>{p.verdict}</span> : "—"}</td>
                {mode === "assessed" && (
                  <td>
                    {p.area ? (
                      <span className="pill area" title={AREA_NAMES[p.area]}>
                        {p.area}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                )}
                {mode === "assessed" && (
                  <td className="muted" style={{ fontSize: 11 }}>{p.assessed_model ?? "—"}</td>
                )}
                <td className="muted">{p.posted_at ?? "—"}</td>
                <td style={{ textAlign: "center" }} title={p.description ? "has JD" : "no JD"}>
                  {p.description ? "✓" : ""}
                </td>
                <td className="muted">{p.first_run_id ? `#${p.first_run_id}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {sel !== null && (
        <PostingDrawer id={sel} onClose={() => setSel(null)} onChanged={load} />
      )}
    </div>
  );
}
