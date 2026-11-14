#!/usr/bin/env python3
"""Модельная карта ликвидаций и Fear & Greed. Дневки ETH и BTC.

  python liq_fng.py            # нужен data/<COIN>_daily_full.csv из strategy_lab.py fetch

Карта ликвидаций строится как у агрегаторов (Coinglass и др.), потому что бесплатной истории нет:
прирост OI за день = новые позиции по средней цене дня, делятся на лонги и шорты по long/short ratio,
для плеч 10/25/50/100x считаются цены ликвидации. Уровни, которые цена прошла за день (high/low), сгорают
и считаются фактической ликвидацией; при падении OI и со временем остальные уровни гасятся.
"""
import itertools
import sys

import numpy as np
import pandas as pd

from oi_funding_premium import evaluate, z

LEVERAGE = {10: 0.4, 25: 0.3, 50: 0.2, 100: 0.1}   # доля новых позиций по плечу
MMR = 0.9                                           # ликвидация чуть раньше 1/L из-за поддерживающей маржи


def liquidation_map(df, half_life, window):
    """Возвращает по дням: кластеры шорт-ликвидаций выше цены и лонг-ликвидаций ниже (в пределах window),
    и модельный объём ликвидаций лонгов и шортов за день."""
    grid = np.exp(np.arange(np.log(df.low.min() * 0.3), np.log(df.high.max() * 3), 0.005))
    longs, shorts = np.zeros(len(grid)), np.zeros(len(grid))
    decay = 0.5 ** (1 / half_life)
    out = np.full((len(df), 4), np.nan)
    oi, ls = df.oi.values, df.ls_ratio.values
    hi, lo, cl = df.high.values, df.low.values, df.close.values
    tp = (df.high + df.low + df.close).values / 3
    prev_oi = np.nan
    for i in range(len(df)):
        if np.isnan(oi[i]) or np.isnan(ls[i]):
            continue
        # сгорают уровни, которые цена прошла за день
        hit_l = grid >= lo[i]
        hit_s = grid <= hi[i]
        liq_l, liq_s = longs[hit_l & (grid < tp[i])].sum(), shorts[hit_s & (grid > tp[i])].sum()
        longs[hit_l & (grid < tp[i])] = 0
        shorts[hit_s & (grid > tp[i])] = 0
        longs *= decay
        shorts *= decay
        if not np.isnan(prev_oi):
            d = oi[i] - prev_oi
            if d > 0:
                lf = ls[i] / (1 + ls[i])
                for lev, w in LEVERAGE.items():
                    pl = tp[i] * (1 - MMR / lev)
                    ps = tp[i] * (1 + MMR / lev)
                    longs[np.searchsorted(grid, pl)] += d * w * lf
                    shorts[np.searchsorted(grid, ps)] += d * w * (1 - lf)
            elif d < 0 and (longs.sum() + shorts.sum()) > 0:
                k = max(0.0, 1 + d / (longs.sum() + shorts.sum()))
                longs *= k
                shorts *= k
        prev_oi = oi[i]
        near = (grid > cl[i] * (1 - window)) & (grid < cl[i] * (1 + window))
        out[i] = [shorts[near & (grid > cl[i])].sum(), longs[near & (grid < cl[i])].sum(), liq_l, liq_s]
    return pd.DataFrame(out, index=df.index, columns=["above", "below", "liq_long", "liq_short"])


def signals(df, hl, win, k):
    m = liquidation_map(df, hl, win)
    started = m.above.notna()
    imb = ((m.above - m.below) / (m.above + m.below)).fillna(0).where(started)   # нет кластеров рядом = нейтрально
    flush = (z(np.log1p(m.liq_long), 90).fillna(0) - z(np.log1p(m.liq_short), 90).fillna(0)).where(started)
    # > 0: вымыло лонгов сильнее обычного
    prem, mom = z(df.cb_premium), np.log(df.close).diff(30)
    fng = df.fng
    fng_mom = fng.rolling(7).mean() - fng.rolling(30).mean()
    base = {
        "премия [LO]": (prem > 0).astype(float),
        "моментум 30д [LO]": (mom > 0).astype(float),
        "моментум 30д + премия [LO]": ((mom > 0) & (prem > 0)).astype(float),
    }
    S = dict(base)
    S["карта: магнит (к большему кластеру)"] = np.sign(imb.where(imb.abs() > k / 2, 0))
    S["карта: магнит [LO]"] = (imb > k / 2).astype(float)
    S["карта: против кластера"] = -np.sign(imb.where(imb.abs() > k / 2, 0))
    S["ликвидации: после вымывания лонгов long [LO]"] = (flush.rolling(3).max() > k).astype(float)
    S["ликвидации: после вымывания шортов short"] = -(flush.rolling(3).min() < -k).astype(float)
    S["F&G: выше 50 [LO]"] = (fng > 50).astype(float)
    S["F&G: импульс 7д > 30д [LO]"] = (fng_mom > 0).astype(float)
    S["F&G: страх < 25 покупать [LO]"] = (fng < 25).astype(float)
    S["F&G: жадность > 75 продавать"] = -(fng > 75).astype(float)
    for bname, b in base.items():
        S[f"{bname} + магнит вверх"] = b * (imb > -k / 2)
        S[f"{bname} + без вымывания шортов"] = b * (flush.rolling(3).min() > -k)
        S[f"{bname} + F&G импульс"] = b * (fng_mom > 0)
        S[f"{bname} + F&G не жадность > 75"] = b * (fng < 75)
        S[f"{bname} + F&G > 50"] = b * (fng > 50)
        S[f"{bname} + 2x в страхе < 25"] = b * np.where(fng < 25, 2, 1)
    valid = started & prem.notna() & fng.notna()
    valid &= valid.cumsum() > 90   # прогрев карты
    return {kk: np.where(valid, np.asarray(v, dtype=float), np.nan) for kk, v in S.items()}


def run(coin, fee=0.001):
    df = pd.read_csv(f"data/{coin}_daily_full.csv")
    ret = np.log(df.close).diff().values
    rows = []
    for hl, win, k in itertools.product((15, 45), (0.05, 0.10), (0.5, 1.0)):
        for name, pos in signals(df, hl, win, k).items():
            r = evaluate(pos, ret, fee)
            if r:
                rows.append(dict(strategy=name, **r))
    res = pd.DataFrame(rows)
    valid = ~np.isnan(signals(df, 15, 0.05, 0.5)["премия [LO]"])
    bh = evaluate(np.where(valid, 1.0, np.nan), ret, fee)
    dt = pd.to_datetime(df.timestamp, unit="ms")
    print(f"\n################ {coin}: окно {dt[np.argmax(valid)]:%Y-%m-%d} .. {dt.iloc[-1]:%Y-%m-%d}, комиссия {fee:.2%}")
    print(f"Buy & hold: {bh['cagr']:+.0%} в год, Sharpe {bh['sharpe']:.2f}, просадка {bh['dd']:.0%}, "
          f"1-я половина {bh['h1']:+.0%}, 2-я {bh['h2']:+.0%}")
    agg = res.groupby("strategy").agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"),
                                      h1=("h1", "median"), h2=("h2", "median"), in_mkt=("in_mkt", "median"),
                                      trades_y=("trades_y", "median")).sort_values("sharpe", ascending=False)
    f = agg.copy()
    for c in ("cagr", "dd", "h1", "h2"):
        f[c] = agg[c].map("{:+.0%}".format)
    f["in_mkt"] = agg.in_mkt.map("{:.0%}".format)
    f["sharpe"] = agg.sharpe.map("{:.2f}".format)
    f["trades_y"] = agg.trades_y.map("{:.0f}".format)
    print(f.to_string())
    return agg


if __name__ == "__main__":
    for c in ("ETH", "BTC"):
        run(c)
    sys.exit(0)
