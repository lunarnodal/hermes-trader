#!/usr/bin/env python3
"""Control-group cross-market backtest (90d, ALL verified 24h signals).
Question: does the pipeline's discriminative power (correct-calls hit more
often than wrong-calls) survive in JP/HK/CA/EMEA, or does it flatten?
Same proxies as last night's final round; range=6mo.
"""
import json, time, sqlite3, urllib.request, urllib.parse, datetime as dt
from collections import defaultdict

DB = "/home/trading/trading-ai/data/paper_trading.db"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research/1.0"}
TIER = 0.2  # ±0.2% neutral band (pipeline 24h tier)

def fetch(symbol, rng="6mo"):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
           + f"?range={rng}&interval=1d&events=div%2Csplit")
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
            ts, q = r["timestamp"], r["indicators"]["quote"][0]
            bars = [(t, c) for t, c in zip(ts, q["close"]) if c is not None]
            if bars:
                return bars
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None

def p_utc(s):
    return dt.datetime.strptime(s.replace("T"," ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()

# verified proxies (last night's final round)
M = {
 "JP":  ("JPY", {"financials":"8306.T","healthcare":"4568.T","consumer":"9983.T","technology":"6501.T",
        "energy":"5020.T","materials":"5401.T","industrials":"6503.T","market":"^N225"}),
 "HK":  ("HKD", {"financials":"0005.HK","healthcare":"1093.HK","consumer":"1929.HK","technology":"0700.HK",
        "energy":"0857.HK","materials":"0386.HK","industrials":"2601.HK","market":"^HSI"}),
 "CA":  ("CAD", {"financials":"RY.TO","technology":"SHOP.TO","energy":"SU.TO",
        "industrials":"CNR.TO","market":"XTR.TO"}),
 "EMEA":("EUR", {"financials":"DBK.DE","consumer":"MC.PA","technology":"SAP.DE",
        "energy":"RWE.DE","materials":"RIO.L","industrials":"ROR.L","market":"^STOXX50E"}),
}
FX = {"JP":"JPY=X","HK":"HKD=X","CA":"CAD=X","EMEA":"EUR=X"}
US_ETF = {"financials":"XLF","healthcare":"XLV","consumer":"XLY","technology":"SOXX",
          "energy":"XLE","materials":"XLB","industrials":"XLI","market":"SPY"}

print("=== fetch (6mo) ===")
data = {}
for mkt, (cur, secs) in M.items():
    for sec, s in secs.items():
        data[(mkt, sec)] = fetch(s)
        n = len(data[(mkt, sec)]) if data[(mkt, sec)] else 0
        print(f"  {mkt:5s} {sec:11s} {s:10s} -> {n} bars", flush=True)
        time.sleep(0.4)
    data[(mkt, "FX")] = fetch(FX[mkt])
    time.sleep(0.4)
for s in dict.fromkeys(US_ETF.values()):
    data[("US", s)] = fetch(s)
    time.sleep(0.4)
print(f"  US done")

db = sqlite3.connect(DB)
rows = db.execute("""SELECT created_at, verified_at, query, direction, was_correct
    FROM predictions WHERE timeframe='24h' AND was_correct IS NOT NULL
    AND created_at > datetime('now','-90 days') ORDER BY created_at""").fetchall()
db.close()

signals = []
for created, verified, query, direction, wc in rows:
    q = query.lower()
    if "healthcare" in q or "biotech" in q: sec = "healthcare"
    elif "consumer" in q: sec = "consumer"
    elif "technology" in q or "ai sector" in q: sec = "technology"
    elif "financial" in q: sec = "financials"
    elif "energy" in q: sec = "energy"
    elif "materials" in q or "mining" in q: sec = "materials"
    elif "industrials" in q or "defense" in q: sec = "industrials"
    elif "overall market" in q: sec = "market"
    else: continue
    signals.append({"start": p_utc(created), "end": p_utc(verified), "sec": sec, "dir": direction, "wc": wc})
print(f"\n=== {len(signals)} signals (87d, {sum(1 for s in signals if s['wc'])} correct / {sum(1 for s in signals if not s['wc'])} wrong) ===")

def us_move(sec, start, end):
    bars = data.get(("US", US_ETF[sec])) or []
    a = [b for b in bars if b[0] <= start]
    b = [b for b in bars if b[0] >= end - 86400]
    return (b[-1][1] / a[-1][1] - 1) * 100 if a and b else None

print("\n=== US BASELINE (sanity: correct vs wrong calls) ===")
us_hit = {"correct":[0,0], "wrong":[0,0]}
us_moves = {"correct":[], "wrong":[]}
for s in signals:
    us = us_move(s["sec"], s["start"], s["end"])
    if us is None: continue
    k = "correct" if s["wc"] else "wrong"
    d = s["dir"]
    hit = (d=="bullish" and us > TIER) or (d=="bearish" and us < -TIER) or (d=="neutral" and abs(us) <= TIER)
    us_hit[k][0] += 1; us_hit[k][1] += 1 if hit else 0
    us_moves[k].append(us)

for k in ("correct","wrong"):
    n, h = us_hit[k]
    m = us_moves[k]
    print(f"  US {k:8s}: {h}/{n} hit ({h/n:.0%}), avg move {sum(m)/len(m):+.2f}%")

print("\n=== FOREIGN MARKETS: full control group (USD terms, ±0.2% tier) ===")
print(f"{'mkt':7s} {'n':>4s} {'CORR hit%':>10s} {'WRONG hit%':>11s} {'gap':>6s} | {'all hit%':>8s} | all-avg%")
for mkt, (cur, secs) in M.items():
    fbars = data.get((mkt, "FX")) or []
    stats = {"correct":[0,0], "wrong":[0,0], "all":[0,0], "moves":[], "bydir":defaultdict(lambda:[0,0])}
    for s in signals:
        bars = data.get((mkt, s["sec"]))
        if not bars: continue
        cand = [b for b in bars if s["start"] < b[0] <= s["end"] + 14*3600]
        if not cand: continue
        idx = bars.index(cand[0])
        if idx == 0: continue
        move = (cand[0][1] / bars[idx-1][1] - 1) * 100
        fs = [f for f in fbars if f[0] <= s["start"] + 86400]
        fe = [f for f in fbars if f[0] >= s["end"]]
        fx = (fs[-1][1] / fe[0][1] - 1) * 100 if fs and fe else 0.0
        mfx = (1 + move/100) * (1 + fx/100) * 100 - 100
        d = s["dir"]
        hit = (d=="bullish" and mfx > TIER) or (d=="bearish" and mfx < -TIER) or (d=="neutral" and abs(mfx) <= TIER)
        k = "correct" if s["wc"] else "wrong"
        stats[k][0] += 1; stats[k][1] += 1 if hit else 0
        stats["all"][0] += 1; stats["all"][1] += 1 if hit else 0
        stats["moves"].append(mfx)
        b = stats["bydir"][d]; b[0] += 1; b[1] += 1 if hit else 0
    cn, ch = stats["correct"]; wn, wh = stats["wrong"]
    an, ah = stats["all"]
    gap = (ch/cn - wh/wn) if cn and wn else 0
    print(f"{mkt:7s} {an:4d} {ch/cn:10.0%} {wh/wn:11.0%} {gap:+6.0%} | {ah/an:8.0%} | {sum(stats['moves'])/len(stats['moves']):+7.2f}%")
    dl = "   ".join(f"{d} {bydir[1]}/{bydir[0]}" for d, bydir in stats["bydir"].items())
    print(f"{'':7s} dir: {dl}")

print("\n=== monthly stability (all foreign markets combined, direction-correct vs wrong) ===")
mon = defaultdict(lambda: {"c":[0,0], "w":[0,0]})
for mkt, (cur, secs) in M.items():
    fbars = data.get((mkt, "FX")) or []
    for s in signals:
        bars = data.get((mkt, s["sec"]))
        if not bars: continue
        cand = [b for b in bars if s["start"] < b[0] <= s["end"] + 14*3600]
        if not cand: continue
        idx = bars.index(cand[0])
        if idx == 0: continue
        move = (cand[0][1] / bars[idx-1][1] - 1) * 100
        fs = [f for f in fbars if f[0] <= s["start"] + 86400]
        fe = [f for f in fbars if f[0] >= s["end"]]
        fx = (fs[-1][1] / fe[0][1] - 1) * 100 if fs and fe else 0.0
        mfx = (1 + move/100) * (1 + fx/100) * 100 - 100
        d = s["dir"]
        hit = (d=="bullish" and mfx > TIER) or (d=="bearish" and mfx < -TIER) or (d=="neutral" and abs(mfx) <= TIER)
        k = "c" if s["wc"] else "w"
        mo = dt.datetime.fromtimestamp(s["start"], dt.timezone.utc).strftime("%Y-%m")
        mon[mo][k][0] += 1; mon[mo][k][1] += 1 if hit else 0
for mo in sorted(mon):
    c, w = mon[mo]["c"], mon[mo]["w"]
    if c[0] and w[0]:
        print(f"  {mo}: correct {c[1]}/{c[0]} ({c[1]/c[0]:.0%})  wrong {w[1]}/{w[0]} ({w[1]/w[0]:.0%})  gap {c[1]/c[0]-w[1]/w[0]:+.0%}")
