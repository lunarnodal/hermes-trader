"""
hard_gates.py — Deterministic risk layer that LLM cannot override.

This module sits AFTER the critic verdict and enforces non-negotiable
hard limits. It is intentionally separate from the LLM-driven selector
and rule engine so that no model output, prompt injection, or logic error
in the reasoning layer can bypass these protections.

All gates return (approved: bool, reason: str). An approved=False blocks
the trade unconditionally; approved=True passes it onward.
"""

from __future__ import annotations

import logging
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

# ─── Gate helpers ───────────────────────────────────────────────────────────

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
            """SELECT COALESCE(SUM(pnl), 0.0)
               FROM positions
               WHERE closed_at IS NOT NULL
                 AND date(closed_at) = date(?)

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
            "WHERE ticker = ? AND closed_at IS NULL",
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
            "SELECT current_price, shares FROM positions WHERE closed_at IS NULL",
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
            "WHERE ticker = ? AND closed_at IS NULL",
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


# ─── Manual override flag ───────────────────────────────────────────────────

# Set _BYPASS = True to disable all gates (intended for manual trading only).
# NEVER set this from an LLM prompt or rule file.
_BYPASS = False


# ─── Composite gate ─────────────────────────────────────────────────────────

def gate_all(
    conn: "sqlite3.Connection",
    ticker: str,
    proposed_value: float,
    action: str = "BUY",
) -> tuple[bool, str]:
    """
    Run every hard gate in sequence. Returns (approved, first_failure_reason).

    The bypass exists only for manual-intervention workflows. It is NOT
    exposed through any API, config key, or rule file.
    """
    if _BYPASS:
        return True, "[bypass active]"

    gates = [
        check_daily_loss_breaker,
        lambda c: check_max_single_asset(c, ticker, proposed_value),
        lambda c: check_min_cash_reserve(c, proposed_value),
        lambda c: check_duplicate_order(c, ticker, action),
    ]

    for check in gates:
        approved, reason = check(conn)
        if not approved:
            return False, reason

    return True, ""
