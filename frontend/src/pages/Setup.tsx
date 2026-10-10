import { useState } from "react";
import { get, esc } from "../api";

export function Setup() {
  const [rep, setRep] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function runDoctor() {
    setBusy(true);
    try { setRep(await get("/api/doctor")); } finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth: 950, margin: "0 auto" }}>
      <div className="panel doctor">
        <h2>Doctor — capability report</h2>
        <div className="small">
          Read-only, inexpensive calls; never stores credentials. The definitive creation-eligibility
          probe (CreateAgent 403-maintenance vs any-other-error) is the separate <span className="mono">
          scripts/probe_create_eligibility.py</span> because it may create a resource.
        </div>
        <div className="spacer" />
        <button onClick={runDoctor} disabled={busy}>{busy ? "Running…" : "Run doctor"}</button>
        <div className="spacer" />
        {rep && (
          <table>
            <thead><tr><th>status</th><th>check</th><th>detail</th></tr></thead>
            <tbody>
              {rep.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <td className={r.status}>{r.status}</td>
                  <td>{r.check}</td>
                  <td className="small">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rep && (
          <div className="note">
            {rep.failed ? "One or more FAIL — resolve before relying on the live exhibit."
              : "No hard failures. If rawResponse.content is empty or the tag census is empty, the raw-tag exhibit degrades to inference."}
          </div>
        )}
      </div>
    </div>
  );
}
