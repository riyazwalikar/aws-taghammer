import { useEffect, useState } from "react";
import { get, highlightAllTags, scanTags } from "./api";
import { ChatTrace } from "./pages/ChatTrace";
import { Batch } from "./pages/Batch";
import { Tampering } from "./pages/Tampering";
import { Setup } from "./pages/Setup";

export type Agent = {
  key: string; label: string; region: string; agent_id: string;
  alias_id: string; agent_name?: string; model: string; has_tools: boolean; note?: string;
};

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect x="1.5" y="1.5" width="45" height="45" rx="11" fill="#12192a" stroke="#2a3448"/>
      {/* angle-bracket tag being struck */}
      <path d="M19 17 L12 24 L19 31" stroke="#45b7ff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M29 17 L36 24 L29 31" stroke="#45b7ff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M26 13 L22 35" stroke="#45b7ff" strokeWidth="2.6" strokeLinecap="round"/>
      {/* hammer */}
      <g transform="rotate(38 24 24)">
        <rect x="22.4" y="9" width="3.2" height="22" rx="1.4" fill="#c9792a"/>
        <rect x="15" y="6" width="18" height="8" rx="2" fill="#ff9900"/>
        <rect x="15" y="6" width="5" height="8" rx="2" fill="#ffb84d"/>
      </g>
    </svg>
  );
}

const TABS = ["Chat / Trace", "Batch / Tags", "Tampering", "Setup"] as const;

export function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Chat / Trace");
  const [cfg, setCfg] = useState<any>(null);
  const [agentsList, setAgentsList] = useState<Agent[]>([]);
  const [agentKey, setAgentKey] = useState<string>("");
  const [refreshing, setRefreshing] = useState(false);
  const [promptOpen, setPromptOpen] = useState(false);
  const [prompt, setPrompt] = useState<any>(null);
  const [promptLoading, setPromptLoading] = useState(false);

  async function loadAgents(keepSelection = true) {
    setRefreshing(true);
    try {
      const r = await get("/api/agents");
      setAgentsList(r.agents);
      const keys = r.agents.map((a: Agent) => a.key);
      if (!keepSelection || !keys.includes(agentKey)) {
        if (r.agents.length) setAgentKey(r.agents[0].key);
      }
    } catch { /* ignore */ } finally { setRefreshing(false); }
  }

  useEffect(() => {
    get("/api/config").then(setCfg).catch(() => {});
    loadAgents(false);
  }, []);

  // Refetch the system prompt when the panel is open and the agent changes.
  useEffect(() => {
    if (!promptOpen || !agentKey) return;
    setPromptLoading(true); setPrompt(null);
    get(`/api/agents/${agentKey}/prompt`)
      .then(setPrompt).catch((e) => setPrompt({ error: String(e) }))
      .finally(() => setPromptLoading(false));
  }, [promptOpen, agentKey]);

  const mode = cfg?.config.demo_mode ?? "?";
  const evidence = mode === "classic-live" ? "LIVE_CLASSIC"
    : mode === "recorded-live" ? "RECORDED_CLASSIC" : "NOVA_LAB";
  const sel = agentsList.find((a) => a.key === agentKey);

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <Logo />
          <div>
            <h1>AWS Bedrock TagHammer</h1>
            <div className="sub">Tag Boundary Injection · Agents (Classic)</div>
          </div>
        </div>
        <div className="badges">
          <span className="badge">{sel?.model ?? cfg?.config.nova_model_id}</span>
          <span className="badge">agent <b>{sel?.agent_name ?? sel?.agent_id ?? "—"}</b></span>
          {sel?.has_tools && <span className="badge tools">tools</span>}
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</div>
          ))}
        </nav>
      </header>

      <div className="agentbar">
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className="small">Agent:</span>
          <select value={agentKey} onChange={(e) => setAgentKey(e.target.value)} style={{ width: 360 }}>
            {agentsList.map((a) => (
              <option key={a.key} value={a.key}>
                {a.agent_name ?? a.agent_id}
              </option>
            ))}
          </select>
          <button className="secondary" onClick={() => loadAgents(true)} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "⟳ Refresh agents"}
          </button>
          <button className="secondary" onClick={() => setPromptOpen((v) => !v)}>
            {promptOpen ? "Hide system prompt" : "View system prompt"}
          </button>
          {sel?.note && <span className="small">{sel.note}</span>}
        </div>
        {promptOpen && (
          <div className="panel" style={{ marginTop: 8, background: "var(--panel)" }}>
            <h2>User-defined system prompt (agent instruction, live from AWS)</h2>
            {promptLoading && <div className="small">loading…</div>}
            {prompt?.error && <div className="small" style={{ color: "var(--bad)" }}>{prompt.error}</div>}
            {prompt && !prompt.error && (
              <>
                <div className="small">
                  {prompt.agent_name} · {prompt.agent_id} · {prompt.foundation_model} · {prompt.status}
                  {prompt.prompt_overrides ? " · prompt-overrides present" : " · managed default templates"}
                </div>
                <pre className="block" style={{ whiteSpace: "pre-wrap" }}
                     dangerouslySetInnerHTML={{ __html: highlightAllTags(prompt.instruction || "") }} />
                {(() => {
                  const hits = scanTags(prompt.instruction || "");
                  return hits.length ? (
                    <div className="small">
                      tags in <b>source=system_prompt</b>:{" "}
                      {hits.map((h, i) => (
                        <span key={i} className="taghi" style={{ marginRight: 6 }}>
                          &lt;{h.kind === "close" ? "/" : ""}{h.tag}&gt;@{h.offset}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="small">no tags in the system prompt</div>
                  );
                })()}
              </>
            )}
          </div>
        )}
      </div>

      <main>
        {tab === "Chat / Trace" && <ChatTrace cfg={cfg} agent={sel} />}
        {tab === "Batch / Tags" && <Batch cfg={cfg} agent={sel} agentKey={agentKey} />}
        {tab === "Tampering" && <Tampering />}
        {tab === "Setup" && <Setup />}
      </main>
    </div>
  );
}
