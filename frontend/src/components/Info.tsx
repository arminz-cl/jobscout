import { useEffect, useState } from "react";
import { api, AppConfig } from "../api";

/**
 * Info tab — reference material, only what the user explicitly asks to keep here.
 * Add a new <section> per request. Nothing goes here automatically.
 */
export function Info() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  useEffect(() => {
    api.config().then(setCfg).catch(() => {});
  }, []);

  return (
    <div className="panel" style={{ lineHeight: 1.7 }}>
      <h2>Info</h2>

      {/* ── current config ── */}
      <section style={{ marginBottom: 26 }}>
        <h3 style={{ margin: "0 0 6px" }}>Current run config</h3>
        <table style={{ maxWidth: 460 }}>
          <tbody>
            <tr><td>mode</td><td><b>{cfg?.mode ?? "…"}</b></td></tr>
            <tr><td>source</td><td>{cfg?.source ?? "…"}</td></tr>
            <tr><td>model (config)</td><td>{cfg?.model ?? "…"}</td></tr>
            <tr>
              <td>quality weight</td>
              <td>
                {cfg?.quality_weight != null
                  ? `${cfg.quality_weight} quality / ${(1 - cfg.quality_weight).toFixed(2)} chance`
                  : "…"}
              </td>
            </tr>
            <tr><td>next fetch window</td><td>{cfg?.resolved_window ?? "…"}</td></tr>
            <tr>
              <td>last run</td>
              <td>{cfg?.last_run ? new Date(cfg.last_run).toLocaleString() : "never"}</td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* ── groq budget ── */}
      <section style={{ marginBottom: 26 }}>
        <h3 style={{ margin: "0 0 6px" }}>Groq free-tier budget — <code>openai/gpt-oss-120b</code></h3>

        <p style={{ margin: "0 0 8px" }}>
          <b>TPM (tokens per minute)</b> — total tokens (prompt + reply) allowed in any rolling
          60&nbsp;seconds. Refills continuously. <b>This is what makes it slow.</b>
          <br />
          <b>TPD (tokens per day)</b> — total per 24&nbsp;hours, resets around 00:00&nbsp;UTC.
          <b> This is what makes it stop.</b>
        </p>

        <table style={{ maxWidth: 560 }}>
          <tbody>
            <tr><td><b>TPM</b></td><td>8,000</td><td className="muted">deep call ≈ 6k → wait ~45s between calls</td></tr>
            <tr><td><b>TPD</b></td><td>200,000</td><td className="muted">per key, per model, per org</td></tr>
            <tr><td>requests / min</td><td>~30</td><td className="muted">not the binding limit</td></tr>
            <tr><td>requests / day</td><td>1,000</td><td className="muted">not the binding limit</td></tr>
            <tr><td>daily reset</td><td>~00:00 UTC</td><td className="muted">rolling-ish near the boundary</td></tr>
          </tbody>
        </table>

        <h4 style={{ margin: "14px 0 4px" }}>How many calls per key per day</h4>
        <table style={{ maxWidth: 560 }}>
          <tbody>
            <tr>
              <td><b>deep assessment</b></td>
              <td>~6,000 tokens</td>
              <td className="muted"><b>~33 / day</b> (TPD ÷ size)</td>
            </tr>
            <tr>
              <td><b>triage batch</b> (5 JDs)</td>
              <td>~4,500 tokens</td>
              <td className="muted"><b>~44 batches / day ≈ 220 postings</b></td>
            </tr>
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          Two keys on different orgs = double. When exhausted: swap the key in <code>.env</code> ·
          Gemini free tier (separate provider, ~1,500 req/day) · Groq dev tier (add a card,
          ~25× the limits, ≈ $3 for a full run).
        </p>
      </section>
    </div>
  );
}
