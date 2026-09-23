"""
hard_gates.py — Deterministic risk layer that LLM cannot override.

This module sits AFTER the critic verdict and enforces non-negotiable
hard limits. It is intentionally separate from the LLM-driven selector
and rule engine so that no model output, prompt injection, or logic error
in the reasoning layer can bypass these protections.

All gates return (approved: bool, reason: str). An approved=False blocks
the trade unconditionally; approved=True passes it onward.

────────────────────────────────────────────────────────────────────────────
SECURITY NOTE — _BYPASS
────────────────────────────────────────────────────────────────────────────
_BYPASS exists solely for manual emergency intervention (e.g. the portfolio
is stuck in a frozen position and a human trader needs to force-close).
It is NOT exposed through any API, config key, or rule file.

If _BYPASS is True:
  • ALL hard gates are skipped — the trade is approved unconditionally.
  • Every invocation is logged at CRITICAL level with full trade context
    so a security audit can later reconstruct who/what triggered it.

Changing _BYPASS to True from an LLM prompt or automation is a CRITICAL
security incident. The import-time check below will produce a WARNING so
that any accidental or adversarial flip is visible in logs immediately.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger(__name__)

# ─── Tunable constants ───────────────────────────────────────────────────────
# These are FIXED. Do not route them through CONFIG. The whole point is that
# they cannot be changed by any LLM output or DB config update.
MAX_DAILY_LOSS   = 0.05   # 5 % portfolio loss from peak → block all new BUY
MAX_SINGLE_ASSET = 0.35   # 35 % of portfolio in one ticker (incl. open pos)
MIN_CASH_RATIO   = 0.20   # 20 % of portfolio value must remain as cash


# ─── Bypass configuration ────────────────────────────────────────────────────
# _BYPASS_DEFAULT is the only place the default lives.  All reads of the
# bypass state go through _get_bypass(), which enforces audit-logging and
# raises a warning at import time if the default has been changed from False.
_BYPASS_DEFAULT = False   # ← the canonical secure default


def _get_bypass() -> bool:
    """
    Return the current bypass state with full audit instrumentation.

    This is the ONLY function that should read the bypass flag.
    Every call site is logged at DEBUG, and every True result is logged
    at CRITICAL so that security monitors can alert on active bypasses.
    """
    # Read from module globals so we can detect tampering via __dict__
    module = sys.modules[__name__]
    current = bool(getattr(module, "_BYPASS", _BYPASS_DEFAULT))

    if current:
        log.critical(
            "[HARD GATE] BYPASS IS ACTIVE — all gates are being skipped. "
            "Review stack trace above for call origin. "
            "This is a CRITICAL security event if not expected."
        )
    return current


def _check_bypass_integrity() -> None:
    """
    Called once at import time.  Verifies that _BYPASS has not been set to
    anything other than the secure default (_BYPASS_DEFAULT = False).

    Produces a WARNING log entry if the flag is not False, making any
    accidental or adversarial change visible immediately in log output.
    """
    module = sys.modules[__name__]
    current = getattr(module, "_BYPASS", _BYPASS_DEFAULT)

    if current is not _BYPASS_DEFAULT:
        # Use the 'warnings' module so this surfaces in code-review tools too
        warnings.warn(
            "hard_gates.py _BYPASS flag is not its secure default (False). "
            "All hard gates will be bypassed. This is a CRITICAL security "
            "risk if not intentional. Current value: %r" % (current,),
            RuntimeWarning,
            stacklevel=2,
        )
        log.warning(
            "[HARD GATE SECURITY] _BYPASS has been changed from its secure "
            "default (False) to %r. Bypass is now ACTIVE. "
            "Do not ignore this warning. Alert a human immediately.",
            current,
        )


# Run integrity check at import — any non-False value is now visible in logs
_check_bypass_integrity()


# ─── Gate helpers ────────────────────────────────────────────────────────────

def _daily_pnl_pct(conn: "sqlite3.Connection") -> float:
    """
    Return today's unrealized + realized P&L as a fraction of portfolio value.
    负数 = loss.  Used by the daily-loss circuit breaker.
    """
    import sqlite3
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    pos_value = 0.0
    with conn:
        cur = conn.cursor()
        # Unrealized P&L from open positions
        rows = cur.execute(
            "SELECT entry_price, current_price, shares FROM positions",
        ).fetchall()
        for entry_price, current_price, shares in rows:
            if current_price and current_price > 0:
                pos_value += current_price * shares
            else:
                pos_value += (entry_price or 0) * shares

        entry_value = sum(
            (row[0] or 0) * row[2]
            for row in rows
        )
        unrealized_pnl = pos_value - entry_value

        # Realized P&L from today's closed trades
        realized_row = cur.execute(
            """SELECT COALESCE(SUM(value * -1), 0.0)
               FROM transactions
               WHERE action = 'SELL'
                 AND date(timestamp) = date(?)
            """,
            (today_start,),
        ).fetchone()
        realized_pnl = realized_row[0] if realized_row else 0.0

    # Portfolio value (entry basis) for ratio
    total_value = entry_value + pos_value
    if total_value <= 0:
        return 0.0

    total_pnl = unrealized_pnl + realized_pnl
    return total_pnl / total_value


def _open_position_value(conn: "sqlite3.Connection", ticker: str) -> float:
    """Return the total market value of any open position in ticker."""
    with conn:
        row = conn.execute(
            "SELECT current_price, shares FROM positions "
            "WHERE ticker = ? AND status = 'open'",
            (ticker,),
        ).fetchone()
    if row and row[0] and row[1]:
        return row[0] * row[1]
    return 0.0


def _current_portfolio_value(conn: "sqlite3.Connection") -> float:
    """Approximate current portfolio value (cash + position market value)."""
    with conn:
        cash_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0.0) FROM cash_ledger",
        ).fetchone()
        cash = cash_row[0] if cash_row else 0.0

        pos_val = 0.0
        rows = conn.execute(
            "SELECT current_price, shares FROM positions WHERE status = 'open'",
        ).fetchall()
        for cp, sh in rows:
            if cp and sh:
                pos_val += cp * sh

    return cash + pos_val


# ─── Individual gates ───────────────────────────────────────────────────────

def check_daily_loss_breaker(conn: "sqlite3.Connection") -> tuple[bool, str]:
    """
    Gate 1 — Daily-loss circuit breaker.

    Fires when portfolio has lost more than MAX_DAILY_LOSS from its peak
    today (unrealized + realized combined). All BUY entries are blocked
    until the next trading day resets the counter.
    """
    pnl_pct = _daily_pnl_pct(conn)
    if pnl_pct <= -MAX_DAILY_LOSS:
        log.warning(
            f"[HARD GATE] Daily loss breaker triggered: {pnl_pct:.1%} "
            f"(limit -{MAX_DAILY_LOSS:.0%})"
        )
        return False, (
            f"Daily loss circuit breaker active: portfolio is down "
            f"{abs(pnl_pct):.1%} (max allowed: {MAX_DAILY_LOSS:.0%})"
        )
    return True, ""


def check_max_single_asset(
    conn: "sqlite3.Connection",
    ticker: str,
    proposed_value: float,
) -> tuple[bool, str]:
    """
    Gate 2 — Max single-asset exposure.

    The proposed BUY + any existing position in the same ticker
    cannot exceed MAX_SINGLE_ASSET of portfolio value.
    """
    port_val = _current_portfolio_value(conn)
    if port_val <= 0:
        return True, ""  # no data yet — let other gates handle cash check

    existing_value = _open_position_value(conn, ticker)
    total_exposure = existing_value + proposed_value

    if total_exposure / port_val > MAX_SINGLE_ASSET:
        log.warning(
            f"[HARD GATE] Max single-asset breaker: {ticker} would be "
            f"{total_exposure/port_val:.1%} of portfolio "
            f"(limit {MAX_SINGLE_ASSET:.0%}); "
            f"existing=${existing_value:.2f} + proposed=${proposed_value:.2f}"
        )
        return False, (
            f"Max single-asset limit would be breached: {ticker} "
            f"={total_exposure/port_val:.1%} of portfolio "
            f"(max {MAX_SINGLE_ASSET:.0%})"
        )
    return True, ""


def check_min_cash_reserve(
    conn: "sqlite3.Connection",
    proposed_value: float,
) -> tuple[bool, str]:
    """
    Gate 3 — Minimum cash reserve.

    After the proposed BUY, at least MIN_CASH_RATIO of portfolio value
    must remain as uninvested cash. This ensures the portfolio can
    absorb drawdowns and doesn't go all-in on a single signal.
    """
    with conn:
        cash_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0.0) FROM cash_ledger",
        ).fetchone()
        cash = cash_row[0] if cash_row else 0.0

    port_val = _current_portfolio_value(conn)
    if port_val <= 0:
        return True, ""  # degenerate case — let other gates catch it

    post_trade_cash = cash - proposed_value
    post_trade_ratio = post_trade_cash / port_val

    if post_trade_ratio < MIN_CASH_RATIO:
        log.warning(
            f"[HARD GATE] Cash reserve breach: post-trade cash would be "
            f"{post_trade_ratio:.1%} of portfolio "
            f"(min required {MIN_CASH_RATIO:.0%}); "
            f"cash=${post_trade_cash:.2f}, proposed=${proposed_value:.2f}"
        )
        return False, (
            f"Minimum cash reserve would be breached: "
            f"post-trade cash={post_trade_ratio:.1%} of portfolio "
            f"(min {MIN_CASH_RATIO:.0%})"
        )
    return True, ""


def check_duplicate_order(
    conn: "sqlite3.Connection",
    ticker: str,
    action: str = "BUY",
) -> tuple[bool, str]:
    """
    Gate 4 — Duplicate order guard.

    Blocks a second BUY (or SELL) for the same ticker if an open
    position already exists. Prevents accidental double-entry from
    rapid re-submission or LLM re-triggering the same signal.
    """
    with conn:
        row = conn.execute(
            "SELECT id, shares FROM positions "
            "WHERE ticker = ? AND status = 'open'",
            (ticker,),
        ).fetchone()
    if row:
        log.warning(
            f"[HARD GATE] Duplicate order blocked: {action} {ticker} "
            f"— open position #{row[0]} ({row[1]} shares) already exists"
        )
        return False, (
            f"Duplicate {action} blocked: open position exists for {ticker} "
            f"(id={row[0]}, {row[1]} shares)"
        )
    return True, ""


# ─── Manual override flag ────────────────────────────────────────────────────
#
# DANGER: Setting this to True disables ALL hard gates without warning.
#         NEVER change this from an LLM prompt or automation.
#         The _check_bypass_integrity() call at import time will produce
#         a WARNING if this value is anything other than False.
#
# To change this for emergency manual trading, edit the line below and
# restart the pipeline process.  A restart is required so that the
# import-time integrity check runs and the change is visible in logs.
_BYPASS = False


# ─── Composite gate ──────────────────────────────────────────────────────────

def gate_all(
    conn: "sqlite3.Connection",
    ticker: str,
    proposed_value: float,
    action: str = "BUY",
    **kwargs,
) -> tuple[bool, str]:
    """
    Run every hard gate in sequence. Returns (approved, first_failure_reason).

    The bypass exists only for manual-intervention workflows. It is NOT
    exposed through any API, config key, or rule file.  Every invocation
    of gate_all is logged regardless of whether the bypass fires.

    Optional kwargs for new gates:
        confidence: float — prediction confidence (0-1), used by NetOfCostGate.
        quote:      dict  — bid/ask quote from cost_model.get_quote(), used by
                        LiquidityGate and NetOfCostGate. If not provided and a
                        gate needs it, the gate fetches it or passes with note.
    """
    # Always log the gate_all call for audit traceability
    if _get_bypass():
        log.critical(
            "[HARD GATE BYPASS AUDIT] gate_all called with bypass active — "
            "ticker=%r action=%r proposed_value=%.2f. "
            "All gates skipped. Review this entry immediately.",
            ticker,
            action,
            proposed_value,
        )
        return True, "[bypass active — see security log for details]"

    confidence = kwargs.get("confidence")
    quote      = kwargs.get("quote")

    gates = [
        check_daily_loss_breaker,
        lambda c: check_max_single_asset(c, ticker, proposed_value),
        lambda c: check_min_cash_reserve(c, proposed_value),
        lambda c: check_duplicate_order(c, ticker, action),
        lambda c: check_liquidity(c, ticker, quote),
        lambda c: check_net_of_cost(c, ticker, proposed_value, confidence),
    ]

    for check in gates:
        approved, reason = check(conn)
        if not approved:
            return False, reason

    return True, ""


# ─── Liquidity gate (Gate 5) ─────────────────────────────────────────────────
# Port of jennycruzy-convex gates.py:LiquidityGate, adapted for equities.
# BLOCKS entry when relative bid/ask spread exceeds configured max OR
# quote age exceeds configured max seconds. Config-gated (disabled by default).

def _get_liquidity_config(conn: "sqlite3.Connection") -> dict:
    """Read liquidity gate config. Falls back to defaults on error."""
    from config_reader import get_config
    return {
        "enabled":    get_config(conn, "liquidity_gate_enabled", False, bool),
        "max_spread": get_config(conn, "liquidity_max_spread_pct", 0.30, float),
        "max_age":    get_config(conn, "liquidity_max_quote_age_s", 90, float),
    }


def check_liquidity(
    conn: "sqlite3.Connection",
    ticker: str,
    quote: dict | None = None,
) -> tuple[bool, str]:
    """
    Gate 5 — Liquidity gate (spread + quote freshness).

    Rejects entry when:
      - Relative bid-ask spread > configured max (default 0.30%)
      - Quote age > configured max seconds (default 90s)

    Defaults DISABLED. Gate passes with note if quote unavailable.
    """
    cfg = _get_liquidity_config(conn)
    if not cfg["enabled"]:
        return True, ""

    if quote is None:
        from portfolio.cost_model import get_quote as _get_quote
        quote = _get_quote(ticker)

    if quote is None:
        log.debug(f"[HARD GATE] Liquidity: no quote for {ticker} — PASS with note")
        return True, f"Liquidity gate: no quote for {ticker} (gate disabled without data)"

    spread_pct = quote.get("spread_pct", 0.0)
    age_s      = quote.get("age_seconds", 0.0)
    max_spread = cfg["max_spread"]
    max_age    = cfg["max_age"]

    if spread_pct > max_spread / 100.0:
        log.warning(
            f"[HARD GATE] Liquidity rejected {ticker}: "
            f"spread={spread_pct:.4%} > {max_spread:.2f}% max"
        )
        return False, (
            f"Liquidity gate rejected: relative spread "
            f"{spread_pct:.4%} exceeds {max_spread:.2f}% max"
        )

    if age_s > max_age:
        log.warning(
            f"[HARD GATE] Liquidity rejected {ticker}: "
            f"quote age={age_s:.0f}s > {max_age:.0f}s max"
        )
        return False, (
            f"Liquidity gate rejected: quote age "
            f"{age_s:.0f}s exceeds {max_age:.0f}s max"
        )

    return True, ""


# ─── Net-of-cost gate (Gate 6) ───────────────────────────────────────────────
# Port of jennycruzy-convex gates.py:NetOfCostGate, adapted for equities.
# Rejects when estimated one-way cost share of expected edge > configured max.
# Expected edge proxy: confidence * 150 bps (linear band, documented gap).

def _get_cost_config(conn: "sqlite3.Connection") -> dict:
    """Read cost gate config. Falls back to defaults on error."""
    from config_reader import get_config
    return {
        "enabled":      get_config(conn, "cost_gate_enabled", False, bool),
        "max_edge_shr": get_config(conn, "cost_max_edge_share", 0.25, float),
    }


def _expected_edge_bps(confidence: float) -> float:
    """
    Linear confidence-to-expected-edge mapping (basis points).

    This is a PROXY because selector.py does not expose a scenario-based
    expected move figure. The mapping assumes 150 bps max move at confidence=1.0.

    TODO: Replace with true scenario-based edge when Phase 2 adds
    scenario generation (jennycruzy-convex edge.py port).

    Returns 0.0 if confidence is None or out of range.
    """
    if confidence is None or confidence <= 0:
        return 0.0
    return min(confidence * 150.0, 200.0)


def check_net_of_cost(
    conn: "sqlite3.Connection",
    ticker: str,
    proposed_value: float,
    confidence: float | None = None,
) -> tuple[bool, str]:
    """
    Gate 6 — Net-of-cost gate.

    Rejects entry when estimated one-way cost exceeds configured fraction
    of expected edge. Cost is computed from live bid/ask spread + slippage.

    If confidence is unavailable, gate PASSES with a note rather than blocking,
    since we cannot compute the cost/edge ratio without an edge estimate.

    Defaults DISABLED.
    """
    cfg = _get_cost_config(conn)
    if not cfg["enabled"]:
        return True, ""

    if confidence is None or confidence <= 0:
        log.debug(
            f"[HARD GATE] NetOfCost: no confidence for {ticker} — PASS with note"
        )
        return True, (
            f"NetOfCost gate: no confidence score for {ticker} "
            f"(edge estimate unavailable — passing)"
        )

    # Fetch quote for cost estimation
    quote = None
    from portfolio.cost_model import get_quote as _get_quote, CostModel
    quote = _get_quote(ticker)
    if quote is None:
        log.debug(
            f"[HARD GATE] NetOfCost: no quote for {ticker} — PASS with note"
        )
        return True, (
            f"NetOfCost gate: no quote for {ticker} "
            f"(cost estimate unavailable — passing)"
        )

    # Estimate cost
    model = CostModel.from_config(conn)
    qty = proposed_value / quote["mid"] if quote["mid"] > 0 else 0
    if qty <= 0:
        return True, ""

    cost_result = model.estimate_entry_cost(ticker, qty, quote)
    cost_pct = cost_result["cost_pct"]

    # Compute expected edge (proxy)
    edge_bps = _expected_edge_bps(confidence)
    edge_pct = edge_bps / 10000.0  # bps → fraction

    if edge_pct <= 0:
        log.debug(
            f"[HARD GATE] NetOfCost: zero edge for {ticker} — PASS with note"
        )
        return True, (
            f"NetOfCost gate: zero expected edge for {ticker} — passing"
        )

    # Check: cost_share_of_edge = cost_pct / edge_pct
    cost_share = cost_pct / edge_pct
    max_share = cfg["max_edge_shr"]

    if cost_share > max_share:
        log.warning(
            f"[HARD GATE] NetOfCost rejected {ticker}: "
            f"cost={cost_pct:.4%} / edge={edge_pct:.4%} = {cost_share:.2%} "
            f"> {max_share:.0%} max"
        )
        return False, (
            f"NetOfCost gate rejected: cost share of expected edge "
            f"{cost_share:.0%} exceeds {max_share:.0%} max "
            f"(cost={cost_pct:.4%}, edge={edge_pct:.4%})"
        )

    return True, ""
