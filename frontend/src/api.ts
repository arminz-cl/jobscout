export interface AppConfig {
  mode: string;
  source: string;
  model: string;
  locations: { label: string; work_type: number | null }[];
  time_posted: string;
  resolved_window: string;
  last_run: string | null;
  max_results_per_query: number;
}

export interface QueryGroup {
  name: string;
  title: string;
  summary: string;
  queries: string[];
  active: boolean;
}

export interface Run {
  id: number;
  source: string;
  mode: string | null;
  groups: string | null;
  kind: string | null;
  started_at: string;
  finished_at: string | null;
  status: string | null;
  time_posted_used: string | null;
  n_fetched: number;
  n_unique: number;
  n_new: number;
  n_assessed: number;
  note: string | null;
  is_current?: boolean;
  busy?: boolean;
}

export interface RunnerState {
  busy: boolean;
  current_run_id: number | null;
  kind: string | null;
  groups: string[] | null;
  error: string | null;
}

export interface AssessorInfo {
  configured: boolean;
  provider: string | null;
  model: string | null;
  model_tag: string | null;
  has_key: boolean;
  pending: number;
}

export interface Facets {
  total: number;
  with_description: number;
  without_description: number;
  assessed: number;
  pending_assessment: number;
  unseen: number;
  starred: number;
  by_group: {
    name: string;
    title: string;
    summary: string;
    count: number;
    active: boolean;
  }[];
  verdicts: Record<string, number>;
  workplace: Record<string, number>;
  companies: { name: string; count: number }[];
}

// The A/B/C/D taxonomy from target-areas.md — shown alongside the bare letter.
export const AREA_NAMES: Record<string, string> = {
  A: "SWE · data-heavy",
  B: "AI Engineer",
  C: "SWE · general",
  D: "Data Engineer",
};

// query-group name -> short label for run/filter chips.
// Titles come from the API (config-driven); this is the fallback / registry that
// callers seed from `query-groups` or `facets.by_group`.
export const groupTitles: Record<string, string> = {};
export const seedGroupTitles = (gs: { name: string; title: string }[]) => {
  for (const g of gs) groupTitles[g.name] = g.title;
};
export const groupLabel = (name: string) => groupTitles[name] ?? name;
export const groupsLabel = (csv: string | null | undefined) =>
  csv ? csv.split(",").map(groupLabel).join(" + ") : "—";

export interface Company {
  name: string;
  count: number;
  pursue: number;
  starred: number;
  rating: number;
  note: string | null;
}

export interface Posting {
  id: number;
  external_id: string;
  url: string;
  title: string;
  company: string;
  location: string;
  remote: number | null;
  workplace_type: string | null;
  description: string | null;
  comp_raw: string | null;
  posted_at: string | null;
  matched_query: string | null;
  matched_group: string | null;
  first_run_id: number | null;
  seen_at: string | null;
  starred: number;
  first_seen_at: string;
  last_seen_at: string;
  area: string | null;
  area_reason: string | null;
  target_quality: string | null;
  chance: string | null;
  verdict: string | null;
  overall: string | null;
  gaps_hit: string[] | string | null;
  comp_vs_baseline: string | null;
  keep_de_titles: number | null;
  assessed_model: string | null;
  assessed_at: string | null;
  status_state: string | null;
  status_note: string | null;
}

const WINDOW_LABELS: Record<string, string> = {
  r86400: "24h",
  r604800: "7d",
  r2592000: "30d",
};
export const windowLabel = (w: string | null) => (w ? WINDOW_LABELS[w] ?? w : "—");

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  config: () => j<AppConfig>("/config"),
  queryGroups: () => j<QueryGroup[]>("/query-groups"),
  runs: (limit = 20) => j<Run[]>(`/runs?limit=${limit}`),
  run: (id: number) => j<Run>(`/runs/${id}`),
  runner: () => j<RunnerState>("/runner"),
  startRun: (body: {
    groups?: string[] | null;
    since?: string | null;
    phase?: "cards" | "descriptions" | "full" | "assess";
    then_assess?: boolean;
    assess_limit?: number | null;
  }) => j<{ run_id: number }>("/runs", { method: "POST", body: JSON.stringify(body) }),
  stopRun: (id: number) => j<{ ok: boolean }>(`/runs/${id}/stop`, { method: "POST" }),
  assessor: () => j<AssessorInfo>("/assessor"),
  postings: (params: Record<string, string | number | boolean | undefined>) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params))
      if (v !== undefined && v !== "") qs.set(k, String(v));
    return j<{ count: number; results: Posting[] }>(`/postings?${qs}`);
  },
  facets: () => j<Facets>("/postings/facets"),
  companies: () => j<Company[]>("/companies"),
  setCompanyRating: (name: string, rating: number) =>
    j<{ ok: boolean }>("/companies/rating", {
      method: "POST",
      body: JSON.stringify({ name, rating }),
    }),
  posting: (id: number) => j<Posting>(`/postings/${id}`),
  setStatus: (id: number, state: string, note?: string) =>
    j<{ ok: boolean }>(`/postings/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ state, note }),
    }),
  setStarred: (id: number, starred: boolean) =>
    j<{ ok: boolean }>(`/postings/${id}/star`, {
      method: "POST",
      body: JSON.stringify({ starred }),
    }),
  fetchDescription: (id: number) =>
    j<Posting>(`/postings/${id}/description`, { method: "POST" }),
  assess: (id: number) => j<Posting>(`/postings/${id}/assess`, { method: "POST" }),
};
