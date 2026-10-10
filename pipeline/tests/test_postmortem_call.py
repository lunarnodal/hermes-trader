"""R2-5: post-mortem LLM call — budget, truncation handling, placeholder rejection."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pm = pytest.importorskip("reasoning.postmortem")


class _Resp:
    def __init__(self, content, finish="stop", reasoning=None):
        self._b = {"choices": [{"finish_reason": finish, "message": {
            "content": content, "reasoning_content": reasoning}}],
            "usage": {"completion_tokens": 900, "prompt_tokens": 1200}}

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


def _patch(monkeypatch, resp, seen):
    def fake_post(url, json=None, timeout=None):
        seen.update(json)
        return resp
    monkeypatch.setattr(pm.requests, "post", fake_post)


def test_budget_is_8192(monkeypatch):
    seen = {}
    _patch(monkeypatch, _Resp('{"root_cause": "x", "lesson": "y"}'), seen)
    pm.call_reasoning_model("p")
    assert seen["max_tokens"] == 8192


def test_valid_answer_after_think_block(monkeypatch):
    body = "<think>long reasoning</think>\n```json\n" + json.dumps(
        {"root_cause": "Tariff headline reversed intraday", "lesson": "Discount headline-only macro calls"}) + "\n```"
    _patch(monkeypatch, _Resp(body), {})
    out = pm.call_reasoning_model("p")
    assert out["lesson"].startswith("Discount")


def test_truncated_response_is_discarded(monkeypatch):
    _patch(monkeypatch, _Resp('{"root_cause": "Tariff head', finish="length"), {})
    assert pm.call_reasoning_model("p") is None


def test_placeholder_lesson_rejected(monkeypatch):
    _patch(monkeypatch, _Resp('{"root_cause": "...", "lesson": "..."}'), {})
    assert pm.call_reasoning_model("p") is None
