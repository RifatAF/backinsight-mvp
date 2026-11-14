#!/usr/bin/env python3
"""Риск-менеджмент для правила R3 (BTC за 30 дней > 0 и RSI14 монеты > 50): стопы, трейлинг, тейки, размер позиции.

  python risk_test.py      # данные из coins_test.py (data/<COIN>USD_cb_daily.csv)

Исполнение на дневных свечах, консервативно:
- вход и выход по сигналу по цене закрытия дня;
- стоп проверяется по минимуму дня; если открытие уже ниже стопа (гэп), выход по открытию;
- тейк проверяется по максимуму дня; если в один день задеты и стоп, и тейк, считается стоп;
- комиссия 0.1% со стороны, на стопах ещё 0.1% проскальзывания;
- после выбивания стопом новый вход только когда сигнал выключится и включится заново.
"""
import math
import sys

import numpy as np
import pandas as pd

import predictive_lab as pl
from coins_test import COINS, load

FEE, SLIP = 0.001, 0.001

VARIANTS = {
    "база (без стопов)":                       dict(),
    "стоп 8%":                                 dict(stop_pct=0.08),
    "стоп 15%":                                dict(stop_pct=0.15),
    "трейлинг 15% от максимума":               dict(trail_pct=0.15),
    "трейлинг 25% от максимума":               dict(trail_pct=0.25),
    "трейлинг 2 ATR":                          dict(trail_atr=2),
    "трейлинг 3 ATR":                          dict(trail_atr=3),
    "трейлинг 4 ATR":                          dict(trail_atr=4),
    "тейк 30% целиком":                        dict(tp_pct=0.30),
    "50% тейк на +25%, остаток трейлинг 3 ATR": dict(tp_pct=0.25, tp_frac=0.5, trail_atr=3),
    "размер по волатильности (цель 50%/год)":  dict(vol_target=0.5),
    "волатильность + трейлинг 3 ATR":          dict(vol_target=0.5, trail_atr=3),
    "риск 2% на сделку, стоп 2 ATR + трейлинг 3 ATR": dict(risk=0.02, stop_atr=2, trail_atr=3),
}


def atr(df, n=14):
    tr = np.maximum(df.high - df.low, np.maximum((df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()))
    return tr.rolling(n).mean()


def simulate(df, sig, p, start):
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    a = atr(df).values
    vol = np.log(df.close).diff().rolling(30).std().values * math.sqrt(365)
    sig = sig.values
    cash, units = 1.0, 0.0
    entry = peak = stop = tp = 0.0
    tp_done, armed = False, True
    eq = np.full(len(c), np.nan)
    trades = stops = 0
    for t in range(start, len(c)):
        if units > 0:
            # 1) стоп внутри дня
            if stop > 0 and l[t] <= stop:
                px = (o[t] if o[t] < stop else stop) * (1 - SLIP)
                cash += units * px * (1 - FEE)
                units, armed = 0.0, False
                stops += 1
            # 2) тейк внутри дня
            elif tp > 0 and not tp_done and h[t] >= tp:
                px = max(o[t], tp)
                q = units * p.get("tp_frac", 1.0)
                cash += q * px * (1 - FEE)
                units -= q
                tp_done = True
                if units <= 1e-12:
                    units, armed = 0.0, False
        if units > 0:
            # 3) обновление трейлинга по закрытию дня
            peak = max(peak, h[t])
            levels = [stop]
            if "trail_pct" in p:
                levels.append(peak * (1 - p["trail_pct"]))
            if "trail_atr" in p and not np.isnan(a[t]):
                levels.append(peak - p["trail_atr"] * a[t])
            stop = max(levels)
            # 4) выход по сигналу
            if not sig[t]:
                cash += units * c[t] * (1 - FEE)
                units = 0.0
        if not sig[t]:
            armed = True
        # 5) вход по сигналу
        if units == 0 and sig[t] and armed and not np.isnan(a[t]) and not np.isnan(vol[t]):
            equity = cash
            size = 1.0
            if "vol_target" in p:
                size = min(1.0, p["vol_target"] / max(vol[t], 1e-6))
            if "risk" in p:
                size = min(1.0, p["risk"] / (p["stop_atr"] * a[t] / c[t]))
            spend = equity * size
            units = spend * (1 - FEE) / c[t]
            cash -= spend
            entry = peak = c[t]
            stop = 0.0
            if "stop_pct" in p:
                stop = entry * (1 - p["stop_pct"])
            if "stop_atr" in p:
                stop = entry - p["stop_atr"] * a[t]
            if "trail_pct" in p:
                stop = max(stop, entry * (1 - p["trail_pct"]))
            if "trail_atr" in p:
                stop = max(stop, entry - p["trail_atr"] * a[t])
            tp = entry * (1 + p["tp_pct"]) if "tp_pct" in p else 0.0
            tp_done = False
            trades += 1
        eq[t] = cash + units * c[t]
    return pd.Series(eq, index=df.index), trades, stops


def metrics(eq):
    eq = eq.dropna()
    r = np.log(eq).diff().dropna()
    yrs = len(r) / 365
    dd = (eq / eq.cummax() - 1).min()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    return dict(cagr=cagr, sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365), dd=dd,
                calmar=cagr / abs(dd) if dd < 0 else np.nan)


def load_ohlc(coin):
    load(coin)   # гарантирует наличие файла
    df = pd.read_csv(f"data/{coin}USD_cb_daily.csv")
    df.index = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    return df


def main():
    btc = load_ohlc("BTC").close
    curves, rows = {}, []
    for coin in COINS:
        df = load_ohlc(coin)
        c = df.close
        b = btc.reindex(c.index).ffill()
        sig = ((np.log(b).diff(30) > 0) & (pl.rsi(c) > 50)).fillna(False)
        start = 200
        for name, p in VARIANTS.items():
            eq, n, ns = simulate(df, sig, p, start)
            curves[(coin, name)] = eq
            rows.append(dict(coin=coin, group=COINS[coin], variant=name, trades_y=n / (len(df) - start) * 365,
                             stop_share=ns / max(n, 1), **metrics(eq)))
        hold = c.iloc[start:] / c.iloc[start]
        curves[(coin, "держать")] = hold
        rows.append(dict(coin=coin, group=COINS[coin], variant="держать", trades_y=0, stop_share=0, **metrics(hold)))
    r = pd.DataFrame(rows)
    r.to_csv("results_risk.csv", index=False)
    pct = "{:+.0%}".format

    base = r[r.variant == "база (без стопов)"].set_index("coin")
    order = list(VARIANTS) + ["держать"]
    agg = r.groupby("variant").agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"),
                                   calmar=("calmar", "median"), trades_y=("trades_y", "median"),
                                   stop_share=("stop_share", "median")).reindex(order)
    better = {}
    for v in order:
        x = r[r.variant == v].set_index("coin")
        better[v] = f"{(x.calmar > base.calmar).mean():.0%}"
    agg["Calmar лучше базы"] = pd.Series(better)
    print("=== Медиана по 16 монетам (Calmar = годовая доходность / макс. просадка) ===")
    print(agg.to_string(formatters={"cagr": pct, "dd": pct, "sharpe": "{:.2f}".format, "calmar": "{:.2f}".format,
                                    "trades_y": "{:.0f}".format, "stop_share": "{:.0%}".format}))

    # портфели: равные доли, ребаланс раз в месяц
    def portfolio(coins, variant):
        eqs = pd.concat([curves[(k, variant)] for k in coins], axis=1).dropna()
        rets = eqs.pct_change().fillna(0)
        w = pd.Series(1 / len(coins), index=eqs.columns)
        val, out = 1.0, []
        hold_w = w.copy()
        for i, (dt, row) in enumerate(rets.iterrows()):
            if i == 0 or dt.day == 1:
                hold_w = w * val
            hold_w = hold_w * (1 + row)
            val = hold_w.sum()
            out.append(val)
        return pd.Series(out, index=eqs.index)

    print("\n=== Портфели (равные доли, ребаланс раз в месяц) ===")
    for coins in (["BTC", "ETH"], ["BTC", "ETH", "SOL"], [k for k, g in COINS.items() if g == "ETF"]):
        print(f"\n{' + '.join(coins)}")
        prow = []
        for v in order:
            eq = portfolio(coins, v)
            m = metrics(eq)
            yearly = eq.groupby(eq.index.year).apply(lambda s: s.iloc[-1] / s.iloc[0] - 1)
            prow.append(dict(variant=v, since=eq.index[0].strftime("%Y-%m"), **m,
                             worst_year=yearly.min(), last_2y=(eq.iloc[-1] / eq.iloc[-730]) ** 0.5 - 1))
        t = pd.DataFrame(prow).set_index("variant").sort_values("calmar", ascending=False)
        print(t.to_string(formatters={"cagr": pct, "dd": pct, "worst_year": pct, "last_2y": pct,
                                      "sharpe": "{:.2f}".format, "calmar": "{:.2f}".format}))


if __name__ == "__main__":
    sys.exit(main())
