# Hermes Trader — Autonomous AI Trading Agent

> Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) · Aug 28 – Sep 4, 2026

An autonomous multi-layer AI trading system that ingests financial news, generates sector predictions, critiques its own reasoning, and executes paper trades through Alpaca — with a natural language interface powered by Hermes and MiniMax M2.7 running on a linked DGX Spark cluster.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES VM (ESXi)                         │
│         Dedicated Hermes Agent — MiniMax M2.7 client        │
│    Discord · Home Assistant · Morning Briefing Cron         │
└──────────────────────┬──────────────────────────────────────┘
                       │ 10GbE infra
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
│    RSS + Finnhub + Marketaux → Qwen3-30B scoring           │
│    → BGE-M3 embedding → Qdrant (67K+ vectors)              │
│                                                             │
│  Daily Predictions (8AM ET, M-F):                          │
│    DeepSeek R1-70B → Calibration → Critic → Portfolio      │
│                                                             │
│  Portfolio Manager (9:35AM, 12PM, 3:35PM ET):              │
│    7-gate validation → Alpaca paper execution              │
│                                                             │
│  MCP Servers:                                               │
│    Alpaca MCP     (port 8100) — 72 trading tools           │
│    Pipeline MCP   (port 8101) — 12 pipeline tools          │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Features

### Three-Layer Prediction Protection

Every prediction passes through three independent filters before triggering a trade:

1. **Calibration** — adjusts confidence based on rolling sector win rates (e.g. technology at 30% win rate → -0.10 penalty)
2. **Critic Agent** — independently challenges predictions, flagging contradictions with macro context and known sector dependencies
3. **Portfolio Gates** — 7-gate validation before any order executes (market hours, circuit breaker, position size, sector breaker, confidence threshold, weekly limit, VIX)

### Self-Learning System

- 216+ inference rules discovered through automated post-mortem analysis
- Indirect dependency graph (e.g. TSMC → NVDA, energy → airlines)
- Track record fed back into DeepSeek prompts to correct systematic biases
- Weekly and monthly performance reports generated automatically

### Hermes Orchestration

- Natural language interface via Discord
- Morning briefing cron (8:30 AM ET) — synthesizes predictions, weekly reports, calibration history, and portfolio state into trade approval recommendations
- Validate proposed trades gate-by-gate before execution
- Execute trades that pass all system requirements
- Refuse trades that violate portfolio rules with specific reasoning

### Dual Alpaca Account Architecture

- **Account #1**: Organic pipeline trading (autonomous, since May 2026)
- **Account #2**: Hackathon demo account (PA3Y2DOOQXZW, clean $100K)
- All Hermes-initiated trades validated through `execute_trade()` before placement

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Orchestrator | Hermes Agent + MiniMax M2.7 NVFP4 (Gemini cluster) |
| Gemini Cluster | 2x DGX Spark GB10, linked at 132 Gbps CX7, vLLM-Ray |
| Reasoning | DeepSeek R1-70B via llama.cpp (airig) |
| Scoring | Qwen3-30B-A3B (135 t/s on 4x RTX 3090) |
| Embeddings | BGE-M3 FP16 |
| Vector Store | Qdrant (67K+ trading signals) |
| Trading API | Alpaca Markets (paper trading) |
| MCP Servers | Alpaca MCP (72 tools) + Trading Pipeline MCP (12 tools) |
| Interface | Discord via Hermes gateway |
| Cluster Link | NVIDIA CX7 QSFP at 132 Gbps |

---

## Pipeline Flow

```
News Sources (13)
  RSS Feeds + Finnhub + Marketaux
       ↓ every 5 min
  Qwen3-30B sentiment scoring
       ↓
  BGE-M3 embedding → Qdrant
       ↓ 8AM ET daily
  DeepSeek R1-70B sector predictions
  [with track record context + indirect signal graph]
       ↓
  Calibration (rolling win rate adjustment)
       ↓
  Prediction Critic (challenge/approve/reject)
       ↓
  Hermes Morning Briefing (8:30 AM ET)
  [synthesizes reports + predictions → approval recommendation]
       ↓
  Portfolio Manager — 7-gate validation
  [market hours, circuit breaker, position size,
   sector breaker, confidence threshold, weekly limit, VIX]
       ↓
  Alpaca Paper Trade Execution
```

---

## Trading Pipeline MCP Tools (12)

| Tool | Description |
|------|-------------|
| `get_daily_predictions` | Today's sector predictions with critic verdicts |
| `get_sector_calibration` | Historical win rates and calibration adjustments |
| `get_portfolio_state` | Current positions, cash, P&L |
| `get_sector_breakers` | Which sectors are paused and why |
| `get_recent_signals` | Latest scored news signals from Qdrant |
| `get_active_rules` | Top inference rules from post-mortem learning |
| `get_critic_verdicts` | Recent approve/challenge/reject decisions |
| `get_latest_weekly_report` | Most recent weekly performance report |
| `get_latest_monthly_report` | Most recent monthly performance report |
| `get_trade_history` | Recent closed trades with outcomes |
| `validate_trade` | Run a proposed trade through all 7 portfolio gates |
| `execute_trade` | Validate + execute (only places order if all gates pass) |

---

## Live Performance (as of Aug 2026)

- **Runtime**: 3+ months autonomous operation
- **Predictions generated**: 500+
- **Rules learned**: 216+ (from automated post-mortem analysis)
- **Qdrant vectors**: 67,000+
- **Portfolio**: $100,000 paper capital, profitable
- **Best trade**: +32.5% (RELL)
- **Sector win rates**: Healthcare 100% recent (best), Technology improving

---

## Hackathon Submission

**Built with:**
- Alpaca Trading API + MCP Server
- MiniMax M2.7 NVFP4 (local inference via vLLM-Ray on Gemini cluster)
- DeepSeek R1-70B (local inference via llama.cpp on airig)
- Hermes Agent framework
- FastMCP for custom pipeline MCP server

**Demo**: Interact with Hermes via Discord — ask for portfolio analysis, request trades, or try to bypass the safety gates (they'll hold). Morning briefing runs automatically at 8:30 AM ET on weekdays.

---

## Setup

Key services:

```bash
# Alpaca MCP Server (72 tools) — airig
alpaca-mcp-server --transport streamable-http --host 0.0.0.0 --port 8100

# Trading Pipeline MCP Server (12 tools) — airig
python3 pipeline/alpaca_feed/pipeline_mcp.py

# Gemini vLLM cluster — sparky (head node)
sparkrun run ~/minimax-m2-nvfp4-gemini.yaml

# Hermes Gateway — dedicated VM
hermes gateway run
```

Environment variables required in `pipeline/.env`:

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_HACKATHON_KEY=...
ALPACA_HACKATHON_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

---

*Built during the Alpaca AI Trading Agents Hackathon, August 2026*
