# Hermes Trader — Installation Guide

## Prerequisites

- Python 3.11+
- Git
- Docker (for Qdrant)
- At least one OpenAI-compatible inference endpoint (local or hosted)
- Alpaca Markets paper trading account — [sign up free](https://alpaca.markets)

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/lunarnodal/hermes-trader
cd hermes-trader
```

### 2. Set Up Python Environment

```bash
cd pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp pipeline/.env.example pipeline/.env
```

Edit `pipeline/.env` and fill in:
- Alpaca API keys (required)
- Finnhub and Marketaux API keys (required for news ingestion)
- Inference endpoint URLs and model names (required)
- Storage paths (required)

### 4. Start Qdrant

```bash
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
```

### 5. Initialize the Databases

```bash
cd pipeline
python3 -c "from portfolio.db import init_db; init_db()"
```

### 6. Start the Pipeline Services

```bash
# Alpaca MCP Server (72 trading tools)
alpaca-mcp-server --transport streamable-http --host 0.0.0.0 --port 8100 &

# Trading Pipeline MCP Server (16 pipeline tools)
python3 alpaca_feed/pipeline_mcp.py &

# Trading Dashboard
python3 dashboard/app.py &
```

### 7. Run the Signal Pipeline Manually (First Test)

```bash
# Ingest news
python3 feeds/ingest.py

# Score sentiment
python3 feeds/score.py

# Embed signals into Qdrant
python3 feeds/embed.py

# Run daily predictions
python3 reasoning/daily_predictions.py
```

---

## Inference Engine Setup

The pipeline requires three OpenAI-compatible endpoints:

| Role | Endpoint | Recommended Model | Min VRAM |
|------|----------|-------------------|----------|
| Reasoning/Predictions | `SPARK_LLAMA_HOST` | DeepSeek R1-70B | 40GB |
| Sentiment Scoring | `LLAMA_SCORE_URL` | Qwen3-30B | 20GB |
| Embeddings | `LLAMA_EMBED_URL` | BGE-M3 | 2GB |

Any OpenAI-compatible server works — llama.cpp, Ollama, vLLM, LM Studio, Jan, or a hosted provider like OpenRouter.

**Example with Ollama:**
```bash
ollama serve
ollama pull deepseek-r1:70b
ollama pull qwen3:30b
ollama pull bge-m3

# Set in .env:
SPARK_LLAMA_HOST=http://localhost:11434
LLAMA_SCORE_URL=http://localhost:11434/v1/chat/completions
LLAMA_EMBED_URL=http://localhost:11434/v1/embeddings
```

**Lower VRAM alternative:**
If you don't have 40GB+ VRAM for the reasoning model, use a hosted provider:
```bash
# OpenRouter example
SPARK_LLAMA_HOST=https://openrouter.ai/api/v1
REASONING_MODEL=deepseek/deepseek-r1
# Add OPENAI_API_KEY=your_openrouter_key to .env
```

---

## Cron Schedule (Optional — Automated Operation)

Add to crontab (`crontab -e`) for fully autonomous operation:

```cron
# Signal pipeline — every 5 minutes
*/5 * * * * cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/feeds/ingest.py
*/5 * * * * cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/feeds/score.py
*/5 * * * * cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/feeds/embed.py

# Daily predictions — 8 AM ET weekdays
0 8 * * 1-5 TZ=America/New_York cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/reasoning/daily_predictions.py

# Portfolio entry — 9:35 AM ET weekdays
35 9 * * 1-5 TZ=America/New_York cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/portfolio/manager.py --mode entry

# Midday check — 12 PM ET weekdays
0 12 * * 1-5 TZ=America/New_York cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/portfolio/manager.py --mode exits

# Portfolio close — 3:35 PM ET weekdays
35 15 * * 1-5 TZ=America/New_York cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/portfolio/manager.py --mode close

# Weekly report — Sunday 11 PM
0 23 * * 0 cd /path/to/hermes-trader && pipeline/.venv/bin/python3 pipeline/reasoning/weekly_report.py
```

---

## Hermes Agent Integration (Optional)

To add the natural language interface:

1. Install [Hermes Agent](https://hermes-agent.nousresearch.com)
2. Configure MCP servers in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  alpaca:
    url: http://localhost:8100/mcp
  trading_pipeline:
    url: http://localhost:8101/mcp
model:
  default: your-model-name
  base_url: http://localhost:8000/v1
```

3. Add a SOUL.md describing the trading system context
4. Start the gateway: `hermes gateway run`

---

## MCP Server Tools

Once running, the pipeline exposes 16 tools via the MCP protocol:

| Tool | Description |
|------|-------------|
| `get_daily_predictions` | Today's sector predictions |
| `get_sector_calibration` | Historical win rates |
| `get_portfolio_state` | Current positions and P&L |
| `get_sector_breakers` | Paused sectors |
| `get_recent_signals` | Latest news signals (7-day window) |
| `get_active_rules` | Learned inference rules |
| `get_critic_verdicts` | Recent prediction decisions |
| `get_latest_weekly_report` | Weekly performance report |
| `get_latest_monthly_report` | Monthly performance report |
| `get_trade_history` | Recent closed trades |
| `get_organic_account_info` | Primary Alpaca account |
| `get_hackathon_account_info` | Secondary Alpaca account |
| `get_signal_ledger` | Rejected signal tracking |
| `get_idle_cash_analysis` | Cash opportunity cost |
| `validate_trade` | Gate-by-gate trade validation |
| `execute_trade` | Validated trade execution |

---

## Directory Structure

```
hermes-trader/
└── pipeline/
    ├── alpaca_feed/       # Alpaca integration, MCP server
    ├── dashboard/         # Web dashboard (Flask)
    ├── data/              # SQLite databases (gitignored)
    ├── feeds/             # News ingestion and scoring
    ├── portfolio/         # Position management, gates, selector
    ├── reasoning/         # Predictions, critic, post-mortem
    ├── rules/             # Inference rule discovery
    ├── tickers/           # Ticker taxonomy
    └── .env               # Your config (never commit this)
```

---

## Troubleshooting

**No signals in Qdrant after running embed.py**
Check that `score.py` ran successfully first and produced scored JSONL files in `TIMESERIES_DIR`.

**Predictions fail with connection error**
Verify your inference endpoint is running and `SPARK_LLAMA_HOST` points to it correctly.

**Portfolio manager finds no predictions**
Predictions must run at 8 AM ET before the 9:35 AM portfolio entry window. Run `daily_predictions.py` manually to test.

**Alpaca orders not filling**
Confirm `ALPACA_PAPER=true` and that market hours are active (9:30 AM - 4:00 PM ET weekdays).

---

*For questions or issues, open a GitHub issue at github.com/lunarnodal/hermes-trader*

