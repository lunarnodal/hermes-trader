"""
Trading Pipeline MCP Server

Exposes trading system data to Hermes via MCP protocol.
Runs on airig:8101, accessible from sparkier.

Tools exposed:
  - get_daily_predictions: Today's sector predictions with confidence
  - get_sector_calibration: Win rates and calibration adjustments
  - get_recent_signals: Latest scored news signals from Qdrant
  - get_portfolio_state: Current positions, cash, P&L
  - get_active_rules: Top inference rules being applied
  - get_critic_verdicts: Recent prediction critic decisions
  - get_sector_breakers: Which sectors are paused and why
  - validate_trade: Run a proposed trade through all portfolio gates
  - execute_trade: Validate and execute a trade (gates must pass)
"""

import sqlite3
import json
import os
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure pipeline directory is in path for imports
_pipeline_dir = str(Path(__file__).parent.parent)
if _pipeline_dir not in sys.path:
    sys.path.insert(0, _pipeline_dir)

from mcp.server.fastmcp import FastMCP
from portfolio.market_calendar import is_trading_day
from portfolio.vix_gate import get_vix_gate
from alpaca_feed.data import get_live_prices

log = logging.getLogger(__name__)

mcp = FastMCP("Trading Pipeline", host="0.0.0.0", port=8101)

PAPER_DB     = Path("/home/trading/trading-ai/data/paper_trading.db")
PORTFOLIO_DB = Path("/home/trading/trading-ai/data/portfolio.db")
RULES_DB     = Path("/home/trading/trading-ai/data/rules.db")
LESSONS_DB   = Path("/home/trading/trading-ai/data/lessons.db")


@mcp.tool()
def get_daily_predictions() -> str:
    """
    Get today's sector predictions from the AI pipeline.
    Returns direction, confidence, and critic verdict for each sector.
    """
    conn = sqlite3.connect(PAPER_DB)
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT query, direction, confidence, critic_verdict, critic_reasoning,
               reasoning_summary, created_at
        FROM predictions
        WHERE date(created_at) = ? AND was_correct IS NULL
        ORDER BY created_at DESC
    """, (today,)).fetchall()
    conn.close()

    if not rows:
        return json.dumps({"error": "No predictions found for today", "date": today})

    predictions = []
    for r in rows:
        query = r[0]
        sector = query.split('—')[0].strip() if '—' in query else query[:40]
        predictions.append({
            "sector":         sector,
            "direction":      r[1],
            "confidence":     f"{r[2]:.0%}",
            "critic_verdict": r[3] or "pending",
            "critic_note":    r[4][:100] if r[4] else "",
            "summary":        r[5][:150] if r[5] else "",
            "generated_at":   r[6][:16],
        })

    return json.dumps({
        "date": today,
        "predictions": predictions,
        "count": len(predictions),
        "actionable": [p for p in predictions
                       if float(p["confidence"].strip('%'))/100 >= 0.70
                       and p["critic_verdict"] != "reject"]
    }, indent=2)


@mcp.tool()
def get_sector_calibration() -> str:
    """
    Get sector win rates and calibration adjustments.
    Shows which sectors the AI has been accurate on historically.
    """
    conn = sqlite3.connect(PAPER_DB)
    rows = conn.execute("""
        SELECT
            CASE
                WHEN query LIKE '%energy%' THEN 'energy'
                WHEN query LIKE '%technology%' OR query LIKE '%semiconductor%' THEN 'technology'
                WHEN query LIKE '%financial%' OR query LIKE '%bank%' THEN 'financials'
                WHEN query LIKE '%healthcare%' OR query LIKE '%biotech%' THEN 'healthcare'
                WHEN query LIKE '%materials%' OR query LIKE '%mining%' THEN 'materials'
                WHEN query LIKE '%industrial%' THEN 'industrials'
                WHEN query LIKE '%consumer%' THEN 'consumer'
                ELSE 'market_overview'
            END as sector,
            COUNT(*) as total,
            SUM(CASE WHEN was_correct=1 THEN 1 ELSE 0 END) as correct
        FROM predictions
        WHERE was_correct IS NOT NULL
        GROUP BY sector
        ORDER BY correct*1.0/COUNT(*) DESC
    """).fetchall()
    conn.close()

    calibration = []
    for r in rows:
        sector, total, correct = r
        win_rate = correct / total if total > 0 else 0
        if win_rate < 0.35:
            adj = -0.20
        elif win_rate < 0.45:
            adj = -0.10
        elif win_rate > 0.55:
            adj = +0.05
        else:
            adj = 0.0

        calibration.append({
            "sector":     sector,
            "win_rate":   f"{win_rate:.0%}",
            "correct":    f"{correct}/{total}",
            "adjustment": f"{adj:+.2f}",
            "status":     "strong" if win_rate > 0.55 else
                          "weak" if win_rate < 0.35 else "average"
        })

    return json.dumps({"sector_calibration": calibration}, indent=2)


@mcp.tool()
def get_portfolio_state() -> str:
    """
    Get current portfolio state: positions, cash, P&L, sector exposure.
    """
    conn = sqlite3.connect(PORTFOLIO_DB)

    cash_row = conn.execute(
        "SELECT balance FROM cash_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    cash = cash_row[0] if cash_row else 0

    positions = conn.execute("""
        SELECT ticker, sector, shares, entry_price, current_price,
               stop_loss, tiers_triggered, entry_date
        FROM positions WHERE status='open'
        ORDER BY entry_date
    """).fetchall()

    pos_list = []
    total_pos_value = 0
    for p in positions:
        current = p[4] or p[3]
        value = p[2] * current
        pnl_pct = (current - p[3]) / p[3] * 100
        total_pos_value += value
        pos_list.append({
            "ticker":     p[0],
            "sector":     p[1],
            "shares":     p[2],
            "entry":      f"${p[3]:.2f}",
            "current":    f"${current:.2f}",
            "pnl_pct":    f"{pnl_pct:+.1f}%",
            "value":      f"${value:.2f}",
            "stop":       f"${p[5]:.2f}",
            "tiers":      p[6] or 0,
            "held_since": p[7][:10],
        })

    total = cash + total_pos_value
    conn.close()

    return json.dumps({
        "cash":            f"${cash:,.2f}",
        "positions_value": f"${total_pos_value:,.2f}",
        "total":           f"${total:,.2f}",
        "return_pct":      f"{(total-100000)/100000*100:+.2f}%",
        "open_positions":  len(pos_list),
        "positions":       pos_list,
        "cash_pct":        f"{cash/total*100:.0f}%" if total > 0 else "100%",
    }, indent=2)


@mcp.tool()
def get_sector_breakers() -> str:
    """
    Get sector circuit breaker status — which sectors are paused due to losses.
    """
    conn = sqlite3.connect(PORTFOLIO_DB)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    sectors = ['technology', 'healthcare', 'energy', 'defense',
               'financials', 'consumer', 'materials', 'industrials']

    breakers = []
    for sector in sectors:
        stops = conn.execute("""
            SELECT COUNT(*), GROUP_CONCAT(ticker || ' (' || pnl_pct || '%)', ', ')
            FROM positions
            WHERE sector=? AND status='closed'
              AND exit_reason LIKE 'stop_loss%'
              AND exit_date >= ?
        """, (sector, cutoff)).fetchone()

        count = stops[0] if stops else 0
        tickers = stops[1] if stops and stops[1] else ""
        paused = count >= 2

        breakers.append({
            "sector":   sector,
            "stops_7d": count,
            "paused":   paused,
            "reason":   f"{tickers}" if paused else "open for entries",
        })

    conn.close()
    return json.dumps({
        "sector_breakers": breakers,
        "paused_count": sum(1 for b in breakers if b["paused"]),
    }, indent=2)


@mcp.tool()
def get_recent_signals(sector: str = "", limit: int = 5) -> str:
    """
    Get recent high-confidence signals from the news pipeline.
    Optionally filter by sector (energy, technology, healthcare, etc.)
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchText

        client = QdrantClient(host="localhost", port=6333)

        filter_conditions = None
        if sector:
            filter_conditions = Filter(
                must=[FieldCondition(key="sectors", match=MatchText(text=sector))]
            )

        results = client.scroll(
            collection_name="trading_signals",
            scroll_filter=filter_conditions,
            limit=limit,
            with_payload=True,
            order_by="timestamp"
        )

        signals = []
        for point in results[0]:
            p = point.payload
            signals.append({
                "title":     p.get("title", "")[:80],
                "source":    p.get("source", ""),
                "sentiment": p.get("sentiment", ""),
                "confidence": p.get("confidence", 0),
                "sectors":   p.get("sectors", []),
                "tickers":   p.get("tickers", []),
                "themes":    p.get("macro_themes", [])[:3],
            })

        return json.dumps({
            "sector_filter": sector or "all",
            "signals": signals,
            "count": len(signals)
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_active_rules(limit: int = 10) -> str:
    """
    Get the top active inference rules the system has learned.
    These rules were discovered through post-mortem analysis of failed predictions.
    """
    conn = sqlite3.connect(RULES_DB)
    rows = conn.execute("""
        SELECT trigger, sectors, confidence, source, created_at
        FROM inference_rules
        WHERE status='active' OR status IS NULL
        ORDER BY confidence DESC, created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM inference_rules").fetchone()[0]
    conn.close()

    rules = [{"trigger": r[0], "sectors": r[1],
              "confidence": r[2], "source": r[3]} for r in rows]

    return json.dumps({"total_rules": total, "top_rules": rules}, indent=2)


@mcp.tool()
def get_critic_verdicts(days: int = 3) -> str:
    """
    Get recent prediction critic verdicts — which predictions were approved,
    challenged, or rejected and why.
    """
    conn = sqlite3.connect(PAPER_DB)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT query, direction, confidence, critic_verdict,
               critic_reasoning, critic_confidence, created_at
        FROM predictions
        WHERE critic_verdict IS NOT NULL AND critic_verdict != ''
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 20
    """, (cutoff,)).fetchall()
    conn.close()

    verdicts = []
    for r in rows:
        sector = r[0].split('—')[0].strip()[:30]
        verdicts.append({
            "sector":        sector,
            "direction":     r[1],
            "original_conf": f"{r[2]:.0%}",
            "verdict":       r[3],
            "adjusted_conf": f"{r[5]:.0%}" if r[5] else "same",
            "reason":        r[4][:120] if r[4] else "",
            "time":          r[6][:16],
        })

    summary = {v: sum(1 for x in verdicts if x["verdict"] == v)
               for v in ["approve", "challenge", "reject"]}

    return json.dumps({
        "days": days,
        "summary": summary,
        "verdicts": verdicts
    }, indent=2)


@mcp.tool()
def validate_trade(ticker: str, shares: float, side: str = "buy") -> str:
    """
    Validate a proposed trade against all portfolio gates.
    Returns gate-by-gate approval status and reasoning.
    Use this before execute_trade to understand why a trade may be rejected.
    """
    gates = []
    approved = True

    # Get current price
    try:
        prices = get_live_prices([ticker])
        price = prices.get(ticker)
    except Exception:
        price = None

    value = round(shares * price, 2) if price else None

    # Gate 1: Market hours
    try:
        trading_day = is_trading_day()
    except Exception:
        trading_day = True  # assume open if check fails

    if not trading_day:
        from datetime import date
        gates.append({
            "gate": "Market Hours",
            "pass": False,
            "reason": f"Market is closed today ({date.today()})"
        })
        approved = False
    else:
        gates.append({"gate": "Market Hours", "pass": True,
                      "reason": "Market is open"})

    if side.lower() == "buy":
        conn = sqlite3.connect(PORTFOLIO_DB)

        # Gate 2: Portfolio circuit breaker
        snaps = conn.execute("""
            SELECT total_value FROM portfolio_snapshots
            ORDER BY snapshot_at DESC LIMIT 10
        """).fetchall()
        if snaps:
            peak = max(s[0] for s in snaps)
            current_val = snaps[0][0]
            drawdown = (peak - current_val) / peak
            if drawdown >= 0.05:
                gates.append({
                    "gate": "Portfolio Circuit Breaker",
                    "pass": False,
                    "reason": f"Portfolio down {drawdown:.1%} from peak — all entries paused"
                })
                approved = False
            else:
                gates.append({"gate": "Portfolio Circuit Breaker", "pass": True,
                              "reason": f"Drawdown {drawdown:.1%} within limit"})

        # Gate 3: Position size (max 10% of portfolio)
        if value:
            cash_row = conn.execute(
                "SELECT balance FROM cash_ledger ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cash = cash_row[0] if cash_row else 0
            max_position = cash * 0.10
            if value > max_position:
                gates.append({
                    "gate": "Position Size",
                    "pass": False,
                    "reason": f"${value:,.0f} exceeds 10% max position size (${max_position:,.0f} of ${cash:,.0f} cash)"
                })
                approved = False
            else:
                gates.append({"gate": "Position Size", "pass": True,
                              "reason": f"${value:,.0f} within 10% limit"})

        # Gate 4: Sector circuit breaker
        sector_map = {
            'XLK': 'technology', 'NVDA': 'technology', 'AAPL': 'technology',
            'MSFT': 'technology', 'AMD': 'technology', 'INTC': 'technology',
            'XLV': 'healthcare', 'UNH': 'healthcare', 'JNJ': 'healthcare',
            'XLE': 'energy', 'XOM': 'energy', 'CVX': 'energy',
            'XLF': 'financials', 'JPM': 'financials', 'BAC': 'financials',
            'XLB': 'materials', 'XLI': 'industrials', 'XLP': 'consumer',
            'ITA': 'defense', 'SPY': 'macro', 'QQQ': 'technology',
            'AMZN': 'technology', 'CRM': 'technology', 'GOOGL': 'technology',
            'META': 'technology', 'TSLA': 'technology',
        }
        sector = sector_map.get(ticker.upper(), 'unknown')

        if sector != 'unknown':
            cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            stops = conn.execute("""
                SELECT COUNT(*) FROM positions
                WHERE sector=? AND status='closed'
                  AND exit_reason LIKE 'stop_loss%'
                  AND exit_date >= ?
            """, (sector, cutoff_7d)).fetchone()[0]

            if stops >= 2:
                gates.append({
                    "gate": "Sector Circuit Breaker",
                    "pass": False,
                    "reason": f"{sector} sector has {stops} stop losses in last 7 days — entries paused"
                })
                approved = False
            else:
                gates.append({"gate": "Sector Circuit Breaker", "pass": True,
                              "reason": f"{sector}: {stops} stops this week, below threshold"})

        # Gate 5: Prediction confidence
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if sector and sector != 'unknown':
            paper_conn = sqlite3.connect(PAPER_DB)
            pred = paper_conn.execute("""
                SELECT direction, confidence, critic_verdict FROM predictions
                WHERE query LIKE ? AND date(created_at) = ?
                  AND was_correct IS NULL
                ORDER BY created_at DESC LIMIT 1
            """, (f'%{sector}%', today)).fetchone()
            paper_conn.close()

            if pred:
                direction, confidence, verdict = pred
                if confidence < 0.70:
                    gates.append({
                        "gate": "Prediction Confidence",
                        "pass": False,
                        "reason": f"{sector}: {direction} {confidence:.0%} — below 0.70 threshold (critic: {verdict})"
                    })
                    approved = False
                else:
                    gates.append({"gate": "Prediction Confidence", "pass": True,
                                  "reason": f"{sector}: {direction} {confidence:.0%} ≥ 0.70"})
            else:
                gates.append({
                    "gate": "Prediction Confidence",
                    "pass": False,
                    "reason": f"No prediction found for {sector} today"
                })
                approved = False

        # Gate 6: Weekly trade limit
        week_start = datetime.now(timezone.utc)
        week_start = week_start - timedelta(days=week_start.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_buys = conn.execute("""
            SELECT COUNT(*) FROM transactions
            WHERE action='BUY' AND timestamp >= ?
        """, (week_start.isoformat(),)).fetchone()[0]

        if week_buys >= 5:
            gates.append({
                "gate": "Weekly Trade Limit",
                "pass": False,
                "reason": f"Weekly limit reached ({week_buys}/5 trades this week)"
            })
            approved = False
        else:
            gates.append({"gate": "Weekly Trade Limit", "pass": True,
                          "reason": f"{week_buys}/5 trades used this week"})

        # Gate 7: VIX
        try:
            vix = get_vix_gate()
            if vix['action'] == 'pause':
                gates.append({"gate": "VIX Gate", "pass": False,
                              "reason": vix['reason']})
                approved = False
            else:
                gates.append({"gate": "VIX Gate", "pass": True,
                              "reason": vix['reason']})
        except Exception:
            gates.append({"gate": "VIX Gate", "pass": True,
                          "reason": "VIX check unavailable — proceeding"})

        conn.close()

    passed = sum(1 for g in gates if g["pass"])
    total_gates = len(gates)

    return json.dumps({
        "ticker":   ticker,
        "shares":   shares,
        "side":     side,
        "price":    f"${price:.2f}" if price else "unknown",
        "value":    f"${value:,.2f}" if value else "unknown",
        "approved": approved,
        "summary":  f"{passed}/{total_gates} gates passed",
        "gates":    gates,
        "verdict":  "APPROVED — trade meets all system requirements" if approved
                    else "REJECTED — trade does not meet system requirements"
    }, indent=2)


@mcp.tool()
def execute_trade(ticker: str, shares: float, side: str = "buy") -> str:
    """
    Execute a trade through the portfolio management system.
    Runs all gates via validate_trade first — only places order if ALL gates pass.
    This is the ONLY correct way to place orders through Hermes.
    """
    # Step 1: Run validation
    validation = json.loads(validate_trade(ticker, shares, side))

    if not validation['approved']:
        failed = [g for g in validation['gates'] if not g['pass']]
        return json.dumps({
            "status":      "REJECTED",
            "ticker":      ticker,
            "shares":      shares,
            "side":        side,
            "message":     f"Trade rejected — {len(failed)} gate(s) failed",
            "failed_gates": failed,
            "all_gates":   validation['gates'],
            "action":      "No order placed. Address the failed gates before retrying."
        }, indent=2)

    # Step 2: All gates passed — place the order
    try:
        from alpaca_feed.trading import place_market_order
        result = place_market_order(
            ticker, shares, side,
            reason=f"Hermes-initiated {side} — all gates passed"
        )

        if result.get('success'):
            return json.dumps({
                "status":       "EXECUTED",
                "ticker":       ticker,
                "shares":       shares,
                "side":         side,
                "order_id":     result.get('order_id'),
                "alpaca_status": result.get('status'),
                "gates_passed": validation['summary'],
                "message":      "Order placed successfully via Alpaca paper account"
            }, indent=2)
        else:
            return json.dumps({
                "status":  "FAILED",
                "ticker":  ticker,
                "error":   result.get('error'),
                "message": "All gates passed but Alpaca order failed"
            }, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "ERROR",
            "ticker": ticker,
            "error":  str(e)
        }, indent=2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log.info("Starting Trading Pipeline MCP on 0.0.0.0:8101")
    mcp.run(transport="streamable-http")

