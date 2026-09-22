# Graveyard

Defects that reached production and were later killed. Each entry documents
what happened, the cost, how it was found, and what now catches it.

> "A warning nobody reads is not a control."

---

## Entry 1: Debug override permanently disabled entry-window gate

- **What:** `is_entry_window() or True` in manager.py always evaluated to True,
  meaning all entry-window checks were bypassed. The `or True` was a debug
  override left in after a one-off test.
- **Cost:** Entries executed outside the 9:30-10:00 / 15:30-16:00 windows for
  the period between the debug insertion and the fix. Slippage on after-hours
  fills and trades during lunch-hour illiquidity.
- **How found:** Code review during audit of the portfolio manager flow.
- **Commit that killed it:** 6e9e831 (2026-09-21)
- **What now catches it:** Any line with `or True` or `and False` in a gate
  condition is a review-time red flag. The execution breaker (Item A) limits
  blast radius if a gate is bypassed and causes repeated failures.

## Entry 2: Dead promotion loop in rule discovery created approved-but-not-promoted entries

- **What:** `discover_rules.py` had a module-level promotion loop that ran at
  import time. It created `approved` (not `promoted`) entries in rule_proposals,
  which then blocked new proposals from being seeded - the proposal table filled
  with stuck entries.
- **Cost:** Rule discovery produced no new rules for the duration the dead code
  was active. The discovery pipeline appeared to run (no error) but output was
  empty because new proposals were blocked by stale approved entries.
- **How found:** Investigating why discover-rules was producing zero new rules
  despite active signal patterns. The dead promotion loop was unreachable code
  that ran at module import.
- **Commit that killed it:** 9d8e28a (2026-09-04)
- **What now catches it:** `propose_rule()` handles count>=2 promotion
  inline; the module-level loop was removed. Future audits check for
  module-level side effects in pipeline scripts.

## Entry 3: Wrong variable name in theta sector gate (`adj_confidence` vs `confidence`)

- **What:** The theta_sector_block gate in selector.py referenced
  `adj_confidence` which did not exist - the actual variable was `confidence`.
  This caused a `NameError` that was silently caught by the outer exception
  handler, meaning the theta sector gate never actually checked win rates.
- **Cost:** Cash-secured puts were written into sectors with <20% win rate
  (the gate was supposed to block these). The gate silently passed every
  candidate because the exception handler treated the crash as "no block".
- **How found:** Tracing theta-gang execution path during a sector performance
  audit that showed theta positions in losing sectors.
- **Commit that killed it:** 0dea2fa (2026-09-12)
- **What now catches it:** Variable name is now `confidence` matching the
  selector context. Any gate that depends on a variable name is a candidate
  for a lint check.

## Entry 4: hard_gates.py queried wrong columns - `closed_at` and `positions` table

- **What:** The realized P&L query in hard_gates.py used `closed_at` column
  (does not exist - the column is `exit_date`) and queried the `positions`
  table instead of `transactions` for realized P&L data.
- **Cost:** The hard gate for realized P&L concentration never fired - it
  raised `sqlite3.OperationalError` on every call, which was silently caught
  by the outer gate handler and treated as "gate passed." A gate that fails
  open is worse than no gate.
- **How found:** Manual audit of hard_gates.py gate logic during the
  post-emerald cleanup. The error was caught but logged at debug level.
- **Commit that killed it:** 5ab750f (2026-09-15)
- **What now catches it:** Queries use `status`/`exit_date` columns and the
  `transactions` table. Gate exceptions now log at WARNING level so silent
  failures are visible.

## Entry 5: Healthcare sector mapped to XLK (tech ETF) - keyword shadowing

- **What:** The sector-to-ETF mapping sent healthcare signals to XLK (tech ETF)
  because the keyword matching was not ordered correctly - "healthcare" matched
  a tech-sector keyword before the healthcare-specific one. Additionally,
  Financials keywords were evaluated after Real estate keywords, causing
  financial signals to be routed to real estate ETFs.
- **Cost:** Healthcare signals were being used to trade tech ETFs - the signal
  was real but the target was wrong. Trades were placed but the underlying
  thesis was broken (healthcare news does not move XLK the same way it moves
  XLV). This produced false-negative results in the signal_ledger since the
  sector attribution looked correct on paper.
- **How found:** Sector performance audit showed healthcare signals with
  anomalously low win rates - investigation revealed the ETF target was XLK
  not XLV.
- **Commit that killed it:** 6c1d74d (2026-09-21, t_7ec31cb6)
- **What now catches it:** Macro keywords are hoisted to the top of the
  matching list, semiconductors are aliased explicitly, and Financials
  keywords are evaluated before Real estate. Single-source sector_etf.py
  module (4b76ffb) prevents mapping divergence.
