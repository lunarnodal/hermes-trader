#!/usr/bin/env python3
"""Probe candidate sector index/ETF symbols on Yahoo.
Fetch chart meta (shortName, exchange, currency) + bar count for each candidate.
Prints a clean table so we can name-verify before ANY use in the backtest.
"""
import json, time, urllib.request, urllib.parse

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
            first = bars[0][0] if bars else None
            last = bars[-1][0] if bars else None
            import datetime as dt
            f = dt.datetime.fromtimestamp(first, dt.timezone.utc).strftime("%Y-%m-%d") if first else "-"
            l = dt.datetime.fromtimestamp(last, dt.timezone.utc).strftime("%Y-%m-%d") if last else "-"
            return {
                "shortName": meta.get("shortName"),
                "longName": meta.get("longName"),
                "exch": meta.get("exchange"),
                "fullExch": meta.get("fullExchangeName"),
                "cur": meta.get("currency"),
                "bars": len(bars),
                "first": f,
                "last": l,
                "err": None,
            }
        except Exception as e:
            if a == 2:
                return {"shortName": None, "longName": None, "exch": None, "fullExch": None,
                        "cur": None, "bars": 0, "first": "-", "last": "-", "err": repr(e)[:80]}
            time.sleep(1.0 * (a + 1))
    return None

# market -> list of (label, symbol, note)
CANDIDATES = {
    "CA": [
        ("XEG  Energy (iShares)", "XEG.TO"),
        ("XMV  Materials (iShares)", "XMV.TO"),
        ("XIC  Industrials (iShares)", "XIC.TO"),
        ("XTR  Composite (iShares)", "XTR.TO"),
        ("XFB  Financials (iShares)", "XFB.TO"),
        ("XPH  Healthcare (iShares)", "XPH.TO"),
        ("XTT  Technology (iShares)", "XTT.TO"),
        ("XCY  Consumer (iShares)", "XCY.TO"),
        ("XEN  Energy alt", "XEN.TO"),
        ("XAU  GoldMiners", "XAU.TO"),
        ("ZFA  Financials (BMO)", "ZFA.TO"),
        ("ZEB  Banks (BMO)", "ZEB.TO"),
        ("ZEN  Energy (BMO)", "ZEN.TO"),
        ("ZHV  Healthcare (BMO)", "ZHV.TO"),
        ("ZTH  Technology (BMO)", "ZTH.TO"),
        ("ZIC  Industrials (BMO)", "ZIC.TO"),
        ("ZSM  SmallCap (BMO)", "ZSM.TO"),
        ("ZPH  Pharma (BMO)", "ZPH.TO"),
        ("ZMIN  Mining (BMO)", "ZMIN.TO"),
        ("VFH  Financials (Vanguard)", "VFH.TO"),
        ("VHT  Healthcare (Vanguard)", "VHT.TO"),
        ("VTE  Technology (Vanguard)", "VTE.TO"),
        ("VEC  Consumer (Vanguard)", "VEC.TO"),
        ("VEN  Energy (Vanguard)", "VEN.TO"),
        ("VMC  Materials (Vanguard)", "VMC.TO"),
        ("VIC  Industrials (Vanguard)", "VIC.TO"),
        ("FX  CAD=X", "CAD=X"),
    ],
    "EMEA": [
        ("SX1E Financials (Stoxx)", "^SX1E"),
        ("SX2E ConsDiscr (Stoxx)", "^SX2E"),
        ("SX3E ConsStaples (Stoxx)", "^SX3E"),
        ("SX4E Energy (Stoxx)", "^SX4E"),
        ("SX5E Industrials (Stoxx)", "^SX5E"),
        ("SX6E Materials (Stoxx)", "^SX6E"),
        ("SX7E Technology (Stoxx)", "^SX7E"),
        ("SX8E Utilities (Stoxx)", "^SX8E"),
        ("STOXX50E (market)", "^STOXX50E"),
        ("EUNF  EuroBank (Xetra)", "EUNF.DE"),
        ("EUCO  EuroStoxx50 (Xetra)", "EUCO.DE"),
        ("EUSK  EuroStoxx50 (Xetra)", "EUSK.DE"),
        ("EUDV  EuroDiv (Xetra)", "EUDV.DE"),
        ("EUSH  EuroStoxxHealth (Xetra)", "EUSH.DE"),
        ("EUTE  EuroTech (Xetra)", "EUTE.DE"),
        ("EUMN  EuroMiners (Xetra)", "EUMN.DE"),
        ("EURA  EuroAutos (Xetra)", "EURA.DE"),
        ("EUSG  EuroStoxx (Xetra)", "EUSG.DE"),
        ("FX  EUR=X", "EUR=X"),
    ],
    "JP": [
        ("N225  Nikkei (market)", "^N225"),
        ("TP50  TOPIX (market alt)", "^TP50"),
        ("1681.T iShares", "1681.T"),
        ("1657.T iShares", "1657.T"),
        ("1321.T iShares", "1321.T"),
        ("1306.T eMAXIS Slim TOPIX", "1306.T"),
        ("1307.T eMAXIS Slim World", "1307.T"),
        ("1529.T", "1529.T"),
        ("1658.T", "1658.T"),
        ("FX  JPY=X", "JPY=X"),
        ("8306.T (round1 fin)", "8306.T"),
        ("4568.T (round1 health)", "4568.T"),
        ("9983.T (round1 cons)", "9983.T"),
        ("6501.T (round1 tech)", "6501.T"),
        ("5020.T (round1 energy)", "5020.T"),
        ("5401.T (round1 mat)", "5401.T"),
        ("6503.T (round1 ind)", "6503.T"),
    ],
    "HK": [
        ("HSI  HangSeng (market)", "^HSI"),
        ("HSCEI  HangSeng ChinaEnt", "^HSCEI"),
        ("0005.HK (round1 fin)", "0005.HK"),
        ("1093.HK (round1 health)", "1093.HK"),
        ("1929.HK (round1 cons)", "1929.HK"),
        ("0700.HK (round1 tech)", "0700.HK"),
        ("0857.HK (round1 energy)", "0857.HK"),
        ("0386.HK (round1 mat)", "0386.HK"),
        ("2601.HK (round1 ind)", "2601.HK"),
        ("FX  HKD=X", "HKD=X"),
    ],
}

print("PROBING candidate symbols...\n")
for mkt, cands in CANDIDATES.items():
    print(f"================ {mkt} ================")
    print(f"{'label':34s} {'sym':10s} {'bars':>5s} {'first':10s} {'last':10s} {'exch':9s} {'cur':5s} shortName")
    print("-" * 110)
    for label, sym, *_ in cands:
        m = fetch_meta(sym)
        sn = (m["shortName"] or "?")[:38]
        print(f"{label:34s} {sym:10s} {m['bars']:5d} {m['first']:10s} {m['last']:10s} "
              f"{str(m['exch']):9s} {str(m['cur']):5s} {sn}")
        time.sleep(0.35)
    print()
print("DONE.")
