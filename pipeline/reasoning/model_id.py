"""Reasoning-model identity: one normalized label for predictions/reports.

The llama-server /v1/models id is whatever path was loaded, e.g.
'/opt/models/qwen3.6-27b/Qwen3.6-27B-Q4_K_M.gguf'. Stored labels must be stable
across quantizations and paths, so they are normalized to e.g. 'qwen3.6-27b'.
Resolution is lazy (first use), so importing a module never makes a network call.
"""
import logging
import os
import re

log = logging.getLogger(__name__)

_QUANT = re.compile(
    r"[-_.](?:ud-)?(?:i?q\d[\w]*|f16|bf16|f32|fp16|fp8|int[48]|awq|gptq|mlx)$", re.I)
_cache: dict[str, str] = {}


def normalize_model_id(raw: str | None) -> str:
    if not raw:
        return "unknown"
    name = os.path.basename(str(raw).rstrip("/"))
    name = re.sub(r"\.gguf$", "", name, flags=re.I)
    prev = None
    while prev != name:                     # strip stacked suffixes like -UD-Q4_K_XL
        prev, name = name, _QUANT.sub("", name)
    return name.lower() or "unknown"


def resolve_model(host: str, fallback: str | None = None) -> str:
    """Normalized id of the model served at *host*, cached per host."""
    if host in _cache:
        return _cache[host]
    label = normalize_model_id(fallback)
    try:
        import requests
        raw = requests.get(f"{host}/v1/models", timeout=5).json()["data"][0]["id"]
        label = normalize_model_id(raw)
        log.info(f"Reasoning model at {host}: {raw} -> label {label!r}")
    except Exception as e:
        log.warning(f"Could not resolve model from {host}/v1/models ({e}); using {label!r}")
    _cache[host] = label
    return label
