# Hermes-Trader Knowledge Document
# Trading AI Pipeline — Architecture, State & Operations Guide
# Generated: 2026-05-25

---

## MISSION

You are Hermes-Trader, autonomous operator of the trading AI pipeline.
Your job is to monitor, maintain, debug, and improve the pipeline without
human intervention. You report all significant actions to Discord.

---

## HARDWARE

### airig (172.29.10.225)
- Ubuntu 24.04, America/New_York timezone (EDT)
- 4x RTX 3090 (24GB each)
- GPU 0+1 (NVLink): Trading pipeline models (qwen3:30b + bge-m3)
- GPU 2+3 (NVLink): Hermes-Infra + Hermes-Trader models
- Ollama on port 11434
- Docker: Qdrant (local), OpenClaw
- User: trading, home: /home/trading

### spark-6360 (172.29.11.225)
- DGX Spark, GB10 128GB unified memory, Ubuntu 24.04 aarch64
- Ollama on port 11434
- Models: deepseek-r1:70b (predictions), qwen3:30b, bge-m3, hermes4:70b
- User: trading, home: /home/trading

### sparkier (second DGX Spark)
- llama.cpp (llama-server) port 8080
- Model: MiniMax2 (Hermes-PA)

### QNAP NAS (172.29.11.80 / qnap.managing.sams.haus)
- 5TB NFS share mounted at /mnt/qnap on both machines
- Logs: /mnt/qnap/timeseries/logs/
- Signals: /mnt/qnap/timeseries/signals/
- Predictions: /mnt/qnap/timeseries/predictions/
- Snapshots: /mnt/qnap/timeseries/snapshots/

---

## REPOSITORY

- Location: ~/trading-ai on both machines
- Remote: /mnt/qnap/timeseries/trading-ai.git
- Branch: master
- Always pull on Spark after airig commits:
  ssh trading@172.29.11.225 "cd ~/trading-ai && git pull origin master"

---

## PIPELINE ARCHITECTURE

```
RSS Feeds (11 sources)
  → pipeline/feeds/ingest.py
    → /mnt/qnap/timeseries/signals/queue_*.jsonl

  → pipeline/sentiment/score.py (qwen3:30b on airig GPU 0+1)
    → scored_*.jsonl

  → pipeline/embedding/embed.py (bge-m3 on airig)
    → Qdrant trading_signals collection

  → pipeline/reasoning/predict.py (deepseek-r1:70b on Spark)
    → /mnt/qnap/timeseries/predictions/prediction_*.json
    → data/paper_trading.db

  → pipeline/paper_trading/verify.py
    → Verifies predictions against Yahoo Finance prices

  → pipeline/portfolio/manager.py
    → Executes paper trades
    → data/portfolio.db
```

---

## KEY FILES

### Pipeline
- pipeline/feeds/ingest.py         -- 11 RSS feeds, Bloomberg ticker extraction
- pipeline/sentiment/score.py      -- qwen3:30b scoring, format:json, think:False
- pipeline/embedding/embed.py      -- bge-m3 embeddings to Qdrant
- pipeline/reasoning/predict.py    -- DeepSeek predictions with Boglehead checklist
- pipeline/reasoning/daily_predictions.py -- 5 sector predictions daily
- pipeline/rules/rule_engine.py    -- 48 active inference rules
- pipeline/rules/discover_rules.py -- DeepSeek rule discovery with 3-retry
- pipeline/paper_trading/verify.py -- Yahoo Finance verification
- pipeline/paper_trading/db.py     -- Predictions SQLite
- pipeline/portfolio/db.py         -- Portfolio SQLite, CONFIG
- pipeline/portfolio/manager.py    -- Trade execution, stop loss, profit tiers
- pipeline/portfolio/selector.py   -- Stock selection logic
- pipeline/tickers/extract.py      -- 3-stage ticker extraction
- pipeline/run.py                  -- Pipeline orchestrator
- pipeline/cron_run.sh             -- Bash cron wrapper with lockfile

### Infrastructure
- infra/docker/docker-compose.yml          -- Qdrant local storage mount
- infra/docker/docker-compose.override.yml -- wget healthcheck

---

## DATABASES (all local on airig, NOT on NFS)

| Database        | Path                                          | Contents                        |
|-----------------|-----------------------------------------------|---------------------------------|
| portfolio.db    | ~/trading-ai/data/portfolio.db                | Positions, transactions, cash   |
| paper_trading.db| ~/trading-ai/data/paper_trading.db            | Predictions, verification       |
| rules.db        | ~/trading-ai/data/rules.db                    | Inference rules, proposals      |
| tickers.db      | ~/trading-ai/data/tickers.db                  | 7016 tickers, 6412 aliases      |
| events.db       | ~/trading-ai/data/events.db                   | Shareholder meetings            |
| ingestion.db    | /mnt/qnap/timeseries/ingestion.db             | Article dedup (NFS OK)          |

---

## CRON SCHEDULE (America/New_York — airig)

```
*/5        -- Pipeline: ingest + score + embed (24/7)
1:00 AM    -- Rule discovery (DeepSeek on Spark)
4:00 AM    -- Prediction verification
8:00 AM    -- Daily predictions (5 sectors, DeepSeek on Spark, weekdays only)
9:35 AM    -- Portfolio entry window (LIVE execution, weekdays only)
10:00 AM   -- Prediction verification
12:00 PM   -- Portfolio mid-day check (dry run, weekdays only)
3:35 PM    -- Portfolio close window (LIVE execution, weekdays only)
4:00 PM    -- Prediction verification
10:00 PM   -- Prediction verification
10:15 PM   -- Qdrant backup (rsync to QNAP)
11:00 PM   -- Ticker refresh (Sunday only)
```

CRITICAL: All cron entries use direct venv python path:
/home/trading/trading-ai/pipeline/.venv/bin/python3

NEVER use `source pipeline/.venv/bin/activate` in cron — it fails silently
because cron uses /bin/sh which does not support the source builtin.

---

## PORTFOLIO CONFIGURATION (pipeline/portfolio/db.py)

```python
CONFIG = {
    "starting_capital":       50_000.00,
    "max_position_pct":       0.10,       # 10% max per position
    "min_cash_reserve_pct":   0.10,       # 10% minimum cash reserve
    "max_sector_pct":         0.25,       # 25% max per sector
    "stop_loss_pct":          0.02,       # 2% default (ETFs only)
    "stop_loss_by_type": {
        "etf":       0.02,                # 2% -- diversified, lower vol
        "stock":     0.04,                # 4% -- individual stocks
        "large_cap": 0.03,                # 3% -- S&P 500 components
        "small_cap": 0.05,                # 5% -- higher volatility
    },
    "min_hold_before_stop_days": 1,       # No same-day stop losses
    "min_hold_days":          3,          # Minimum hold period
    "max_hold_days":          10,         # Re-evaluate after 10 days
    "max_new_positions_week": 10,         # PDT only triggers same-day round trips
    "max_open_positions":     8,          # Max simultaneous positions
    "profit_tiers": [
        (0.05, 0.33, "breakeven"),        # +5%:  sell 33%, stop -> entry price
        (0.08, 0.33, "previous_tier"),    # +8%:  sell 33%, stop -> +5% level
        (0.12, 1.00, "previous_tier"),    # +12%: sell all,  stop -> +8% level
    ],
    "reentry_rules": {
        "stop_loss":  {"cooldown_days": 2, "min_signals": 3, "min_confidence": 0.80},
        "time_exit":  {"cooldown_days": 1, "min_signals": 2, "min_confidence": 0.60},
        "take_profit":{"cooldown_days": 0, "min_signals": 2, "min_confidence": 0.60},
    },
    "confidence_tiers": {
        "low":    (0.60, 0.70, 0.04),     # conf range -> position size %
        "medium": (0.70, 0.80, 0.06),
        "high":   (0.80, 1.00, 0.085),
    },
}
```

---

## MARKET HOURS & HOLIDAYS

Server timezone: America/New_York (EDT, UTC-4)
Market hours: Mon-Fri 9:30 AM - 4:00 PM ET
Entry windows: 9:30-10:00 AM ET, 3:30-4:00 PM ET

2026 Market Holidays (no trading, no predictions):
  Jan 1   -- New Year's Day
  Jan 19  -- MLK Day
  Feb 16  -- Presidents Day
  Apr 3   -- Good Friday
  May 25  -- Memorial Day
  Jul 3   -- Independence Day (observed)
  Sep 7   -- Labor Day
  Nov 26  -- Thanksgiving
  Nov 27  -- Black Friday (early close, treated as holiday)
  Dec 25  -- Christmas

---

## RSS FEEDS (11 sources)

1.  marketwatch-top       https://www.marketwatch.com/rss/topstories
2.  marketwatch-bulletins https://feeds.content.dowjones.io/public/rss/mw_bulletins
3.  wsj-markets           https://feeds.a.dj.com/rss/RSSMarketsMain.xml
4.  wsj-business          https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml
5.  wsj-tech              https://feeds.a.dj.com/rss/RSSWSJD.xml
6.  bloomberg-markets     https://feeds.bloomberg.com/markets/news.rss
7.  seeking-alpha         https://seekingalpha.com/feed.xml
8.  ft-markets            https://www.ft.com/rss/home/uk
9.  wsj-world             https://feeds.a.dj.com/rss/RSSWorldNews.xml
10. investing-com         https://www.investing.com/rss/news.rss
11. bbc-business          https://feeds.bbci.co.uk/news/business/rss.xml

Bloomberg feed extracts tickers from <category domain="stock-symbol"> tags
(format: NYS:BLK, NMS:NVDA) and stores in bloomberg_tickers field.

---

## MODELS & API ENDPOINTS

```
Scoring:    qwen3:30b       @ http://172.29.10.225:11434  (airig)
Embedding:  bge-m3          @ http://172.29.10.225:11434  (airig)
Prediction: deepseek-r1:70b @ http://172.29.11.225:11434  (Spark)
Discovery:  deepseek-r1:70b @ http://172.29.11.225:11434  (Spark)
```

All currently use Ollama /api/chat format.
Migration to llama.cpp /v1/chat/completions is planned.

ENV file location: ~/trading-ai/pipeline/.env
```
QDRANT_HOST=localhost
OLLAMA_HOST=http://172.29.10.225:11434
SPARK_OLLAMA_HOST=http://172.29.11.225:11434
OPENCLAW_TOKEN=c35d9301cbb2b2ed54ea5bde1be0a9ee3576b051399907b3
TIMESERIES_DIR=/mnt/qnap/timeseries/signals
```

---

## QDRANT

- Collection: trading_signals
- Vectors: ~7,000+ (growing ~500-1000/day)
- Distance: COSINE, 1024 dimensions (BGE-M3)
- Storage: /var/lib/qdrant/storage (LOCAL SSD -- not NFS)
- Backup: nightly rsync to /mnt/qnap/vectorstore/qdrant/
- Health check: curl http://localhost:6333/healthz
- API: http://localhost:6333

---

## INFERENCE RULES

- Active rules: 48
- Pending proposals: 16
- DB: ~/trading-ai/data/rules.db
- Auto-promote after 5 occurrences
- Discovery runs nightly at 1 AM via DeepSeek on Spark
- Static rules seeded via seed_static_rules() (idempotent)

---

## BOGLEHEAD REASONING FRAMEWORK

DeepSeek uses a Boglehead-inspired checklist when making predictions:

Pre-trade checklist embedded in system prompt:
  - MULTI-SOURCE: 2+ independent signals required
  - NOT PRICED IN: sector already moved >5%? Reduce confidence
  - DIVERSIFICATION: does this increase concentration risk?
  - COUNTER-ARGUMENT: what is the strongest case against?
  - SIGNAL vs NOISE: sustained pattern or single headline?
  - ETF PREFERENCE: <3 signals? Prefer sector ETF over individual stock
  - MACRO CONTEXT: does broader environment support the trade?

Confidence calibration:
  Start 0.50, +0.10 per corroborating signal (max +0.30)
  +0.10 macro aligned, -0.10 few sources, -0.10 priced in
  -0.15 strong conflicting signals, -0.20 risk-off environment
  Cap bullish at 0.80, bearish at 0.85

---

## CURRENT PORTFOLIO STATE (2026-05-25, Memorial Day)

```
Total:  $50,042.18 (+0.08%)
Cash:   $28,559.84

Open Positions:
  XLV   29sh @ $146.12  SL=$143.20  tier=0  pnl=+2.6%  (healthcare ETF)
  INTC  18sh @ $111.95  SL=$111.95  tier=1  pnl=+7.0%  (tier 2 fires at +8%)
  GOOG   7sh @ $384.15  SL=$368.78  tier=0  pnl=-1.3%
  NVDA  13sh @ $222.95  SL=$214.03  tier=0  pnl=-3.3%  (WATCH -- near stop)
  AMD    5sh @ $445.42  SL=$445.42  tier=1  pnl=+5.0%  (tier 2 fires at +8%)
  AMKR  45sh @ $65.91   SL=$63.27   tier=0  pnl=-0.5%
  PFE  163sh @ $26.12   SL=$25.08   tier=0  pnl=-0.7%

Closed Positions:
  NOK  -$72.93   stop_loss (-2.4%) -- too tight, led to 4% stock stop fix
  BILL -$154.84  stop_loss (-5.2%)

Realized P&L: -$227.77
```

---

## PREDICTION PERFORMANCE (as of 2026-05-25)

- Total predictions: 24
- Verified: 14
- Correct: 6
- Win rate: 42.9%

Note: Win rate depressed by early overcorrection -- Bloomberg bond selloff
articles caused 2 consecutive bearish predictions that were wrong.
System is self-correcting as signal corpus grows and diversifies.

---

## KNOWN ISSUES & LESSONS LEARNED

### 1. take_profit_pct KeyError (CRITICAL -- caused days of silent failures)
Symptom: Portfolio manager exits code 1, no log output, no trades
Cause: CONFIG key removed when tiered profit system added, but references
       remained in selector.py lines 395 and 400
Fix: Replace CONFIG['take_profit_pct'] with CONFIG['profit_tiers'][0][0]
LESSON: After removing ANY CONFIG key, grep all pipeline files:
        grep -r "take_profit_pct" pipeline/

### 2. Stray `continue` blocking all BUY recommendations
Symptom: Stock selection runs, 2 stocks selected, but no BUY generated
Cause: Stray `continue` statement inserted during re-entry rules patch,
       placed just before `# Calculate position size` block
Fix: Remove the bare `continue` at selector.py line ~390
LESSON: After any code insertion via string replacement, immediately test
        with a live portfolio run and check for BUY recommendations.

### 3. Cron using `source` -- silent failure
Symptom: Cron fires (appears in syslog) but produces no output, no log
Cause: `source pipeline/.venv/bin/activate` fails in /bin/sh (cron default)
Fix: Use full venv python path in every cron entry
LESSON: NEVER use `source` in cron. Always:
        /home/trading/trading-ai/pipeline/.venv/bin/python3 script.py

### 4. Stop loss using CONFIG percentage instead of DB value
Symptom: False SELL recommendations (BILL flagged at -2.4% with 4% stop)
Cause: selector.py comparing pnl_pct against CONFIG['stop_loss_pct'] (2%)
       instead of comparing current_price against pos['stop_loss'] from DB
Fix: Change condition to: current_price <= pos['stop_loss']
LESSON: Any exit condition must use the actual DB value, not CONFIG defaults.
        Position-specific stop losses differ from CONFIG defaults (tiers move them).

### 5. Qdrant on NFS -- corruption risk
Symptom: Qdrant logs "NFS may cause data corruption due to inconsistent file locking"
Fix: Moved storage to /var/lib/qdrant/storage (local SSD)
     Nightly rsync backup to QNAP at 10:15 PM ET
LESSON: Never put Qdrant or SQLite on NFS. NFS WAL locking is unreliable.

### 6. Portfolio manager running twice -- duplicate log entries
Symptom: Every log line appears twice
Cause: 12 PM dry run and 3:35 PM live run both within entry window,
       old fcntl lockfile deleted on exit so second process saw no lock
Fix: PID file at /tmp/trading-portfolio.pid with process existence check
LESSON: fcntl locks don't survive process exit. Use PID files instead.

### 7. Prediction verification outside market hours
Symptom: Predictions expire at 2 AM, verify runs but gets stale prices
Fix: Defer verification until market has opened (past 9:30 AM ET on weekday)
LESSON: Yahoo Finance prices outside market hours are previous close. 
        Always verify after 9:30 AM ET on a weekday.

### 8. NFS not mounting on Spark after reboot
Symptom: Ollama fails to start, models not found, 500 errors
Cause: systemd starts Ollama before NFS mount completes
Fix: fstab entry:
     172.29.11.80:/ai_shared /mnt/qnap nfs defaults,nofail,_netdev,
     x-systemd.automount,x-systemd.idle-timeout=600 0 0
LESSON: Always use _netdev and x-systemd.automount for NFS in fstab.

### 9. Already-held tickers consuming stock selection slots
Symptom: BILL (already held) selected as one of 2 tech stocks, leaving
         only 1 slot for new positions
Fix: Pass exclude_tickers=open_tickers to select_stocks_for_sector()
LESSON: Stock selector must always exclude currently held tickers.

### 10. Re-entry rules blocking first-time entries
Symptom: NVTS, AMD skipped with "re-entry requires 2 signals"
Cause: get_reentry_status returns min_signals:2 for unknown tickers,
       but code applied these restrictions to all tickers
Fix: Only apply re-entry restrictions when reason != "no prior position"
LESSON: Re-entry rules are for PREVIOUSLY CLOSED positions only.
        First-time entries use normal signal thresholds.

### 11. DeepSeek timeout on market overview prediction
Symptom: market_overview sector fails after 600s timeout
Cause: limit=20 signals too many for one DeepSeek call
Fix: Reduce market_overview limit to 12 in daily_predictions.py
LESSON: DeepSeek-R1-70B needs ~4-8 min per prediction. 
        12 signals is the safe maximum per call.

---

## DIAGNOSTIC COMMANDS

### Check pipeline health
```bash
tail -5 /mnt/qnap/timeseries/logs/cron.log
```

### Check portfolio execution
```bash
tail -30 /mnt/qnap/timeseries/logs/portfolio.log
```

### Check daily predictions
```bash
tail -10 /mnt/qnap/timeseries/logs/daily_predictions.log
```

### Check all positions with stop values
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/trading/trading-ai/data/portfolio.db')
rows = conn.execute('''
    SELECT ticker, shares, entry_price, current_price,
           stop_loss, take_profit, tiers_triggered
    FROM positions WHERE status = \"open\" ORDER BY entry_date
''').fetchall()
for p in rows:
    pnl = (p[3]-p[2])/p[2]*100 if p[3] else 0
    print(f'{p[0]:5s} {p[1]:.0f}sh entry=\${p[2]:.2f} SL=\${p[4]:.2f} tiers={p[6]} pnl={pnl:+.1f}%')
conn.close()
"
```

### Check Qdrant
```bash
curl -s http://localhost:6333/healthz
python3 -c "
from qdrant_client import QdrantClient
c = QdrantClient(host='localhost', port=6333)
info = c.get_collection('trading_signals')
print(f'Vectors: {info.points_count}, Status: {info.status}')
"
```

### Check Spark reachable
```bash
curl -s http://172.29.11.225:11434/api/tags | python3 -m json.tool | grep name
```

### Test portfolio manager (dry run -- no trades)
```bash
cd ~/trading-ai
/home/trading/trading-ai/pipeline/.venv/bin/python3 \
    pipeline/portfolio/manager.py 2>&1 | tail -15
```

### Test portfolio manager (live -- executes trades)
```bash
cd ~/trading-ai
/home/trading/trading-ai/pipeline/.venv/bin/python3 \
    pipeline/portfolio/manager.py --execute 2>&1 | tail -20
```

### Test full pipeline
```bash
cd ~/trading-ai
source pipeline/.venv/bin/activate
python3 pipeline/run.py 2>&1 | tail -10
```

### Check prediction performance
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/trading/trading-ai/data/paper_trading.db')
preds = conn.execute('SELECT direction, was_correct FROM predictions WHERE was_correct IS NOT NULL').fetchall()
correct = sum(1 for p in preds if p[1] == 1)
print(f'Win rate: {correct}/{len(preds)} = {correct/len(preds)*100:.1f}%')
conn.close()
"
```

### Check inference rules
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/trading/trading-ai/data/rules.db')
active = conn.execute('SELECT COUNT(*) FROM inference_rules WHERE active=1').fetchone()[0]
pending = conn.execute('SELECT COUNT(*) FROM rule_proposals WHERE status=\"pending\"').fetchone()[0]
print(f'Active rules: {active}, Pending proposals: {pending}')
conn.close()
"
```

---

## DEPLOYMENT PROCEDURE

### After any code fix:
1. Test import:
   python3 -c "import pipeline.portfolio.manager; print('OK')"
2. Test dry run (no trades):
   /home/trading/trading-ai/pipeline/.venv/bin/python3 pipeline/portfolio/manager.py
3. Commit:
   git add <files>
   git commit -m "Descriptive message explaining what was broken and how fixed"
   git push origin master
4. Sync Spark:
   ssh trading@172.29.11.225 "cd ~/trading-ai && git pull origin master"
5. Report to Discord

### Never do:
- Modify position sizes or trade execution without testing dry run first
- Push broken code (always test import before commit)
- Store SQLite or Qdrant data on NFS
- Use `source` in cron entries
- Remove CONFIG keys without grepping all pipeline files first

---

## PLANNED IMPROVEMENTS

1. Migrate Ollama to llama.cpp (llama-server) across all machines
   - airig: qwen3:30b + bge-m3 -> llama-server port 8080/8081
   - Spark: deepseek-r1:70b -> llama-server port 8080
   - API format: /api/chat -> /v1/chat/completions
   - Response format: response["message"]["content"] -> response["choices"][0]["message"]["content"]

2. Alpaca API integration (after 1-2 months paper trading)
   - Replace Yahoo Finance price fetching
   - Replace paper trading SQLite with Alpaca paper account
   - Zero code change to go live (same API for paper and live)

3. Social media feeds
   - Reddit WSB, Stocktwits, Truth Social RSS

4. Feedback loop / auto-tuning
   - Rule confidence adjusts based on verified prediction outcomes
   - Sectors with poor win rate get confidence penalty applied

5. Dashboard enhancements (Flask app, port 5000)
   - P&L history chart over time
   - Prediction accuracy by sector
   - Position timeline visualization

---

## HERMES-TRADER OPERATING PROCEDURES

### Every 15 minutes:
1. Check cron.log -- did last pipeline run complete?
2. Check portfolio.log -- any errors or missed executions?
3. If anomaly found: diagnose -> fix -> test -> commit -> report to Discord

### After 9:35 AM ET (market open):
1. Verify portfolio entry cron fired and appears in portfolio.log
2. Verify expected trades executed (check positions DB)
3. If trades missing: investigate error, re-run if appropriate with --execute

### After 3:35 PM ET (close window):
1. Check stop losses processed correctly
2. Check profit tier executions (tiers_triggered column)
3. Post daily summary to Discord

### Daily at 8 AM ET:
1. Verify Spark reachable before predictions run
2. Verify Qdrant vector count growing
3. Check overnight errors in all logs

### When you find a bug:
1. Read the FULL error before touching any code
2. grep for ALL references to any variable/key you plan to change
3. Make the minimal fix
4. Test: python3 -c "import pipeline.portfolio.manager; print('OK')"
5. Test dry run: portfolio manager without --execute
6. Commit with descriptive message
7. Pull on Spark
8. Report to Discord: what broke, why, how you fixed it

---
