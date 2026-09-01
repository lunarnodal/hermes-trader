# Hermes Trader — Autonomous AI Trading Agent

> Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) · Aug 28 – Sep 4, 2026

An autonomous multi-layer AI trading system that has been running live since May 2026. It ingests financial news, generates sector predictions, critiques its own reasoning, enforces multi-layer safety gates, and executes paper trades through Alpaca — with a natural language interface powered by Hermes and MiniMax M2.7 NVFP4 running on a linked DGX Spark cluster. Core pipeline operations run fully autonomously; Hermes provides on-demand analysis and trade interaction via Discord.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES VM (ESXi)                         │
│         Dedicated Hermes Agent — MiniMax M2.7 client        │
│    Discord · Home Assistant · Morning Briefing Cron         │
└──────────────────────┬──────────────────────────────────────┘
                       │ 10GbE infra (172.29.11.x)
┌──────────────────────▼──────────────────────────────────────┐
│                  GEMINI CLUSTER                              │
│         sparky + sparkier linked at 132 Gbps CX7            │
│     MiniMax M2.7 NVFP4 via vLLM-Ray (port 8000)            │
│              256GB unified memory pool                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  AIRIG (4x RTX 3090, 96GB VRAM)             │
│                                                             │
│  Signal Pipeline (every 5 min):                             │
│    RSS feeds → Qwen3-30B scoring (135 t/s)                 │
│    → BGE-M3 embedding → Qdrant (67K+ vectors)              │
│                                                             │
│  Daily Predictions (8AM ET, M-F):                          │
│    DeepSeek R1-70B → Calibration → Critic → Portfolio      │
│                                                             │
│  Portfolio Manager (9:35AM, 12PM, 3:35PM ET):              │
│    Multi-layer gate validation → Alpaca paper execution     │
│                                                             │
│  MCP Servers:                                               │
│    Alpaca MCP     (port 8100) — 72 trading tools           │
│    Pipeline MCP   (port 8101) — 16 pipeline tools          │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Features

### Multi-Layer Prediction Protection

Every prediction passes through six independent layers before triggering a trade:

1. **7-Day Signal Recency Filter** — only news from the last 7 days reaches DeepSeek; stale articles from weeks ago are excluded
2. **Calibration** — rolling sector win rates adjust raw confidence scores (e.g. technology at 44% win rate → -10% penalty applied to raw score)
3. **Meta-Correction** — sector penalty multiplied against raw confidence before gate check (`adjusted = raw × (1 - sector_penalty)`)
4. **Direction Calibration** — bullish predictions penalized -15%, bearish -25%, mixed hard-blocked (0% historical win rate)
5. **Hard-Block Threshold** — sector win rate <35% AND bullish AND adjusted confidence <65% → auto-reject regardless of critic verdict
6. **Critic Agent** — independently challenges predictions, flagging contradictions with macro context and sector dependencies
7. **7 Portfolio Gates** — market hours, circuit breaker, position size, sector breaker, confidence threshold (post-adjustment), weekly limit, VIX

### Self-Learning System

- 216+ inference rules discovered through automated post-mortem analysis
- Indirect dependency graph (e.g. TSMC → NVDA, energy → airlines)
- Track record fed back into DeepSeek prompts to correct systematic biases
- **Repeating failure circuit breaker** — if the same query has been wrong 3+ times at ≥70% confidence, a skepticism warning is injected before the next prediction
- Signal ledger tracks every rejected prediction with gate reason for outcome analysis
- Date-injected daily queries prevent stale signal matching

### Dynamic Risk Management

- **VIX-adaptive hold periods** — low VIX extends holds 40%, high VIX shortens by 50%
- **Sector-volatility stop losses** — technology gets 1.5× stop room, defense gets 0.9×
- **Bearish signal suppression** — 3+ bearish signals vs bullish blocks entry even on bullish prediction
- **Idle cash opportunity cost tracking** — flags when cash idle 5+ days vs BIL equivalent yield

### Hermes Orchestration Layer

- Natural language interface via Discord
- **Morning briefing cron (8:30 AM ET)** — synthesizes predictions, weekly reports, calibration history, and portfolio state into structured trade approval recommendation
- Gate-by-gate trade validation with specific rejection reasoning
- Full execution pipeline via `execute_trade()` — validation + order placement in one call
- Refuses unsafe orders with exact explanation

### Dual Alpaca Account Architecture

- **Account #1 — Organic** (PA3I1CJSOEVO): autonomous pipeline trading since May 2026
- **Account #2 — Hackathon** (PA3Y2DOOQXZW): clean $100K demo account started Aug 28, 2026
- Explicit MCP tools for each: `get_organic_account_info()` and `get_hackathon_account_info()`
- Safe sell validation prevents naked shorts when accounts diverge

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Orchestrator | Hermes Agent + MiniMax M2.7 NVFP4 (Gemini cluster) |
| Gemini Cluster | 2× DGX Spark GB10, 132 Gbps CX7 link, vLLM-Ray |
| Reasoning | DeepSeek R1-70B via llama.cpp (airig) |
| Scoring | Qwen3-30B-A3B (135 t/s on 4× RTX 3090) |
| Embeddings | BGE-M3 FP16 |
| Vector Store | Qdrant (67K+ trading signals, published date indexed) |
| Trading API | Alpaca Markets (paper trading, 2 accounts) |
| MCP Servers | Alpaca MCP (72 tools) + Trading Pipeline MCP (16 tools) |
| Interface | Discord via Hermes gateway on dedicated ESXi VM |
| Cluster Link | NVIDIA CX7 QSFP at 132 Gbps |

---

## Pipeline Flow

```
News Sources (11 RSS feeds)
       ↓ every 5 min
  Qwen3-30B sentiment scoring
       ↓
  BGE-M3 embedding → Qdrant (7-day recency filter)
       ↓ 8AM ET daily (date-injected queries)
  DeepSeek R1-70B sector predictions
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
  Alpaca Paper Trade Execution (organic + hackathon accounts)
       ↓
  Signal Ledger (tracks rejected signals for outcome analysis)
```

---

## Trading Pipeline MCP Tools (16)

| Tool | Description |
|------|-------------|
| `get_daily_predictions` | Today's sector predictions with critic verdicts |
| `get_sector_calibration` | Historical win rates and calibration adjustments |
| `get_portfolio_state` | Current positions, cash, P&L (organic account) |
| `get_sector_breakers` | Which sectors are paused and why |
| `get_recent_signals` | Latest scored news signals from Qdrant (7-day window) |
| `get_active_rules` | Top inference rules from post-mortem learning |
| `get_critic_verdicts` | Recent approve/challenge/reject decisions |
| `get_latest_weekly_report` | Most recent weekly performance report |
| `get_latest_monthly_report` | Most recent monthly performance report |
| `get_trade_history` | Recent closed trades with outcomes |
| `get_organic_account_info` | Live Alpaca data for organic trading account |
| `get_hackathon_account_info` | Live Alpaca data for hackathon demo account |
| `get_signal_ledger` | Rejected signals with gate reasons for outcome analysis |
| `get_idle_cash_analysis` | Opportunity cost of idle cash vs BIL equivalent |
| `validate_trade` | Run a proposed trade through all portfolio gates |
| `execute_trade` | Validate + execute (only places order if all gates pass) |

---

## Live Performance (as of Sep 1, 2026)

- **Runtime**: 3+ months autonomous operation (since May 2026)
- **Predictions generated**: 500+
- **Rules learned**: 216+ (from automated post-mortem analysis)
- **Qdrant vectors**: 67,000+
- **Organic portfolio**: $100,237 (+0.24%)
- **Hackathon portfolio**: $99,945 (started Aug 28, mirroring organic positions)
- **Best trade**: +32.5% (RELL)
- **30-day win rate**: 67% (4W/2L)
- **Sector win rates**: Healthcare 100% recent, Technology 44% (with hard-block active)

### What Hermes Found (Self-Analysis, Aug 31 2026)

On Aug 31, the operator asked Hermes to perform a deep analysis of 3 months of trading reports. Hermes identified 9 system improvements — all implemented the same night:

- Discovered the system was approving tech predictions at 70% confidence despite a 44% historical win rate
- Found that 6/8 rejected tech signals were correctly blocked — the critic was right every time it triggered
- Identified that the same static query was returning stale AI infrastructure articles from months ago
- Recommended and the team implemented: meta-correction layer, direction calibration, VIX-adaptive holds, dynamic stop losses, stale query detection, and a signal ledger

---

## Hackathon Submission

**Built with:**
- Alpaca Trading API + MCP Server
- MiniMax M2.7 NVFP4 (local inference via vLLM-Ray on Gemini cluster)
- DeepSeek R1-70B (local inference via llama.cpp on airig)
- Hermes Agent framework (Nous Research)
- FastMCP for custom pipeline MCP server

**Demo**: Interact with Hermes via Discord — ask for portfolio analysis, compare both Alpaca accounts, request trades, or try to bypass the safety gates. Morning briefing runs automatically at 8:30 AM ET on weekdays.

---

## Setup

Key services:

```bash
# Alpaca MCP Server (72 tools) — airig
alpaca-mcp-server --transport streamable-http --host 0.0.0.0 --port 8100

# Trading Pipeline MCP Server (16 tools) — airig
python3 pipeline/alpaca_feed/pipeline_mcp.py

# Trading Dashboard — airig
python3 pipeline/dashboard/app.py

# Gemini vLLM cluster — sparky (head node)
sparkrun run ~/minimax-m2-nvfp4-gemini.yaml

# Hermes Gateway — dedicated ESXi VM
hermes gateway run
```

Environment variables required in `pipeline/.env`:

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_HACKATHON_KEY=...
ALPACA_HACKATHON_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
SPARK_LLAMA_HOST=http://<airig-ip>:8083
```

---

*Built during the Alpaca AI Trading Agents Hackathon, August 2026*
