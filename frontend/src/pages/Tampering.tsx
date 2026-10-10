import { useState } from "react";
import { post, get, esc } from "../api";

export function Tampering() {
  const [mode, setMode] = useState<"quick" | "coverage" | "exhaustive">("quick");
  const [preview, setPreview] = useState<any>(null);
  const [trun, setTrun] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function doPreview() {
    setBusy(true); setTrun(null);
    const p = await post("/api/tampering/preview", { mode });
    setPreview(p); setBusy(false);
  }
  async function start() {
    if (!preview) return;
    setBusy(true);
    const r = await post("/api/tampering/runs", { mode });
    poll(r.tampering_run_id);
  }
  async function poll(id: string) {
    const t = await get(`/api/tampering/runs/${id}`);
    setTrun(t);
    if (["running"].includes(t.tampering_run.status)) setTimeout(() => poll(id), 900);
    else setBusy(false);
  }

  const totals = preview?.totals;
  return (
    <div style={{ maxWidth: 1100, margin: "0 auto" }}>
      <div className="panel">
        <h2>Test orchestration tampering</h2>
        <div className="row">
          {(["quick", "coverage", "exhaustive"] as const).map((m) => (
            <button key={m} className={mode === m ? "" : "secondary"} onClick={() => setMode(m)}>{m}</button>
          ))}
          <button className="secondary" onClick={doPreview} disabled={busy}>Preview matrix</button>
        </div>
        <div className="note">
          "Exhaustive" = every eligible cell over the enumerated dimensions within the versioned
          corpus and your caps — never every possible string. A short run is labelled PARTIAL,
          never EXHAUSTIVE_COMPLETE. The button never silently sends thousands of billable calls.
        </div>
      </div>

      <div className="spacer" />
      {preview && (
        <div className="panel">
          <h2>Preview — {preview.corpus_version} / {preview.mode}</h2>
          <table>
            <tbody>
              <tr><th>generated cells</th><td>{totals.generated_cells}</td>
                  <th>eligible</th><td>{totals.eligible_cells}</td></tr>
              <tr><th>skipped</th><td>{totals.skipped_cells}</td>
                  <th>controls / candidate</th><td>{totals.controls_per_candidate}</td></tr>
              <tr><th>repetitions</th><td>{totals.repetitions}</td>
                  <th>projected calls (upper bound)</th><td>{totals.projected_calls_upper_bound}</td></tr>
              <tr><th>budget ceiling</th><td>{totals.budget_ceiling_calls}</td>
                  <th>will be capped</th><td>{String(totals.will_be_capped)}</td></tr>
            </tbody>
          </table>
          <div className="small">{preview.note}</div>
          <div className="spacer" />
          <button onClick={start} disabled={busy}>Start (Quick within cap)</button>
          <div className="spacer" />
          <details>
            <summary>Skipped cells (with reasons)</summary>
            <table>
              <thead><tr><th>reason</th><th>dims</th></tr></thead>
              <tbody>
                {(preview.skipped ?? []).slice(0, 12).map((s: any, i: number) => (
                  <tr key={i}><td>{s.reason}</td><td className="mono">{(s.dims || []).join(" · ")}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      )}

      <div className="spacer" />
      {trun && (
        <div className="panel">
          <h2>Run — status <span className={`pill ${trun.tampering_run.status === "partial" ? "INCONCLUSIVE" : ""}`}>
            {trun.tampering_run.status}</span></h2>
          <div className="small">completed {trun.totals.completed} / eligible {trun.totals.eligible}</div>
          <table>
            <thead><tr><th>cell</th><th>outcome</th><th>confidence</th></tr></thead>
            <tbody>
              {(trun.totals.results ?? []).map((r: any, i: number) => (
                <tr key={i}>
                  <td className="mono">{r.cell}</td>
                  <td><span className={`pill ${r.outcome}`}>{r.outcome}</span></td>
                  <td><span className={`pill ${r.confidence}`}>{r.confidence}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="note">
            Managed default parser: parser-internal decision is not observable. ORCHESTRATION_BEHAVIOR_CHANGED
            reaches at most SUGGESTIVE — consistent with a boundary parse but cannot exclude that the model
            would have chosen that action anyway. The tag-free semantic control is mandatory in every pair.
          </div>
        </div>
      )}
    </div>
  );
}
