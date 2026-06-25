"""
Phase 4 — Periodic self-reflection reports

Generates weekly, monthly, quarterly, and yearly performance reports.
Each report is written by DeepSeek after reviewing all available data,
and saved as markdown to /mnt/qnap/timeseries/reports/

Reports serve dual purpose:
  1. Human-readable performance summary for you to review
  2. Learning input — DeepSeek identifies patterns and recommends adjustments

Cron schedule:
  Weekly:    Sunday 11:00 PM ET
  Monthly:   1st Sunday of month (after weekly)
  Quarterly: 1st Sunday of quarter
  Yearly:    January 1st
"""

import sqlite3
import logging
import json
import requests
import os
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

PAPER_DB    = Path("/home/trading/trading-ai/data/paper_trading.db")
RULES_DB    = Path("/home/trading/trading-ai/data/rules.db")
LESSONS_DB  = Path("/home/trading/trading-ai/data/lessons.db")
PORTFOLIO_DB = Path("/home/trading/trading-ai/data/portfolio.db")
REPORTS_DIR = Path("/mnt/qnap/timeseries/reports")

SPARK_HOST  = os.getenv("SPARK_LLAMA_HOST",
              os.getenv("SPARK_OLLAMA_HOST", "http://172.29.11.225:8080"))
MODEL       = "deepseek-r1"


# ─── Data gathering ───────────────────────────────────────────────────────────

def get_portfolio_data(since: datetime) -> dict:
    """Gather portfolio performance data for period"""
    conn = sqlite3.connect(PORTFOLIO_DB)

    # Snapshots for P&L curve
    snaps = conn.execute("""
        SELECT snapshot_at, total_value, cash, positions_value, open_positions
        FROM portfolio_snapshots
        WHERE snapshot_at >= ?
        ORDER BY snapshot_at
    """, (since.isoformat(),)).fetchall()

    # Closed positions in period
    closed = conn.execute("""
        SELECT ticker, sector, entry_price, exit_price, pnl, pnl_pct,
               exit_reason, entry_date, exit_date, shares
        FROM positions
        WHERE status = 'closed' AND exit_date >= ?
        ORDER BY exit_date
    """, (since.isoformat(),)).fetchall()

    # Currently open positions
    open_pos = conn.execute("""
        SELECT ticker, sector, entry_price, current_price,
               (current_price - entry_price) * shares AS unrealized_pnl,
               ((current_price - entry_price) / entry_price * 100) AS unrealized_pct,
               tiers_triggered
        FROM positions WHERE status = 'open'
    """).fetchall()

    # Cash flow
    cash_now = conn.execute(
        "SELECT SUM(amount) FROM cash_ledger"
    ).fetchone()[0] or 0

    total_now = conn.execute("""
        SELECT SUM(shares * current_price) FROM positions WHERE status = 'open'
    """).fetchone()[0] or 0

    conn.close()

    wins   = [c for c in closed if (c[4] or 0) > 0]
    losses = [c for c in closed if (c[4] or 0) <= 0]
    total_pnl = sum(c[4] or 0 for c in closed)

    # Sector breakdown
    sector_pnl = defaultdict(float)
    sector_trades = defaultdict(int)
    for c in closed:
        sector_pnl[c[1] or 'unknown'] += c[4] or 0
        sector_trades[c[1] or 'unknown'] += 1

    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for c in closed:
        reason = c[6] or 'unknown'
        if 'profit_tier' in reason or 'take_profit' in reason:
            exit_reasons['profit_exit'] += 1
        elif 'stop_loss' in reason:
            exit_reasons['stop_loss'] += 1
        elif 'time_exit' in reason:
            exit_reasons['time_exit'] += 1
        else:
            exit_reasons['other'] += 1

    return {
        'period_start':     since.isoformat(),
        'total_trades':     len(closed),
        'wins':             len(wins),
        'losses':           len(losses),
        'win_rate':         len(wins) / len(closed) if closed else 0,
        'total_pnl':        round(total_pnl, 2),
        'avg_win':          round(sum(c[4] for c in wins) / len(wins), 2) if wins else 0,
        'avg_loss':         round(sum(c[4] for c in losses) / len(losses), 2) if losses else 0,
        'best_trade':       max(closed, key=lambda c: c[4] or 0) if closed else None,
        'worst_trade':      min(closed, key=lambda c: c[4] or 0) if closed else None,
        'sector_pnl':       dict(sector_pnl),
        'sector_trades':    dict(sector_trades),
        'exit_reasons':     dict(exit_reasons),
        'open_positions':   len(open_pos),
        'portfolio_value':  round(cash_now + total_now, 2),
        'snapshots':        len(snaps),
        'start_value':      snaps[0][1] if snaps else 50000,
        'end_value':        snaps[-1][1] if snaps else cash_now + total_now,
    }


def get_prediction_data(since: datetime) -> dict:
    """Gather prediction performance data for period"""
    conn = sqlite3.connect(PAPER_DB)

    preds = conn.execute("""
        SELECT query, direction, confidence, was_correct, actual_direction,
               created_at, timeframe
        FROM predictions
        WHERE created_at >= ? AND was_correct IS NOT NULL
        ORDER BY created_at
    """, (since.isoformat(),)).fetchall()

    conn.close()

    if not preds:
        return {'total': 0, 'correct': 0, 'win_rate': 0, 'by_sector': {}}

    correct = sum(1 for p in preds if p[3] == 1)

    # By sector
    sector_stats = defaultdict(lambda: {'correct': 0, 'total': 0})
    for p in preds:
        q = p[0].lower()
        sector = 'other'
        for s, keywords in [
            ('technology', ['technology', 'semiconductor', 'ai']),
            ('healthcare', ['healthcare', 'biotech']),
            ('energy', ['energy', 'oil']),
            ('financials', ['financial', 'bank']),
            ('market_overview', ['market outlook', 'macro', 's&p']),
            ('materials', ['materials', 'mining']),
            ('industrials', ['industrials', 'defense']),
            ('consumer', ['consumer', 'retail']),
        ]:
            if any(kw in q for kw in keywords):
                sector = s
                break
        sector_stats[sector]['total'] += 1
        if p[3] == 1:
            sector_stats[sector]['correct'] += 1

    # Direction accuracy
    direction_stats = defaultdict(lambda: {'correct': 0, 'total': 0})
    for p in preds:
        direction_stats[p[1]]['total'] += 1
        if p[3] == 1:
            direction_stats[p[1]]['correct'] += 1

    return {
        'total':           len(preds),
        'correct':         correct,
        'win_rate':        round(correct / len(preds), 3) if preds else 0,
        'by_sector':       {s: {**v, 'win_rate': round(v['correct']/v['total'], 3)}
                           for s, v in sector_stats.items()},
        'by_direction':    {d: {**v, 'win_rate': round(v['correct']/v['total'], 3)}
                           for d, v in direction_stats.items()},
        'high_conf_wrong': [
            {'query': p[0][:60], 'confidence': p[2], 'actual': p[4]}
            for p in preds if p[3] == 0 and p[2] >= 0.75
        ],
    }


def get_rules_data(since: datetime) -> dict:
    """Gather rule discovery data for period"""
    conn = sqlite3.connect(RULES_DB)

    new_rules = conn.execute("""
        SELECT trigger, sectors, source, confidence, created_at
        FROM inference_rules
        WHERE created_at >= ?
        ORDER BY created_at DESC
    """, (since.isoformat(),)).fetchall()

    total_rules = conn.execute(
        "SELECT COUNT(*) FROM inference_rules WHERE active=1"
    ).fetchone()[0]

    conn.close()

    return {
        'new_rules':    len(new_rules),
        'total_rules':  total_rules,
        'rules_added':  [{'trigger': r[0], 'source': r[2]} for r in new_rules[:10]],
    }


def get_lessons_data(since: datetime) -> dict:
    """Gather post-mortem lessons for period"""
    try:
        conn = sqlite3.connect(LESSONS_DB)
        lessons = conn.execute("""
            SELECT prediction_id, direction, confidence, root_cause, lesson
            FROM lessons_learned
            WHERE analyzed_at >= ?
            ORDER BY analyzed_at DESC
        """, (since.isoformat(),)).fetchall()

        deps = conn.execute("""
            SELECT from_entity, to_entity, relationship, occurrences
            FROM indirect_dependencies
            WHERE last_seen >= ?
            ORDER BY last_seen DESC LIMIT 10
        """, (since.isoformat(),)).fetchall()

        conn.close()
        return {
            'lessons':      len(lessons),
            'top_lessons':  [{'cause': l[3][:80], 'lesson': l[4][:80]}
                            for l in lessons[:5]],
            'dependencies': [{'from': d[0], 'to': d[1], 'times': d[3]}
                            for d in deps],
        }
    except:
        return {'lessons': 0, 'top_lessons': [], 'dependencies': []}


# ─── DeepSeek report generation ───────────────────────────────────────────────

REPORT_PROMPT = """You are analyzing the performance of an autonomous AI trading system.
Write a {period_name} performance report based on the data below.

The system trades US stocks and ETFs using news sentiment analysis.
Starting capital: $50,000. Philosophy: Boglehead-inspired, disciplined profit taking.

=== PORTFOLIO PERFORMANCE ===
{portfolio_summary}

=== PREDICTION ACCURACY ===
{prediction_summary}

=== LEARNING & ADAPTATION ===
{learning_summary}

Write a comprehensive report with these sections:
1. Executive Summary (3-4 sentences, key numbers)
2. Portfolio Performance (P&L, best/worst trades, sector breakdown)
3. Prediction Accuracy (win rate by sector, what worked/didn't)
4. Key Mistakes & Root Causes (what went wrong and why)
5. What Worked Well (genuine wins and correct calls)
6. Learning Progress (rules added, dependencies discovered, calibration changes)
7. Recommended Adjustments (specific CONFIG or strategy changes for next period)
8. Outlook (what to watch for in the coming period)

Be specific with numbers. Be honest about failures. Identify actionable improvements.
Include a section on idle cash opportunity cost and what BIL/treasury parking would have added.
Format as clean markdown."""


def generate_report_with_deepseek(period_name: str,
                                   portfolio: dict,
                                   predictions: dict,
                                   rules: dict,
                                   lessons: dict) -> str:
    """Ask DeepSeek to write the report narrative"""

    best  = portfolio['best_trade']
    worst = portfolio['worst_trade']
    best_str  = f"{best[0]}  ${best[4]:+.2f}"  if best  else "N/A"
    worst_str = f"{worst[0]} ${worst[4]:+.2f}" if worst else "N/A"

    portfolio_summary = (
        f"Period: {portfolio['period_start'][:10]} to today\n"
        f"Trades: {portfolio['total_trades']} ({portfolio['wins']} wins, {portfolio['losses']} losses)\n"
        f"Win rate: {portfolio['win_rate']:.1%}\n"
        f"Total P&L: ${portfolio['total_pnl']:+.2f}\n"
        f"Avg win: ${portfolio['avg_win']:+.2f} | Avg loss: ${portfolio['avg_loss']:+.2f}\n"
        f"Portfolio value: ${portfolio['portfolio_value']:,.2f}\n"
        f"Exit breakdown: {portfolio['exit_reasons']}\n"
        f"Sector P&L: {json.dumps(portfolio['sector_pnl'], indent=2)}\n"
        f"Best trade: {best_str}\n"
        f"Worst trade: {worst_str}\n"
    )

    prediction_summary = f"""
Total verified predictions: {predictions['total']}
Overall win rate: {predictions['win_rate']:.1%}
By sector: {json.dumps({s: f"{v['correct']}/{v['total']} = {v['win_rate']:.0%}"
                         for s, v in predictions.get('by_sector', {}).items()}, indent=2)}
By direction: {json.dumps({d: f"{v['correct']}/{v['total']} = {v['win_rate']:.0%}"
                            for d, v in predictions.get('by_direction', {}).items()}, indent=2)}
High confidence wrong predictions: {len(predictions.get('high_conf_wrong', []))}
"""

    learning_summary = f"""
New rules added: {rules['new_rules']} (total: {rules['total_rules']})
Post-mortem analyses: {lessons['lessons']}
Indirect dependencies discovered: {len(lessons.get('dependencies', []))}
Top lessons: {json.dumps([l['lesson'] for l in lessons.get('top_lessons', [])], indent=2)}
Key dependencies found: {json.dumps([f"{d['from']} → {d['to']} ({d['times']}x)"
                                      for d in lessons.get('dependencies', [])[:5]], indent=2)}
"""

    try:
        resp = requests.post(
            f"{SPARK_HOST}/v1/chat/completions",
            json={
                "model":       MODEL,
                "stream":      False,
                "max_tokens":  4096,
                "temperature": 0.2,
                "messages": [{
                    "role":    "user",
                    "content": REPORT_PROMPT.format(
                        period_name=period_name,
                        portfolio_summary=portfolio_summary,
                        prediction_summary=prediction_summary,
                        learning_summary=learning_summary
                    )
                }]
            },
            timeout=600
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip thinking tags
        if "</think>" in content:
            content = content[content.rfind("</think>") + 8:].strip()

        return content

    except Exception as e:
        log.error(f"DeepSeek report generation failed: {e}")
        return f"Report generation failed: {e}"


# ─── Report assembly ──────────────────────────────────────────────────────────

def build_report(period_name: str, since: datetime) -> str:
    """Gather all data and generate full report"""
    log.info(f"Building {period_name} report from {since.date()} to today")

    portfolio   = get_portfolio_data(since)

    # Cash yield opportunity cost
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent))
        from portfolio.db import init_db as _init_port
        from portfolio.cash_yield import get_weekly_summary, calculate_shadow_yield
        _pconn = _init_port()
        cash_yield_summary = get_weekly_summary(_pconn)
        cash_yield_data    = calculate_shadow_yield(_pconn, since)
        _pconn.close()
    except Exception as e:
        cash_yield_summary = f"Cash yield tracking unavailable: {e}"
        cash_yield_data    = {}
    predictions = get_prediction_data(since)
    rules       = get_rules_data(since)
    lessons     = get_lessons_data(since)

    log.info(f"Data gathered: {portfolio['total_trades']} trades, "
             f"{predictions['total']} predictions, "
             f"{rules['new_rules']} new rules")

    # Generate narrative with DeepSeek
    log.info("Generating report narrative with DeepSeek...")
    narrative = generate_report_with_deepseek(
        period_name, portfolio, predictions, rules, lessons
    )

    # Build full report
    now = datetime.now()
    report = f"""# Trading AI {period_name} Report
**Generated:** {now.strftime('%Y-%m-%d %H:%M ET')}
**Period:** {since.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}

---

{narrative}

---

## Raw Data

### Portfolio
```json
{json.dumps(portfolio, indent=2, default=str)}
```

### Predictions
```json
{json.dumps(predictions, indent=2, default=str)}
```

### Rules Added This Period
{chr(10).join(f"- `{r['trigger']}` ({r['source']})" for r in rules['rules_added'])}

### Cash Yield (Shadow BIL Position)
{cash_yield_summary}

### Indirect Dependencies Discovered
{chr(10).join(f"- {d['from']} → {d['to']} ({d['times']}x)" for d in lessons.get('dependencies', []))}
"""
    return report


def save_report(report: str, period_name: str, since: datetime) -> Path:
    """Save report to QNAP"""
    period_dir = REPORTS_DIR / period_name.lower()
    period_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{since.strftime('%Y-%m-%d')}_{period_name.lower()}.md"
    filepath = period_dir / filename
    filepath.write_text(report)
    log.info(f"Report saved: {filepath}")
    return filepath


def run_weekly_report() -> Path:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    report = build_report("Weekly", since)
    return save_report(report, "weekly", since)


def run_monthly_report() -> Path:
    since = datetime.now(timezone.utc) - timedelta(days=30)
    report = build_report("Monthly", since)
    return save_report(report, "monthly", since)


def run_quarterly_report() -> Path:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    report = build_report("Quarterly", since)
    return save_report(report, "quarterly", since)


def run_yearly_report() -> Path:
    since = datetime.now(timezone.utc) - timedelta(days=365)
    report = build_report("Yearly", since)
    return save_report(report, "yearly", since)


def should_run_monthly() -> bool:
    """First Sunday of the month"""
    today = date.today()
    return today.weekday() == 6 and today.day <= 7


def should_run_quarterly() -> bool:
    """First Sunday of Jan, Apr, Jul, Oct"""
    today = date.today()
    return should_run_monthly() and today.month in (1, 4, 7, 10)


def should_run_yearly() -> bool:
    """January 1st"""
    today = date.today()
    return today.month == 1 and today.day == 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("/mnt/qnap/timeseries/logs/reports.log"),
            logging.StreamHandler()
        ]
    )

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--period",
                        choices=["weekly", "monthly", "quarterly", "yearly", "auto"],
                        default="auto",
                        help="Report period to generate")
    args = parser.parse_args()

    if args.period == "weekly" or args.period == "auto":
        log.info("Generating weekly report...")
        path = run_weekly_report()
        print(f"Weekly report: {path}")

    if args.period == "monthly" or (args.period == "auto" and should_run_monthly()):
        log.info("Generating monthly report...")
        path = run_monthly_report()
        print(f"Monthly report: {path}")

    if args.period == "quarterly" or (args.period == "auto" and should_run_quarterly()):
        log.info("Generating quarterly report...")
        path = run_quarterly_report()
        print(f"Quarterly report: {path}")

    if args.period == "yearly" or (args.period == "auto" and should_run_yearly()):
        log.info("Generating yearly report...")
        path = run_yearly_report()
        print(f"Yearly report: {path}")
