"""
Phase 3 — Indirect signal propagation graph

When signals appear about entity A, and we know A affects B,
automatically generate derived signals for B and inject them
into the prediction context.

Dependencies come from two sources:
  1. Learned: indirect_dependencies table (built by post-mortems)
  2. Static: hardcoded known market relationships

These derived signals are appended to the Qdrant query results
before DeepSeek reasons over them, enriching the prediction context.
"""

import sqlite3
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

log = logging.getLogger(__name__)

LESSONS_DB = Path("/home/trading/trading-ai/data/lessons.db")

# ─── Static dependency graph ──────────────────────────────────────────────────
# Known market relationships that don't need to be learned
# Format: {trigger_keyword: [(affected_sector, direction_multiplier, relationship)]}
# direction_multiplier: 1.0 = same direction, -1.0 = inverse

STATIC_DEPENDENCIES = {
    # Monetary policy — absolute sector impacts
    "interest_rate_increase": [
        ("real_estate", "bearish", "higher borrowing costs hurt real estate"),
        ("utilities",   "bearish", "bond alternatives reduce utility appeal"),
        ("financials",  "bullish", "banks benefit from higher net interest margins"),
        ("technology",  "bearish", "growth stocks devalued by rising discount rates"),
        ("consumer",    "bearish", "credit costs rise, spending falls"),
    ],
    "interest_rate_decrease": [
        ("real_estate", "bullish", "lower borrowing costs boost real estate"),
        ("utilities",   "bullish", "yield seekers rotate to utilities"),
        ("technology",  "bullish", "growth stocks revalued upward"),
        ("financials",  "bearish", "bank margins compress with lower rates"),
        ("consumer",    "bullish", "cheaper credit boosts spending"),
    ],
    "fed_policy": [
        ("financials",  "bullish", "rate environment benefits bank margins"),
        ("technology",  "bearish", "policy tightening affects growth stocks"),
    ],
    "quantitative_tightening": [
        ("technology",  "bearish", "liquidity reduction hurts growth stocks"),
        ("real_estate", "bearish", "tighter financial conditions"),
    ],
    "quantitative_easing": [
        ("technology",  "bullish", "liquidity boost helps growth stocks"),
        ("real_estate", "bullish", "easier financial conditions"),
    ],

    # Energy / commodities
    "oil_price_increase": [
        ("energy",      "bullish", "direct revenue benefit for oil companies"),
        ("airlines",    "bearish", "fuel costs rise, margins compress"),
        ("transport",   "bearish", "operating costs increase"),
        ("consumer",    "bearish", "disposable income squeezed by fuel costs"),
        ("materials",   "bullish", "energy-related materials demand rises"),
    ],
    "oil_price_decrease": [
        ("energy",      "bearish", "revenue pressure on oil companies"),
        ("airlines",    "bullish", "fuel cost relief boosts margins"),
        ("transport",   "bullish", "lower operating costs"),
        ("consumer",    "bullish", "more disposable income"),
    ],
    "commodity_supply_disruption": [
        ("materials",   "bullish", "supply shortage raises prices"),
        ("industrials", "bearish", "input costs rise"),
        ("consumer",    "bearish", "higher goods prices"),
    ],

    # Macro conditions
    "inflation_rate": [
        ("technology",  "bearish", "real returns compressed, growth devalued"),
        ("energy",      "bullish", "commodity prices rise with inflation"),
        ("materials",   "bullish", "hard assets benefit from inflation"),
        ("consumer",    "bearish", "purchasing power reduced"),
        ("real_estate", "bullish", "hard asset inflation hedge"),
    ],
    "recession_risk": [
        ("consumer",    "bearish", "spending cuts in economic downturn"),
        ("technology",  "bearish", "enterprise IT budget cuts"),
        ("financials",  "bearish", "credit losses rise in recession"),
        ("utilities",   "bullish", "defensive sector rotation"),
        ("healthcare",  "bullish", "defensive sector rotation"),
    ],
    "gdp_growth": [
        ("consumer",    "bullish", "economic expansion boosts spending"),
        ("financials",  "bullish", "credit demand rises with growth"),
        ("industrials", "bullish", "manufacturing demand increases"),
        ("technology",  "bullish", "enterprise spending rises"),
    ],
    "inflation_rising": [
        ("technology",  "bearish", "real returns compressed"),
        ("energy",      "bullish", "commodity prices rise"),
        ("consumer",    "bearish", "purchasing power reduced"),
    ],

    # Geopolitical
    "military_conflict": [
        ("energy",      "bullish", "supply disruption risk raises prices"),
        ("defense",     "bullish", "defense spending increases"),
        ("technology",  "bearish", "risk-off sentiment hurts growth"),
        ("consumer",    "bearish", "uncertainty reduces spending"),
    ],
    "trade_sanctions": [
        ("technology",  "bearish", "export restriction risk"),
        ("energy",      "bearish", "trade flow disruption"),
        ("consumer",    "bearish", "supply chain impact on goods"),
    ],
    "geopolitical_tension": [
        ("energy",      "bullish", "supply risk premium added"),
        ("defense",     "bullish", "increased defense spending"),
        ("consumer",    "bearish", "uncertainty reduces spending"),
        ("technology",  "bearish", "risk-off sentiment"),
    ],
    "supply_chain_disruption": [
        ("technology",  "bearish", "component shortage hurts production"),
        ("consumer",    "bearish", "product availability reduced"),
        ("industrials", "bearish", "manufacturing disruption"),
        ("materials",   "bullish", "shortage raises input prices"),
    ],
    "iran": [
        ("energy",      "bullish", "Strait of Hormuz supply disruption risk"),
        ("defense",     "bullish", "Middle East conflict drives spending"),
        ("airlines",    "bearish", "airspace and route disruption"),
    ],
    "russia_ukraine": [
        ("energy",      "bullish", "European energy supply risk"),
        ("agriculture", "bullish", "wheat and grain supply disruption"),
        ("defense",     "bullish", "NATO defense spending increases"),
        ("materials",   "bullish", "metal and commodity supply risk"),
    ],
    "middle_east_conflict": [
        ("energy",      "bullish", "oil supply disruption risk"),
        ("defense",     "bullish", "defense spending increases"),
        ("technology",  "bearish", "risk-off sentiment"),
    ],

    # Supply chain
    "tsmc": [
        ("semiconductors","bullish","TSMC is primary chip manufacturer"),
        ("technology",  "bullish", "chip supply security"),
        ("nvidia",      "bullish", "NVDA depends on TSMC"),
        ("amd",         "bullish", "AMD depends on TSMC"),
    ],
    "samsung_strike": [
        ("semiconductors","bearish","supply disruption from Samsung"),
        ("technology",  "bearish", "component shortage risk"),
    ],
    "china_trade": [
        ("technology",  "bearish", "export restriction and China market risk"),
        ("semiconductors","bearish","China chip market exposure"),
        ("consumer",    "bearish", "supply chain exposure to China"),
    ],

    # Dollar
    "dollar_strengthening": [
        ("commodities", "bearish", "dollar-priced commodities cost more"),
        ("emerging_markets","bearish","dollar debt more expensive"),
        ("technology",  "bearish", "US tech exports more expensive abroad"),
    ],
    "dollar_weakening": [
        ("commodities", "bullish", "commodities cheaper in other currencies"),
        ("technology",  "bullish", "US tech exports more competitive"),
        ("energy",      "bullish", "oil priced in dollars becomes cheaper"),
    ],

    # Corporate events
    "earnings_beat": [
        ("technology",  "bullish", "sector confidence boost from strong earnings"),
        ("financials",  "bullish", "market sentiment positive"),
    ],
    "earnings_miss": [
        ("technology",  "bearish", "sector confidence hit from weak earnings"),
        ("financials",  "bearish", "market sentiment negative"),
    ],
    "major_investment": [
        ("technology",  "bullish", "capital deployment signals confidence"),
        ("industrials", "bullish", "infrastructure investment positive"),
    ],
    "merger": [
        ("financials",  "bullish", "M&A activity signals market confidence"),
        ("technology",  "bullish", "consolidation can boost valuations"),
    ],

    # Jobs / consumer
    "jobs_strong": [
        ("consumer",    "bullish", "employment drives spending"),
        ("real_estate", "bullish", "income drives housing demand"),
        ("financials",  "bullish", "economic health signal"),
        ("technology",  "bearish", "Fed less likely to cut rates"),
    ],
    "jobs_weak": [
        ("consumer",    "bearish", "unemployment reduces spending"),
        ("technology",  "bullish", "Fed more likely to cut rates"),
        ("real_estate", "bearish", "income uncertainty reduces demand"),
    ],
}


def get_learned_dependencies(conn: sqlite3.Connection) -> dict:
    """Load learned dependencies from lessons DB"""
    deps = defaultdict(list)
    try:
        rows = conn.execute("""
            SELECT from_entity, to_entity, relationship, occurrences
            FROM indirect_dependencies
            WHERE occurrences >= 1
            ORDER BY occurrences DESC
        """).fetchall()

        for from_e, to_e, rel, occ in rows:
            deps[from_e.lower()].append({
                'to':           to_e,
                'relationship': rel,
                'occurrences':  occ,
                'confidence':   min(0.90, 0.60 + occ * 0.05),
            })

        log.debug(f"Loaded {len(rows)} learned dependencies")
    except Exception as e:
        log.warning(f"Could not load learned dependencies: {e}")
    return deps


def find_triggered_dependencies(signals: list[dict]) -> list[dict]:
    """
    Scan signals for dependency triggers and return derived signals.
    
    For each signal, check if its title/sectors match any known dependencies.
    If so, create a derived signal for the downstream sector.
    """
    derived = []
    
    try:
        lessons_conn = sqlite3.connect(LESSONS_DB)
        learned_deps = get_learned_dependencies(lessons_conn)
        lessons_conn.close()
    except:
        learned_deps = {}

    all_deps = {**STATIC_DEPENDENCIES}
    # Merge learned deps
    for trigger, dep_list in learned_deps.items():
        if trigger not in all_deps:
            all_deps[trigger] = []
        for dep in dep_list:
            all_deps[trigger].append((
                dep['to'],
                1.0,  # direction multiplier
                dep['relationship']
            ))

    processed_pairs = set()  # avoid duplicate derived signals

    for signal in signals:
        title   = signal.get('title', '').lower()
        sectors = [s.lower() for s in signal.get('sectors', [])]
        tickers = [t.lower() for t in signal.get('tickers', [])]
        sentiment = signal.get('sentiment', 'neutral')
        confidence = signal.get('confidence', 0.65)

        # Check each trigger
        for trigger, downstream_list in all_deps.items():
            trigger_lower = trigger.lower()

            # Does this signal match the trigger?
            # Check macro_themes first (most reliable), then fall back to text
            macro_themes = [t.lower() for t in signal.get('macro_themes', [])]
            matched = (
                any(trigger_lower in theme for theme in macro_themes) or
                trigger_lower in title or
                any(trigger_lower in s for s in sectors) or
                any(trigger_lower in t for t in tickers)
            )

            if not matched:
                continue

            for downstream in downstream_list:
                if isinstance(downstream, tuple):
                    affected_sector, direction_mult, relationship = downstream
                    dep_confidence = 0.70
                else:
                    affected_sector = downstream['to']
                    direction_mult  = 1.0
                    relationship    = downstream.get('relationship', '')
                    dep_confidence  = downstream.get('confidence', 0.70)

                # Calculate derived sentiment based on economic relationship
                # direction_mult > 0: sector benefits FROM this trigger event
                #   e.g. rate hike (bearish macro) + financials(+1.0) = bullish financials
                #   e.g. oil rise (bullish energy) + energy(+1.0) = bullish energy
                # direction_mult < 0: sector is hurt BY this trigger event
                #   e.g. oil rise (bullish energy) + airlines(-1.0) = bearish airlines
                #   e.g. rate hike (bearish macro) + technology(-1.0) = bearish technology
                if isinstance(direction_mult, str):
                    derived_sentiment = direction_mult  # explicit: "bullish"/"bearish"
                elif direction_mult > 0:
                    derived_sentiment = sentiment  # same direction as trigger
                else:
                    derived_sentiment = "bearish"  # negative impact
                # Reduce confidence for indirect signals
                derived_confidence = round(confidence * dep_confidence, 2)

                # Dedup
                pair_key = (trigger, affected_sector, derived_sentiment)
                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)

                derived_signal = {
                    'title':      f"[INDIRECT] {trigger} → {affected_sector}: {relationship[:60]}",
                    'sentiment':  derived_sentiment,
                    'confidence': derived_confidence,
                    'sectors':    [affected_sector],
                    'tickers':    [],
                    'source':     'signal_graph',
                    'derived_from': signal.get('title', '')[:60],
                    'trigger':    trigger,
                }
                derived.append(derived_signal)
                log.debug(f"Derived: {trigger} → {affected_sector} "
                         f"({derived_sentiment} {derived_confidence:.2f})")

    if derived:
        log.info(f"Signal graph: {len(derived)} derived signals from "
                 f"{len(signals)} input signals")

    return derived


def enrich_signals(signals: list[dict],
                   target_sector: str = None) -> list[dict]:
    """
    Add derived indirect signals to a signal list.
    Optionally filter derived signals to only those affecting target_sector.
    """
    derived = find_triggered_dependencies(signals)

    # Subsector to parent sector mapping
    SUBSECTOR_MAP = {
        'semiconductors': 'technology', 'nvidia': 'technology',
        'amd': 'technology', 'apple': 'technology', 'intel': 'technology',
        'ai': 'technology', 'data_center': 'technology',
        'biotech': 'healthcare', 'pharma': 'healthcare',
        'oil_gas': 'energy', 'renewables': 'energy', 'utilities': 'energy',
        'banks': 'financials', 'real_estate': 'financials',
        'defense': 'industrials', 'aerospace': 'industrials',
        'retail': 'consumer', 'discretionary': 'consumer',
        'metals': 'materials', 'mining': 'materials', 'chemicals': 'materials',
    }

    if target_sector and derived:
        def matches_sector(d, target):
            for s in d.get('sectors', []):
                s_lower = s.lower()
                if s_lower == target.lower():
                    return True
                if SUBSECTOR_MAP.get(s_lower) == target.lower():
                    return True
            return False
        derived = [d for d in derived if matches_sector(d, target_sector)]

    if derived:
        log.info(f"Enriched signals: {len(signals)} original + "
                 f"{len(derived)} derived = {len(signals)+len(derived)} total")

    return signals + derived


def get_dependency_summary() -> str:
    """Human-readable summary of the dependency graph"""
    lines = [f"Signal graph: {len(STATIC_DEPENDENCIES)} static triggers"]
    try:
        conn = sqlite3.connect(LESSONS_DB)
        count = conn.execute(
            "SELECT COUNT(*) FROM indirect_dependencies"
        ).fetchone()[0]
        conn.close()
        lines.append(f"Learned dependencies: {count}")
    except:
        pass

    lines.append("\nTop static triggers:")
    for trigger, deps in list(STATIC_DEPENDENCIES.items())[:8]:
        affected = [d[0] for d in deps]
        lines.append(f"  '{trigger}' → {affected}")

    return '\n'.join(lines)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    print(get_dependency_summary())

    # Test with sample signals
    test_signals = [
        {
            'title': 'Federal Reserve signals rate hike amid inflation concerns',
            'sentiment': 'bearish',
            'confidence': 0.80,
            'sectors': ['macro', 'financials'],
            'tickers': []
        },
        {
            'title': 'Oil prices surge as Iran tensions escalate',
            'sentiment': 'bullish',
            'confidence': 0.85,
            'sectors': ['energy'],
            'tickers': []
        },
    ]

    print("\nTest enrichment:")
    enriched = enrich_signals(test_signals)
    print(f"Input: {len(test_signals)} signals")
    print(f"Output: {len(enriched)} signals ({len(enriched)-len(test_signals)} derived)")
    for s in enriched:
        if s.get('source') == 'signal_graph':
            print(f"  DERIVED: {s['title'][:70]} [{s['sentiment']} {s['confidence']}]")