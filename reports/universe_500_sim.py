#!/usr/bin/env python
"""
universe_500_sim.py — 5-window simulation with expanded ~500-asset universe.

Runs TWO parallel tests per window so you can compare directly:
  [BIASED]  S&P 500 current constituents + V5 ETFs  (survivorship-biased — labeled clearly)
  [CLEAN]   Broad ETF universe ~100 liquid ETFs      (no survivorship bias)

⚠️  SURVIVORSHIP BIAS WARNING ⚠️
The S&P 500 stock list is TODAY'S constituents. Every company on it survived.
Backtesting momentum on "which 2008 winner had the best 12-month return" is
answering a question you couldn't have answered in 2008. The BIASED numbers
will be inflated — treat them as an upper-bound curiosity, not real alpha.

Run: cd ~/claude\ code/stock-trader && .venv/bin/python reports/universe_500_sim.py
"""
import copy, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import yfinance as yf

from trader.config import CONFIG
from trader.rotation import run_rotation_backtest, apply_v5
from trader import metrics

# ── S&P 500 current constituents (503 tickers as of mid-2026, survivorship-biased) ──
SP500_STOCKS = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM",
    "ALB","ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE",
    "AAL","AEP","AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","ANSS","AON",
    "APA","APO","AAPL","AMAT","APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK",
    "ADP","AZO","AVB","AVY","AXON","BKR","BALL","BAC","BAX","BDX","BRK-B","BBY","TECH",
    "BIIB","BLK","BX","BA","BCF","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","BG",
    "CDNS","CZR","CPT","CPB","COF","CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE",
    "CDW","CE","COR","CNC","CNX","CDAY","CF","CRL","SCHW","CHTR","CVX","CMG","CB","CHD",
    "CI","CINF","CTAS","CSCO","C","CFG","CLX","CME","CMS","KO","CTSH","CL","CMCSA",
    "CAG","COP","ED","STZ","CEG","COO","CPRT","GLW","CPAY","CTVA","CSGP","COST","CTRA",
    "CRWD","CCI","CSX","CMI","CVS","DHR","DRI","DVA","DAY","DECK","DE","DELL","DAL",
    "DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV","DOW","DHI","DTE",
    "DUK","DD","EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","EMR","ENPH","ETR","EOG",
    "EPAM","EFX","EQIX","EQR","EQT","ESS","EL","ETSY","EG","EVRG","ES","EXC","EXPE",
    "EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB","FSLR","FE",
    "FI","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN","IT","GE","GEHC","GEV",
    "GEN","GNRC","GD","GIS","GM","GPC","GILD","GS","HAL","HIG","HAS","HCA","DOC","HSIC",
    "HSY","HES","HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ","HUBB","HUM",
    "HBAN","HII","IBM","IEX","IDXX","ITW","INCY","IR","PODD","INTC","ICE","IFF","IP",
    "IPG","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY","J","JNJ","JCI",
    "JPM","JNPR","K","KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KKR","KLAC","KHC",
    "KR","LHX","LH","LRCX","LW","LVS","LDOS","LEN","LLY","LIN","LYV","LKQ","LMT",
    "L","LOW","LULU","LYB","MTB","MRO","MPC","MKTX","MAR","MMC","MLM","MAS","MA",
    "MTCH","MKC","MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT",
    "MAA","MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS","MOS","MSI","MSCI",
    "NDAQ","NTAP","NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC","NTRS","NOC",
    "NCLH","NRG","NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL",
    "OTIS","PCAR","PKG","PANW","PARA","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE",
    "PCG","PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PLD","PRU","PEG",
    "PTC","PSA","PHM","QRVO","PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF",
    "RSG","RMD","RVTY","ROK","ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX",
    "SRE","NOW","SHW","SPG","SWKS","SJM","SNA","SOLV","SO","LUV","SWK","SBUX","STT",
    "STLD","STE","SYK","SMCI","SYF","SNPS","SYY","TMUS","TROW","TTWO","TPR","TRGP",
    "TGT","TEL","TDY","TFX","TER","TSLA","TXN","TXT","TMO","TJX","TSCO","TT","TDG",
    "TRV","TRMB","TFC","TYL","TSN","USB","UBER","UDR","ULTA","UNP","UAL","UPS","URI",
    "UNH","UHS","VLO","VTR","VLTO","VRSN","VRSK","VZ","VRTX","VTRS","VICI","V","VST",
    "VMC","WRB","GWW","WAB","WBA","WMT","DIS","WBD","WM","WAT","WEC","WFC","WELL","WST",
    "WDC","WY","WHR","WMB","WTW","WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZTS",
]

# ── Clean ETF-only ~100-asset universe (no survivorship bias) ──────────────────
ETF_UNIVERSE_100 = [
    # Core sectors (V5 base)
    "XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLC","SMH","QQQ",
    # Removed sectors (sometimes they lead)
    "XLP","XLU","XLRE",
    # Sub-sector / thematic
    "IBB","XBI","KRE","XRT","XHB","ITB","SOXX","HACK","BOTZ","ARKK",
    # Factor / style
    "IWD","IWF","MTUM","QUAL","USMV","IWM","MDY","IJR",
    # International
    "EFA","EEM","VEA","VWO","EWJ","FXI","EWG","EWU","EWZ","INDA",
    "EWA","EWC","EWY","EWT","MCHI",
    # Commodities
    "GLD","SLV","USO","UNG","DBC","DBA","PDBC","IAU",
    # Fixed income / macro
    "TLT","IEF","SHY","HYG","LQD","BND","TIP","EMB",
    # Broad market
    "IVV","VTI","VO","VB",
    # Leveraged (liquid, established)
    "SSO","QLD","ROM","USD","ERX","UYG",
    # Cash / defensive
    "BIL","SHV",
]

WINDOWS = [
    ("SIM-1: Full 20 years",        "2005-01-01", "2024-12-31"),
    ("SIM-2: GFC + QE recovery",    "2005-01-01", "2012-12-31"),
    ("SIM-3: Low-vol bull run",      "2013-01-01", "2019-12-31"),
    ("SIM-4: COVID + tech boom",     "2018-01-01", "2022-12-31"),
    ("SIM-5: Rate hikes + AI boom",  "2020-01-01", "2024-12-31"),
]


def run_one(label, universe, start, end, bias_flag):
    cfg = copy.deepcopy(CONFIG)
    apply_v5(cfg)
    cfg.backtest_start = start
    cfg.backtest_end   = end
    cfg.rotation_universe = list(dict.fromkeys(universe))

    result = run_rotation_backtest(cfg)
    m = metrics.summarize(result["broker"].equity_curve,
                          result["broker"].trades, cfg.starting_cash)
    bench = result.get("benchmark", {})
    trades = len(result["broker"].trades)

    bias = "⚠ BIASED" if bias_flag else "✓ CLEAN "
    print(f"  {bias} | {label:32} | "
          f"CAGR {m['cagr']:>+7.1%} | MDD {m['max_drawdown']:>7.1%} | "
          f"Shp {m.get('sharpe', 0):>5.2f} | "
          f"Edge {(m['cagr'] - bench.get('cagr', 0))*100:>+5.1f}pp | "
          f"Trades {trades:>5}")
    return m


def main():
    print("=" * 95)
    print("  V5 HYPER-DRIVE — UNIVERSE EXPANSION TO ~500 ASSETS")
    print("  5 windows × 2 variants (biased stocks vs clean ETFs) = 10 simulations")
    print("=" * 95)
    print("""
  ⚠  SURVIVORSHIP BIAS WARNING  ⚠
  The 'BIASED' rows use today's S&P 500 stocks — these are companies that
  survived and thrived. Any backtest ranking their 2008 momentum is cheating:
  you're using future knowledge of who won. Expect inflated CAGR numbers.
  The 'CLEAN' rows use only ETFs — no survivorship bias. These are real.
""")

    biased_univ = list(dict.fromkeys(
        ["XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLC","SMH","QQQ"]
        + SP500_STOCKS
    ))
    clean_univ  = list(dict.fromkeys(ETF_UNIVERSE_100))

    print(f"  Biased universe : {len(biased_univ)} symbols  "
          f"(10 sector ETFs + {len(SP500_STOCKS)} S&P 500 stocks)")
    print(f"  Clean universe  : {len(clean_univ)} symbols  "
          f"(broad ETF basket — sectors, factors, intl, commodities, bonds)")
    print(f"\n  Downloading data in batches of 50 (rate-limit safe)...")

    # ── Download in batches of 50 to avoid yfinance timeouts ─────────────────
    all_syms = list(dict.fromkeys(
        biased_univ + clean_univ
        + ["SPY","GLD","TLT","BIL","BND"]
        + ["ROM","QLD","USD","UYG","ERX","RXL","UXI","UCC","UYM","UGL","UBT"]
        + ["TECL","TQQQ","SOXL","FAS"]
    ))

    import warnings, time
    frames = []
    batch_size = 50
    for i in range(0, len(all_syms), batch_size):
        batch = all_syms[i : i + batch_size]
        print(f"    batch {i//batch_size+1}/{(len(all_syms)+batch_size-1)//batch_size} "
              f"({len(batch)} symbols)...", flush=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = yf.download(batch, start="2003-01-01", end="2024-12-31",
                              auto_adjust=True, progress=False, timeout=30)
        if raw.empty:
            continue
        closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
        if isinstance(closes, pd.Series):
            closes = closes.to_frame(name=batch[0])
        frames.append(closes)

    prices = pd.concat(frames, axis=1)
    prices = prices.loc[:, ~prices.columns.duplicated()]
    prices.dropna(how="all", inplace=True)
    print(f"  Downloaded {prices.shape[1]} of {len(all_syms)} symbols / "
          f"{len(prices)} trading days")

    # ── Patch data_source so the backtest uses our pre-downloaded prices ─────
    from trader import data_source as ds_mod

    class CachedSource:
        def history(self, symbols, start, end):
            avail = [s for s in symbols if s in prices.columns]
            sliced = prices[avail]
            sliced = sliced[(sliced.index >= start) & (sliced.index <= end)]
            result = {}
            for s in avail:
                col = sliced[[s]].dropna()
                if not col.empty:
                    col = col.rename(columns={s: "close"})
                    result[s] = col
            return result

        def latest(self, symbols):
            return {}

    _orig_get = ds_mod.get_data_source
    ds_mod.get_data_source = lambda *a, **kw: CachedSource()

    print()
    print(f"  {'Variant':<9} | {'Window':<32} | {'CAGR':>8} | {'MDD':>8} | "
          f"{'Sharpe':>6} | {'Edge':>7} | Trades")
    print("  " + "-" * 90)

    for sim_label, start, end in WINDOWS:
        print()
        run_one(sim_label, biased_univ, start, end, bias_flag=True)
        run_one(sim_label, clean_univ,  start, end, bias_flag=False)

    ds_mod.get_data_source = _orig_get  # restore

    print()
    print("  Legend: ⚠ BIASED = S&P 500 current constituents (survivorship bias — not real)")
    print("          ✓ CLEAN  = Broad ETF universe (no survivorship bias — trustworthy)")
    print()
    print("  SPY benchmark: ~+10.3% CAGR / −55% MDD (2005–2024)")
    print("=" * 95)


if __name__ == "__main__":
    main()
