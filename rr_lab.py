#!/usr/bin/env python3
"""Сделки по RR (риск/прибыль) на зонах проторговки и карте ликвидаций. BTC и ETH, по половине капитала.

  python rr_lab.py

Сетап проверяется раз в день по закрытию (только когда нет открытой сделки):
  тренд: цена выше SMA200 дневных закрытий;
  стоп (zone): под ближайшей зоной ниже цены (VAL профиля 30 дней, если он ниже цены, иначе POC) минус 1%;
        (atr): 2 ATR(14) ниже цены; расстояние до стопа от 1.5% до 12%, иначе сделки нет;
  цель: по варианту: 2R, 3R или ближайший крупный кластер ликвидаций шортов выше цены (берётся,
        только если до него не меньше 2R);
  размер: риск RISK капитала половины на сделку (позиция = RISK / расстояние до стопа), не больше 100% половины.
Выход: стоп или цель по 4-часовым свечам (гэп через уровень исполняется по открытию свечи), или через 30 дней.
Комиссия спот 0.1% за сторону. Окно 2021-06 .. сейчас.
"""
import math
import sys

import numpy as np
import pandas as pd

import abtest as A
import zones_lab as Z
from max_lab import load

START = "2021-06-01"
MAX_DAYS = 30


def liq_target(h, l, c, v, price, rng=0.25, bin_=0.005):
    """Цена корзины с наибольшим объёмом неснятых ликвидаций шортов в (price, price·(1+rng)]."""
    nxt_max = np.append(np.maximum.accumulate(h[::-1])[::-1][1:], -np.inf)
    lv, wt = [], []
    for L, w in A.ZONE_LEVS.items():
        q = c * (1 + 1 / L - 0.005)
        ok = (q > nxt_max) & (q > price) & (q <= price * (1 + rng))
        lv.append(q[ok])
        wt.append((v * 0.5 * w)[ok])
    lv, wt = np.concatenate(lv), np.concatenate(wt)
    if not len(lv):
        return None
    b = np.floor(np.log(lv / price) / bin_).astype(int)
    agg = pd.Series(wt).groupby(b).sum()
    k = agg.idxmax()
    return price * math.exp((k + 0.5) * bin_)


def run(coin, target_mode, risk, stop_mode="zone"):
    x = Z.load4h(coin)
    d = load(coin).close
    sma = d.rolling(200).mean()
    dd_ = load(coin)
    tr_ = pd.concat([dd_.high - dd_.low, (dd_.high - dd_.close.shift()).abs(), (dd_.low - dd_.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr_.rolling(14).mean()
    H, Lw, C, O, V = x.high.values, x.low.values, x.close.values, x.open.values, x.volusd.values
    days = d.loc[START:].index
    ends = np.searchsorted(x.index.values, (days + pd.Timedelta(days=1)).values)
    eq, cash, trade, curve, trades = 1.0, 1.0, None, [], []
    last_end = ends[0]
    for day, e in zip(days, ends):
        # сделка живёт по 4ч свечам от прошлого закрытия дня до этого
        if trade:
            for i in range(last_end, e):
                st, tg = trade["stop"], trade["target"]
                px = None
                if Lw[i] <= st:
                    px = min(O[i], st)
                elif H[i] >= tg:
                    px = max(O[i], tg)
                if px is not None:
                    break
            else:
                px = C[e - 1] if (day - trade["day"]).days >= MAX_DAYS else None
            if px is not None:
                cash += trade["qty"] * px * (1 - A.FEE)
                trades.append((trade["qty"] * px * (1 - A.FEE) - trade["cost"]) / trade["risk_usd"])
                trade = None
        last_end = e
        p = C[e - 1]
        if trade is None and pd.notna(sma.get(day)) and p > sma[day] and e > A.ZONE_LIQ_DAYS * 6:
            sp = e - A.ZONE_PROFILE_DAYS * 6
            poc, val, vah = A.volume_profile(H[sp:e], Lw[sp:e], V[sp:e])
            zone = val if val < p else poc if poc < p else None
            if stop_mode == "atr":
                zone = (p - 2 * atr[day]) / 0.99 if pd.notna(atr.get(day)) else None
            if zone:
                stop = zone * 0.99
                dist = 1 - stop / p
                if 0.015 <= dist <= 0.12:
                    if target_mode == "liq":
                        sl = e - A.ZONE_LIQ_DAYS * 6
                        tg = liq_target(H[sl:e], Lw[sl:e], C[sl:e], V[sl:e], p)
                        ok = tg is not None and (tg / p - 1) >= 2 * dist
                    else:
                        tg, ok = p * (1 + target_mode * dist), True
                    if ok:
                        w = min(1.0, risk / dist)
                        notional = w * cash
                        qty = notional / p
                        trade = dict(day=day, stop=stop, target=tg, qty=qty, cost=notional * (1 + A.FEE),
                                     risk_usd=notional * dist)
                        cash -= notional * (1 + A.FEE)
        curve.append(cash + (trade["qty"] * p if trade else 0.0))
    return pd.Series(curve, days), trades


def main():
    rows = []
    variants = [(2, 0.02, "zone"), (3, 0.02, "zone"), ("liq", 0.02, "zone"), (2, 0.05, "zone"), (3, 0.05, "zone"),
                ("liq", 0.05, "zone"), (2, 0.02, "atr"), (3, 0.02, "atr"), (3, 0.05, "atr")]
    m1 = pd.read_csv("improve_results/zones_curves.csv", index_col=0, parse_dates=True)["M1 (текущая система)"] \
        if len(sys.argv) < 2 else None
    print(f"{'стоп':>5s} {'цель':>5s} {'риск':>5s} {'в год':>6s} {'Sharpe':>6s} {'просадка':>8s} {'2021–23':>8s} {'2024–26':>8s} "
          f"{'сделок':>6s} {'выигр.':>6s} {'ср. R':>6s} {'корр M1':>7s}")
    for tm, risk, sm in variants:
        res = [run(k, tm, risk, sm) for k in Z.COINS]
        e = (res[0][0] + res[1][0]) / 2
        tr = res[0][1] + res[1][1]
        c, s, dd = Z.metrics(e)
        c1, c2 = Z.metrics(e.loc[:"2023-12-31"])[0], Z.metrics(e.loc["2024-01-01":])[0]
        corr = e.pct_change().corr(m1.pct_change()) if m1 is not None else np.nan
        print(f"{sm:>5s} {str(tm) + ('R' if tm != 'liq' else ''):>5s} {risk:5.0%} {c:+6.0%} {s:6.2f} {dd:+8.0%} {c1:+8.0%} "
              f"{c2:+8.0%} {len(tr):6d} {np.mean(np.array(tr) > 0):6.0%} {np.mean(tr):+6.2f} {corr:7.2f}")


if __name__ == "__main__":
    sys.exit(main())
