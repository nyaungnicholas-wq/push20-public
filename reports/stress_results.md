# Rotation-v2 champion — robustness / stress battery

## 1. Simple vs fully-tuned champion (2005-2024)
   (Are the round-3/4 micro-tweaks real, or noise? If simple ≈ tuned, prefer simple.)
  simple champion            CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  tuned champion             CAGR +12.8%  MDD -30.7%  Sharpe 0.83  | SPY +10.3%  edge +2.5% [WIN ]

## 2. Transaction-cost sensitivity (simple champion, 2005-2024)
   (10bps is the study default. Does the edge survive realistic / pessimistic costs?)
  slippage 5bps/side         CAGR +13.4%  MDD -31.0%  Sharpe 0.84  | SPY +10.3%  edge +3.1% [WIN ]
  slippage 10bps/side        CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  slippage 20bps/side        CAGR +12.2%  MDD -31.3%  Sharpe 0.78  | SPY +10.3%  edge +1.9% [WIN ]
  slippage 30bps/side        CAGR +11.4%  MDD -31.5%  Sharpe 0.73  | SPY +10.3%  edge +1.1% [WIN ]
  slippage 50bps/side        CAGR  +9.6%  MDD -31.9%  Sharpe 0.65  | SPY +10.3%  edge -0.7% [LOSE]

## 3. Risk-off asset dependence (simple champion, 2005-2024)
   (How much of the edge rides on the GLD choice specifically?)
  defensive = GLD            CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  defensive = TLT            CAGR +10.4%  MDD -26.5%  Sharpe 0.75  | SPY +10.3%  edge +0.1% [WIN ]
  defensive = IEF            CAGR +10.2%  MDD -19.2%  Sharpe 0.77  | SPY +10.3%  edge -0.1% [LOSE]
  defensive = BIL            CAGR  +9.1%  MDD -18.9%  Sharpe 0.70  | SPY +10.3%  edge -1.3% [LOSE]

## 4. Start-date sensitivity (simple champion → 2024-12-31)
   (Is the 20-year result an artifact of starting in 2005?)
  start 2005                 CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  start 2006                 CAGR +12.4%  MDD -31.1%  Sharpe 0.79  | SPY +10.5%  edge +1.9% [WIN ]
  start 2007                 CAGR +12.9%  MDD -31.1%  Sharpe 0.81  | SPY +10.3%  edge +2.6% [WIN ]
  start 2008                 CAGR +12.5%  MDD -31.1%  Sharpe 0.79  | SPY +10.7%  edge +1.8% [WIN ]
  start 2010                 CAGR +13.1%  MDD -22.5%  Sharpe 0.87  | SPY +13.7%  edge -0.5% [LOSE]
  start 2012                 CAGR +14.3%  MDD -22.5%  Sharpe 0.95  | SPY +14.5%  edge -0.3% [LOSE]

## 5. Parameter perturbation (simple champion, 2005-2024)
   (Is the champion on a knife-edge, or a broad plateau? Stable = trustworthy.)
  vol_target 0.08            CAGR +12.5%  MDD -29.5%  Sharpe 0.82  | SPY +10.3%  edge +2.1% [WIN ]
  vol_target 0.10            CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  vol_target 0.12            CAGR +13.4%  MDD -32.8%  Sharpe 0.81  | SPY +10.3%  edge +3.1% [WIN ]
  vol_target 0.15            CAGR +13.4%  MDD -35.0%  Sharpe 0.78  | SPY +10.3%  edge +3.1% [WIN ]
  lookback 210d              CAGR +13.4%  MDD -29.3%  Sharpe 0.84  | SPY +10.3%  edge +3.0% [WIN ]
  lookback 231d              CAGR +14.0%  MDD -29.2%  Sharpe 0.88  | SPY +10.3%  edge +3.7% [WIN ]
  lookback 252d              CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  lookback 273d              CAGR +12.2%  MDD -31.7%  Sharpe 0.77  | SPY +10.3%  edge +1.8% [WIN ]

## 6. Cross-check vs SPY on every window (simple champion)
  2005-2024                  CAGR +13.0%  MDD -31.1%  Sharpe 0.82  | SPY +10.3%  edge +2.7% [WIN ]
  2005-2014                  CAGR +11.4%  MDD -31.1%  Sharpe 0.72  | SPY +7.7%  edge +3.7% [WIN ]
  2015-2024                  CAGR +14.8%  MDD -22.6%  Sharpe 0.93  | SPY +13.1%  edge +1.7% [WIN ]
  2018-2024                  CAGR +16.3%  MDD -22.5%  Sharpe 0.94  | SPY +13.7%  edge +2.7% [WIN ]
