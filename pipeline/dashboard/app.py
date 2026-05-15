#!/usr/bin/env python3
"""
Trading AI Dashboard
Simple Flask dashboard showing signals, predictions, and performance
"""

import json
import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template_string

sys.path.insert(0, str(Path(__file__).parent.parent))

from paper_trading.db import init_db, get_performance_summary
from qdrant_client import QdrantClient

app = Flask(__name__)

PAPER_DB  = Path(os.environ.get("PAPER_DB_PATH",
            "/home/trading/trading-ai/data/paper_trading.db"))
RULES_DB  = Path("/home/trading/trading-ai/data/rules.db")
SIGNALS_DIR = Path("/mnt/qnap/timeseries/signals")
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading AI Dashboard</title>
<meta http-equiv="refresh" content="60">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #e6edf3; min-height: 100vh; }
  .header { background: #161b22; border-bottom: 1px solid #30363d;
            padding: 16px 24px; display: flex; align-items: center;
            justify-content: space-between; }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header .subtitle { color: #8b949e; font-size: 13px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 16px; padding: 24px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: 20px; }
  .card h2 { font-size: 14px; font-weight: 600; color: #8b949e;
             text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px; }
  .stat { display: flex; justify-content: space-between; align-items: center;
          padding: 8px 0; border-bottom: 1px solid #21262d; }
  .stat:last-child { border-bottom: none; }
  .stat-label { color: #8b949e; font-size: 13px; }
  .stat-value { font-size: 15px; font-weight: 600; }
  .bullish { color: #3fb950; }
  .bearish { color: #f85149; }
  .neutral { color: #8b949e; }
  .mixed   { color: #d29922; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
           font-size: 11px; font-weight: 600; }
  .badge-bull { background: #0d4a1f; color: #3fb950; }
  .badge-bear { background: #4a0d0d; color: #f85149; }
  .badge-neut { background: #21262d; color: #8b949e; }
  .prediction { padding: 12px 0; border-bottom: 1px solid #21262d; }
  .prediction:last-child { border-bottom: none; }
  .pred-query { font-size: 13px; margin-bottom: 6px; color: #e6edf3; }
  .pred-meta { font-size: 11px; color: #8b949e; display: flex; gap: 12px; }
  .signal-row { padding: 8px 0; border-bottom: 1px solid #21262d;
                font-size: 12px; }
  .signal-row:last-child { border-bottom: none; }
  .signal-title { color: #e6edf3; margin-bottom: 3px; }
  .signal-meta { color: #8b949e; display: flex; gap: 8px; }
  .progress-bar { background: #21262d; border-radius: 4px; height: 6px;
                  margin-top: 4px; }
  .progress-fill { height: 100%; border-radius: 4px; background: #3fb950; }
  .win-fill { background: #3fb950; }
  .loss-fill { background: #f85149; }
  .timestamp { color: #8b949e; font-size: 11px; }
  .full-width { grid-column: 1 / -1; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 12px; color: #8b949e; font-weight: 500;
       border-bottom: 1px solid #30363d; }
  td { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  tr:last-child td { border-bottom: none; }
  .correct { color: #3fb950; }
  .wrong { color: #f85149; }
  .pending { color: #8b949e; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>⚡ Trading AI Dashboard</h1>
    <div class="subtitle">Auto-refreshes every 60s</div>
  </div>
  <div class="timestamp" id="ts"></div>
</div>
<div class="grid" id="grid">Loading...</div>
<script>
document.getElementById('ts').textContent = new Date().toLocaleString();

async function load() {
  const data = await fetch('/api/data').then(r => r.json());
  const grid = document.getElementById('grid');

  const dirColor = d => d === 'bullish' ? 'bullish' : d === 'bearish' ? 'bearish' : 'neutral';
  const dirBadge = d => `<span class="badge badge-${d === 'bullish' ? 'bull' : d === 'bearish' ? 'bear' : 'neut'}">${d?.toUpperCase() || 'N/A'}</span>`;

  grid.innerHTML = `
    <!-- Pipeline Stats -->
    <div class="card">
      <h2>Pipeline Health</h2>
      <div class="stat">
        <span class="stat-label">Qdrant Vectors</span>
        <span class="stat-value">${data.qdrant_count?.toLocaleString()}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Active Rules</span>
        <span class="stat-value">${data.rules_count}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Pending Proposals</span>
        <span class="stat-value">${data.proposals_count}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Signals (24h)</span>
        <span class="stat-value">${data.signals_24h}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Last Pipeline Run</span>
        <span class="stat-value timestamp">${data.last_run || 'N/A'}</span>
      </div>
    </div>

    <!-- Sentiment Distribution -->
    <div class="card">
      <h2>Signal Sentiment (48h)</h2>
      <div class="stat">
        <span class="stat-label">Bullish</span>
        <span class="stat-value bullish">${data.sentiment?.bullish || 0}
          (${data.sentiment?.bull_pct || 0}%)</span>
      </div>
      <div class="stat">
        <span class="stat-label">Bearish</span>
        <span class="stat-value bearish">${data.sentiment?.bearish || 0}
          (${data.sentiment?.bear_pct || 0}%)</span>
      </div>
      <div class="stat">
        <span class="stat-label">Neutral</span>
        <span class="stat-value neutral">${data.sentiment?.neutral || 0}
          (${data.sentiment?.neut_pct || 0}%)</span>
      </div>
      <div class="stat">
        <span class="stat-label">Avg Confidence</span>
        <span class="stat-value">${data.sentiment?.avg_conf || 0}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Top Sector</span>
        <span class="stat-value">${data.sentiment?.top_sector || 'N/A'}</span>
      </div>
    </div>

    <!-- Prediction Performance -->
    <div class="card">
      <h2>Prediction Performance</h2>
      <div class="stat">
        <span class="stat-label">Total Predictions</span>
        <span class="stat-value">${data.perf?.total || 0}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Verified</span>
        <span class="stat-value">${data.perf?.verified || 0}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Win Rate</span>
        <span class="stat-value ${data.perf?.win_rate > 0.5 ? 'bullish' : 'bearish'}">
          ${data.perf?.win_rate_pct || 'N/A'}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Avg Confidence</span>
        <span class="stat-value">${data.perf?.avg_conf || 'N/A'}</span>
      </div>
      ${data.perf?.verified > 0 ? `
      <div class="progress-bar" style="margin-top:12px">
        <div class="progress-fill win-fill"
             style="width:${data.perf?.win_rate_pct || '0%'}"></div>
      </div>` : '<div class="stat"><span class="stat-label" style="color:#8b949e;font-size:12px">No verified predictions yet</span></div>'}
    </div>

    <!-- Recent Predictions -->
    <div class="card full-width">
      <h2>Predictions</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>Query</th><th>Direction</th>
            <th>Probability</th><th>Confidence</th>
            <th>Timeframe</th><th>Status</th><th>Created</th>
          </tr>
        </thead>
        <tbody>
          ${(data.predictions || []).map(p => `
          <tr>
            <td>${p.id}</td>
            <td>${p.query}</td>
            <td>${dirBadge(p.direction)}</td>
            <td>${(p.probability * 100).toFixed(0)}%</td>
            <td>${(p.confidence * 100).toFixed(0)}%</td>
            <td>${p.timeframe}</td>
            <td class="${p.was_correct === 1 ? 'correct' : p.was_correct === 0 ? 'wrong' : 'pending'}">
              ${p.was_correct === 1 ? '✓ Correct' : p.was_correct === 0 ? '✗ Wrong' :
                p.expires_in < 0 ? '⏳ Pending verification' : `⏱ ${p.expires_in}h left`}
            </td>
            <td class="timestamp">${p.created_at}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>

    <!-- Top Sectors -->
    <div class="card">
      <h2>Top Sectors (48h)</h2>
      ${(data.top_sectors || []).map(s => `
      <div class="stat">
        <span class="stat-label">${s.sector}</span>
        <span class="stat-value ${dirColor(s.bias)}">${s.count} signals</span>
      </div>`).join('')}
    </div>

    <!-- Top Tickers -->
    <div class="card">
      <h2>Most Mentioned Tickers (48h)</h2>
      ${(data.top_tickers || []).map(t => `
      <div class="stat">
        <span class="stat-label">${t.ticker}</span>
        <span class="stat-value ${dirColor(t.bias)}">${t.count} × ${dirBadge(t.bias)}</span>
      </div>`).join('')}
    </div>

    <!-- Recent Signals -->
    <div class="card full-width">
      <h2>Recent Signals</h2>
      ${(data.recent_signals || []).map(s => `
      <div class="signal-row">
        <div class="signal-title">${s.title}</div>
        <div class="signal-meta">
          <span>${dirBadge(s.sentiment)}</span>
          <span>conf=${(s.confidence*100).toFixed(0)}%</span>
          <span>${s.source}</span>
          ${s.tickers?.length ? `<span>📈 ${s.tickers.join(' ')}</span>` : ''}
          ${s.sectors?.length ? `<span>🏭 ${s.sectors.slice(0,3).join(', ')}</span>` : ''}
        </div>
      </div>`).join('')}
    </div>
  `;
}
load();
</script>
</body>
</html>'''


@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/data')
def api_data():
    data = {}

    # Qdrant stats
    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        info = client.get_collection('trading_signals')
        data['qdrant_count'] = info.points_count
    except Exception as e:
        data['qdrant_count'] = 0

    # Rules DB stats
    try:
        conn = sqlite3.connect(RULES_DB)
        data['rules_count'] = conn.execute(
            "SELECT COUNT(*) FROM inference_rules WHERE active=1"
        ).fetchone()[0]
        data['proposals_count'] = conn.execute(
            "SELECT COUNT(*) FROM rule_proposals WHERE status='pending'"
        ).fetchone()[0]
        conn.close()
    except:
        data['rules_count'] = 0
        data['proposals_count'] = 0

    # Signal stats from scored files
    from collections import Counter, defaultdict
    signals = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    try:
        for f in sorted(SIGNALS_DIR.glob('scored_*.jsonl'), reverse=True)[:20]:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            s = json.loads(line)
                            if isinstance(s, dict):
                                signals.append(s)
                        except:
                            continue

        data['signals_24h'] = len([s for s in signals
            if s.get('scored_at', '') > (datetime.now(timezone.utc)
                                         - timedelta(hours=24)).isoformat()])

        bull = sum(1 for s in signals if s.get('sentiment') == 'bullish')
        bear = sum(1 for s in signals if s.get('sentiment') == 'bearish')
        neut = sum(1 for s in signals if s.get('sentiment') == 'neutral')
        total = len(signals) or 1
        avg_conf = sum(s.get('confidence', 0) for s in signals) / total

        sector_counts = Counter()
        sector_sentiment = defaultdict(list)
        ticker_counts = Counter()
        ticker_sentiment = defaultdict(list)

        for s in signals:
            for sector in s.get('sectors', []):
                sector_counts[sector] += 1
                sector_sentiment[sector].append(s.get('sentiment'))
            for ticker in s.get('tickers', []):
                ticker_counts[ticker] += 1
                ticker_sentiment[ticker].append(s.get('sentiment'))

        data['sentiment'] = {
            'bullish':   bull,
            'bearish':   bear,
            'neutral':   neut,
            'bull_pct':  round(bull/total*100, 1),
            'bear_pct':  round(bear/total*100, 1),
            'neut_pct':  round(neut/total*100, 1),
            'avg_conf':  round(avg_conf, 2),
            'top_sector': sector_counts.most_common(1)[0][0]
                          if sector_counts else 'N/A'
        }

        def bias(sentiments):
            b = sentiments.count('bullish')
            br = sentiments.count('bearish')
            return 'bullish' if b > br else 'bearish' if br > b else 'neutral'

        data['top_sectors'] = [
            {'sector': s, 'count': c, 'bias': bias(sector_sentiment[s])}
            for s, c in sector_counts.most_common(8)
        ]
        data['top_tickers'] = [
            {'ticker': t, 'count': c, 'bias': bias(ticker_sentiment[t])}
            for t, c in ticker_counts.most_common(10)
            if len(t) <= 5
        ]
        data['recent_signals'] = [
            {
                'title':      s.get('title', '')[:80],
                'sentiment':  s.get('sentiment'),
                'confidence': s.get('confidence', 0),
                'source':     s.get('source', ''),
                'tickers':    s.get('tickers', []),
                'sectors':    s.get('sectors', []),
            }
            for s in signals[:15]
        ]

    except Exception as e:
        data['signals_24h'] = 0
        data['sentiment'] = {}
        data['top_sectors'] = []
        data['top_tickers'] = []
        data['recent_signals'] = []

    # Paper trading stats
    try:
        conn = init_db()
        summary = get_performance_summary(conn)
        preds = conn.execute('''
            SELECT id, query, timeframe, direction, probability,
                   confidence, was_correct, created_at, verified_at
            FROM predictions ORDER BY created_at DESC
        ''').fetchall()

        now = datetime.now(timezone.utc)
        hours_map = {'24h': 24, '48h': 48, '1w': 168}
        pred_list = []
        for p in preds:
            created = datetime.fromisoformat(
                p[7].replace('Z', '+00:00')
            )
            hours = hours_map.get(p[2], 24)
            expires_in = round(hours - (now - created).total_seconds()/3600, 1)
            pred_list.append({
                'id':          p[0],
                'query':       p[1][:55] + '...' if len(p[1]) > 55 else p[1],
                'timeframe':   p[2],
                'direction':   p[3],
                'probability': p[4],
                'confidence':  p[5],
                'was_correct': p[6],
                'created_at':  p[7][:16].replace('T', ' '),
                'expires_in':  max(0, expires_in),
            })

        verified = sum(1 for p in pred_list if p['was_correct'] is not None)
        correct  = sum(1 for p in pred_list if p['was_correct'] == 1)
        win_rate = correct / verified if verified > 0 else 0
        avg_conf = sum(p['confidence'] for p in pred_list) / len(pred_list) if pred_list else 0

        data['predictions'] = pred_list
        data['perf'] = {
            'total':        len(pred_list),
            'verified':     verified,
            'correct':      correct,
            'win_rate':     win_rate,
            'win_rate_pct': f"{win_rate:.0%}",
            'avg_conf':     f"{avg_conf:.2f}",
        }
        conn.close()
    except Exception as e:
        data['predictions'] = []
        data['perf'] = {'total': 0, 'verified': 0}

    # Last pipeline run from cron log
    try:
        cron_log = Path('/mnt/qnap/timeseries/logs/cron.log')
        lines = cron_log.read_text().splitlines()
        last_complete = [l for l in lines if 'Pipeline complete' in l]
        if last_complete:
            data['last_run'] = last_complete[-1][:19]
    except:
        data['last_run'] = 'N/A'

    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
