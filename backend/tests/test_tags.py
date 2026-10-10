import json

from app import tags


def _kinds(hits, tag):
    return sorted(h["kind"] for h in hits if h["tag"] == tag)


def test_matched_pair():
    hits = tags.scan("<thinking>reason</thinking>", "raw_response")
    assert _kinds(hits, "thinking") == ["close", "open"]


def test_unmatched_close():
    hits = tags.scan("hello </answer> world", "input")
    assert _kinds(hits, "answer") == ["unmatched"]


def test_unmatched_open():
    hits = tags.scan("<answer> dangling", "raw_response")
    assert _kinds(hits, "answer") == ["unmatched"]


def test_nested_repeated():
    hits = tags.scan("<thinking><thinking>x</thinking>", "raw_response")
    kinds = _kinds(hits, "thinking")
    # two opening tokens (one matched, one left dangling) + one close
    assert kinds.count("open") + kinds.count("unmatched") == 2
    assert kinds.count("close") == 1
    assert "unmatched" in kinds  # inner/outer imbalance detected


def test_self_closing():
    hits = tags.scan("<invoke/>", "raw_response")
    assert _kinds(hits, "invoke") == ["self"]


def test_html_escaped_decoded_view():
    hits = tags.scan("&lt;answer&gt;hi&lt;/answer&gt;", "input")
    # literal surface has no real tags; decoded surface recovers them
    surfaces = {h["surface"] for h in hits if h["tag"] == "answer"}
    assert "decoded" in surfaces


def test_json_encoded_response():
    blob = json.dumps({"output": {"message": {"content": [
        {"text": "<thinking>t</thinking>\n<answer>a</answer>"}]}}})
    hits = tags.scan(blob, "raw_response", json_path="raw")
    assert any(h["tag"] == "answer" and h["surface"] == "decoded" for h in hits)


def test_case_variation():
    hits = tags.scan("<ANSWER>x</ANSWER>", "raw_response")
    variants = {h["case_variant"] for h in hits if h["tag"] == "answer"}
    assert "upper" in variants


def test_input_presence_not_model_evidence():
    # tag in INPUT is recorded under source=input, distinct from raw_response.
    hits = tags.scan("</answer>", "input")
    assert all(h["source"] == "input" for h in hits)
