import { useState } from "react";
import { highlightAllTags } from "../api";
import { CodeBlock } from "./CodeBlock";

const EV_TABS = [
  ["input", "Submitted input"],
  ["prompt", "Bedrock model prompt"],
  ["raw", "Raw model response"],
  ["rationale", "Trace rationale"],
  ["action", "Parsed action"],
  ["code", "Code interp"],
  ["final", "Final answer"],
  ["json", "Raw JSON"],
] as const;

// `data` is the GET /api/runs/{id} response: { run, artifacts, tag_occurrences, events }
export function Evidence({ data }: { data: any }) {
  const [tab, setTab] = useState("raw");
  if (!data) return <div className="small">Select a prompt on the left to load its evidence.</div>;
  const art = (n: string) => data.artifacts?.[n];
  const field = (n: string) => {
    const a = art(n);
    if (!a || !a.present) return <span className="notprovided">not provided by AWS</span>;
    return <CodeBlock text={a.content} />;
  };
  const hl = (n: string) => art(n)?.present
    ? <CodeBlock text={art(n).content} html={highlightAllTags(art(n).content)} />
    : <span className="notprovided">not provided by AWS</span>;
  const rc = data.events?.find((e: any) => e.type === "returnControl");

  return (
    <>
      <div className="evtabs">
        {EV_TABS.map(([k, label]) => (
          <div key={k} className={`evtab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{label}</div>
        ))}
      </div>
      {tab === "input" && field("submitted_input")}
      {tab === "prompt" && hl("model_prompt")}
      {tab === "raw" && hl("raw_response")}
      {tab === "rationale" && field("rationale")}
      {tab === "action" && (rc
        ? <CodeBlock text={JSON.stringify(rc.returnControl, null, 2)} />
        : <span className="notprovided">no return-control action (finalized directly)</span>)}
      {tab === "code" && (art("code_executed")?.present
        ? <>
            <div className="small">Code the agent actually ran in the sandboxed interpreter:</div>
            <CodeBlock text={art("code_executed").content} />
            <div className="small" style={{ marginTop: 8 }}>Execution output (from AWS's sandbox):</div>
            <CodeBlock text={art("code_output")?.content ?? ""} />
          </>
        : <span className="notprovided">no code-interpreter execution in this run</span>)}
      {tab === "final" && field("final_answer")}
      {tab === "json" && <CodeBlock text={JSON.stringify(data.events, null, 2)} />}

      <h2 style={{ marginTop: 12 }}>Tag occurrences by source</h2>
      <TagTable rows={data.tag_occurrences ?? []} />
      <div className="spacer" />
      <a href={`/api/exports/${data.run.id}`} target="_blank" rel="noreferrer">
        <button className="secondary">Export run JSON</button>
      </a>
    </>
  );
}

function TagTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <div className="small">no control tags detected in any source</div>;
  return (
    <table>
      <thead><tr><th>source</th><th>tag</th><th>kind</th><th>case</th><th>offset</th></tr></thead>
      <tbody>
        {rows.slice(0, 60).map((r, i) => (
          <tr key={i}>
            <td>{r.source}</td><td className="mono">{r.tag}</td>
            <td>{r.kind}</td><td>{r.case_variant}</td>
            <td className="mono">{r.start_offset}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
