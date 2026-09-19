# Causality Analysis Report — 2026-09-18

## 1. Data Scope
- Predictions: 09-16 (9), 09-17 (9), 09-18 (9, created 08:14 UTC by daily run) + 1 residual from 09-15 — **28 total unverified**; last successful verification run 2026-09-16 22:00 UTC (verify.log)
- Signals: 20 most recent high-confidence (MCP); signal_ledger 7d: 190 entries (166/190 sector=unknown, 190/190 event_type=other)
- Closed trades (3d): 0 (no new executions; 30d base unchanged: 4W/2L +$155.97 per 09-17 report)
- Pipeline health: 285 active inference rules, 258 indirect_dependencies, 0 parse errors, Qdrant 1,375 signals/1d, 106,211 points
- Prior report status: 09-17 card `fix-verification-etf-keyword-shadowing` **never created** (no matching Kanban task, manifest unchanged since 09-07). Related in-flight card t_d476a213 (4 ETF fixes: AIQ removal, space→ITA, consumer→XLY) is verified on staging (t_74e257e4 PASS 14/14 probes) and sitting at auditor (t_5268b6f4) — but its scope does NOT cover the market/financials shadowing below.

## 2. 48h Signal-to-Move Cross-Reference
No new 2+ independent-source corroboration pairs for causal rules in the 48h window. Candidates examined and rejected:
- **OpenAI $122B capital raise** (finnhub-amzn, bullish 0.91, ai_infrastructure/semiconductors) — strongest single signal of the window. Rejected as a NEW rule: the causal mechanism (mega capex → AI infrastructure supply chain) is already covered by existing rules `hyperscaler capex increase → ai infrastructure supply chain` and `NVDA earnings beat → ai infrastructure supply chain`. Single source, and the prediction batch it feeds is unverified — no outcome evidence yet. **Watch item**: if 09-16/09-17 tech-bullish predictions verify correct while semis/power names move, the existing rules get credit; propose an AI-lab-specific rule only after that verification.
- **TSK affirms guidance, rules out dividend, shares tumble** (investing-com, bearish 0.85, energy/utilities) — ticker-specific dividend-signal; no cross-sector causal link.
- **JPMorgan initiations (Dianthus OW, Old National OW)** — analyst_initiation event type already implemented in prior cycles; classifier coverage issue is tracked under Finding C, not a causal rule.
- **BoE hold at 3.75% expected** (0.75) — rates→financials/energy links already exist in the 258-rule set.
- 09-16/09-17 prediction batches remain unverified (Finding A), so no new signal→outcome pairs can be scored this cycle at all.

## 3. Finding A — Verification cron starved since 2026-09-16 22:00 UTC (BLOCKING)
Every `--verify` and `--discover-rules` cron run since Sep 17 05:00 UTC has been skipped; 28 predictions are now past their 24h window.

Evidence (two independent sources):
1. **cron.log**: 7 consecutive skips at job-collision seconds — Sep 17 05:00 (rule-discovery), 08:00, 14:00, 20:00, Sep 18 02:00, 05:00, 08:00 UTC — each logging `Pipeline already running (PID N), skipping` at the exact second the 5-minute pipeline job fires. The 5-minute job itself ran 422 times in the same window and completed normally every time; `ps` shows no hung pipeline process and no lockfile exists now — so the lock claim at skip time was a spawn-order race at the shared :00 boundary, not a real concurrent run.
2. **verify.log + DB**: last verification run 2026-09-16 22:00 UTC ("No expired predictions"); `SELECT COUNT(*) FROM predictions WHERE verified_at IS NULL` = 28 (09-15 residual 1 + three full daily batches).

Downstream impact (corroborating source 3): **sector_trim STFT = 0.000 for all 9 sectors** (trim.log 09-18 05:00 ET + sector_trim table) — trim.py has had no new verified outcomes to consume, so LTFT is frozen at pre-break values and the hard-block/critic/trim calibration loop has been running on stale data for ~36h. The 09-17 12:00 UTC batch (which the 09-16 card claimed would get "first clean verification" at 09-17 16:00 UTC) was never verified — it is still in the backlog.

Mechanism note: all dedicated jobs fire at :00 minutes that coincide with the `*/5` pipeline boundary, and cron_run.sh uses ONE shared lockfile (`/tmp/trading-ai-pipeline.lock`) with no retry. The last successful verify (09-16 22:00 UTC = 18:00 ET) predates the current crontab times (4 AM/10 AM/4 PM/10 PM ET); since the crontab edit, the dedicated job loses the spawn race 7/7. A single lost race = a lost verification cycle with no catch-up trigger.

Immediate action for operator: after card 1 below ships (or immediately, accepting market/financials ETF contamination per Finding B), run `ssh trading@172.29.10.225 /home/trading/trading-ai/pipeline/cron_run.sh --verify` to flush the 28-item backlog — verify.py is catch-up by design (verifies everything expired).

## 4. Finding B — Residual keyword shadowing in sector_etf.py still unfixed (re-list of 09-17 card)
Live probe of `get_sector_etf()` against the 9 production query templates, run 2026-09-18 ~09:15 UTC on airig:

| Production query | Maps to | Should be |
|------------------|---------|-----------|
| Overall market outlook ... S&P 500, ... Fed | **XLF** | SPY |
| Financial sector outlook ... real estate, Fed policy | **VNQ** | XLF |
| Technology and AI ... semiconductors, ai infrastructure | **AIQ** | SOXX (arguable) |

Root cause unchanged from 09-17: no macro keyword (market/s&p) exists in KEYWORDS so "fed" wins for the market query; the real-estate block sits above the financials block; plural "semiconductors" fails the singular `\bsemiconductor\b` word-boundary regex so "ai" wins. Confirmed the in-flight staging fix (t_d476a213, verified on staging) does NOT include these three — staging sector_etf.py has the same gaps.

Escalated urgency: with 28 predictions in the verification backlog, the next catch-up verify will validate the market and financials rows against the WRONG ETFs (XLF for market, VNQ for financials) — re-contaminating exactly the sectors near the 35% hard-block threshold, on the first batch that was supposed to be clean.

## 5. Finding C — signal_ledger sector/event_type attribution is dead
DB (7d): 166/190 (87%) of ledger entries sector=unknown (12 ai_infrastructure, 10 healthcare, 2 energy only); event_type=other for 190/190 (100%).
Source (second source): `pipeline/portfolio/selector.py` `_log_rejected_signal(...)` calls hardcode `sector="unknown"` and never pass event_type — the schema default ('other') is what lands in the DB.
Impact: sector-keyed win-rate lookups (hard-block gate, sector_trim feedback) and the Sunday signal-classification review job (which depends on event_type breakdowns) have no usable input. Prior taxonomy work (sec_filing, leadership_transition, merger_arbitrage event types — all archived cards) is invisible in the ledger because the ledger write path never consults the classifier. Unresolved and re-flagged three consecutive reports (09-16, 09-17, 09-18) with no open Kanban card found.

## 6. Finding D — rule_performance table never written (0 rows)
`SELECT COUNT(*) FROM rule_performance` = 0, and grep across pipeline/*.py finds no writer (table exists only in schema). The per-rule outcome feedback loop — the basis for ranking get_active_rules() and pruning bad inference rules — has no measurement. Rule discovery is still churning (09-18 01:01: 349 pending proposals, 5 promoted/night) with zero accuracy attribution. No open Kanban card found for this.

## 7. Sectors Reviewed — No New Causal Rules
- **No new indirect_dependency rule cards this cycle.** All 48h signal candidates were single-source, ticker-specific, or already covered by the 258-rule set (see §2). The OpenAI capital-raise candidate is logged as a watch item pending verification of the 09-16/09-17 tech batches.
- **Accuracy snapshot (14d, 60 predictions, 33% overall)** — interpret with caution, all numbers predate the verification backlog: bearish macro 17% (6/7), merger-arb neutral 75% (3/4), healthcare bullish 57% (4/7, still XLK-contaminated era), tech bullish 29% (2/7), materials 0% (all directions, n=7). Mixed direction: 9 predictions, 0% — gate-blocked by design, not a causal pattern.
- **rule_performance empty (Finding D)** means none of these win rates can yet be attributed to specific rules — the measurement layer must land before rule-level causal conclusions improve.

## Kanban Cards to Create
1. `fix-verification-cron-lock-starvation` — **Fix cron lock starvation: dedicated jobs (--verify, --discover-rules) lose the shared-lock spawn race to the 5-minute pipeline 7/7 since 09-16 22:00 UTC; add job-scoped locks or retry-on-collision + age-based stale detection; then flush the 28-item verification backlog** (Risk: low | Sectors: pipeline, risk | Owner: coder)
   - Evidence: cron.log 7 consecutive "already running, skipping" at job-collision seconds (09-17 05:00 → 09-18 08:00 UTC) + 5-min job healthy 422 runs + ps shows no hung process (false lock claim); verify.log last run 09-16 22:00 UTC; DB 28 unverified; trim STFT=0.0 all sectors (downstream confirmation)
   - Urgency: high — sector_trim/win-rate/hard-block calibration running on 36h-stale data; every further cycle widens the backlog
   - Expected impact: verification resumes on schedule, trim feedback loop restarts, rule discovery unblocks
   - Verification: two consecutive --verify cron runs appear in cron.log without a skip line; verified_at populated for all 28 backlog rows within one cycle; STFT non-zero for at least one sector at next 5 AM trim
2. `fix-verification-etf-keyword-shadowing` — **Fix residual keyword shadowing in sector_etf.py: add macro keywords (market, s&p) BEFORE financials block, move real-estate block below financials, add plural semiconductors alias** (Risk: low | Sectors: pipeline, risk | Owner: coder)
   - Evidence: live get_sector_etf() probe 09-18 09:15 UTC (market→XLF, financials→VNQ, tech→AIQ) + KEYWORDS table inspection (no macro entry; real-estate above financials) — two independent sources. Re-listed: 09-17 report listed this card but it was never created, and the in-flight staging card t_d476a213 does not cover these three mappings
   - Urgency: high — must ship before the 28-item verification backlog flushes, or market/financials rows verify against wrong ETFs and re-contaminate win rates near the 35% hard-block threshold
   - Expected impact: restores valid market/financials win rates for the hard-block gate and sector_trim feedback
   - Verification: unit check all 9 production query templates map to SPY/XLF/SOXX-or-AIQ/XLV/XLP/XLB/XLI/XLE; next verify.log shows "Verified via SPY" / "Verified via XLF" for market/financials rows
3. `fix-signal-ledger-sector-attribution` — **Populate signal_ledger sector + event_type: derive sector from query via get_sector_etf() keyword map and event_type from the classifier in selector.py _log_rejected_signal (currently hardcoded sector='unknown', event_type omitted)** (Risk: low | Sectors: pipeline, signal-quality | Owner: coder)
   - Evidence: DB 7d aggregate 166/190 unknown + 190/190 event_type=other; selector.py source hardcodes sector='unknown' and passes no event_type — two independent sources
   - Expected impact: sector-keyed win-rate lookups (hard-block, trim) and the Sunday signal-classification review get real input; prior event-type taxonomy work (sec_filing, merger_arbitrage, leadership_transition) becomes visible in the ledger
   - Verification: after one cycle, >80% of new ledger rows have sector != unknown and event_type != other for titles the classifier handles
4. `wire-rule-performance-attribution` — **Write rule_performance rows when predictions verify: attribute each verified prediction to the inference rules that contributed (rules_version + signals context already on the prediction row), so get_active_rules() ranking and rule pruning run on measured accuracy** (Risk: medium | Sectors: pipeline, rules | Owner: coder)
   - Evidence: rule_performance COUNT(*)=0 all-time + grep shows no writer in pipeline/ (schema-only table); rule discovery promoting 5 rules/night with zero outcome attribution (rule_discovery.log 09-18 01:01)
   - Expected impact: closes the learn-from-outcomes loop at the rule level; prerequisite for rule-level causal claims and safe pruning
   - Verification: after next verification cycle, rule_performance has rows referencing verified prediction IDs; get_active_rules() ordering changes to be performance-backed
