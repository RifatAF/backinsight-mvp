#!/usr/bin/env python3
"""Слоистая система на 16 монетах и по годам.

  python layered_coins.py

Слои (для всех монет одинаково):
  1. режим цикла: MVRV BTC ниже 90-го перцентиля своей истории (общерыночный);
  2. тренд: BTC за 30 дней > 0 и RSI14 монеты > 50 (правило R3);
  3. ончейн-подтверждение (общерыночное, по BTC): доля «за» из hashprice, Hash Ribbons, роста адресов
     с балансом и роста стейблкоинов; размер ×(0.5 + 0.5·доля);
  4. размер по волатильности монеты: min(1, 50% / годовая волатильность за 30 дней).
Ончейн-сигналы отбирались на первой половине истории BTC (onchain_research.py), для альтов это проверка вне выборки.
Цены Coinbase, комиссия 0.1% за изменение позиции, только long, без плеча.
"""
import math
import sys

import numpy as np
import pandas as pd

import predictive_lab as pl
from coins_test import COINS, load as load_cb
from onchain_research import load as load_cm, onchain_features

FEE = 0.001
CONFIRM = ["адреса с балансом: рост 30д", "hashprice: z", "стейблкоины: рост 30д", "Hash Ribbons: SMA30/SMA60 хешрейта"]


def market_layers():
    cm = load_cm("btc")
    f = onchain_features(cm)
    conf = pd.concat([(f[m] > f[m].rolling(365, min_periods=180).median()).where(f[m].notna()) for m in CONFIRM], axis=1)
    conf = conf.mean(axis=1, skipna=True)
    mv = cm.CapMVRVCur.shift(1)
    pct = mv.expanding(365).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    regime = (pct < 0.9).astype(float).where(pct.notna())
    out = pd.DataFrame({"regime": regime, "conf": conf})
    out.index = out.index.normalize()
    return out


def positions(c, btc, mk):
    btc = btc.reindex(c.index).ffill()
    trend = ((np.log(btc).diff(30) > 0) & (pl.rsi(c) > 50)).astype(float)
    vol = np.log(c).diff().rolling(30).std() * math.sqrt(365)
    vt = (0.5 / vol).clip(upper=1)
    m = mk.reindex(c.index).ffill()
    return {
        "держать": pd.Series(1.0, index=c.index),
        "тренд R3": trend,
        "тренд × волатильность": trend * vt,
        "4 слоя": m.regime * trend * vt * (0.5 + 0.5 * m.conf),
    }


def equity(pos, c):
    r = np.log(c.astype(float)).diff().values
    p = np.nan_to_num(pos.values.astype(float))
    nxt = np.r_[r[1:], 0]
    lp = np.log1p(p * np.expm1(nxt) - FEE * np.abs(np.diff(np.r_[0, p])))
    return pd.Series(np.exp(np.cumsum(lp)), index=c.index)


def metrics(eq):
    r = np.log(eq).diff().dropna()
    yrs = len(r) / 365
    dd = (eq / eq.cummax() - 1).min()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    return dict(cagr=cagr, sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365), dd=dd, calmar=cagr / abs(dd))


def yearly(eq):
    return eq.groupby(eq.index.year).apply(lambda s: s.iloc[-1] / s.iloc[0] - 1)


def main():
    mk = market_layers()
    btc = load_cb("BTC")
    btc.index = btc.index.tz_localize(None)
    curves, rows = {}, []
    for coin, grp in COINS.items():
        c = load_cb(coin)
        c.index = c.index.tz_localize(None)
        P = positions(c, btc, mk)
        valid = pd.concat(P.values(), axis=1).notna().all(axis=1)
        start = max(int(np.argmax(valid.values)), 200)
        c = c.iloc[start:]
        for name, pos in P.items():
            eq = equity(pos.iloc[start:], c)
            curves[(coin, name)] = eq
            rows.append(dict(coin=coin, group=grp, variant=name, since=c.index[0].strftime("%Y-%m"), **metrics(eq)))
    r = pd.DataFrame(rows)
    order = ["держать", "тренд R3", "тренд × волатильность", "4 слоя"]
    pct = "{:+.0%}".format
    f2 = "{:.2f}".format

    print("=== Медиана по 16 монетам ===")
    agg = r.groupby("variant").agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"),
                                   calmar=("calmar", "median")).reindex(order)
    tr = r[r.variant == "тренд R3"].set_index("coin")
    for v in order:
        x = r[r.variant == v].set_index("coin")
        agg.loc[v, "Sharpe лучше тренда"] = f"{(x.sharpe > tr.sharpe).mean():.0%}"
        agg.loc[v, "просадка меньше тренда"] = f"{(x.dd > tr.dd).mean():.0%}"
    print(agg.to_string(formatters={"cagr": pct, "dd": pct, "sharpe": f2, "calmar": f2}))

    print("\n=== По монетам: Sharpe / годовая / просадка ===")
    w = r.pivot_table(index="coin", columns="variant", values=["sharpe", "cagr", "dd"], aggfunc="first")
    t = pd.DataFrame(index=w.index)
    t["с"] = r.drop_duplicates("coin").set_index("coin").since
    t["группа"] = pd.Series(COINS)
    for v in order:
        t[v] = [f"{w.loc[k, ('sharpe', v)]:.2f} / {w.loc[k, ('cagr', v)]:+.0%} / {w.loc[k, ('dd', v)]:+.0%}" for k in w.index]
    print(t.sort_values("группа").to_string())

    print("\n=== По группам (медиана) ===")
    g = r.groupby(["group", "variant"]).agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"))
    print(g.reindex(order, level=1).to_string(formatters={"cagr": pct, "dd": pct, "sharpe": f2}))

    def portfolio(coins, v):
        eqs = pd.concat([curves[(k, v)] for k in coins], axis=1).dropna()
        rets = eqs.pct_change().fillna(0)
        val, hold_w, out = 1.0, None, []
        for i, (dt, row) in enumerate(rets.iterrows()):
            if i == 0 or dt.day == 1:
                hold_w = pd.Series(val / len(coins), index=eqs.columns)
            hold_w = hold_w * (1 + row)
            val = hold_w.sum()
            out.append(val)
        return pd.Series(out, index=eqs.index)

    for name, coins in (("BTC + ETH", ["BTC", "ETH"]),
                        ("6 монет с ETF", [k for k, g in COINS.items() if g == "ETF"]),
                        ("все 16 монет", list(COINS))):
        P = {v: portfolio(coins, v) for v in order}
        print(f"\n=== Портфель {name}, равные доли, ребаланс раз в месяц, с {P['держать'].index[0]:%Y-%m} ===")
        m = pd.DataFrame({v: metrics(e) for v, e in P.items()}).T
        print(m.to_string(formatters={"cagr": pct, "dd": pct, "sharpe": f2, "calmar": f2}))
        y = pd.DataFrame({v: yearly(e) for v, e in P.items()})
        print("по годам:")
        print(y.to_string(formatters={v: pct for v in order}))
        # худшие и лучшие скользящие 365 дней
        roll = pd.DataFrame({v: e / e.shift(365) - 1 for v, e in P.items()}).dropna()
        print("скользящие 12 месяцев: худшие / медиана / доля окон в плюсе")
        print(pd.DataFrame({"худшие": roll.min(), "медиана": roll.median(), "в плюсе": (roll > 0).mean()})
              .to_string(formatters={"худшие": pct, "медиана": pct, "в плюсе": "{:.0%}".format}))


if __name__ == "__main__":
    sys.exit(main())
