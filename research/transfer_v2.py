#!/usr/bin/env python3
"""Cross-market transferability v2 — real sector ETF / named-basket backtest.
Identical scoring to /tmp/control_group.py (v1): first-overlapping-session bar,
close-vs-prior-close, USD terms via FX, ±0.2% neutral tier. Only the DATA is upgraded.
All series name-verified via Yahoo chart meta (shortName) at probe stage — names in VERIFY.
Read-only: no DB writes, no pipeline code touched.
"""
import json, os, time, sqlite3, urllib.request, urllib.parse, datetime as dt
from collections import defaultdict

DB = "/home/trading/trading-ai/data/paper_trading.db"
OUT = "/home/trading/trading-ai/reports/research"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research/1.0"}
TIER = 0.2

# ---- name-verified series (symbol -> verified shortName from chart meta) ----
VERIFY = {
    "XEG.TO": "ISHARES SP TSX CAPPED ENERGY", "ZEB.TO": "BMO EQUAL WEIGHT BANKS INDEX",
    "ZCN.TO": "BMO SP TSX CAPPED COMP", "RY.TO": "ROYAL BANK OF CANADA",
    "SHOP.TO": "SHOPIFY", "BCE.TO": "BCE INC", "L.TO": "LOBLAW",
    "HBM.TO": "HUDBAY", "WPM.TO": "WHEATON PRECIOUS METALS", "CNR.TO": "CANADIAN NATIONAL RAILWAY",
    "CAD=X": "USD/CAD",
    "DBK.DE": "DEUTSCHE BANK", "GLE.PA": "SOCIETE GENERALE", "BNP.PA": "BNP PARIBAS",
    "MC.PA": "LVMH", "KER.PA": "KERING", "SAP.DE": "SAP SE", "IFX.DE": "INFINEON TECHNOLOGIES",
    "ASML.AS": "ASML", "TTE.PA": "TOTALENERGIES", "RWE.DE": "RWE", "ENI.MI": "ENI",
    "RIO.L": "RIO TINTO", "BHP.L": "BHP", "VOW3.DE": "VOLKSWAGEN", "MBG.DE": "MERCEDES-BENZ",
    "AIR.PA": "AIRBUS", "STOXX50E": "EURO STOXX 50", "EUR=X": "USD/EUR",
    "8306.T": "MITSUBISHI UFJ FINANCIAL", "8308.T": "RESONA",
    "4568.T": "DAIICHI SANKYO", "4523.T": "EISAI", "4519.T": "CHUGAI PHARMACEUTICAL", "4578.T": "OTSUKA",
    "9983.T": "FAST RETAILING", "8267.T": "AEON", "7203.T": "TOYOTA MOTOR",
    "6501.T": "HITACHI", "6861.T": "KEYENCE", "8035.T": "TOKYO ELECTRON", "6857.T": "ADVANTEST", "6758.T": "SONY",
    "5020.T": "ENEOS", "5019.T": "IDEMITSU KOSAN", "1605.T": "INPEX",
    "5401.T": "NIPPON STEEL", "5713.T": "SUMITOMO METAL MINING",
    "6503.T": "MITSUBISHI ELECTRIC", "7011.T": "MITSUBISHI HEAVY",
    "N225": "NIKKEI 225", "JPY=X": "USD/JPY",
    "0005.HK": "HSBC", "0388.HK": "HKEX", "1398.HK": "ICBC", "0998.HK": "CITIC BANK",
    "1093.HK": "CSPC", "1099.HK": "SINOPHARM", "1177.HK": "SBP GROUP",
    "1929.HK": "CHOW TAI FOOK", "0345.HK": "VITASOY", "1880.HK": "DUTY-FREE", "2020.HK": "ANTA",
    "0700.HK": "TENCENT", "9988.HK": "BABA-W", "3690.HK": "MEITUAN", "9618.HK": "JD",
    "0857.HK": "PETROCHINA", "1088.HK": "CHINA SHENHUA",
    "0358.HK": "JIANGXI COPPER", "2600.HK": "CHALCO",
    "0658.HK": "C TRANSMISSION", "0390.HK": "CHINA RAILWAY", "3311.HK": "CHINA STATE CON",
    "HSI": "HANG SENG INDEX", "HKD=X": "USD/HKD",
    "XLF": "FINANCIAL SELECT", "XLV": "HEALTH CARE SELECT", "XLY": "CONSUMER DISCRETIO",
    "SOXX": "PHLX SOX SEMICONDUCTOR", "XLE": "ENERGY SELECT", "XLB": "MATERIALS SELECT",
    "XLI": "INDUSTRIAL SELECT", "SPY": "S&P 500",
}

# market -> (fx_symbol, sector -> [symbols] )  (empty list = sector skipped for market)
M = {
 "CA":   ("CAD=X", {"financials": ["ZEB.TO"], "healthcare": [], "consumer": ["L.TO"],
        "technology": ["SHOP.TO","BCE.TO"], "energy": ["XEG.TO"],
        "materials": ["HBM.TO","WPM.TO"], "industrials": ["CNR.TO"], "market": ["ZCN.TO"]}),
 "EMEA": ("EUR=X", {"financials": ["DBK.DE","GLE.PA","BNP.PA"], "healthcare": [],
        "consumer": ["MC.PA","KER.PA"], "technology": ["SAP.DE","IFX.DE","ASML.AS"],
        "energy": ["TTE.PA","RWE.DE","ENI.MI"], "materials": ["RIO.L","BHP.L"],
        "industrials": ["VOW3.DE","MBG.DE","AIR.PA"], "market": ["^STOXX50E"]}),
 "JP":   ("JPY=X", {"financials": ["8306.T","8308.T"], "healthcare": ["4568.T","4523.T","4519.T","4578.T"],
        "consumer": ["9983.T","8267.T","7203.T"], "technology": ["6501.T","6861.T","8035.T","6857.T","6758.T"],
        "energy": ["5020.T","5019.T","1605.T"], "materials": ["5401.T","5713.T"],
        "industrials": ["6503.T","7011.T"], "market": ["^N225"]}),
 "HK":   ("HKD=X", {"financials": ["0005.HK","0388.HK","1398.HK","0998.HK"],
        "healthcare": ["1093.HK","1099.HK","1177.HK"],
        "consumer": ["1929.HK","0345.HK","1880.HK","2020.HK"],
        "technology": ["0700.HK","9988.HK","3690.HK","9618.HK"],
        "energy": ["0857.HK","1088.HK"], "materials": ["0358.HK","2600.HK"],
        "industrials": ["0658.HK","0390.HK","3311.HK"], "market": ["^HSI"]}),
}
US_ETF = {"financials":"XLF","healthcare":"XLV","consumer":"XLY","technology":"SOXX",
          "energy":"XLE","materials":"XLB","industrials":"XLI","market":"SPY"}

def ysym(s):  # normalize index symbols for URL quoting (^ handled by quote)
    return s

def fetch(symbol, rng="6mo"):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
           + f"?range={rng}&interval=1d&events=div%2Csplit")
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
            meta = r.get("meta", {})
            ts, q = r["timestamp"], r["indicators"]["quote"][0]
            bars = [(t, c) for t, c in zip(ts, q["close"]) if c is not None]
            if bars:
                return bars, meta.get("shortName"), meta.get("currency")
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None, None, None

def p_utc(s):
    return dt.datetime.strptime(s.replace("T"," ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()

# ---- fetch all series, re-verify names at fetch time ----
print("=== fetch + re-verify names (6mo daily) ===")
data = {}   # sym -> bars
fmeta = {}  # sym -> (shortName, cur)
all_syms = set()
for mkt,(fx,secs) in M.items():
    all_syms.add(fx)
    for sec, syms in secs.items():
        all_syms.update(syms)
    all_syms.add("STOXX50E" if "^STOXX50E" in all_syms else "^STOXX50E")
for s in list(US_ETF.values()): all_syms.add(s)
all_syms = {("^STOXX50E" if s=="STOXX50E" else "^N225" if s=="N225" else "^HSI" if s=="HSI" else s) for s in all_syms}

CACHE = f"{OUT}/cache_series.json"
cache = {}
if os.path.exists(CACHE):
    cache = json.load(open(CACHE))
    print(f"  loaded cache: {len(cache)} symbols")

fetches = 0
for s in sorted(all_syms):
    if s in cache and cache[s].get("bars"):
        data[s] = [list(b) for b in cache[s]["bars"]]
        fmeta[s] = (cache[s]["shortName"], cache[s].get("cur"))
    else:
        bars, sn, cur = fetch(s)
        data[s] = bars
        fmeta[s] = (sn, cur)
        cache[s] = {"bars": bars, "shortName": sn, "cur": cur}
        fetches += 1
        time.sleep(0.35)
json.dump(cache, open(CACHE, "w"))
print(f"  {len(all_syms)-fetches} from cache, {fetches} fetched fresh\n")
for s in sorted(all_syms):
    bars = data[s] or []
    sn, _ = fmeta[s]
    key = s.lstrip("^")
    expected = VERIFY.get(s) or VERIFY.get(key)
    ok = ""
    if not bars: ok = "  !! NO BARS"
    elif sn is None: ok = "  !! NO META"
    elif expected and (expected.lower() not in (sn or "").lower()):
        ok = f"  !! NAME MISMATCH expected~'{expected}'"
    print(f"  {s:10s} {len(bars):4d} bars  {sn}{ok}", flush=True)

# hard gate: any series with name mismatch or no bars -> abort (never use unverified)
bad = [s for s in all_syms if not data[s]]
for s in all_syms:
    sn, _ = fmeta[s]
    key = s.lstrip("^")
    expected = VERIFY.get(s) or VERIFY.get(key)
    if sn and expected and expected.lower() not in sn.lower():
        bad.append(s)
if bad:
    print("ABORT: unverified series:", bad); raise SystemExit(1)
print("  all series name-verified OK\n")

json.dump({s: [list(b) for b in data[s]] for s in data if data[s]},
          open(f"{OUT}/series_2026-09-25.json","w"))

# ---- signals (identical to v1) ----
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
    st_t = p_utc(created)
    signals.append({"start": st_t, "end": p_utc(verified), "sec": sec,
                    "dir": direction, "wc": wc,
                    "mo": dt.datetime.fromtimestamp(st_t, dt.timezone.utc).strftime("%Y-%m")})
NC, NW = sum(s["wc"] for s in signals), sum(not s["wc"] for s in signals)
print(f"=== {len(signals)} signals (87d: {NC} correct / {NW} wrong) ===")

def fx_at(mkt, start, end):
    fbars = data[M[mkt][0]]
    fs = [f for f in fbars if f[0] <= start + 86400]
    fe = [f for f in fbars if f[0] >= end]
    return (fs[-1][1] / fe[0][1] - 1) * 100 if fs and fe else 0.0

def basket_move(mkt, syms, start, end):
    """first session bar after signal per member; mean of member session returns (local ccy)."""
    moves = []
    for s in syms:
        bars = data[s]
        cand = [b for b in bars if start < b[0] <= end + 14*3600]
        if not cand: return None
        idx = bars.index(cand[0])
        if idx == 0: return None
        moves.append((cand[0][1] / bars[idx-1][1] - 1) * 100)
    return sum(moves) / len(moves)

def us_move(sec, start, end):
    bars = data[US_ETF[sec]]
    a = [b for b in bars if b[0] <= start]
    b = [b for b in bars if b[0] >= end - 86400]
    return (b[-1][1] / a[-1][1] - 1) * 100 if a and b else None

def hit_of(d, mfx):
    return (d=="bullish" and mfx > TIER) or (d=="bearish" and mfx < -TIER) or (d=="neutral" and abs(mfx) <= TIER)

# ---- US baseline ----
print("=== US BASELINE (sanity, target gap +29 ±2) ===")
us_hit = {"correct":[0,0], "wrong":[0,0], "all":[0,0]}
us_moves = defaultdict(list)
for s in signals:
    us = us_move(s["sec"], s["start"], s["end"])
    if us is None: continue
    for k in (("correct" if s["wc"] else "wrong"), "all"):
        us_hit[k][0] += 1; us_hit[k][1] += 1 if hit_of(s["dir"], us) else 0
    us_moves["all"].append(us)
for k in ("correct","wrong","all"):
    n,h = us_hit[k]
    print(f"  US {k:8s}: {h}/{n} = {h/n:.1%}")
us_gap = us_hit["correct"][1]/us_hit["correct"][0] - us_hit["wrong"][1]/us_hit["wrong"][0]
print(f"  US GAP: {us_gap:+.1%}  (v1 reference: +29)")

# ---- foreign markets ----
results = {}
print("\n=== FOREIGN MARKETS (v2 data, USD terms, ±0.2% tier) ===")
hdr = f"{'mkt':6s} {'n':>4s} {'corr%':>7s} {'wrong%':>7s} {'gap':>6s} {'all%':>6s} {'avgMove':>8s}"
print(hdr)
for mkt,(fx,secs) in M.items():
    st = {"correct":[0,0], "wrong":[0,0], "all":[0,0],
          "bydir":defaultdict(lambda:[0,0]), "bysec":defaultdict(lambda:{"c":[0,0],"w":[0,0],"n":0,"mv":[]}),
          "moves":[], "mon":defaultdict(lambda:{"c":[0,0],"w":[0,0]})}
    for s in signals:
        syms = secs.get(s["sec"], [])
        if not syms: continue
        mv = basket_move(mkt, syms, s["start"], s["end"])
        if mv is None: continue
        mfx = (1 + mv/100) * (1 + fx_at(mkt, s["start"], s["end"])/100) * 100 - 100
        h = hit_of(s["dir"], mfx)
        k = "correct" if s["wc"] else "wrong"
        st[k][0] += 1; st[k][1] += 1 if h else 0
        st["all"][0] += 1; st["all"][1] += 1 if h else 0
        st["moves"].append(mfx)
        b = st["bydir"][s["dir"]]; b[0]+=1; b[1]+=1 if h else 0
        sb = st["bysec"][s["sec"]]; sb["n"]+=1; sb["mv"].append(mfx)
        sb["c" if s["wc"] else "w"][0]+=1; sb["c" if s["wc"] else "w"][1]+=1 if h else 0
        mo = st["mon"][s["mo"]]; mo["c" if s["wc"] else "w"][0]+=1; mo["c" if s["wc"] else "w"][1]+=1 if h else 0
    cn,ch = st["correct"]; wn,wh = st["wrong"]; an,ah = st["all"]
    gap = (ch/cn - wh/wn) if cn and wn else 0
    results[mkt] = {**st, "n":an, "gap":gap}
    print(f"{mkt:6s} {an:4d} {ch/cn:7.1%} {wh/wn:7.1%} {gap:+6.1%} {ah/an:6.1%} {sum(st['moves'])/len(st['moves']):+8.2f}%")
    for d,b in sorted(st["bydir"].items()):
        print(f"        dir {d:8s} {b[1]}/{b[0]} = {b[1]/b[0]:.1%}")
    for sec in sorted(st["bysec"], key=lambda x:-st["bysec"][x]["n"]):
        sb = st["bysec"][sec]
        sg = (sb["c"][1]/sb["c"][0] - sb["w"][1]/sb["w"][0]) if sb["c"][0] and sb["w"][0] else float("nan")
        print(f"        sec {sec:11s} n={sb['n']:3d} corr {sb['c'][1]}/{sb['c'][0]}  wrong {sb['w'][1]}/{sb['w'][0]}  gap {sg:+6.1%}  avg {sum(sb['mv'])/len(sb['mv']):+.2f}%")
    for mo in sorted(st["mon"]):
        mc,mw = st["mon"][mo]["c"], st["mon"][mo]["w"]
        if mc[0] and mw[0]:
            print(f"        {mo}: corr {mc[1]}/{mc[0]} ({mc[1]/mc[0]:.0%})  wrong {mw[1]}/{mw[0]} ({mw[1]/mw[0]:.0%})  gap {mc[1]/mc[0]-mw[1]/mw[0]:+.0%}")

json.dump({m: {"n": r["n"], "gap": r["gap"],
               "correct": r["correct"], "wrong": r["wrong"], "all": r["all"],
               "bydir": dict(r["bydir"]),
               "bysec": {k: {"n":v["n"],"c":v["c"],"w":v["w"],"avg":sum(v["mv"])/len(v["mv"])} for k,v in r["bysec"].items()},
               "mon": {k: v for k,v in r["mon"].items()},
               "avg_move": sum(r["moves"])/len(r["moves"])} for m,r in results.items()},
          open(f"{OUT}/results_2026-09-25.json","w"), indent=1)
print(f"\nSaved {OUT}/results_2026-09-25.json")
