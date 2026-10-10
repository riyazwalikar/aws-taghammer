"""Tag scanner. An ANALYZER, not Bedrock's parser.

A tag present in the INPUT is not evidence the model emitted it. The scanner
counts opening/closing/self-closing tags, case, offsets, and unmatched
sequences per source, and keeps the exact raw snippet + a JSON path hint. It is
aware of escaped tags, code fences, and JSON-encoded response strings: it scans
both the raw view and a decoded view, tagging which is which.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, asdict

KNOWN_TAGS = ["thinking", "answer", "function_calls", "invoke", "parameters",
              "final_response"]

# open: <tag ...> (not self-closing); close: </tag>; self: <tag .../>
_OPEN = re.compile(r"<\s*([a-zA-Z_][\w\-]*)\b([^>]*?)(?<!/)>")
_CLOSE = re.compile(r"<\s*/\s*([a-zA-Z_][\w\-]*)\s*>")
_SELF = re.compile(r"<\s*([a-zA-Z_][\w\-]*)\b[^>]*?/>")


@dataclass
class TagHit:
    source: str
    tag: str
    kind: str            # open | close | self | unmatched
    case_variant: str    # lower | upper | mixed
    start_offset: int
    end_offset: int
    snippet: str
    json_path: str
    surface: str         # literal | decoded


def _case(s: str) -> str:
    if s.islower():
        return "lower"
    if s.isupper():
        return "upper"
    return "mixed"


def _scan_surface(text: str, source: str, surface: str, json_path: str) -> list[TagHit]:
    hits: list[TagHit] = []
    opens: list[tuple[str, int]] = []  # stack of (lower_tag, index_in_hits)

    # Collect self-closing first and blank them so open/close regex skip them.
    masked = text
    for m in _SELF.finditer(text):
        tag = m.group(1)
        hits.append(TagHit(source, tag.lower(), "self", _case(tag), m.start(), m.end(),
                           text[max(0, m.start() - 10):m.end() + 10], json_path, surface))
    masked = _SELF.sub(lambda m: " " * (m.end() - m.start()), masked)

    # Walk opens and closes in order to detect matched/unmatched by a simple stack.
    tokens = []
    for m in _OPEN.finditer(masked):
        tokens.append(("open", m.group(1), m.start(), m.end()))
    for m in _CLOSE.finditer(masked):
        tokens.append(("close", m.group(1), m.start(), m.end()))
    tokens.sort(key=lambda t: t[2])

    stack: list[int] = []
    for kind, tag, s, e in tokens:
        idx = len(hits)
        hits.append(TagHit(source, tag.lower(), kind, _case(tag), s, e,
                           text[max(0, s - 10):e + 10], json_path, surface))
        if kind == "open":
            stack.append(idx)
        else:  # close
            # find matching open of same tag
            for j in range(len(stack) - 1, -1, -1):
                if hits[stack[j]].tag == tag.lower():
                    del stack[j]
                    break
            else:
                hits[idx].kind = "unmatched"  # close with no open
    # opens left on the stack are unmatched
    for idx in stack:
        hits[idx].kind = "unmatched"
    return hits


def _maybe_json_decode(text: str) -> str | None:
    """If text looks like a JSON-encoded string/object holding a 'content' or
    'text' field, return the decoded inner text (Classic wraps model output in
    rawResponse.content as a JSON string)."""
    t = text.strip()
    if not t or t[0] not in "{[\"":
        return None
    try:
        obj = json.loads(t)
    except (ValueError, TypeError):
        return None
    # dig for common fields
    parts: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("text", "content", "reason", "question") and isinstance(v, str):
                    parts.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    joined = "\n".join(p for p in parts if p)
    return joined or None


def scan(text: str | None, source: str, json_path: str = "") -> list[dict]:
    """Scan a source's text (literal + HTML-unescaped + JSON-decoded views)."""
    if not text:
        return []
    out: list[TagHit] = []
    out += _scan_surface(text, source, "literal", json_path)

    unescaped = html.unescape(text)
    if unescaped != text:
        out += _scan_surface(unescaped, source, "decoded", json_path + "#html_unescape")

    decoded = _maybe_json_decode(text)
    if decoded and decoded != text:
        out += _scan_surface(decoded, source, "decoded", json_path + "#json_decode")

    return [asdict(h) for h in out]


def summarize(hits: list[dict]) -> dict:
    """Compact counts by tag/kind for a source, restricted to known tags plus
    any others actually seen."""
    summary: dict[str, dict[str, int]] = {}
    for h in hits:
        d = summary.setdefault(h["tag"], {"open": 0, "close": 0, "self": 0, "unmatched": 0})
        d[h["kind"]] = d.get(h["kind"], 0) + 1
    return summary
