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
from portfolio.db import (init_db as init_portfolio_db, get_open_positions,
                          get_cash_balance, get_portfolio_value, CONFIG as PORT_CONFIG)
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
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       background:#0d1117;color:#e6edf3;min-height:100vh;font-size:13px}
  a{color:#58a6ff}
  .header{background:#161b22;border-bottom:1px solid #30363d;
          padding:12px 20px;display:flex;align-items:center;justify-content:space-between;
          position:sticky;top:0;z-index:100}
  .header h1{font-size:16px;font-weight:600}
  .header-right{display:flex;align-items:center;gap:12px}
  .status-badge{display:flex;align-items:center;gap:6px;font-size:12px;
                padding:4px 10px;border-radius:20px;font-weight:500}
  .status-active{background:#1a3a2a;color:#3fb950;border:1px solid #238636}
  .status-macro{background:#3a2a0a;color:#d29922;border:1px solid #9e6a03}
  .status-breaker{background:#3a1a1a;color:#f85149;border:1px solid #da3633}
  .status-dot{width:7px;height:7px;border-radius:50%;background:currentColor}
  .last-run{font-size:11px;color:#8b949e}
  .metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;padding:16px 20px}
  .metric{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
  .metric-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px}
  .metric-value{font-size:22px;font-weight:600}
  .metric-sub{font-size:11px;color:#8b949e;margin-top:3px}
  .green{color:#3fb950} .red{color:#f85149} .yellow{color:#d29922} .blue{color:#58a6ff} .gray{color:#8b949e}
  .grid{display:grid;gap:12px;padding:0 20px 20px}
  .grid-2{grid-template-columns:1fr 1fr}
  .grid-3{grid-template-columns:1fr 1fr 1fr}
  .grid-1-2{grid-template-columns:1fr 2fr}
  .grid-2-1{grid-template-columns:2fr 1fr}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;
        cursor:grab;user-select:none;transition:border-color 0.15s}
  .card:active{cursor:grabbing}
  .card.drag-over{border-color:#58a6ff;background:#1c2128}
  .card.dragging{opacity:0.4;border-color:#58a6ff}
  .card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
  .card-title{font-size:13px;font-weight:600;color:#e6edf3}
  .drag-handle{font-size:16px;color:#30363d;cursor:grab;line-height:1}
  .drag-handle:hover{color:#8b949e}
  table{width:100%;border-collapse:collapse}
  th{font-size:11px;color:#8b949e;font-weight:400;text-align:left;
     padding:0 0 6px;border-bottom:1px solid #21262d}
  td{padding:6px 0;border-bottom:1px solid #21262d;vertical-align:middle}
  tr:last-child td{border-bottom:none}
  .badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500}
  .badge-bull{background:#1a3a2a;color:#3fb950}
  .badge-bear{background:#3a1a1a;color:#f85149}
  .badge-neut{background:#1c2128;color:#8b949e;border:1px solid #30363d}
  .badge-mix{background:#2a2a1a;color:#d29922}
  .correct{color:#3fb950} .wrong{color:#f85149} .pending{color:#8b949e}
  .timestamp{color:#8b949e;font-size:11px}
  .bar-bg{background:#21262d;border-radius:3px;height:6px;flex:1}
  .bar-fill{border-radius:3px;height:6px;transition:width 0.3s}
  details summary{cursor:pointer;font-size:12px;color:#8b949e;padding:8px 0;
                  border-top:1px solid #21262d;margin-top:8px;list-style:none}
  details summary::-webkit-details-marker{display:none}
  details summary::before{content:"▶ ";font-size:10px}
  details[open] summary::before{content:"▼ "}
  .activity-item{display:flex;justify-content:space-between;align-items:center;
                 padding:5px 0;border-bottom:1px solid #21262d;font-size:12px}
  .activity-item:last-child{border-bottom:none}
  .pred-card{background:#0d1117;border:1px solid #21262d;border-radius:6px;
             padding:8px 12px;margin-bottom:6px}
  .pred-row{display:flex;justify-content:space-between;align-items:center}
  .pred-sector{font-size:12px;color:#e6edf3;margin-bottom:4px}
  .chart-wrap{position:relative;width:100%}
  .legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:#8b949e;margin-bottom:8px}
  .legend-dot{width:8px;height:8px;border-radius:2px;display:inline-block;margin-right:4px}
  .sector-bar{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .sector-name{font-size:12px;width:100px;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sector-count{font-size:11px;color:#8b949e;width:30px;text-align:right}
  .pos-pnl-bar{height:3px;border-radius:2px;margin-top:3px}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>Trading AI</h1>
  </div>
  <div class="header-right">
    <span class="last-run" id="lastRun">Loading...</span>
    <span class="status-badge status-active" id="statusBadge">
      <span class="status-dot"></span>
      <span id="statusText">Trading active</span>
    </span>
  </div>
</div>

<div class="metrics" id="metrics">
  <div class="metric">
    <div class="metric-label">Portfolio value</div>
    <div class="metric-value" id="mPortfolio">—</div>
    <div class="metric-sub" id="mReturn">—</div>
  </div>
  <div class="metric">
    <div class="metric-label">Cash available</div>
    <div class="metric-value" id="mCash">—</div>
    <div class="metric-sub" id="mCashPct">—</div>
  </div>
  <div class="metric">
    <div class="metric-label">Win rate</div>
    <div class="metric-value" id="mWinRate">—</div>
    <div class="metric-sub" id="mVerified">—</div>
  </div>
  <div class="metric">
    <div class="metric-label">Open positions</div>
    <div class="metric-value" id="mPositions">—</div>
    <div class="metric-sub" id="mSlots">—</div>
  </div>
  <div class="metric">
    <div class="metric-label">Signal vectors</div>
    <div class="metric-value" id="mVectors">—</div>
    <div class="metric-sub" id="mSignals24">—</div>
  </div>
  <div class="metric">
    <div class="metric-label">Cash yield (BIL shadow)</div>
    <div class="metric-value" id="mCashYield">—</div>
    <div class="metric-sub" id="mCashYieldSub">—</div>
  </div>
</div>

<div id="dashGrid">

  <div class="grid grid-2" id="row0">
    <div class="card" draggable="true" id="card-pnl">
      <div class="card-header">
        <span class="card-title">Portfolio value</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div class="legend">
        <span><span class="legend-dot" style="background:#58a6ff"></span>Value</span>
        <span><span class="legend-dot" style="background:#30363d;border:1px dashed #8b949e"></span>$50k baseline</span>
      </div>
      <div class="chart-wrap" style="height:160px">
        <canvas id="pnlChart" role="img" aria-label="Portfolio value over time">Portfolio value chart</canvas>
      </div>
    </div>

    <div class="card" draggable="true" id="card-winrate">
      <div class="card-header">
        <span class="card-title">Win rate trend</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div class="legend">
        <span><span class="legend-dot" style="background:#f85149"></span>Win rate</span>
        <span><span class="legend-dot" style="background:#30363d;border:1px dashed #8b949e"></span>50% target</span>
      </div>
      <div class="chart-wrap" style="height:160px">
        <canvas id="winChart" role="img" aria-label="Win rate trend over time">Win rate chart</canvas>
      </div>
    </div>
  </div>

  <div class="grid grid-2-1" id="row1">
    <div class="card" draggable="true" id="card-positions">
      <div class="card-header">
        <span class="card-title">Open positions</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Ticker</th><th>Sector</th><th>Shares</th>
            <th style="text-align:right">Entry</th>
            <th style="text-align:right">P&L</th>
            <th style="text-align:right">Stop</th>
            <th style="text-align:center">Tier</th>
            <th style="text-align:right">Days</th>
          </tr>
        </thead>
        <tbody id="positionsBody">
          <tr><td colspan="8" class="gray" style="text-align:center;padding:12px">Loading...</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card" draggable="true" id="card-sector">
      <div class="card-header">
        <span class="card-title">Sector exposure</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div class="chart-wrap" style="height:180px">
        <canvas id="sectorChart" role="img" aria-label="Sector exposure donut chart">Sector allocation</canvas>
      </div>
      <div id="sectorLegend" class="legend" style="margin-top:8px;flex-direction:column;gap:4px"></div>
    </div>
  </div>

  <div class="grid grid-2" id="row2">
    <div class="card" draggable="true" id="card-predictions">
      <div class="card-header">
        <span class="card-title">Today\'s predictions</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div id="todayPreds">
        <div class="gray" style="font-size:12px">Loading...</div>
      </div>
      <details id="predsHistory">
        <summary id="predsHistorySummary">Show history</summary>
        <div style="overflow-x:auto;margin-top:10px">
          <table>
            <thead>
              <tr>
                <th>#</th><th>Query</th><th>Direction</th>
                <th style="text-align:right">Conf</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody id="predsHistoryBody"></tbody>
          </table>
        </div>
      </details>
    </div>

    <div class="card" draggable="true" id="card-activity">
      <div class="card-header">
        <span class="card-title">Recent activity</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div id="activityFeed">
        <div class="gray" style="font-size:12px">Loading...</div>
      </div>
    </div>
  </div>

  <div class="grid grid-2" id="row3">
    <div class="card" draggable="true" id="card-sectors">
      <div class="card-header">
        <span class="card-title">Top sectors (48h signals)</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div id="sectorBars"></div>
    </div>

    <div class="card" draggable="true" id="card-tickers">
      <div class="card-header">
        <span class="card-title">Most mentioned tickers</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div id="tickerBars"></div>
    </div>
  </div>

  <div class="grid grid-1-2" id="row4">
    <div class="card" draggable="true" id="card-sentiment">
      <div class="card-header">
        <span class="card-title">Sentiment (48h)</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <div class="chart-wrap" style="height:160px">
        <canvas id="sentChart" role="img" aria-label="Sentiment distribution chart">Sentiment chart</canvas>
      </div>
    </div>

    <div class="card" draggable="true" id="card-closed">
      <div class="card-header">
        <span class="card-title">Closed positions</span>
        <span class="drag-handle" title="Drag to reorder">⠿</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th style="text-align:right">Entry</th>
            <th style="text-align:right">Exit</th>
            <th style="text-align:right">P&L</th>
            <th>Reason</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody id="closedBody">
          <tr><td colspan="6" class="gray" style="text-align:center;padding:12px">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
const fmt$ = v => '$' + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
const fmtK = v => '$' + (v/1000).toFixed(1) + 'k';
const fmtPct = v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%';

function dirBadge(d) {
  if (d === 'bullish') return '<span class="badge badge-bull">bullish</span>';
  if (d === 'bearish') return '<span class="badge badge-bear">bearish</span>';
  if (d === 'mixed')   return '<span class="badge badge-mix">mixed</span>';
  return '<span class="badge badge-neut">neutral</span>';
}

let pnlChartInst, winChartInst, sectorChartInst, sentChartInst;
const SECTOR_COLORS = ['#58a6ff','#3fb950','#d29922','#f85149','#bc8cff','#79c0ff','#56d364','#ffa657'];

async function loadData() {
  const data = await fetch('/api/data').then(r => r.json());

  // Status badge
  const badge = document.getElementById('statusBadge');
  const statusText = document.getElementById('statusText');
  const macroP = (data.predictions||[]).find(p =>
    p.query && (p.query.includes('market outlook') || p.query.includes('macro')));
  const circuitBreaker = (data.portfolio?.return_pct || 0) < -2;
  // VIX gate check
  const vix = data.vix || {};
  if (vix.action === 'pause') {
    badge.className = 'status-badge status-breaker';
    statusText.textContent = `VIX ${vix.vix?.toFixed(1)} — volatility halt`;
  } else if (vix.action === 'reduce') {
    badge.className = 'status-badge status-macro';
    statusText.textContent = `VIX ${vix.vix?.toFixed(1)} — reduced size`;
  }

  const nowDt = new Date();
  const day = nowDt.getDay();
  const hour = nowDt.getHours();
  const min = nowDt.getMinutes();
  const timeVal = hour * 60 + min;
  const isWeekday = day >= 1 && day <= 5;
  const isMarketHours = timeVal >= 570 && timeVal <= 960; // 9:30-16:00
  const marketOpen = isWeekday && isMarketHours;
  if (circuitBreaker) {
    badge.className = 'status-badge status-breaker';
    statusText.textContent = 'Circuit breaker active';
  } else if (macroP && macroP.direction === 'bearish' && macroP.confidence >= 0.70) {
    badge.className = 'status-badge status-macro';
    statusText.textContent = 'Macro gate — entries paused';
  } else if (!marketOpen) {
    badge.className = 'status-badge';
    badge.style.background = '#1c2128';
    badge.style.color = '#8b949e';
    badge.style.border = '1px solid #30363d';
    const nextOpen = day === 0 ? 'Mon' : day === 6 ? 'Mon' : day === 5 && timeVal > 960 ? 'Mon' : 'today';
    statusText.textContent = isWeekday ? 'Market closed' : 'Weekend — market closed';
  } else {
    badge.className = 'status-badge status-active';
    statusText.textContent = 'Trading active';
  }

  // Last run
  if (data.last_run) document.getElementById('lastRun').textContent = 'Last pipeline: ' + data.last_run;

  // Metrics
  const p = data.portfolio || {};
  const retPct = p.return_pct || 0;
  const cashPct = p.total_value ? Math.round(p.cash / p.total_value * 100) : 0;
  const maxPos = 12;

  document.getElementById('mPortfolio').innerHTML =
    `<span class="${retPct >= 0 ? 'green' : 'red'}">${fmt$(p.total_value||0)}</span>`;
  document.getElementById('mReturn').textContent = fmtPct(retPct) + ' all time';
  document.getElementById('mCash').textContent = fmt$(p.cash||0);
  document.getElementById('mCashPct').textContent = cashPct + '% of portfolio';

  const wr = data.perf?.win_rate || 0;
  document.getElementById('mWinRate').innerHTML =
    `<span class="${wr >= 0.5 ? 'green' : wr >= 0.4 ? 'yellow' : 'red'}">${data.perf?.win_rate_pct||'N/A'}</span>`;
  document.getElementById('mVerified').textContent =
    `${data.perf?.correct||0}/${data.perf?.verified||0} verified`;

  document.getElementById('mPositions').textContent = p.open_count || 0;
  document.getElementById('mSlots').textContent = `${p.open_count||0}/${maxPos} slots used`;
  document.getElementById('mVectors').textContent = (data.qdrant_count||0).toLocaleString();
  document.getElementById('mSignals24').textContent = `+${data.signals_24h||0} today`;

  const cy = data.cash_yield || {};
  if (cy.daily_earnings) {
    document.getElementById('mCashYield').innerHTML =
      `<span class="green">$${cy.daily_earnings.toFixed(2)}/day</span>`;
    document.getElementById('mCashYieldSub').textContent =
      `$${cy.monthly_earnings?.toFixed(0)}/mo · ${cy.annual_yield?.toFixed(2)}% APY`;
  }

  // P&L chart
  const snaps = data.snapshots || [];
  const pnlLabels = snaps.map(s => s.date);
  const pnlVals   = snaps.map(s => s.total_value);
  if (pnlChartInst) pnlChartInst.destroy();
  pnlChartInst = new Chart(document.getElementById('pnlChart'), {
    type: 'line',
    data: {
      labels: pnlLabels,
      datasets: [
        {
          label: 'Portfolio',
          data: pnlVals,
          borderColor: '#58a6ff',
          backgroundColor: 'rgba(88,166,255,0.08)',
          fill: true, tension: 0.3, pointRadius: 1, borderWidth: 2
        },
        {
          label: 'Baseline',
          data: pnlLabels.map(() => 50000),
          borderColor: '#30363d',
          borderDash: [4,4], borderWidth: 1, pointRadius: 0, fill: false
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 10 }, maxTicksLimit: 8 },
             grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', font: { size: 10 },
                      callback: v => fmtK(v) },
             grid: { color: '#21262d' } }
      }
    }
  });

  // Win rate chart — weekly buckets from predictions
  const preds = data.predictions || [];
  const weekBuckets = {};
  preds.forEach(p => {
    const d = new Date(p.created_at);
    const week = 'W' + Math.ceil((d - new Date('2026-05-18')) / (7*86400000));
    if (!weekBuckets[week]) weekBuckets[week] = {correct:0, verified:0};
    if (p.was_correct !== null) {
      weekBuckets[week].verified++;
      if (p.was_correct === 1) weekBuckets[week].correct++;
    }
  });
  const wkLabels = Object.keys(weekBuckets).sort();
  const wkRates  = wkLabels.map(w => weekBuckets[w].verified > 0
    ? Math.round(weekBuckets[w].correct / weekBuckets[w].verified * 100) : null);

  if (winChartInst) winChartInst.destroy();
  winChartInst = new Chart(document.getElementById('winChart'), {
    type: 'line',
    data: {
      labels: wkLabels,
      datasets: [
        {
          label: 'Win rate',
          data: wkRates,
          borderColor: '#f85149',
          backgroundColor: 'rgba(248,81,73,0.08)',
          fill: true, tension: 0.3, pointRadius: 4, borderWidth: 2,
          spanGaps: true
        },
        {
          label: '50% target',
          data: wkLabels.map(() => 50),
          borderColor: '#30363d',
          borderDash: [4,4], borderWidth: 1, pointRadius: 0, fill: false
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 10 } },
             grid: { color: '#21262d' } },
        y: { min: 0, max: 100,
             ticks: { color: '#8b949e', font: { size: 10 },
                      callback: v => v + '%' },
             grid: { color: '#21262d' } }
      }
    }
  });

  // Positions table
  const positions = data.positions || [];
  const posBody = document.getElementById('positionsBody');
  if (!positions.length) {
    posBody.innerHTML = '<tr><td colspan="8" class="gray" style="text-align:center;padding:12px">No open positions</td></tr>';
  } else {
    posBody.innerHTML = positions.map(p => {
      const pnl = p.unrealized_pct || 0;
      const pnlCls = pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'gray';
      const tiers = p.tiers_triggered || 0;
      const tierBadge = tiers > 0
        ? `<span style="background:#1a2a3a;color:#58a6ff;padding:1px 5px;border-radius:3px;font-size:10px">T${tiers}</span>`
        : '—';
      return `<tr>
        <td style="font-weight:600">${p.ticker}</td>
        <td class="gray">${p.sector||'—'}</td>
        <td>${p.shares}</td>
        <td style="text-align:right">${fmt$(p.entry_price)}</td>
        <td style="text-align:right" class="${pnlCls}">${fmtPct(pnl)}</td>
        <td style="text-align:right" class="gray">${fmt$(p.stop_loss)}</td>
        <td style="text-align:center">${tierBadge}</td>
        <td style="text-align:right" class="gray">${p.hold_days||0}d</td>
      </tr>`;
    }).join('');
  }

  // Sector donut
  const positionsBySector = {};
  positions.forEach(p => {
    const s = p.sector || 'other';
    positionsBySector[s] = (positionsBySector[s] || 0) + (p.current_value || 0);
  });
  const cashVal = data.portfolio?.cash || 0;
  const sectorLabels = [...Object.keys(positionsBySector), 'Cash'];
  const sectorVals   = [...Object.values(positionsBySector), cashVal];
  const sectorColors = SECTOR_COLORS.slice(0, sectorLabels.length - 1).concat(['#21262d']);

  if (sectorChartInst) sectorChartInst.destroy();
  sectorChartInst = new Chart(document.getElementById('sectorChart'), {
    type: 'doughnut',
    data: {
      labels: sectorLabels,
      datasets: [{ data: sectorVals, backgroundColor: sectorColors, borderWidth: 0 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '65%',
      plugins: { legend: { display: false } }
    }
  });
  document.getElementById('sectorLegend').innerHTML = sectorLabels.map((s, i) =>
    `<span style="display:flex;align-items:center;gap:6px">
      <span style="width:8px;height:8px;border-radius:2px;background:${sectorColors[i]};flex-shrink:0"></span>
      <span style="color:#8b949e">${s}</span>
      <span style="color:#e6edf3;margin-left:auto">${fmtK(sectorVals[i])}</span>
    </span>`
  ).join('');

  // Today\'s predictions
  const now = new Date();
  const todayPreds = preds.filter(p => {
    const d = new Date(p.created_at);
    return (now - d) / 36e5 <= 24;
  });
  const todayEl = document.getElementById('todayPreds');
  if (!todayPreds.length) {
    todayEl.innerHTML = '<div class="gray" style="font-size:12px">No predictions in last 24h</div>';
  } else {
    todayEl.innerHTML = todayPreds.map(p => {
      const status = p.was_correct === 1 ? '<span class="correct">✓</span>'
                   : p.was_correct === 0 ? '<span class="wrong">✗</span>'
                   : p.expires_in > 0 ? `<span class="gray">⏱${p.expires_in}h</span>`
                   : '<span class="gray">⏳</span>';
      const sector = p.query.replace(/—.*/,'').replace('sector outlook','').trim();
      return `<div class="pred-card">
        <div class="pred-sector">${sector}</div>
        <div class="pred-row">
          ${dirBadge(p.direction)}
          <span class="gray">${Math.round(p.confidence*100)}% conf</span>
          ${status}
        </div>
      </div>`;
    }).join('');
  }

  // Prediction history
  document.getElementById('predsHistorySummary').textContent =
    `Show history (${preds.length} total)`;
  document.getElementById('predsHistoryBody').innerHTML =
    [...preds].reverse().map(p =>
      `<tr>
        <td class="gray">${p.id}</td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.query}</td>
        <td>${dirBadge(p.direction)}</td>
        <td style="text-align:right">${Math.round(p.confidence*100)}%</td>
        <td class="${p.was_correct===1?'correct':p.was_correct===0?'wrong':'pending'}">
          ${p.was_correct===1?'✓ Correct':p.was_correct===0?'✗ Wrong':p.expires_in>0?'⏱ '+p.expires_in+'h':'⏳'}
        </td>
        <td class="timestamp">${p.created_at}</td>
      </tr>`
    ).join('');

  // Activity feed from closed positions
  const closed = data.closed_positions || [];
  const actEl = document.getElementById('activityFeed');
  if (!closed.length) {
    actEl.innerHTML = '<div class="gray" style="font-size:12px">No recent activity</div>';
  } else {
    actEl.innerHTML = closed.slice(0,8).map(c => {
      const isWin = c.pnl > 0;
      const reasonShort = c.reason?.includes('profit') ? 'PROFIT'
                        : c.reason?.includes('stop')   ? 'STOP'
                        : c.reason?.includes('time')   ? 'TIME'
                        : 'EXIT';
      return `<div class="activity-item">
        <span class="${isWin?'green':'red'}" style="font-weight:600">${reasonShort} ${c.ticker}</span>
        <span class="${isWin?'green':'red'}">${isWin?'+':''}${fmt$(c.pnl)} (${fmtPct(c.pnl_pct)})</span>
        <span class="gray">${c.date}</span>
      </div>`;
    }).join('');
  }

  // Sector bars
  const sectors = data.top_sectors || [];
  const maxSector = Math.max(...sectors.map(s => s.count), 1);
  const breakers = data.sector_breakers || {};
  document.getElementById('sectorBars').innerHTML = sectors.map(s => {
    const w = Math.round(s.count / maxSector * 100);
    const col = s.bias === 'bullish' ? '#3fb950' : s.bias === 'bearish' ? '#f85149' : '#8b949e';
    const breaker = breakers[s.sector] || {};
    const paused  = breaker.paused;
    const stops   = breaker.stops || 0;
    const sectorLabel = s.sector
      .replace('technology', 'tech')
      .replace('healthcare', 'health')
      .replace('financials', 'finance')
      .replace('industrials', 'industrial')
      .replace('materials', 'materials')
      .replace('market_overview', 'macro');
    const statusDot = paused
      ? `<span title="${stops} stop losses this week — entries paused"
           style="display:inline-flex;align-items:center;gap:3px;
                  background:#3a1a1a;color:#f85149;font-size:10px;
                  padding:1px 5px;border-radius:3px;margin-left:4px;">
           🔴 paused</span>`
      : `<span title="${stops} stop losses this week — entries open"
           style="display:inline-flex;align-items:center;gap:3px;
                  color:#3fb950;font-size:10px;margin-left:4px;">●</span>`;
    return `<div class="sector-bar">
      <span class="sector-name" style="width:110px">${sectorLabel}${statusDot}</span>
      <div class="bar-bg"><div class="bar-fill" style="width:${w}%;background:${col}"></div></div>
      <span class="sector-count">${s.count}</span>
    </div>`;
  }).join('');

  // Ticker bars
  const tickers = data.top_tickers || [];
  const maxTicker = Math.max(...tickers.map(t => t.count), 1);
  document.getElementById('tickerBars').innerHTML = tickers.map(t => {
    const w = Math.round(t.count / maxTicker * 100);
    const col = t.bias === 'bullish' ? '#3fb950' : t.bias === 'bearish' ? '#f85149' : '#8b949e';
    return `<div class="sector-bar">
      <span class="sector-name" style="font-weight:600">${t.ticker}</span>
      <div class="bar-bg"><div class="bar-fill" style="width:${w}%;background:${col}"></div></div>
      <span class="sector-count">${t.count}</span>
    </div>`;
  }).join('');

  // Sentiment donut
  const sent = data.sentiment || {};
  if (sentChartInst) sentChartInst.destroy();
  sentChartInst = new Chart(document.getElementById('sentChart'), {
    type: 'doughnut',
    data: {
      labels: ['Bullish', 'Bearish', 'Neutral'],
      datasets: [{
        data: [sent.bullish||0, sent.bearish||0, sent.neutral||0],
        backgroundColor: ['#3fb950', '#f85149', '#30363d'],
        borderWidth: 0
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '60%',
      plugins: {
        legend: {
          display: true, position: 'right',
          labels: { color: '#8b949e', font: { size: 11 }, boxWidth: 10, padding: 8 }
        }
      }
    }
  });

  // Closed positions table
  document.getElementById('closedBody').innerHTML = closed.length
    ? closed.map(c => `<tr>
        <td style="font-weight:600">${c.ticker}</td>
        <td style="text-align:right">${fmt$(c.entry)}</td>
        <td style="text-align:right">${fmt$(c.exit)}</td>
        <td style="text-align:right" class="${c.pnl>=0?'green':'red'}">${c.pnl>=0?'+':''}${fmt$(c.pnl)}</td>
        <td class="gray" style="font-size:11px">${c.reason?.split(' ')[0]||'—'}</td>
        <td class="timestamp">${c.date}</td>
      </tr>`).join('')
    : '<tr><td colspan="6" class="gray" style="text-align:center;padding:12px">No closed positions</td></tr>';
}

// Drag and drop
let dragSrc = null;
function initDrag() {
  document.querySelectorAll('.card').forEach(card => {
    card.addEventListener('dragstart', e => {
      dragSrc = card;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.id);
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      document.querySelectorAll('.card').forEach(c => c.classList.remove('drag-over'));
      saveLayout();
    });
    card.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (dragSrc && dragSrc !== card) card.classList.add('drag-over');
    });
    card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
    card.addEventListener('drop', e => {
      e.preventDefault();
      card.classList.remove('drag-over');
      if (dragSrc && dragSrc !== card) {
        const parent = card.parentNode;
        const srcParent = dragSrc.parentNode;
        if (parent === srcParent) {
          const cards = [...parent.children];
          const srcIdx = cards.indexOf(dragSrc);
          const dstIdx = cards.indexOf(card);
          if (srcIdx < dstIdx) parent.insertBefore(dragSrc, card.nextSibling);
          else parent.insertBefore(dragSrc, card);
        } else {
          const srcNext = dragSrc.nextSibling;
          parent.insertBefore(dragSrc, card);
          if (srcNext) srcParent.insertBefore(card, srcNext);
          else srcParent.appendChild(card);
        }
        saveLayout();
      }
    });
  });
}

function saveLayout() {
  const layout = {};
  document.querySelectorAll('[id^="row"]').forEach(row => {
    layout[row.id] = [...row.querySelectorAll('.card')].map(c => c.id);
  });
  localStorage.setItem('tradingDashLayout', JSON.stringify(layout));
}

function restoreLayout() {
  try {
    const saved = localStorage.getItem('tradingDashLayout');
    if (!saved) return;
    const layout = JSON.parse(saved);
    Object.entries(layout).forEach(([rowId, cardIds]) => {
      const row = document.getElementById(rowId);
      if (!row) return;
      cardIds.forEach(cardId => {
        const card = document.getElementById(cardId);
        if (card) row.appendChild(card);
      });
    });
  } catch(e) {}
}

restoreLayout();
initDrag();
loadData();
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

    # Portfolio data
    try:
        port_conn    = init_portfolio_db()
        cash         = get_cash_balance(port_conn)
        positions    = get_open_positions(port_conn)
        port_value   = get_portfolio_value(port_conn)
        starting     = PORT_CONFIG["starting_capital"]
        ret_pct      = (port_value - starting) / starting * 100

        # Recent recommendations
        recs = port_conn.execute("""
            SELECT ticker, action, sector, signal_count, avg_confidence,
                   suggested_shares, suggested_value, rationale, generated_at, status
            FROM recommendations
            ORDER BY generated_at DESC LIMIT 10
        """).fetchall()

        # Closed positions for P&L history
        closed = port_conn.execute("""
            SELECT ticker, entry_price, exit_price, pnl, pnl_pct,
                   exit_reason, exit_date
            FROM positions WHERE status = 'closed'
            ORDER BY exit_date DESC LIMIT 10
        """).fetchall()

        port_conn.close()

        data['portfolio'] = {
            'cash':          round(cash, 2),
            'positions_value': round(port_value - cash, 2),
            'total_value':   round(port_value, 2),
            'starting':      starting,
            'return_pct':    round(ret_pct, 2),
            'open_count':    len(positions),
        }

        data['positions'] = [
            {
                'ticker':          p['ticker'],
                'sector':          p.get('sector', ''),
                'shares':          p['shares'],
                'entry_price':     p['entry_price'],
                'current_price':   p['current_price'],
                'cost_basis':      p['cost_basis'],
                'current_value':   p['current_value'],
                'unrealized_pnl':  p['unrealized_pnl'],
                'unrealized_pct':  p['unrealized_pct'],
                'stop_loss':       p['stop_loss'],
                'take_profit':     p['take_profit'],
                'hold_days':       p['hold_days'],
            }
            for p in positions
        ]

        data['recommendations'] = [
            {
                'ticker':     r[0],
                'action':     r[1],
                'sector':     r[2],
                'signals':    r[3],
                'confidence': round(r[4] * 100) if r[4] else 0,
                'shares':     r[5],
                'value':      round(r[6], 2) if r[6] else 0,
                'rationale':  r[7][:80] if r[7] else '',
                'generated':  r[8][:16].replace('T', ' ') if r[8] else '',
                'status':     r[9],
            }
            for r in recs
        ]

        data['closed_positions'] = [
            {
                'ticker':     c[0],
                'entry':      c[1],
                'exit':       c[2],
                'pnl':        round(c[3], 2) if c[3] else 0,
                'pnl_pct':    round(c[4], 2) if c[4] else 0,
                'reason':     c[5],
                'date':       c[6][:10] if c[6] else '',
            }
            for c in closed
        ]

    except Exception as e:
        data['portfolio'] = {}
        data['positions'] = []
        data['recommendations'] = []
        data['closed_positions'] = []

    # Sector circuit breaker status
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent))
        from portfolio.manager import get_sector_stop_count, is_sector_breaker
        from portfolio.db import init_db as _init_port
        _pconn = _init_port()
        _sectors = ['technology','healthcare','energy','defense',
                    'financials','consumer','materials','industrials','macro']
        data['sector_breakers'] = {
            s: {
                'stops':   get_sector_stop_count(_pconn, s, days=7),
                'paused':  is_sector_breaker(s, _pconn),
            }
            for s in _sectors
        }
        _pconn.close()
    except Exception as e:
        data['sector_breakers'] = {}

    # Portfolio snapshots for P&L chart
    try:
        port_conn = init_portfolio_db()
        snaps = port_conn.execute("""
            SELECT snapshot_at, total_value
            FROM portfolio_snapshots
            ORDER BY snapshot_at ASC
        """).fetchall()
        port_conn.close()
        daily = {}
        for snap_at, total in snaps:
            day = snap_at[:10]
            daily[day] = round(total, 2)
        data['snapshots'] = [
            {'date': d, 'total_value': v}
            for d, v in sorted(daily.items())
        ]
    except Exception as e:
        data['snapshots'] = []

    # VIX volatility gate
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent))
        from portfolio.vix_gate import get_vix_gate
        data['vix'] = get_vix_gate()
    except Exception as e:
        data['vix'] = {'vix': None, 'action': 'ok', 'size_multiplier': 1.0,
                       'reason': 'VIX unavailable'}

    # Shadow BIL cash yield
    try:
        from portfolio.cash_yield import get_current_shadow_position
        from portfolio.db import init_db as _init_port2
        _pc2 = _init_port2()
        data['cash_yield'] = get_current_shadow_position(_pc2)
        _pc2.close()
    except Exception as e:
        data['cash_yield'] = {}

    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)