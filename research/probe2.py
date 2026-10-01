#!/usr/bin/env python3
"""Probe round 2: verify round-1 names + all v2 basket candidates. Saves JSON."""
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
            first = bars[0][0] if bars else None
            last = bars[-1][0] if bars else None
            f = dt.datetime.fromtimestamp(first, dt.timezone.utc).strftime("%Y-%m-%d") if first else "-"
            l = dt.datetime.fromtimestamp(last, dt.timezone.utc).strftime("%Y-%m-%d") if last else "-"
            return {"shortName": meta.get("shortName"), "longName": meta.get("longName"),
                    "exch": meta.get("exchange"), "fullExch": meta.get("fullExchangeName"),
                    "cur": meta.get("currency"), "bars": len(bars), "first": f, "last": l, "err": None}
        except Exception as e:
            if a == 2:
                return {"shortName": None, "longName": None, "exch": None, "fullExch": None,
                        "cur": None, "bars": 0, "first": "-", "last": "-", "err": repr(e)[:80]}
            time.sleep(1.0 * (a + 1))
    return None

SYMS = {
    # CA round-1 re-verify + market proxy candidates
    "RY.TO": "CA round1 fin", "SHOP.TO": "CA round1 tech", "SU.TO": "CA round1 energy",
    "CNR.TO": "CA round1 'industrials'", "XAW.TO": "CA iShares TSX 60", "ZCN.TO": "CA BMO TSX Comp",
    "^TSX": "CA TSX composite idx", "XBC.TO": "CA iShares Crossover Bank", "XCU.TO": "CA iShares Utilities",
    # CA baskets
    "BHC.TO": "CA Bausch health", "AMT.TO": "CA Almac", "CGC.TO": "CA Canopy", "WTC.TO": "CA ?WTC",
    "L.N": "CA Loblaws", "CNE.TO": "CA Cineplex", "GMT.A": "CA MTY", "PC.TO": "CA ?PC", "PCB.TO": "CA ?PCB",
    "DLR.TO": "CA ?DLR", "HBM.TO": "CA ?HBM", "BFAM.TO": "CA Quebecor", "Q.TO": "CA ?Q",
    "WES.TO": "CA Wesdome", "WPM.TO": "CA Wheaton PM", "HTO.TO": "CA ?HTO", "G.TO": "CA ?G",
    "GIL.TO": "CA GILAT", "CNQ.TO": "CA Cenovus", "WAF.TO": "CA ?WAF", "ATL.TO": "CA ?ATL",
    # EMEA baskets
    "DBK.DE": "EMEA DeutscheBank", "ING.AS": "EMEA ING", "GLE.PA": "EMEA SocGen", "BCS.L": "EMEA Barclays",
    "MC.PA": "EMEA LVMH", "RALG.L": "EMEA Richemont", "DEO.PA": "EMEA Diageo", "HEIA.DE": "EMEA Heineken",
    "SAP.DE": "EMEA SAP", "IFX.DE": "EMEA Infineon", "ASML.AS": "EMEA ASML", "TTE.PA": "EMEA TotalEnergies",
    "RWE.DE": "EMEA RWE", "ENI.MI": "EMEA Eni", "ELE.DE": "EMEA E.ON", "SHEL.L": "EMEA Shell",
    "RIO.L": "EMEA Rio Tinto", "BHP.L": "EMEA BHP", "GENC.L": "EMEA Glencore", "MT.MI": "EMEA Metallo",
    "ROR.L": "EMEA RollsRoyce", "VOW3.DE": "EMEA VW", "MBG.DE": "EMEA Mercedes", "DAI.DE": "EMEA Daimler(old)",
    "AIR.PA": "EMEA Airbus", "CRH.IM": "EMEA CRH",
    # JP additions
    "4063.T": "JP Mizuho", "8058.T": "JP SMFG", "8308.T": "JP Resona", "4578.T": "JP Takeda",
    "4523.T": "JP Astellas", "4519.T": "JP Eisai", "7735.T": "JP Nintendo", "9984.T": "JP Seven&i",
    "8267.T": "JP MUJI", "6861.T": "JP TokyoElectron", "6758.T": "JP Sony", "6857.T": "JP Kioxia",
    "1605.T": "JP Idemitsu", "5019.T": "JP JXTG", "5713.T": "JP ?5713", "7011.T": "JP MitsubishiHeavy",
    "7974.T": "JP Toyota", "7003.T": "JP IHI", "6501.T": "JP Hitachi r1",
    # HK additions
    "0388.HK": "HK HKEX", "1398.HK": "HK ICBC", "2318.HK": "HK PingAn", "2601.HK": "HK CPIC r1",
    "1177.HK": "HK SinoBiotech", "0369.HK": "HK Jingyi", "2020.HK": "HK ?2020",
    "0998.HK": "HK CITICSI", "0345.HK": "HK Vitasoy", "2007.HK": "HK ?2007", "1880.HK": "HK CTD",
    "9988.HK": "HK Alibaba", "3690.HK": "HK Meituan", "9618.HK": "HK JD",
    "1035.HK": "HK CNOOC", "3993.HK": "HK CMOC", "2883.HK": "HK COSL", "1099.HK": "HK Sinoma",
    "0006.HK": "HK PowerCons", "0658.HK": "HK CRRC", "1071.HK": "HK Haiwen", "3311.HK": "HK APsol",
}

print("PROBE ROUND 2\n")
out = {}
print(f"{'sym':12s} {'bars':>5s} {'first':10s} {'last':10s} {'cur':5s} {'note':24s} shortName / longName")
print("-" * 130)
for sym, note in SYMS.items():
    m = fetch_meta(sym)
    out[sym] = m
    sn = (m["shortName"] or "?")
    ln = (m["longName"] or "")
    disp = f"{sn} / {ln}" if ln and ln != sn else sn
    print(f"{sym:12s} {m['bars']:5d} {m['first']:10s} {m['last']:10s} {str(m['cur']):5s} {note:24s} {disp[:70]}")
    time.sleep(0.3)

with open("/home/trading/trading-ai/research/probe2.json", "w") as fh:
    json.dump(out, fh, indent=1)
print("\nSaved probe2.json")
