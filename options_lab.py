#!/usr/bin/env python3
"""Опционы вместо спота: при сигнале M1 покупаем месячный колл «по рынку» на BTC/ETH (Deribit).

Цена опциона по Блэку-Шоулзу с подразумеваемой волатильностью из индекса DVOL Deribit (data/dvol_daily.csv),
ставка 0. Издержки: комиссия Deribit 0.03% от базового актива (не больше 12.5% премии) + спред 3% премии на
каждую сделку. Колл закрывается, когда сигнал пропал или до экспирации осталось 7 дней (тогда перекат).
Размер: номинал = L × доля монеты × капитал, L = 1, 2, 3, 5. Остальные деньги лежат в USDT.
"""
import math
import sys

import numpy as np
import pandas as pd

import system as S
from max_lab import load

START = "2021-06-01"
COINS = ["BTC", "ETH"]
T_DAYS, ROLL_DAYS, SPREAD, FEE = 30, 7, 0.03, 0.0003


def bs_call(s, k, t, vol):
    if t <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * n(d1) - k * n(d2)


def main():
    dfs = {k: load(k) for k in COINS}
    C = pd.DataFrame({k: v.close for k, v in dfs.items()})
    iv = pd.read_csv("data/dvol_daily.csv", index_col=0, parse_dates=True).reindex(C.index).ffill() / 100
    layers = S.market_layers(S.fetch_onchain(), S.Params())
    pos = pd.DataFrame({k: S.coin_position(C[k], C.BTC, layers, S.Params(mode="A")) for k in COINS})
    idx = C.loc[START:].index

    def cost(prem, s):
        return min(FEE * s, 0.125 * prem) + SPREAD * prem

    def run(L):
        cash, book, eq = 1.0, {}, []
        for day in idx:
            for k in COINS:
                s, v = C.at[day, k], iv.at[day, k]
                p = pos.at[day, k]
                if k in book:
                    o = book[k]
                    left = (o["exp"] - day).days
                    if p <= 0 or left <= ROLL_DAYS:
                        val = bs_call(s, o["k"], left / 365, v)
                        cash += o["q"] * (val - cost(val, s))
                        del book[k]
                if k not in book and p > 0 and cash > 0:
                    equity = cash + sum(o["q"] * bs_call(C.at[day, j], o["k"], (o["exp"] - day).days / 365,
                                                         iv.at[day, j]) for j, o in book.items())
                    q = L * p / len(COINS) * equity / s
                    prem = bs_call(s, s, T_DAYS / 365, v)
                    q = min(q, cash / (prem + cost(prem, s)))
                    cash -= q * (prem + cost(prem, s))
                    book[k] = {"k": s, "exp": day + pd.Timedelta(days=T_DAYS), "q": q}
            eq.append(cash + sum(o["q"] * bs_call(C.at[day, j], o["k"], (o["exp"] - day).days / 365,
                                                  iv.at[day, j]) for j, o in book.items()))
        return pd.Series(eq, idx)

    def m(e):
        r = e.pct_change().dropna()
        yrs = len(r) / 365
        return ((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1, r.mean() / (r.std() + 1e-12) * math.sqrt(365),
                (e / e.cummax() - 1).min())

    for L in (1, 2, 3, 5):
        e = run(L)
        c, s, dd = m(e)
        c1, c2 = m(e.loc[:"2023-12-31"])[0], m(e.loc["2024-01-01":])[0]
        print(f"Коллы вместо спота, номинал x{L}: {c:+5.0%} в год  Sharpe {s:4.2f}  просадка {dd:+5.0%}  "
              f"2021–23 {c1:+5.0%}  2024–26 {c2:+5.0%}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
