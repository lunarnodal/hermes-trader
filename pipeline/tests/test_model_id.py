"""R2-8: stable model labels."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reasoning.model_id import normalize_model_id  # noqa: E402


@pytest.mark.parametrize("raw,label", [
    ("/opt/models/qwen3.6-27b/Qwen3.6-27B-Q4_K_M.gguf", "qwen3.6-27b"),
    ("qwen3.6-27b", "qwen3.6-27b"),
    ("Qwen3.6-27B-UD-Q4_K_XL.gguf", "qwen3.6-27b"),
    ("Qwen3.6-27B-Q8_0", "qwen3.6-27b"),
    ("Qwen3.6-27B-BF16.gguf", "qwen3.6-27b"),
    ("DeepSeek-R1-Distill-Llama-70B-IQ4_XS.gguf", "deepseek-r1-distill-llama-70b"),
    (None, "unknown"),
    ("", "unknown"),
])
def test_normalize(raw, label):
    assert normalize_model_id(raw) == label


def test_import_makes_no_network_call(monkeypatch):
    import importlib
    import requests
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(RuntimeError()))
    sys.modules.pop("reasoning.predict", None)
    importlib.import_module("reasoning.predict")
    assert calls == []
