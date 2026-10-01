# Cross-Market Transferability v2 — Real Sector Index/ETF Data
**Date:** 2026-09-25 · **Card:** t_62b516e9 · **Data window:** 87d (2026-06-29 → 2026-09-24)
**Signal set:** 512 verified 24h predictions (127 correct / 385 wrong) — identical to v1
**Methodology:** identical to v1 (`research/control_group.py`) — first-overlapping-session bar,
close vs prior close, USD terms via FX, ±0.2% neutral tier. **Only the data was upgraded**
from single-name proxies to real sector ETFs / name-verified equal-weight baskets.

---

## 1. Bottom line — Go/No-Go table

| Market | Gap (v2) | vs US +27.9 | Verdict | Included sectors (gap) | Excluded sectors (gap) |
|--------|---------|------------|---------|------------------------|------------------------|
| **EMEA** | **+24.1%** | 86% of US | **GO-FILTERED** | market +39.6 · energy +36.4 · financials +23.7 · technology +21.8 · industrials +20.7† · consumer +15.1 | materials −7.1† |
| **JP** | **+14.0%** | 50% of US | **GO-FILTERED** | energy +33.1 · financials +30.0 · industrials +24.8† | technology +2.1 · healthcare +1.0 · market +5.6 · consumer +7.2† · materials −5.3† |
| **CA** | **+7.4%** | 27% of US | **GO-FILTERED (narrow)** | energy +20.9 · technology +12.6 | financials −1.2 · market +4.2 · consumer +12.9‡ · industrials +10.3† · materials −21.4† |
| **HK** | **+2.4%** | 9% of US | **NO-GO** (market-level) | — (energy-only +31.4 is a single-sector carve-out, not a market) | technology −10.6 · market −11.3 · industrials −10.5† · consumer −3.1 · financials −0.2 · healthcare +0.8 · materials −2.5† |

† = noisy (one of the correct/wrong buckets has n < 15) · ‡ = borderline correct-n (14)

**Rationale**
- **EMEA** is the standout: 86% of the US edge, 6 of 7 sectors positive, and the gap is
  *improving* monthly (Jul +16 → Aug +20 → Sep +37). Only materials is weak.
- **JP** holds real edge in energy (+33) and financials (+30), both statistically sound
  (correct n=25/18, wrong n=36/43). Tech/healthcare/market carry no edge — JP is a
  2-sector trade (plus industrials if you accept the noisy bucket).
- **CA** is a 1.5-sector trade: energy (+20.9) is the only solid positive; tech (+12.6)
  secondary. The TSX is bank/energy/finance-complex — the pipeline's US sector-bullish
  leadership simply isn't there.
- **HK** is statistically indistinguishable from a coin flip at market level (+2.4).
  Energy (+31.4, n=63) is the single positive cell; everything else is zero or negative.
  Do not trade HK as a market. If coverage is ever required, energy-only is the only
  defensible slice — and even that is one sector, not a market.

**Cross-market pattern (v1 + v2 consistent):** *energy transfers everywhere* (CA +20.9,
EMEA +36.4, JP +33.1, HK +31.4). Commodity/macro-driven sectors are the transferable
core; US-sector bullish leadership (consumer/tech momentum) does not transfer outside
EMEA.

---

## 2. US baseline (pipeline validation)

Reproduced end-to-end from the same 512 signals, identical scoring:

| Bucket | Hit | Hit % |
|--------|-----|-------|
| Correct calls | 82/127 | **64.6%** |
| Wrong calls | 141/385 | **36.6%** |
| All | 223/512 | 43.6% |

**US gap = +27.9 pts** (v1 reference: +29 → within ±2 acceptance ✓)

---

## 3. Market-level results (USD terms, ±0.2% tier)

| Market | n | Correct hit | Wrong hit | **Gap** | All hit | Avg move |
|--------|---|------------|-----------|---------|---------|----------|
| US (baseline) | 512 | 64.6% | 36.6% | **+27.9** | 43.6% | — |
| EMEA | 448 | 59.8% (67/112) | 35.7% (120/336) | **+24.1** | 41.7% | −0.07% |
| JP | 488 | 55.4% (67/121) | 41.4% (152/367) | **+14.0** | 44.9% | +0.27% |
| CA | 448 | 46.4% (52/112) | 39.0% (131/336) | **+7.4** | 40.8% | +0.08% |
| HK | 504 | 44.4% (56/126) | 42.1% (159/378) | **+2.4** | 42.7% | +0.09% |

n < 512 because a sector is skipped where no verified series existed (CA/EMEA healthcare),
and some first-session bars fell outside the data window.

### By direction (hit %)
| Direction | CA | EMEA | JP | HK |
|-----------|-----|------|-----|-----|
| bullish (n 334/334/386/394) | 44.6% | 44.6% | 50.3% | 48.0% |
| bearish (n 60/60/51/58) | 48.3% | **55.0%** | 45.1% | 37.9% |
| neutral (n 22 each) | 22.7% | 22.7% | 9.1% | 18.2% |
| mixed (n 32/32/29/30) | 0% | 0% | 0% | 0% |

Mixed = 0% everywhere, as designed (mixed predictions are hard-blocked upstream).
EMEA again shows the bearish-skew (v1 finding holds: bearish/macro transfers better).
JP is the exception where *bullish* out-hits bearish (50.3 vs 45.1) — consistent with v1.

### Monthly stability (all signals per market)
| Month | CA gap | EMEA gap | JP gap | HK gap |
|-------|--------|----------|--------|--------|
| 2026-07 | −4% | +16% | +6% | −3% |
| 2026-08 | +11% | +20% | +27% | +8% |
| 2026-09 | +13% | +37% | +13% | +8% |

(June excluded — window opens 2026-06-29, so no *correct* predictions had verified
outcomes by month end; wrong-call counts for June exist but cannot form a gap.)
EMEA is monotonically improving; CA/JP/HK are stable-positive from August on.

---

## 4. Per-sector hit tables (v2 real data)
Format: n (signals replayed) · correct hit n% · wrong hit n% · **gap** · avg move.
† noisy bucket (correct or wrong n < 15).

### Canada (n=448; healthcare skipped — no verified CA sector series)
| Sector | n | Correct | Wrong | Gap | Avg |
|--------|---|---------|-------|-----|-----|
| energy (XEG.TO real ETF) | 64 | 15/26 | 14/38 | **+20.9** | +0.25% |
| technology (SHOP+BCE) | 64 | 13/23 | 18/41 | **+12.6** | +0.25% |
| consumer (Loblaw, n=1) | 64 | 6/14‡ | 15/50 | +12.9 | −0.06% |
| industrials (CNR, n=1) | 64 | 3/6† | 23/58 | +10.3† | −0.02% |
| market (ZCN.TO real ETF) | 64 | 9/16 | 25/48 | +4.2 | −0.00% |
| financials (ZEB.TO real banks ETF) | 64 | 4/19 | 10/45 | −1.2 | −0.10% |
| materials (Hudbay+Wheaton) | 64 | 2/8† | 26/56 | −21.4† | +0.22% |

### EMEA (n=448; healthcare skipped)
| Sector | n | Correct | Wrong | Gap | Avg |
|--------|---|---------|-------|-----|-----|
| market (^STOXX50E real index) | 64 | 12/16 | 17/48 | **+39.6** | −0.04% |
| energy (TTE+RWE+ENI) | 64 | 17/26 | 11/38 | **+36.4** | +0.16% |
| financials (DBK+GLE+BNP) | 64 | 10/19 | 13/45 | **+23.7** | −0.07% |
| technology (SAP+IFX+ASML) | 64 | 14/23 | 16/41 | **+21.8** | −0.10% |
| industrials (VOW3+MBG+AIR) | 64 | 3/6† | 17/58 | +20.7† | −0.09% |
| consumer (LVMH+KERING) | 64 | 8/14‡ | 21/50 | +15.1 | −0.30% |
| materials (RIO+BHP) | 64 | 3/8† | 25/56 | −7.1† | −0.06% |

### Japan (n=488)
| Sector | n | Correct | Wrong | Gap | Avg |
|--------|---|---------|-------|-----|-----|
| energy (ENEOS+IDEMITSU+INPEX) | 61 | 18/25 | 14/36 | **+33.1** | +0.52% |
| financials (MUFG+RESONA) | 61 | 10/18 | 11/43 | **+30.0** | +0.42% |
| industrials (MITSU ELECTRIC+MITSU HEAVY) | 61 | 4/6† | 23/55 | +24.8† | +0.24% |
| consumer (FAST RETAILING+AEON+TOYOTA) | 61 | 5/13‡ | 15/48 | +7.2 | −0.05% |
| market (^N225 real index) | 61 | 8/16 | 20/45 | +5.6 | +0.06% |
| technology (HITACHI+KEYENCE+TOKYO ELECTRON+ADVANTEST+SONY) | 61 | 12/21 | 22/40 | +2.1 | +0.21% |
| healthcare (DAIICHI SANKYO+EISAI+CHUGAI+OTSUKA) | 61 | 7/15 | 21/46 | +1.0 | +0.15% |
| materials (NIPPON STEEL+SUMITOMO METAL MINING) | 61 | 3/7† | 26/54 | −5.3† | +0.60% |

### HK (n=504)
| Sector | n | Correct | Wrong | Gap | Avg |
|--------|---|---------|-------|-----|-----|
| energy (PETROCHINA+SHENHUA) | 63 | 18/26 | 14/37 | **+31.4** | +0.10% |
| healthcare (CSPC+SINOPHARM+SBP) | 63 | 7/15 | 22/48 | +0.8 | +0.27% |
| financials (HSBC+HKEX+ICBC+CITIC) | 63 | 6/19 | 14/44 | −0.2 | +0.23% |
| consumer (CHOW TAI FOOK+VITASOY+CTD+ANTA) | 63 | 5/14‡ | 19/49 | −3.1 | −0.01% |
| materials (JIANGXI COPPER+CHALCO) | 63 | 3/8† | 22/55 | −2.5† | −0.08% |
| industrials (CRRC+CHINA RAILWAY+CSCEC) | 63 | 2/6† | 25/57 | −10.5† | −0.03% |
| technology (TENCENT+ALIBABA+MEITUAN+JD) | 63 | 10/22 | 23/41 | −10.6 | +0.14% |
| market (^HSI real index) | 63 | 5/16 | 20/47 | −11.3 | +0.13% |

---

## 5. v1 (single-name) vs v2 (real ETF/basket) comparison

| Market | v1 gap | v2 gap | Δ | Driver |
|--------|--------|--------|---|--------|
| CA | +13 | +7.4 | **−5.6** | v1 single names (RY/SHOP/SU/CNR) flattered; real banks/energy ETFs + added weak sectors (materials −21) drag |
| EMEA | +10 | +24.1 | **+14.1** | v1 was contaminated: ROR.L silently resolved to **Rotork** (small-cap actuators, used as "industrials"); real mega-cap baskets restore the edge |
| JP | +11 | +14.0 | +3.0 | v1 single names under-represented energy/financials; v2 baskets capture the real sector move |
| HK | −3 | +2.4 | +5.4 | v1 had 0386.HK (Sinopec — an *energy* name) misfiled as materials; v2 corrects sectors but the market still has no edge |

The v2 upgrade was decisive for EMEA: v1's "industrials" series was a mislabeled
small-cap, which is exactly the failure mode this card targeted. **v2 verdicts should be
treated as the authoritative ones.**

---

## 6. Data sources — every series name-verified (acceptance #1)

83/83 series passed the hard verification gate at fetch time (Yahoo chart meta
`shortName` must match the expected instrument, else the run aborts). Full run log:
`research/v2_run.log`. Bar counts: CA 128, EMEA 130, JP 123, HK 125, FX 133 (6mo daily).

**Real sector ETFs/indexes used (3):**
| Symbol | Verified shortName | Role |
|--------|--------------------|------|
| XEG.TO | iSHARES SP TSX CAPPED ENERGY INDEX ETF | CA energy |
| ZEB.TO | BMO EQUAL WEIGHT BANKS INDEX ETF | CA financials |
| ZCN.TO | BMO SP TSX CAPPED COMPOSITE INDEX ETF | CA market |
| ^STOXX50E | EURO STOXX 50 | EMEA market |
| ^N225 | Nikkei 225 | JP market |
| ^HSI | HANG SENG INDEX | HK market |

**Baskets (equal-weight, all members name-verified at fetch):**
- **CA:** technology = Shopify (SHOP.TO) + BCE (BCE.TO) · consumer = Loblaw (L.TO, *n=1*) ·
  materials = Hudbay Minerals (HBM.TO) + Wheaton Precious Metals (WPM.TO) ·
  industrials = CN Railway (CNR.TO, *n=1*)
- **EMEA:** financials = Deutsche Bank (DBK.DE) + SocGen (GLE.PA) + BNP Paribas (BNP.PA) ·
  consumer = LVMH (MC.PA) + Kering (KER.PA) · technology = SAP (SAP.DE) + Infineon (IFX.DE)
  + ASML (ASML.AS) · energy = TotalEnergies (TTE.PA) + RWE (RWE.DE) + Eni (ENI.MI) ·
  materials = Rio Tinto (RIO.L) + BHP (BHP.L) · industrials = VW (VOW3.DE) + Mercedes
  (MBG.DE) + Airbus (AIR.PA)
- **JP:** financials = MUFG (8306.T) + Resona (8308.T) · healthcare = Daiichi Sankyo (4568.T)
  + Eisai (4523.T) + Chugai (4519.T) + Otsuka (4578.T) · consumer = Fast Retailing (9983.T)
  + Aeon (8267.T) + Toyota (7203.T) · technology = Hitachi (6501.T) + Keyence (6861.T) +
  Tokyo Electron (8035.T) + Advantest (6857.T) + Sony (6758.T) · energy = Eneos (5020.T) +
  Idemitsu (5019.T) + Inpex (1605.T) · materials = Nippon Steel (5401.T) + Sumitomo Metal
  Mining (5713.T) · industrials = Mitsubishi Electric (6503.T) + Mitsubishi Heavy (7011.T)
- **HK:** financials = HSBC (0005.HK) + HKEX (0388.HK) + ICBC (1398.HK) + CITIC (0998.HK) ·
  healthcare = CSPC (1093.HK) + Sinopharm (1099.HK) + Sino Biopharma/SBP (1177.HK) ·
  consumer = Chow Tai Fook (1929.HK) + Vitasoy (0345.HK) + CTG Duty Free (1880.HK) +
  ANTA (2020.HK) · technology = Tencent (0700.HK) + Alibaba (9988.HK) + Meituan (3690.HK)
  + JD (9618.HK) · energy = PetroChina (0857.HK) + Shenhua (1088.HK) · materials =
  Jiangxi Copper (0358.HK) + Chalco (2600.HK) · industrials = CRRC (0658.HK) + China
  Railway (0390.HK) + CSCEC Intl (3311.HK)
- **FX:** CAD=X, EUR=X, JPY=X, HKD=X (USD/XXX)
- **US baseline:** XLF, XLV, XLY, SOXX, XLE, XLB, XLI, SPY

**Skipped sectors (no verifiable series, gap reported — not fabricated):**
- **CA healthcare** — iShares/BMO/Vanguard TSX healthcare ETFs all 404 on Yahoo; no 3-5
  name liquid basket resolvable. → reported, not traded.
- **EMEA healthcare** — no Stoxx healthcare line, no Xetra, no 3-name basket of European
  pharma resolvable on Yahoo. → reported, not traded.

**Rejected candidates (404 or wrong instrument — documented so they are not retried
blindly):** XFB.TO / XPH.TO / XTT.TO / XCY.TO / XBC.TO / XCU.TO / ZFA / ZEN / ZHV / ZTH /
ZIC / ZSM / ZPH / ZMIN / all Vanguard CA sector ETFs / ^TSX / ^SX1E..^SX8E / all Xetra
EUNF/EUCO/EUSH/EUTE/EUMN/EURA / EUNF.DE / BCS.L / RALG.L / DEO.PA / HEIA.DE / GENC.L /
SAN.MI / UBI.MI / DST.PA / AIQ.DE / DAI.DE / CRH.IM / MT.MI / ELE.DE / 8312.T / 8058.T
(=Mitsubishi Corp, not SMFG) / 4063.T (=Shin-Etsu, not Mizuho) / 4578.T used as Otsuka
(probe caught it is NOT Takeda) / 7735.T (=Screen, not Nintendo) / 7974.T (=Nintendo, not
Toyota — Toyota is 7203.T) / 1681.T / 1657.T / 1321.T / 1306.T (=index ETFs, not sector
ETFs) / XIC.TO (=Core TSX Composite, not Industrials) / XMV.TO (=Min Vol Canada, not
Materials) / XTR.TO (=Diversified Monthly Income, not Composite) / XAW.TO (=MSCI All
Country World, not TSX 60) / ROR.L (=Rotork plc, **not** Rolls-Royce) / 0836.HK (=China
Resources Power, not Sinopec) / 0386.HK (Sinopec — *energy*, not materials) / 0369.HK /
2007.HK / 1035.HK.

**Round-1 (v1) mislabel corrected:** v1 used 0386.HK (Sinopec, energy) as the HK
*materials* proxy and ROR.L (Rotork) as EMEA *industrials*. v2 sector assignments above
use only names whose shortName was verified at fetch time.

---

## 7. Limitations
1. **Baskets are equal-weight**, not cap-weighted — a 3-name basket over-represents the
   small member. For 4-5 name baskets (JP tech, HK fin/tech) this is acceptable; for
   2-name baskets (JP fin, EMEA materials, HK energy) the result is a 50/50 blend —
   treat those gaps as indicative, not precise.
2. **n=1 baskets:** CA consumer (Loblaw) and CA industrials (CNR) are single names again —
   no liquid basket resolvable; their gaps are the same quality as v1 single names.
3. **Session return = close vs prior close** on the first foreign session bar after the
   signal — not intraday. A signal made at 14:00 ET and a market opening the next day
   captures ~1 session of drift, which is the intended 24h-horizon proxy.
4. **JP bars lag US by a session** (Tokyo trades during US pre-market); +14h tolerance
   handles EMEA/CA next-day sessions, JP/HK same-day.
5. Sector-level cells with correct-n < 15 (marked †) are noisy — the *market-level* gaps
   are the primary statistics; sector verdicts are secondary filters.
6. 87-day window, one regime (July–Sept 2026). Monthly table shows stability within that
   regime but not across regimes.

## 8. Phase 2 (IBKR) — would it change any verdict?
**Probably not the verdicts, but it would strengthen the sector mapping.** IBKR data is
the same underlying daily prices; the go/no-go conclusions rest on signal→price
transferability, which is data-source-independent. What IBKR *would* add: (a) tradeable
Nikkei/TOPIX sector index lines and iShares TSX sector ETFs (XFB.TO et al. may be
exchange-listed even though Yahoo 404s them) — this would upgrade JP/HK healthcare and
CA financials from baskets to real indexes; (b) confirmation that the 3 real ETFs used
(XEG/ZEB/ZCN) are actually executable. None of these would plausibly flip a market-level
verdict (HK has no edge in 7 of 8 sectors — better data won't create an edge). If Phase 2
is run, re-check CA financials (currently banks-only via ZEB) and JP healthcare specifically.

---

## 9. Acceptance checklist
1. ✅ 83/83 series name-verified at fetch (hard gate aborts on mismatch) — `research/v2_run.log`
2. ✅ US baseline reproduced: +27.9 gap vs +29 target (Δ 1.1 pts, within ±2)
3. ✅ Report on airig: `reports/research/cross_market_transfer_v2_2026-09-25.md` + `results_2026-09-25.json`
4. ✅ Read-only: no pipeline code touched, no DB writes (DB opened read-only for SELECT)

**Scripts on airig:** `research/transfer_v2.py` (backtest), `research/probe_symbols.py` +
`probe2.py` + `probe3.py` (name-verification probes), `research/cache_series.json`
(fetched data), `reports/research/series_2026-09-25.json` (raw bars).
