"""R2-7: the repeating-failure alert goes into the reasoner prompt, never the query."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
predict = pytest.importorskip("reasoning.predict")

QUERY = "materials sector outlook — mining, chemicals"
WARN = "REPEATING FAILURE ALERT: This sector query has been wrong 4/5 recent times"


class _Stop(Exception):
    pass


def test_warning_in_prompt_not_in_query(monkeypatch):
    seen = {}

    def fake_qdrant(query, **kw):
        seen["retrieval_query"] = query
        return [{"text": "Copper inventories fell", "sector": "materials"}]

    def fake_post(url, json=None, timeout=None, **kw):
        seen["prompt"] = json["messages"][-1]["content"]
        raise _Stop()

    monkeypatch.setattr(predict, "query_qdrant", fake_qdrant)
    monkeypatch.setattr(predict, "SIGNAL_GRAPH_ENABLED", False, raising=False)
    monkeypatch.setattr(predict, "format_signals_for_reasoning", lambda s: "SIGNALS")
    monkeypatch.setattr(predict.requests, "post", fake_post)
    try:
        predict.run_prediction(QUERY, "24h", 5, context_warning=WARN)
    except _Stop:
        pass
    assert seen["retrieval_query"] == QUERY
    assert seen["prompt"].startswith(f"Query: {QUERY}\n")
    assert WARN in seen["prompt"]


def test_daily_predictions_passes_warning_as_context():
    src = (Path(__file__).resolve().parents[1] / "reasoning" / "daily_predictions.py").read_text()
    assert "context_warning=failure_warning" in src
    assert '[WARNING: {failure_warning}]' not in src
