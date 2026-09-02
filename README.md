# Hermes Trader — Autonomous AI Trading Agent

> Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) · Aug 28 – Sep 4, 2026

An autonomous multi-layer AI trading system that has been running live since May 2026. It ingests financial news, generates sector predictions, critiques its own reasoning, enforces multi-layer safety gates, and executes paper trades through Alpaca — with a natural language interface powered by Hermes and a locally-hosted large language model. Core pipeline operations run fully autonomously; Hermes provides on-demand analysis and trade interaction via Discord.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES VM                                │
│         Dedicated Hermes Agent — LLM client                 │
│    Discord · Home Assistant · Morning Briefing Cron         │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  INFERENCE CLUSTER                           │
│         Large model serving via vLLM or compatible          │
│         256GB+ unified memory recommended                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  PIPELINE HOST (GPU required)                │
│                                                             │
│  Signal Pipeline (every 5 min):                             │
│    RSS feeds → Scoring model (sentiment)                    │
│    → Embedding model → Qdrant (67K+ vectors)               │
│                                                             │
│  Daily Predictions (8AM ET, M-F):                          │
│    Reasoning model → Calibration → Critic → Portfolio      │
│                                                             │
│  Portfolio Manager (9:35AM, 12PM, 3:35PM ET):              │
│    Multi-layer gate validation → Alpaca paper execution     │
│                                                             │
│  MCP Servers:                                               │
│    Alpaca MCP     (port 8100) — 72 trading tools           │
│    Pipeline MCP   (port 8101) — 16 pipeline tools          │
└─────────────────────────────────────────────────────────────┘
```

> **Single machine deployment**: All components can run on one machine with sufficient GPU VRAM. The architecture above shows a distributed setup for scale.

---

## Key Features

### Multi-Layer Prediction Protection

Every prediction passes through six independent layers before triggering a trade:

1. **7-Day Signal Recency Filter** — only news from the last 7 days reaches the reasoning model; stale articles from weeks ago are excluded
2. **Calibration** — rolling sector win rates adjust raw confidence scores (e.g. a sector at 44% win rate gets a -10% penalty applied to raw score)
3. **Meta-Correction** — sector penalty multiplied against raw confidence before gate check (`adjusted = raw × (1 - sector_penalty)`)
4. **Direction Calibration** — bullish predictions penalized -15%, bearish -25%, mixed hard-blocked (0% historical win rate)
5. **Hard-Block Threshold** — sector win rate <35% AND bullish AND adjusted confidence <65% → auto-reject regardless of critic verdict
6. **Critic Agent** — independently challenges predictions, flagging contradictions with macro context and sector dependencies
7. **7 Portfolio Gates** — market hours, circuit breaker, position size, sector breaker, confidence threshold (post-adjustment), weekly limit, VIX

### Self-Learning System

- 216+ inference rules discovered through automated post-mortem analysis
- Indirect dependency graph (e.g. TSMC → NVDA, energy → airlines)
- Track record fed back into reasoning model prompts to correct systematic biases
- **Repeating failure circuit breaker** — if the same sector query has been wrong 3+ times at ≥70% confidence, a skepticism warning is injected before the next prediction
- Signal ledger tracks every rejected prediction with gate reason for outcome analysis
- Date-injected daily queries prevent stale signal matching

### Dynamic Risk Management

- **VIX-adaptive hold periods** — low VIX extends holds 40%, high VIX shortens by 50%
- **Sector-volatility stop losses** — high-volatility sectors get more room, stable sectors get tighter stops
- **Bearish signal suppression** — 3+ bearish signals vs bullish blocks entry even on bullish prediction
- **Idle cash opportunity cost tracking** — flags when cash idle 5+ days vs BIL equivalent yield

### Hermes Orchestration Layer

- Natural language interface via Discord
- **Morning briefing cron (8:30 AM ET)** — synthesizes predictions, weekly reports, calibration history, and portfolio state into structured trade approval recommendation
- Gate-by-gate trade validation with specific rejection reasoning
- Full execution pipeline via `execute_trade()` — validation + order placement in one call
- Refuses unsafe orders with exact explanation

### Dual Alpaca Account Architecture

- **Account #1 — Organic**: autonomous pipeline trading since May 2026
- **Account #2 — Demo**: clean $100K account for demonstration purposes
- Explicit MCP tools for each: `get_organic_account_info()` and `get_hackathon_account_info()`
- Safe sell validation prevents naked shorts when accounts diverge

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Orchestrator | Hermes Agent + locally-hosted LLM |
| Reasoning/Predictions | DeepSeek R1-70B or compatible (70B+ recommended) |
| Sentiment Scoring | Qwen3-30B or compatible |
| Embeddings | BGE-M3 or compatible |
| Vector Store | Qdrant (67K+ trading signals, date-indexed) |
| Trading API | Alpaca Markets (paper trading, 2 accounts) |
| MCP Servers | Alpaca MCP (72 tools) + Trading Pipeline MCP (16 tools) |
| Interface | Discord via Hermes gateway |
| Inference Server | Any OpenAI-compatible endpoint |

---

## Pipeline Flow

```
News Sources (11 RSS feeds)
       ↓ every 5 min
  Scoring model — sentiment analysis
       ↓
  Embedding model → Qdrant (7-day recency filter)
       ↓ 8AM ET daily (date-injected queries)
  Reasoning model — sector predictions
  [with track record context + repeating failure warnings]
       ↓
  Calibration → Meta-Correction → Direction Penalty
       ↓
  Hard-Block Gate (win rate <35% + bullish + conf <65%)
       ↓
  Prediction Critic (challenge/approve/reject)
       ↓
  Hermes Morning Briefing (8:30 AM ET)
  [synthesizes reports + predictions → approval recommendation]
       ↓
  Portfolio Manager — 7-gate validation
  [market hours, circuit breaker, position size (dynamic stop loss),
   sector breaker, confidence threshold, weekly limit, VIX-adaptive]
       ↓
  Alpaca Paper Trade Execution (mirrored to both accounts)
       ↓
  Signal Ledger (tracks rejected signals for outcome analysis)
```

---

## Trading Pipeline MCP Tools (16)

| Tool | Description |
|------|-------------|
| `get_daily_predictions` | Today's sector predictions with critic verdicts |
| `get_sector_calibration` | Historical win rates and calibration adjustments |
| `get_portfolio_state` | Current positions, cash, P&L |
| `get_sector_breakers` | Which sectors are paused and why |
| `get_recent_signals` | Latest scored news signals (7-day window) |
| `get_active_rules` | Top inference rules from post-mortem learning |
| `get_critic_verdicts` | Recent approve/challenge/reject decisions |
| `get_latest_weekly_report` | Most recent weekly performance report |
| `get_latest_monthly_report` | Most recent monthly performance report |
| `get_trade_history` | Recent closed trades with outcomes |
| `get_organic_account_info` | Live Alpaca data for primary trading account |
| `get_hackathon_account_info` | Live Alpaca data for demo account |
| `get_signal_ledger` | Rejected signals with gate reasons for outcome analysis |
| `get_idle_cash_analysis` | Opportunity cost of idle cash vs BIL equivalent |
| `validate_trade` | Run a proposed trade through all portfolio gates |
| `execute_trade` | Validate + execute (only places order if all gates pass) |

---

## Live Performance (as of Sep 2026)

- **Runtime**: 3+ months autonomous operation (since May 2026)
- **Predictions generated**: 500+
- **Rules learned**: 216+ (from automated post-mortem analysis)
- **Qdrant vectors**: 67,000+
- **30-day win rate**: 71% (closed trades)
- **Best trade**: +32.5% (RELL)
- **Sector win rates**: Healthcare 100% recent, Technology 44% (hard-block active)

### What Hermes Discovered (Operator-Requested Analysis, Aug 31 2026)

On Aug 31, the operator asked Hermes to perform a deep analysis of 3 months of trading reports. Hermes identified 9 system improvements — all implemented the same night:

- Discovered the system was approving tech predictions at 70% confidence despite a 44% historical win rate
- Found that 6/8 rejected technology signals were correctly blocked — the critic was right every time
- Identified that the same static query was returning stale articles from months ago
- Recommended and the team implemented: meta-correction layer, direction calibration, VIX-adaptive holds, dynamic stop losses, stale query detection, and a signal ledger

---

## Hackathon Submission

**Built with:**
- Alpaca Trading API + MCP Server
- Locally-hosted LLM inference (OpenAI-compatible)
- Hermes Agent framework (Nous Research)
- FastMCP for custom pipeline MCP server


---

## Setup

See [INSTALL.md](INSTALL.md) for full installation instructions.

Quick start:

```bash
git clone https://github.com/lunarnodal/hermes-trader
cd hermes-trader/pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with your settings
```

---

*Built during the Alpaca AI Trading Agents Hackathon, August 2026*
