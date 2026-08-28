# Hermes Trader — Autonomous AI Trading Agent

> Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) · Aug 28 – Sep 4, 2026

An autonomous multi-layer AI trading system that ingests financial news, generates sector predictions, critiques its own reasoning, and executes paper trades through Alpaca — with a natural language interface powered by Hermes and MiniMax M2.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GEMINI CLUSTER                           │
│         (sparky + sparkier linked at 132 Gbps)             │
│                                                             │
│  sparkier: Hermes Orchestrator (MiniMax M2, 128GB)         │
│    ├── Alpaca MCP (airig:8100) — 72 trading tools          │
│    └── Trading Pipeline MCP (airig:8101) — 9 tools         │
│                                                             │
│  sparky: DeepSeek R1-70B — Deep reasoning engine           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  AIRIG (4x RTX 3090)                        │
│                                                             │
│  Signal Pipeline (every 5 min):                            │
│    RSS + Finnhub + Marketaux → Qwen3-30B scoring           │
│    → BGE-M3 embedding → Qdrant (67K+ vectors)              │
│                                                             │
│  Daily Predictions (8AM ET, M-F):                          │
│    DeepSeek R1-70B → Calibration → Critic → Portfolio      │
│                                                             │
│  Portfolio Manager (9:35AM, 12PM, 3:35PM ET):              │
│    Signal gates → Alpaca paper execution                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Features

**Three-Layer Prediction Protection**
Every prediction passes through three independent filters before triggering a trade:
1. **Calibration** — adjusts confidence based on rolling sector win rates (e.g. technology at 30% win rate → -0.10 penalty)
2. **Critic Agent** — independently challenges predictions, flagging contradictions with macro context and known sector dependencies
3. **Portfolio Gates** — 7-gate validation before any order executes (market hours, circuit breaker, position size, sector breaker, prediction confidence, weekly limit, VIX)

**Self-Learning System**
- 216+ inference rules discovered through automated post-mortem analysis
- Indirect dependency graph (e.g. TSMC → NVDA, energy → airlines)
- Track record fed back into DeepSeek prompts to correct systematic biases
- Lessons database updated after every verified prediction outcome

**Natural Language Trading Interface**
Via Discord, Hermes can:
- Answer trading questions using live Alpaca data + pipeline intelligence
- Validate proposed trades gate-by-gate with specific reasoning
- Execute trades that pass all system requirements
- Refuse trades that violate portfolio rules — explaining exactly why

**Live Alpaca Integration**
- All organic system trades mirror to Alpaca paper account in real-time
- Hermes-initiated trades go through `execute_trade()` MCP tool (gates enforced)
- Direct order placement tools disabled — all orders route through validation

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Orchestrator | Hermes Agent + MiniMax M2 (128GB unified memory) |
| Reasoning | DeepSeek R1-70B via llama.cpp |
| Scoring | Qwen3-30B-A3B (135 t/s on 4x RTX 3090) |
| Embeddings | BGE-M3 FP16 |
| Vector Store | Qdrant (67K+ trading signals) |
| Trading API | Alpaca Markets (paper trading) |
| MCP Servers | Alpaca MCP (72 tools) + Trading Pipeline MCP (9 tools) |
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
  Portfolio Manager
  [7 gates: market hours, circuit breaker, position size,
   sector breaker, confidence threshold, weekly limit, VIX]
       ↓
  Alpaca Paper Trade Execution
```

---

## Trading Pipeline MCP Tools

The `trading_pipeline` MCP server exposes 9 tools to Hermes:

| Tool | Description |
|------|-------------|
| `get_daily_predictions` | Today's sector predictions with critic verdicts |
| `get_sector_calibration` | Historical win rates and calibration adjustments |
| `get_portfolio_state` | Current positions, cash, P&L |
| `get_sector_breakers` | Which sectors are paused and why |
| `get_recent_signals` | Latest scored news signals from Qdrant |
| `get_active_rules` | Top inference rules from post-mortem learning |
| `get_critic_verdicts` | Recent approve/challenge/reject decisions |
| `validate_trade` | Run a proposed trade through all 7 portfolio gates |
| `execute_trade` | Validate + execute (only places order if all gates pass) |

---

## Live Performance (as of Aug 2026)

- **Runtime**: 3+ months autonomous operation
- **Predictions generated**: 480+
- **Rules learned**: 216+ (from automated post-mortem analysis)
- **Qdrant vectors**: 67,000+
- **Portfolio**: $100,000 paper capital, profitable
- **Best trade**: +32.5% (RELL)
- **Sector win rates**: Energy 53% (best), Technology 20%→improving

---

## Hackathon Submission

**Built with:**
- Alpaca Trading API + MCP Server
- MiniMax M2 (local inference via llama.cpp)
- DeepSeek R1-70B (local inference via llama.cpp)
- Hermes Agent framework
- FastMCP for custom MCP server

**Demo**: Interact with Hermes via Discord — ask for portfolio analysis, request trades, or try to bypass the safety gates (they'll hold).

---

## Setup

The system runs on local hardware (DGX Spark cluster + airig GPU server). Key services:

```bash
# Alpaca MCP Server (72 tools)
alpaca-mcp-server --transport streamable-http --host 0.0.0.0 --port 8100

# Trading Pipeline MCP Server (9 tools)  
python3 pipeline/alpaca_feed/pipeline_mcp.py

# Trading Dashboard
python3 pipeline/dashboard/app.py

# Hermes Gateway (sparkier)
hermes gateway run
```

Environment variables required in `pipeline/.env`:
```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_PAPER=true
```

---

*Built during the Alpaca AI Trading Agents Hackathon, August 2026*
