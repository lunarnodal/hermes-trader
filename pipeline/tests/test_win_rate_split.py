"""
# Fixture: 2 bullish-correct, 1 bullish-wrong, 2 mixed, 1 neutral -> directional hit rate 2/3.
# 0 skipped. FAIL on base 410461b, PASS on r2b change.
#
# DB isolation: each test patches PAPER_DB on EVERY consumer module
# (trim, sensitivity, critic, calibration, semantic_gates, selector, postmortem,
# daily_predictions, reports) so no test can see another test's DB or the live path.
# trim.py and sensitivity.py define their OWN PAPER_DB — must patch directly, not config.
"""
import inspect
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib


# ---- Fixture builder ----
# Rows:
#   id 1: bullish, was_correct=1, conf=0.85  (directional, verified 3h ago)
#   id 2: bullish, was_correct=1, conf=0.80  (directional, verified 3h ago)
#   id 3: bullish, was_correct=0, conf=0.70  (directional, verified 3h ago)
#   id 4: mixed,   was_correct=1, conf=0.60  (non-directional, verified 1h ago)
#   id 5: mixed,   was_correct=1, conf=0.60  (non-directional, verified 1h ago)
#   id 6: neutral, was_correct=1, conf=0.50  (non-directional, verified 1h ago)
#
# Directional (bullish+bearish): ids 1,2,3 -> 2 correct / 3 total = 0.667
# Non-directional verified_at is later than directional so test 4 (watermark)
# can distinguish: with filter watermark=3h_ago; without watermark=1h_ago.


import os, shutil

DEFAULT_ROWS = None  # filled below; keeps the existing 6-row fixture

def _make_fixture_db(rows=None):
    """Copy the prod-schema fixture (FIXTURE_PAPER_DB, read-only source), empty
    predictions, reset the trim watermark, insert test rows. Returns (path, conn)."""
    src = os.environ.get('FIXTURE_PAPER_DB')
    assert src and Path(src).exists(), (
        "FIXTURE_PAPER_DB must point to the prod-schema fixture (P-1); refusing to run")
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False); tmp.close()
    shutil.copy2(src, tmp.name)
    conn = sqlite3.connect(tmp.name)
    conn.execute("DELETE FROM predictions")
    try:
        conn.execute("UPDATE trim_consumption_watermark SET last_consumed_verified_at = NULL")
    except sqlite3.OperationalError:
        pass
    cols = "id, query, direction, confidence, was_correct, actual_direction, verified_at, created_at, timeframe, probability, model_used"
    conn.executemany(f"INSERT INTO predictions ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     rows if rows is not None else _default_rows())
    conn.commit()
    return Path(tmp.name), conn


def _default_rows():
    """The 6-row fixture (computed at call time; timestamps relative to now).
    Directional ids 1-3: verified 3h ago. Non-directional ids 4-6: verified 1h
    ago so test 4 (watermark) can distinguish filtered vs unfiltered consumption.
    Every row carries probability (= confidence) and model_used='test-fixture'
    (both NOT NULL, no default, in the prod schema)."""
    now = datetime.now(timezone.utc)
    dir_verified   = (now - timedelta(hours=3)).isoformat()
    nodir_verified = (now - timedelta(hours=1)).isoformat()
    created        = (now - timedelta(hours=4)).isoformat()
    return [
        (1, 'technology outlook Q1', 'bullish', 0.85, 1, 'up',
         dir_verified, created, '24h', 0.85, 'test-fixture'),
        (2, 'AI sector outlook',     'bullish', 0.80, 1, 'up',
         dir_verified, created, '24h', 0.80, 'test-fixture'),
        (3, 'semiconductor outlook', 'bullish', 0.70, 0, 'down',
         dir_verified, created, '24h', 0.70, 'test-fixture'),
        (4, 'market macro review',   'mixed',   0.60, 1, 'neutral',
         nodir_verified, created, '24h', 0.60, 'test-fixture'),
        (5, 'energy mixed signals',  'mixed',   0.60, 1, 'neutral',
         nodir_verified, created, '24h', 0.60, 'test-fixture'),
        (6, 'broad market neutral',  'neutral', 0.50, 1, 'neutral',
         nodir_verified, created, '24h', 0.50, 'test-fixture'),
    ]


# ---- Module DB-isolation helpers ----

def _patch_all_paper_db(fixture_path):
    """Patch PAPER_DB on every consumer module. Returns list of (mod, old) for restore."""
    saved = []
    for mod_name in [
        'portfolio.trim',
        'portfolio.sensitivity',
        'reasoning.critic',
        'reasoning.calibration',
        'reasoning.semantic_gates',
        'reasoning.reports',
        'reasoning.postmortem',
        'reasoning.daily_predictions',
    ]:
        try:
            mod = sys.modules.get(mod_name)
            if mod is None:
                __import__(mod_name)
                mod = sys.modules[mod_name]
        except Exception:
            continue
        if mod and hasattr(mod, 'PAPER_DB'):
            saved.append((mod, getattr(mod, 'PAPER_DB')))
            setattr(mod, 'PAPER_DB', fixture_path)
    return saved


def _restore_paper_db(saved):
    for mod, val in saved:
        if mod:
            setattr(mod, 'PAPER_DB', val)


# =============================================================================
# TEST 1: Fixture validation
# =============================================================================
def test_fixture_directional_count():
    """Verify fixture has exactly 3 directional predictions, 2 correct."""
    path, conn = _make_fixture_db()
    rows = conn.execute(
        "SELECT id FROM predictions "
        "WHERE direction IN ('bullish','bearish') AND was_correct IS NOT NULL"
    ).fetchall()
    conn.close()
    Path(path).unlink(missing_ok=True)
    assert len(rows) == 3, f"Expected 3 directional, got {len(rows)}"


# =============================================================================
# TEST 2: calibration.get_sector_win_rates (real function)
# =============================================================================
def test_calibration_directional_filter():
    """calibration.get_sector_win_rates counts only directional predictions.
    On base: 6 rows counted (no filter). On r2b: 3 rows (directional filter)."""
    path, conn = _make_fixture_db()

    import reasoning.calibration as cal_mod
    importlib.reload(cal_mod)

    result = cal_mod.get_sector_win_rates(conn)
    conn.close()
    Path(path).unlink(missing_ok=True)

    # Sum totals across all sectors — directional only = 3
    total_all = sum(data['total'] for data in result.values())
    assert total_all == 3, (
        f"calibration counted {total_all} predictions (expected 3 directional). "
        f"Filter missing if > 3."
    )


# =============================================================================
# TEST 3: critic.get_sector_win_rate (real function)
# =============================================================================
def test_critic_sector_win_rate_directional():
    """critic.get_sector_win_rate: 6 directional (4 correct) + 4 non-directional (all correct).
    Base (unfiltered): 8/10 = 0.800. Change (directional): 4/6 = 0.667."""
    now = datetime.now(timezone.utc)
    v = (now - timedelta(hours=3)).isoformat(); c = (now - timedelta(hours=4)).isoformat()
    rows = [(i, 'technology outlook', 'bullish', 0.8, w, 'up' if w else 'down', v, c, '24h',
                     0.8, 'test-fixture')
            for i, w in enumerate([1, 1, 1, 1, 0, 0], start=1)]
    rows += [(7, 'technology outlook', 'mixed', 0.6, 1, 'neutral', v, c, '24h', 0.6, 'test-fixture'),
             (8, 'technology outlook', 'mixed', 0.6, 1, 'neutral', v, c, '24h', 0.6, 'test-fixture'),
             (9, 'technology outlook', 'mixed', 0.6, 1, 'neutral', v, c, '24h', 0.6, 'test-fixture'),
             (10, 'technology outlook', 'neutral', 0.5, 1, 'neutral', v, c, '24h', 0.5, 'test-fixture')]
    path, conn = _make_fixture_db(rows); conn.close()
    saved = _patch_all_paper_db(path)
    try:
        import reasoning.critic as critic_mod
        importlib.reload(critic_mod); setattr(critic_mod, 'PAPER_DB', path)
        result = critic_mod.get_sector_win_rate("technology", window=20)
        assert result is not None, "get_sector_win_rate returned None (min-sample guard?)"
        assert abs(result['win_rate'] - 4/6) < 0.01, (
            f"win_rate={result['win_rate']:.3f}; directional 4/6=0.667, unfiltered 8/10=0.800")
    finally:
        _restore_paper_db(saved); Path(path).unlink(missing_ok=True)


# =============================================================================
def test_trim_compute_stft_directional():
    """trim.compute_stft consumes only directional predictions.
    Verify watermark[0] <= threshold. Directional verified_at = 3h ago,
    non-directional = 1h ago. With filter: watermark = 3h. Without: 1h."""
    path, conn = _make_fixture_db()
    conn.close()

    # Threshold between 1h and 3h ago
    threshold = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    saved = _patch_all_paper_db(path)
    try:
        import portfolio.trim as trim_mod
        importlib.reload(trim_mod)
        setattr(trim_mod, 'PAPER_DB', path)

        result = trim_mod.compute_stft()
        assert isinstance(result, dict), f"compute_stft returned {type(result)}"

        check_conn = sqlite3.connect(path)
        wm_row = check_conn.execute(
            "SELECT last_consumed_verified_at FROM trim_consumption_watermark WHERE id=1"
        ).fetchone()
        check_conn.close()

        assert wm_row is not None, "watermark not set"
        assert wm_row[0] <= threshold, (
            f"watermark {wm_row[0]} > threshold {threshold} — "
            f"non-directional predictions were consumed (filter missing)"
        )
    finally:
        _restore_paper_db(saved)
        Path(path).unlink(missing_ok=True)


# =============================================================================
# TEST 5: sensitivity._compute_stft_outcomes (real function)
# =============================================================================
def test_sensitivity_stft_outcomes_real():
    """sensitivity._compute_stft_outcomes on fixture DB produces identical
    consumed row set (3 directional rows) as trim.compute_stft."""
    path, conn = _make_fixture_db()
    conn.close()

    saved = _patch_all_paper_db(path)
    try:
        import portfolio.sensitivity as sens_mod
        importlib.reload(sens_mod)
        setattr(sens_mod, 'PAPER_DB', path)

        test_conn = sqlite3.connect(path)
        sens_result = sens_mod._compute_stft_outcomes(test_conn)
        test_conn.close()

        total = sum(v.get('samples', 0) for v in sens_result.values())

        assert total == 3, (
            f"sensitivity consumed {total} predictions, expected 3 (directional only)"
        )
    finally:
        _restore_paper_db(saved)
        Path(path).unlink(missing_ok=True)


# =============================================================================
# TEST 6: selector text-match win-rate (labeled source check)
# =============================================================================
def test_selector_text_match_source_check():
    """Labeled source check — function not callable in isolation.
    selector text-match predictions query has directional filter;
    signal_ledger recent-window does NOT (excluded by design, item 12 backlog)."""
    import portfolio.selector as sel_mod
    src = inspect.getsource(sel_mod.generate_recommendations)

    assert "direction IN ('bullish','bearish')" in src, (
        "selector text-match predictions query missing directional filter"
    )
    count = src.count("direction IN ('bullish','bearish')")
    assert count == 1, (
        f"selector has {count} directional filters, expected 1 "
        f"(predictions text-match only; signal_ledger excluded per spec)"
    )


# =============================================================================
# TEST 7: semantic_gates.semantic_win_rate (source check)
# =============================================================================
def test_semantic_gates_source_check():
    """semantic_gates.semantic_win_rate query contains directional filter."""
    import reasoning.semantic_gates as sg_mod
    src = inspect.getsource(sg_mod.semantic_win_rate)
    assert "direction IN ('bullish','bearish')" in src, (
        "semantic_gates.semantic_win_rate missing directional filter"
    )


# =============================================================================
# TEST 8: reports.get_prediction_data (real function)
# =============================================================================
def test_reports_prediction_data_real():
    """reports.get_prediction_data returns directional_hit_rate, abstention_accuracy,
    and market_neutral_base_rate computed by the real code.
    Fixture: 6 total, directional 2/3=0.667, abstention 2/2=1.0, neutral base 3/6=0.5."""
    path, conn = _make_fixture_db()
    conn.close()

    saved = _patch_all_paper_db(path)
    try:
        import reasoning.reports as reports_mod
        importlib.reload(reports_mod)
        setattr(reports_mod, 'PAPER_DB', path)

        since = datetime.now(timezone.utc) - timedelta(days=1)
        result = reports_mod.get_prediction_data(since)

        assert 'directional_hit_rate' in result, "missing directional_hit_rate"
        assert 'abstention_accuracy' in result, "missing abstention_accuracy"
        assert 'market_neutral_base_rate' in result, "missing market_neutral_base_rate"

        assert result['total'] == 6, f"expected 6 total, got {result['total']}"
        assert abs(result['directional_hit_rate'] - 0.667) < 0.01, (
            f"directional_hit_rate = {result['directional_hit_rate']}, expected ~0.667"
        )
        assert abs(result['abstention_accuracy'] - 1.0) < 0.01, (
            f"abstention_accuracy = {result['abstention_accuracy']}, expected ~1.0"
        )
        assert abs(result['market_neutral_base_rate'] - 0.5) < 0.01, (
            f"market_neutral_base_rate = {result['market_neutral_base_rate']}, expected ~0.5"
        )
    finally:
        _restore_paper_db(saved)
        Path(path).unlink(missing_ok=True)


# =============================================================================
# TEST 9: No-backfill (safety check — passes on both base and r2b)
# =============================================================================
def test_no_backfill():
    """Consumers only SELECT: was_correct and actual_direction unchanged after calls."""
    path, conn = _make_fixture_db()
    q = "SELECT id, direction, was_correct, actual_direction FROM predictions ORDER BY id"
    before = conn.execute(q).fetchall()
    conn.close()
    saved = _patch_all_paper_db(path)
    try:
        import reasoning.calibration as cal_mod, reasoning.critic as critic_mod
        importlib.reload(cal_mod); importlib.reload(critic_mod)
        setattr(critic_mod, 'PAPER_DB', path)
        c = sqlite3.connect(path); cal_mod.get_sector_win_rates(c); c.close()
        critic_mod.get_sector_win_rate("", window=20)
    finally:
        _restore_paper_db(saved)
    c = sqlite3.connect(path); after = c.execute(q).fetchall(); c.close()
    Path(path).unlink(missing_ok=True)
    assert before == after, f"rows changed:\n{before}\n->\n{after}"


# =============================================================================
# TEST 10: postmortem candidate fetch (real function, captured query)
# =============================================================================
def test_postmortem_candidate_directional():
    """Drive real run_postmortem against the FIXTURE only; capture the candidate query."""
    path, conn = _make_fixture_db(); conn.close()
    saved = _patch_all_paper_db(path)
    import reasoning.postmortem as pm_mod
    importlib.reload(pm_mod)
    setattr(pm_mod, 'PAPER_DB', path)
    real_connect = sqlite3.connect          # captured BEFORE patching (no recursion)
    fixture = Path(path).resolve()
    captured = []

    class _EmptyCursor:
        def fetchall(self): return []
        def fetchone(self): return None

    refused = []

    class _NullConnection:
        """Never opens a file. Every query returns empty; writes are no-ops."""
        row_factory = None
        def execute(self, q, *a, **k): captured.append(str(q)); return _EmptyCursor()
        def executemany(self, *a, **k): return _EmptyCursor()
        def executescript(self, *a, **k): return _EmptyCursor()
        def commit(self): pass
        def close(self): pass
        def cursor(self): return self
        def fetchall(self): return []
        def fetchone(self): return None
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _CapturingConnection:
        def __init__(self, real): self._conn = real
        def execute(self, query, *args, **kwargs):
            captured.append(str(query))
            if 'was_correct = 0' in str(query) and 'predictions' in str(query):
                return _EmptyCursor()
            return self._conn.execute(query, *args, **kwargs)
        def __getattr__(self, name): return getattr(self._conn, name)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_connect(p, *a, **k):
        if Path(str(p)).resolve() == fixture:
            return _CapturingConnection(real_connect(str(p)))
        refused.append(str(p))
        return _NullConnection()          # never touches a real file

    try:
        with patch('sqlite3.connect', _fake_connect):
            try:
                pm_mod.run_postmortem()
            except Exception:
                pass
    finally:
        _restore_paper_db(saved)
        Path(path).unlink(missing_ok=True)

    cand = [q for q in captured if 'was_correct = 0' in q and 'predictions' in q]
    assert cand, f"no candidate query captured; first queries: {captured[:3]}"
    assert "direction IN ('bullish','bearish')" in cand[0], cand[0]


# =============================================================================
def test_daily_predictions_source_check():
    """Labeled source check — nested function, not callable in isolation.
    _check_repeating_failure inside run_daily_predictions has directional filter."""
    import reasoning.daily_predictions as dp_mod
    src = inspect.getsource(dp_mod.run_daily_predictions)
    assert "direction IN ('bullish','bearish')" in src, (
        "daily_predictions._check_repeating_failure missing directional filter"
    )


# =============================================================================
# TEST 12: trim.reconcile_industrials_trim (source check)
# =============================================================================
def test_trim_reconcile_industrials_directional():
    """trim.reconcile_industrials_trim trailing-7d query has directional filter.
    This was the missing fix — r1 had it, r2 dropped it, r2b re-adds it."""
    import portfolio.trim as trim_mod
    src = inspect.getsource(trim_mod.reconcile_industrials_trim)
    assert "direction IN ('bullish','bearish')" in src, (
        "trim.reconcile_industrials_trim missing directional filter"
    )


# =============================================================================
# TEST 13: sensitivity (source check)
# =============================================================================
def test_sensitivity_source_check():
    """sensitivity._compute_stft_outcomes query contains directional filter."""
    import portfolio.sensitivity as sens_mod
    src = inspect.getsource(sens_mod._compute_stft_outcomes)
    assert "direction IN ('bullish','bearish')" in src, (
        "sensitivity._compute_stft_outcomes missing directional filter"
    )
