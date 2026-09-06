import { useCallback, useEffect, useState } from "react";
import { api, Company } from "../api";

export function Companies({ onOpenCompany }: { onOpenCompany: (name: string) => void }) {
  const [rows, setRows] = useState<Company[]>([]);
  const [search, setSearch] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.companies());
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rate = async (name: string, rating: number) => {
    setRows((rs) => rs.map((r) => (r.name === name ? { ...r, rating } : r)));
    try {
      await api.setCompanyRating(name, rating);
    } catch (e: any) {
      setErr(e.message);
      load();
    }
  };

  const filtered = search
    ? rows.filter((r) => r.name.toLowerCase().includes(search.toLowerCase()))
    : rows;

  return (
    <div className="panel">
      <div className="filters">
        <input
          type="search"
          placeholder="company…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="muted">{filtered.length} companies</span>
      </div>
      {err && <div className="error">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>Company</th>
            <th>Rating</th>
            <th>Postings</th>
            <th>Pursue</th>
            <th>Starred</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((c) => (
            <tr key={c.name}>
              <td>
                <button
                  onClick={() => onOpenCompany(c.name)}
                  style={{
                    border: "none",
                    background: "none",
                    padding: 0,
                    color: "var(--accent)",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  {c.name} →
                </button>
              </td>
              <td>
                <Stars value={c.rating} onChange={(v) => rate(c.name, v)} />
              </td>
              <td>{c.count}</td>
              <td>{c.pursue || "—"}</td>
              <td>{c.starred || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stars({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <span style={{ whiteSpace: "nowrap", fontSize: 15 }}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          title={`${n}`}
          onClick={() => onChange(value === n ? 0 : n)}
          style={{
            border: "none",
            background: "none",
            padding: "0 1px",
            cursor: "pointer",
            color: n <= value ? "var(--maybe)" : "var(--border)",
          }}
        >
          {n <= value ? "★" : "☆"}
        </button>
      ))}
    </span>
  );
}
