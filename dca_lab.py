#!/usr/bin/env python3
"""Гибридный DCA: регулярные покупки, объём которых зависит от зоны цены.

  python dca_lab.py

Каждую неделю приходит 1 условный доллар. Варианты:
  DCA: каждую неделю покупаем на весь взнос.
  Зоны Mayer: множитель покупки по Mayer multiple (цена / SMA200 дней): < 0.8 x3, < 1.0 x2, < 1.5 x1, < 2.4 x0.5,
    выше x0. Неистраченное копится в резерве (лежит в USDT без дохода) и тратится в дешёвых зонах.
  Зоны MVRV: то же по MVRV BTC (Coin Metrics): < 1 x3, < 1.5 x2, < 2.5 x1, < 3.5 x0.5, выше x0.
  + продажа: в зоне выше порогов (Mayer > 2.4 или MVRV > 3.5) продаём 5% монет в неделю в резерв.
  Перцентиль: множитель по перцентилю цены за 365 дней: < 20% x2.5, < 50% x1.5, < 80% x1, выше x0.5.
Покупка в зоне x3 = 3 недельных взноса, берутся из резерва (если он есть). Комиссия 0.1%.
Метрики: итог / вложено, годовая доходность на вложенные деньги (IRR), просадка капитала, доля резерва в конце.
"""
import sys

import numpy as np
import pandas as pd

import abtest as A
from levels_lab import load_cb


def irr(flows_dates, flows, final_date, final_value):
    """Годовая доходность денежного потока (взносы с минусом, итог с плюсом), бисекция."""
    t = np.array([(d - flows_dates[0]).days / 365 for d in flows_dates])
    T = (final_date - flows_dates[0]).days / 365
    f = lambda r: sum(-c * (1 + r) ** (T - ti) for c, ti in zip(flows, t)) + final_value
    lo, hi = -0.99, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def run(d, mult_fn, sell_fn=None, start=None):
    close = d.close
    weeks = close.loc[start:].resample("W-MON").first().index
    weeks = [w for w in weeks if w in close.index]
    coins, cash, invested, eq, flows = 0.0, 0.0, 0.0, [], []
    peak, dd = 0.0, 0.0
    for w in weeks:
        p = close.at[w]
        cash += 1.0
        invested += 1.0
        flows.append(1.0)
        if sell_fn and sell_fn(w):
            sell = coins * 0.05
            coins -= sell
            cash += sell * p * (1 - A.FEE)
        m = mult_fn(w)
        spend = min(cash, m)
        if spend > 0:
            coins += spend * (1 - A.FEE) / p
            cash -= spend
        v = coins * p + cash
        eq.append(v / invested)
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    final = coins * close.iloc[-1] + cash
    r = irr(weeks, flows, close.index[-1], final)
    return final / invested, r, dd, cash / final if final else 0


def main():
    oc = pd.read_csv("data/onchain_seed.csv", index_col=0, parse_dates=True)
    mvrv = oc["CapMVRVCur"]
    print(f"{'монета':6s} {'с':10s} {'вариант':34s} {'итог/вложено':>12s} {'IRR':>6s} {'просадка':>8s} {'резерв':>6s}")
    for coin, starts in (("BTC", ["2016-01-01", "2018-01-01", "2021-06-01"]), ("ETH", ["2017-01-01", "2021-06-01"])):
        d = load_cb(coin)
        mayer = d.close / d.close.rolling(200).mean()
        pct = d.close.rolling(365, min_periods=200).apply(lambda s: (s[:-1] < s[-1]).mean(), raw=True)
        mv = (oc[f"{coin.lower()}_mvrv"] if f"{coin.lower()}_mvrv" in oc else mvrv).reindex(d.index).ffill()

        def zone(x, edges, mults):
            if pd.isna(x):
                return 1.0
            for e, m in zip(edges, mults):
                if x < e:
                    return m
            return mults[-1]
        variants = {
            "DCA каждую неделю": (lambda w: 1.0, None),
            "Зоны Mayer": (lambda w: zone(mayer.get(w), [0.8, 1.0, 1.5, 2.4], [3, 2, 1, 0.5, 0]), None),
            "Зоны Mayer + продажа на перегреве": (lambda w: zone(mayer.get(w), [0.8, 1.0, 1.5, 2.4], [3, 2, 1, 0.5, 0]),
                                                  lambda w: mayer.get(w, 0) > 2.4),
            "Зоны MVRV": (lambda w: zone(mv.get(w), [1, 1.5, 2.5, 3.5], [3, 2, 1, 0.5, 0]), None),
            "Зоны MVRV + продажа на перегреве": (lambda w: zone(mv.get(w), [1, 1.5, 2.5, 3.5], [3, 2, 1, 0.5, 0]),
                                                 lambda w: mv.get(w, 0) > 3.5),
            "Перцентиль года": (lambda w: zone(pct.get(w), [0.2, 0.5, 0.8], [2.5, 1.5, 1, 0.5]), None),
        }
        for st in starts:
            for name, (mf, sf) in variants.items():
                x, r, dd, res = run(d, mf, sf, st)
                print(f"{coin:6s} {st:10s} {name:34s} {x:12.2f} {r:+6.0%} {dd:+8.0%} {res:6.0%}")
            print()


if __name__ == "__main__":
    sys.exit(main())
