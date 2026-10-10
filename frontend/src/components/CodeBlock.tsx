import { useState } from "react";

// Renders a code block with a copy button. `text` is always what gets copied;
// pass `html` (pre-escaped/highlighted) to control the rendered markup.
export function CodeBlock({ text, html }: { text: string; html?: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    const t = text ?? "";
    const done = () => { setCopied(true); setTimeout(() => setCopied(false), 1200); };
    try {
      navigator.clipboard.writeText(t).then(done, () => fallback(t, done));
    } catch { fallback(t, done); }
  }
  return (
    <div className="codewrap">
      <button className="copybtn" onClick={copy} title="Copy to clipboard">
        {copied ? "✓ Copied" : "Copy"}
      </button>
      {html != null
        ? <pre className="block" dangerouslySetInnerHTML={{ __html: html }} />
        : <pre className="block">{text}</pre>}
    </div>
  );
}

function fallback(t: string, done: () => void) {
  try {
    const ta = document.createElement("textarea");
    ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); document.body.removeChild(ta); done();
  } catch { /* ignore */ }
}
