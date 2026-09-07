/**
 * Info tab — reference material, only what the user explicitly asks to keep here.
 * Add a new <section> per request. Nothing goes here automatically.
 */
export function Info() {
  return (
    <div className="panel" style={{ lineHeight: 1.7 }}>
      <h2>Info</h2>

      <section style={{ marginBottom: 24 }}>
        <h3 style={{ margin: "0 0 6px" }}>Assessor provider limits — Groq free tier, per model</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          For <code>openai/gpt-oss-120b</code> (from the API response headers and 429 bodies).
          Limits are per model, per organization; a second key on a different org has its own.
        </p>
        <table style={{ maxWidth: 520 }}>
          <tbody>
            <tr>
              <td><b>Tokens / minute (TPM)</b></td>
              <td>8,000</td>
              <td className="muted">refills continuously; a ~6k-token deep call ≈ 1 per 45s</td>
            </tr>
            <tr>
              <td><b>Tokens / day (TPD)</b></td>
              <td>200,000</td>
              <td className="muted">≈ 30–35 deep calls or ≈ 220 triaged postings, then dead until reset</td>
            </tr>
            <tr>
              <td><b>Requests / minute</b></td>
              <td>~30</td>
              <td className="muted">not the binding limit for us</td>
            </tr>
            <tr>
              <td><b>Requests / day</b></td>
              <td>1,000</td>
              <td className="muted">not the binding limit for us</td>
            </tr>
            <tr>
              <td><b>Daily reset</b></td>
              <td>~00:00 UTC</td>
              <td className="muted">rolling-ish; TPD frees up gradually near the boundary</td>
            </tr>
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 13 }}>
          Per call: deep ≈ 6k tokens in · triage batch (5 JDs) ≈ 4.5k in. The <b>TPD</b> is what
          runs out; the <b>TPM</b> is what makes it slow (backoff between calls).
        </p>
        <p className="muted" style={{ fontSize: 13 }}>
          Alternatives when exhausted: swap to the other key in <code>.env</code> · Gemini free
          tier (aistudio.google.com/apikey, ~1,500 req/day, separate provider) · Groq dev tier
          (add a card, ~25× the limits, ≈ $3 for a full run).
        </p>
      </section>
    </div>
  );
}
