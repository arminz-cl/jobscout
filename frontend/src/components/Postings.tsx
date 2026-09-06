import { useCallback, useEffect, useState } from "react";
import { api, AREA_NAMES, Facets, groupLabel, Posting, seedGroupTitles } from "../api";
import { PostingDrawer } from "./PostingDrawer";

type Tri = "any" | "yes" | "no";
const cycle = (t: Tri): Tri => (t === "any" ? "yes" : t === "yes" ? "no" : "any");
const triParam = (t: Tri): boolean | undefined => (t === "any" ? undefined : t === "yes");

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
  const [order, setOrder] = useState(mode === "assessed" ? "verdict" : "first_seen_at");
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
          assessed: mode === "assessed" ? true : undefined,
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
  }, [search, verdict, group, company, runId, unseenOnly, starredOnly, hasDesc, order, mode]);

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
        </select>

        <span className="muted">{loading ? "…" : `${count} shown`}</span>
        {runId != null && (
          <button onClick={onClearRun} style={{ fontSize: 12, padding: "2px 8px" }}>
            run #{runId} ✕
          </button>
        )}
      </div>

      {err && <div className="error">{err}</div>}

      {mode === "assessed" && count === 0 && !loading && (
        <div className="empty">
          No assessments match. The assessor (Phase 3) hasn't run yet — fetched postings are on the
          Postings tab.
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
              <th>Work</th>
              <th>Posted</th>
              <th title="has description">JD</th>
              <th title="has assessment">✓</th>
              {mode === "assessed" && <th>Area</th>}
              {mode === "assessed" && <th>Verdict</th>}
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
                <td className="muted">{p.workplace_type ?? "—"}</td>
                <td className="muted">{p.posted_at ?? "—"}</td>
                <td style={{ textAlign: "center" }} title={p.description ? "has JD" : "no JD"}>
                  {p.description ? "✓" : ""}
                </td>
                <td style={{ textAlign: "center" }} title={p.verdict ? "assessed" : "not assessed"}>
                  {p.verdict || p.assessed_at ? "✓" : ""}
                </td>
                {mode === "assessed" && (
                  <td>
                    {p.area ? (
                      <span className="pill area" title={AREA_NAMES[p.area]}>
                        {p.area} · {AREA_NAMES[p.area] ?? "?"}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                )}
                {mode === "assessed" && (
                  <td>{p.verdict ? <span className={"pill " + p.verdict}>{p.verdict}</span> : "—"}</td>
                )}
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
