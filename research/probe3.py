#!/usr/bin/env python3
"""Probe round 3: gap-fillers for thin baskets. shortName is ground truth."""
import json, time, urllib.request, urllib.parse, datetime as dt

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) research/1.0"}

def fetch_meta(symbol, rng="6mo"):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol)
           + f"?range={rng}&interval=1d&events=div%2Csplit")
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
            meta = r.get("meta", {})
            ts = r.get("timestamp", [])
            close = r["indicators"]["quote"][0]["close"]
            bars = [(t, c) for t, c in zip(ts, close) if c is not None]
            return {"shortName": meta.get("shortName"), "longName": meta.get("longName"),
                    "cur": meta.get("currency"), "bars": len(bars),
                    "first": dt.datetime.fromtimestamp(bars[0][0], dt.timezone.utc).strftime("%Y-%m-%d") if bars else "-",
                    "last": dt.datetime.fromtimestamp(bars[-1][0], dt.timezone.utc).strftime("%Y-%m-%d") if bars else "-",
                    "err": None}
        except Exception as e:
            if a == 2: return {"shortName": None, "longName": None, "cur": None, "bars": 0, "first": "-", "last": "-", "err": repr(e)[:80]}
            time.sleep(1.0 * (a + 1))
    return None

SYMS = {
    # CA gaps
    "L.TO": "CA Loblaws (cons)", "CN.TO": "CA Canadian Tire (cons)", "BCE.TO": "CA BCE Bell (tech)",
    "TECK.A": "CA Teck (mat)", "FMTO.TO": "CA First Quantum (mat)", "APX.TO": "CA Apotex (health)",
    "WDC.TO": "CA ?WDC", "SUN.TO": "CA ?SUN", "CNR.TO": "CA CN Rail (ind)",
    # EMEA gaps
    "GENC.L": "EMEA Glencore (mat)", "SAN.MI": "EMEA Santander (fin)", "BNP.PA": "EMEA BNP (fin)",
    "UBI.MI": "EMEA UniCredit (fin)", "KER.PA": "EMEA Kering (cons)", "AI.PA": "EMEA AirLiquide (cons/mat)",
    "DST.PA": "EMEA Danone (cons)", "TTE.PA": "EMEA TotalEnergies (energy)", "AIQ.DE": "EMEA AirLiquide DE",
    # JP gaps
    "8035.T": "JP TokyoElectron (tech)", "8312.T": "JP SMFG (fin)", "7203.T": "JP Toyota (cons)",
    # HK gaps
    "0358.HK": "HK JiangxiCopper (mat)", "2600.HK": "HK Chalco (mat)", "0390.HK": "HK ChinaRailway (ind)",
    "1088.HK": "HK Shenhua (energy/mat)", "0836.HK": "HK SinoPEC (energy)",
}
print("PROBE ROUND 3 (gap-fillers)\n")
out = {}
print(f"{'sym':12s} {'bars':>5s} {'first':10s} {'last':10s} {'cur':5s} {'note':26s} shortName / longName")
print("-" * 130)
for sym, note in SYMS.items():
    m = fetch_meta(sym); out[sym] = m
    sn = m["shortName"] or "?"; ln = m["longName"] or ""
    disp = f"{sn} / {ln}" if ln and ln != sn else sn
    print(f"{sym:12s} {m['bars']:5d} {m['first']:10s} {m['last']:10s} {str(m['cur']):5s} {note:26s} {disp[:72]}")
    time.sleep(0.3)
json.dump(out, open("/home/trading/trading-ai/research/probe3.json", "w"), indent=1)
print("\nSaved probe3.json")
