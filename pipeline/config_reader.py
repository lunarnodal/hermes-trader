#!/usr/bin/env python3
"""
Config reader helper — reads typed config values from portfolio_config table.

Usage:
    from pipeline.config_reader import get_config

    val = get_config(conn, 'drawdown_circuit_breaker_pct', 0.05, float)
    cfg = get_config(conn, 'event_type_config', {}, dict)

Seeding (run once against production portfolio.db):
    INSERT OR IGNORE INTO portfolio_config (key, value) VALUES
        ('sector_win_rates', '{"ai_infrastructure":0.44,...}'),
        ('hard_block_thresholds', '[0.35, 0.70, 0.55]'),
        ('event_type_config', '{"ai_infrastructure":{...},...}'),
        ('drawdown_circuit_breaker_pct', '0.05');
"""

import json
import logging

log = logging.getLogger(__name__)

# Track which keys already emitted a fallback warning (once per process).
_fallback_warned = set()


def get_config(conn, key: str, default, cast) -> any:
    """
    Read a config value from the portfolio_config table by key.

    Args:
        conn:  sqlite3.Connection to a DB with portfolio_config table.
        key:   Config key to look up.
        default: Value returned when key is not found or an error occurs.
        cast:  Type to cast the stored string value to
               (float, int, bool, str, dict, list).
               For dict/list the stored value is parsed as JSON.

    Returns:
        The cast value from the DB, or default on miss/error.

    Logs:
        WARNING once per process (per key) when falling back to default.
    """
    try:
        row = conn.execute(
            "SELECT value FROM portfolio_config WHERE key = ?", (key,)
        ).fetchone()
    except Exception:
        if key not in _fallback_warned:
            _fallback_warned.add(key)
            log.warning("Fallback to default for key: %s", key)
        return default

    if row is None:
        if key not in _fallback_warned:
            _fallback_warned.add(key)
            log.warning("Fallback to default for key: %s", key)
        return default

    raw = row[0]

    try:
        if cast is dict:
            return json.loads(raw)
        elif cast is list:
            return json.loads(raw)
        elif cast is bool:
            return raw.lower() in ("true", "1", "yes")
        elif cast in (int, float):
            return cast(raw)
        else:
            # str or any other — return raw as-is
            return raw if cast is str else cast(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        if key not in _fallback_warned:
            _fallback_warned.add(key)
            log.warning("Fallback to default for key: %s", key)
        return default
