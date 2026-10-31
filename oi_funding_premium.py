#!/usr/bin/env python3
"""Классика OI + funding с подтверждением премией Coinbase. Дневки, ETH и BTC.

  python oi_funding_premium.py                # после strategy_lab.py fetch --coin ETH/BTC
  python oi_funding_premium.py --fee 0.001

Два прочтения связки OI + funding:
  squeeze: OI растёт и funding перегрет вверх -> лонги переполнены -> short; OI растёт и funding внизу -> long
  trend:   цена и OI растут, funding не перегрет -> long; цена падает, OI растёт, funding не в минусе -> short
Подтверждение премией Coinbase: long только при премии выше нормы, short только ниже.
Перебираются все параметры, в таблице показаны все варианты и медиана, чтобы не выбирать задним числом.
"""
import argparse
import itertools
import math
import sys

import numpy as np
import pandas as pd


def z(s, n=90):
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


def signals(df, n, k, pk):
    oi = np.log(df.oi).diff(n)
    px = np.log(df.close).diff(n)
    fz = z(df.funding)
    pz = z(df.cb_premium)
    valid = oi.notna() & fz.notna() & pz.notna()
    S = {}
    S["squeeze"] = np.where((oi > 0) & (fz < -k), 1, np.where((oi > 0) & (fz > k), -1, 0))
    S["trend"] = np.where((px > 0) & (oi > 0) & (fz < k), 1, np.where((px < 0) & (oi > 0) & (fz > -k), -1, 0))
    confirm_long, confirm_short = pz > pk, pz < -pk
    for name in ("squeeze", "trend"):
        p = S[name]
        S[f"{name} + премия"] = np.where((p == 1) & confirm_long, 1, np.where((p == -1) & confirm_short, -1, 0))
    S["только премия"] = np.where(confirm_long, 1, np.where(confirm_short, -1, 0))
    out = {}
    for name, p in S.items():
        p = np.where(valid, p.astype(float), np.nan)
        out[name] = p
        out[name + " [LO]"] = np.where(np.isnan(p), np.nan, np.clip(p, 0, None))
    return out


def evaluate(pos, ret, fee):
    ok = ~np.isnan(pos)
    p = np.nan_to_num(pos)
    nxt = np.r_[ret[1:], np.nan]
    pnl = p * (np.exp(nxt) - 1) - fee * np.abs(np.diff(np.r_[0, p]))
    lp = np.log1p(pnl)[ok & ~np.isnan(nxt)]
    if len(lp) < 200:
        return None
    half = len(lp) // 2
    eq = np.exp(np.cumsum(lp))

    def cagr(x):
        return np.exp(x.sum() * 365 / len(x)) - 1

    act = p[ok] != 0
    hit = np.mean(np.sign(nxt[ok][act]) == p[ok][act]) if act.any() else np.nan
    return dict(cagr=cagr(lp), sharpe=lp.mean() / (lp.std() + 1e-12) * math.sqrt(365),
                dd=(eq / np.maximum.accumulate(eq) - 1).min(), h1=cagr(lp[:half]), h2=cagr(lp[half:]),
                in_mkt=act.mean(), hit=hit, trades_y=np.abs(np.diff(p[ok])).sum() / ok.sum() * 365)


def run(coin, fee):
    df = pd.read_csv(f"data/{coin}_daily_full.csv")
    df["dt"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    ret = np.log(df.close).diff().values
    rows = []
    for n, k, pk in itertools.product((3, 7), (0.5, 1.0), (0.0, 0.5)):
        for name, pos in signals(df, n, k, pk).items():
            if "премия" not in name and pk != 0.0:
                continue   # варианты без премии не зависят от pk
            r = evaluate(pos, ret, fee)
            if r:
                rows.append(dict(strategy=name, n=n, k=k, pk=pk if "премия" in name else np.nan, **r))
    # buy & hold на том же окне, где есть OI
    first = df.oi.first_valid_index() + 7
    bh = evaluate(np.where(np.arange(len(df)) >= first, 1.0, np.nan), ret, fee)
    res = pd.DataFrame(rows)
    print(f"\n################ {coin}: окно с OI {df.dt[first]:%Y-%m-%d} .. {df.dt.iloc[-1]:%Y-%m-%d}, комиссия {fee:.2%}")
    print(f"Buy & hold: {bh['cagr']:+.0%} в год, Sharpe {bh['sharpe']:.2f}, просадка {bh['dd']:.0%}, "
          f"1-я половина {bh['h1']:+.0%}, 2-я {bh['h2']:+.0%}")

    agg = res.groupby("strategy").agg(variants=("cagr", "size"), cagr_med=("cagr", "median"),
                                      cagr_min=("cagr", "min"), cagr_max=("cagr", "max"),
                                      sharpe_med=("sharpe", "median"), dd_med=("dd", "median"),
                                      h1_med=("h1", "median"), h2_med=("h2", "median"),
                                      both_halves_pos=("h2", lambda s: np.mean((s > 0) & (res.loc[s.index, "h1"] > 0))),
                                      hit_med=("hit", "median"), in_mkt=("in_mkt", "median"),
                                      trades_y=("trades_y", "median")).sort_values("sharpe_med", ascending=False)
    f = agg.copy()
    for c in ("cagr_med", "cagr_min", "cagr_max", "dd_med", "h1_med", "h2_med", "both_halves_pos", "hit_med", "in_mkt"):
        f[c] = agg[c].map("{:+.0%}".format if c not in ("both_halves_pos", "hit_med", "in_mkt") else "{:.0%}".format)
    f["sharpe_med"] = agg.sharpe_med.map("{:.2f}".format)
    f["trades_y"] = agg.trades_y.map("{:.0f}".format)
    print("\nМедиана по всем вариантам параметров (n дней OI/цены, порог funding k, порог премии pk):")
    print(f.to_string())
    return res, bh


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--coins", default="ETH,BTC")
    a = ap.parse_args()
    for coin in a.coins.split(","):
        res, _ = run(coin, a.fee)
        best = res.sort_values("sharpe", ascending=False).head(5)
        print("\nЛучшие 5 вариантов по Sharpe (для справки, выбраны задним числом):")
        print(best[["strategy", "n", "k", "pk", "cagr", "sharpe", "dd", "h1", "h2", "trades_y"]]
              .to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    sys.exit(main())
