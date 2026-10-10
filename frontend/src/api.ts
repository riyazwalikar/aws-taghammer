export async function api(path: string, opts: RequestInit = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const post = (p: string, body: unknown) =>
  api(p, { method: "POST", body: JSON.stringify(body) });
export const get = (p: string) => api(p);

// HTML-escape all AWS/user text before rendering.
export function esc(s: string): string {
  return (s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

// Escape then highlight control-tag literals with visible offsets.
const TAGS = ["thinking", "answer", "function_calls", "invoke", "parameters", "final_response"];
export function highlightTags(raw: string): string {
  let s = esc(raw);
  const re = new RegExp(`(&lt;/?\\s*(?:${TAGS.join("|")})\\b[^&]*?&gt;)`, "gi");
  return s.replace(re, '<span class="taghi">$1</span>');
}

// Lightweight client-side enumeration of ANY tag (for the system-prompt viewer —
// shows operator-defined tags like <plan>/<policy>, not just the 6 control tags).
export type TagHit = { tag: string; kind: "open" | "close" | "self"; offset: number; raw: string };
export function scanTags(s: string): TagHit[] {
  const re = /<(\/?)\s*([a-zA-Z][\w-]*)\b[^>]*?>/g;
  const out: TagHit[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) {
    const kind = m[1] ? "close" : m[0].endsWith("/>") ? "self" : "open";
    out.push({ tag: m[2].toLowerCase(), kind, offset: m.index, raw: m[0] });
  }
  return out;
}

// Highlight ANY tag (generic) — for the system-prompt viewer.
export function highlightAllTags(raw: string): string {
  return esc(raw).replace(/(&lt;\/?\s*[a-zA-Z][\w-]*\b[^&]*?&gt;)/g, '<span class="taghi">$1</span>');
}
