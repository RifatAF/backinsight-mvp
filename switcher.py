#!/usr/bin/env python3
"""Переключатель стратегий: агрессивный режим в бычьем рынке, осторожный в остальное время, или выбор по недавним
результатам. Сравнение со статичными смесями. 16 монет, портфели, по годам.

  python switcher.py

Режимы (из layered_coins.py): «тренд R3», «тренд × волатильность», «4 слоя».
Переключатели (правила заданы заранее, без подбора):
  A. режим BTC: BTC выше SMA200 и SMA200 выше, чем 30 дней назад -> тренд R3, иначе 4 слоя
  B. режим BTC -> тренд × волатильность, иначе 4 слоя
  C. по результатам: 1-го числа каждого месяца берётся режим с лучшим Sharpe за прошлые 180 дней
  D. смесь 1/3 каждого режима (без переключения)
  E. смесь 50% тренд × волатильность + 50% 4 слоя
"""
import math
import sys

import numpy as np
import pandas as pd

from coins_test import COINS, load as load_cb
from layered_coins import equity, market_layers, metrics, positions, yearly

MODES = ["тренд R3", "тренд × волатильность", "4 слоя"]


def daily_ret(pos, c):
    r = np.log(c.astype(float)).diff().values
    p = np.nan_to_num(pos.values.astype(float))
    nxt = np.r_[r[1:], 0]
    return pd.Series(p * np.expm1(nxt) - 0.001 * np.abs(np.diff(np.r_[0, p])), index=c.index)


def perf_switch(P, c, lookback=180):
    rets = pd.DataFrame({m: daily_ret(P[m], c) for m in MODES})
    choice = pd.Series(index=c.index, dtype=object)
    cur = "4 слоя"
    for i, dt in enumerate(c.index):
        if dt.day == 1 and i >= lookback:
            w = rets.iloc[i - lookback:i]
            cur = (w.mean() / (w.std() + 1e-12)).idxmax()
        choice.iloc[i] = cur
    return pd.Series([P[m].iloc[i] for i, m in enumerate(choice)], index=c.index), choice


def all_variants(c, btc, mk):
    P = positions(c, btc, mk)
    b = btc.reindex(c.index).ffill()
    sma = b.rolling(200).mean()
    bull = (b > sma) & (sma > sma.shift(30))
    V = {m: P[m] for m in ["держать"] + MODES}
    V["A: бык -> тренд R3, иначе 4 слоя"] = P["тренд R3"].where(bull, P["4 слоя"])
    V["B: бык -> тренд×вол, иначе 4 слоя"] = P["тренд × волатильность"].where(bull, P["4 слоя"])
    V["C: лучший за 180 дней"], choice = perf_switch(P, c)
    V["D: смесь 1/3 каждого"] = sum(P[m] for m in MODES) / 3
    V["E: 50% тренд×вол + 50% 4 слоя"] = 0.5 * P["тренд × волатильность"] + 0.5 * P["4 слоя"]
    return V, bull


def main():
    mk = market_layers()
    btc = load_cb("BTC")
    btc.index = btc.index.tz_localize(None)
    curves, rows, bull_share = {}, [], None
    for coin, grp in COINS.items():
        c = load_cb(coin)
        c.index = c.index.tz_localize(None)
        V, bull = all_variants(c, btc, mk)
        valid = pd.concat([V[m] for m in MODES], axis=1).notna().all(axis=1)
        start = max(int(np.argmax(valid.values)), 200)
        if coin == "BTC":
            bull_share = bull.iloc[start:].mean()
        cc = c.iloc[start:]
        for name, pos in V.items():
            eq = equity(pos.iloc[start:], cc)
            curves[(coin, name)] = eq
            rows.append(dict(coin=coin, group=grp, variant=name, **metrics(eq)))
    r = pd.DataFrame(rows)
    order = list(dict.fromkeys(r.variant))
    pct, f2 = "{:+.0%}".format, "{:.2f}".format
    print(f"BTC в бычьем режиме {bull_share:.0%} времени с 2016 года")

    print("\n=== Медиана по 16 монетам ===")
    agg = r.groupby("variant").agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"),
                                   calmar=("calmar", "median")).reindex(order)
    best_static = r[r.variant == "тренд × волатильность"].set_index("coin")
    for v in order:
        x = r[r.variant == v].set_index("coin")
        agg.loc[v, "Calmar лучше тренд×вол"] = f"{(x.calmar > best_static.calmar).mean():.0%}"
    print(agg.to_string(formatters={"cagr": pct, "dd": pct, "sharpe": f2, "calmar": f2}))

    def portfolio(coins, v):
        eqs = pd.concat([curves[(k, v)] for k in coins], axis=1).dropna()
        rets = eqs.pct_change().fillna(0)
        val, hw, out = 1.0, None, []
        for i, (dt, row) in enumerate(rets.iterrows()):
            if i == 0 or dt.day == 1:
                hw = pd.Series(val / len(coins), index=eqs.columns)
            hw = hw * (1 + row)
            val = hw.sum()
            out.append(val)
        return pd.Series(out, index=eqs.index)

    for name, coins in (("BTC + ETH", ["BTC", "ETH"]),
                        ("6 монет с ETF", [k for k, g in COINS.items() if g == "ETF"]),
                        ("все 16 монет", list(COINS))):
        P = {v: portfolio(coins, v) for v in order}
        print(f"\n=== Портфель {name}, с {P['держать'].index[0]:%Y-%m} ===")
        m = pd.DataFrame({v: metrics(e) for v, e in P.items()}).T
        roll = pd.DataFrame({v: e / e.shift(365) - 1 for v, e in P.items()}).dropna()
        m["худшие 12 мес."] = roll.min()
        m["окон 12 мес. в плюсе"] = (roll > 0).mean()
        print(m.to_string(formatters={"cagr": pct, "dd": pct, "sharpe": f2, "calmar": f2, "худшие 12 мес.": pct,
                                      "окон 12 мес. в плюсе": "{:.0%}".format}))
        y = pd.DataFrame({v: yearly(e) for v, e in P.items()})
        short = {v: v.split(":")[0] if ":" in v else v for v in order}
        print("по годам:")
        print(y.rename(columns=short).to_string(float_format=lambda v: f"{v:+.0%}"))


if __name__ == "__main__":
    sys.exit(main())
