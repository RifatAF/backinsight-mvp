#!/usr/bin/env python3
"""Стратегии только на funding: по толпе (pro), против толпы (contra) и на смене funding. 19 перпов Binance 2020–2026.

  python funding_lab.py

f7 — средний funding за 7 дней (дневная ставка). Пороги — процентили за прошлый год (без заглядывания вперёд).
Три семейства × (BTC отдельно / корзина 19 монет, каждая по своему funding / кросс-секция 4 лонга + 4 шорта).
Издержки и сам funding учтены (лонг платит, шорт получает). Плацебо: сигнал, сдвинутый по кругу (200 раз).
Проходит, если Sharpe выше 95% плацебо и > 0 в 2020–23 и 2024–26.
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F
from new_lab import placebo


def main():
    C, Fd, V = F.load()
    f7 = Fd.rolling(7).mean()
    q = lambda p: f7.rolling(365, min_periods=180).quantile(p)
    z = (f7 - f7.rolling(365, min_periods=180).mean()) / f7.rolling(365, min_periods=180).std()
    ch = f7 - f7.shift(7)                                   # смена funding за неделю
    alive = (C.notna().cumsum() >= 60).astype(float)
    rows = []

    def test(name, W, every=1):
        W = W.reindex(C.index).fillna(0).astype(float)
        r = F.run_weights(W, C, Fd, every)
        row = F.report(name, r)
        p = placebo(W, C, Fd, 0)
        ok = row["all_sharpe"] > p and row["is_sharpe"] > 0 and row["oos_sharpe"] > 0
        print(f"{'':52s} плацебо 95%: {p:5.2f}  {'ПРОШЛА' if ok else '—'}")
        rows.append(dict(**row, placebo95=p, passed=ok))

    def btc(s):
        return pd.DataFrame({"BTC": s.astype(float)}).reindex(columns=C.columns).fillna(0)

    def basket(S):                                          # каждая монета по своему сигналу, 1/N живых
        S = S.astype(float) * alive
        return S.div(alive.sum(axis=1).clip(lower=1), axis=0)

    F.report("Держать BTC", C.BTC.pct_change().fillna(0))
    F.report("Держать 19 монет поровну", C.pct_change(fill_method=None).mean(axis=1).fillna(0))

    print("\n--- ПО ТОЛПЕ: funding высокий = тренд, идём с ним ---")
    test("BTC лонг при funding 7д > 0", btc(f7.BTC > 0))
    test("BTC лонг при funding в верхних 30% года", btc(f7.BTC > q(0.7).BTC))
    test("Корзина: лонг монеты при её funding > 0", basket(f7 > 0))
    test("Кросс-секция: лонг высокий funding / шорт низкий", F.ls_weights(-f7, C, 4), 7)

    print("\n--- ПРОТИВ ТОЛПЫ ---")
    test("BTC лонг при funding < 0, шорт в верхних 10% года", btc((f7.BTC < 0).astype(float) - (f7.BTC > q(0.9).BTC)))
    test("BTC: лонг z < −1, шорт z > 2", btc((z.BTC < -1).astype(float) - (z.BTC > 2)))
    test("Корзина: лонг монеты при её funding < 0", basket(f7 < 0))
    test("Корзина: лонг z < −1, шорт z > 2", basket((z < -1).astype(float) - (z > 2)))
    test("Кросс-секция: лонг низкий funding / шорт высокий", F.ls_weights(f7, C, 4), 7)

    print("\n--- СМЕНА FUNDING (за неделю) ---")
    test("BTC лонг, когда funding растёт", btc(ch.BTC > 0))
    test("BTC лонг, когда funding падает", btc(ch.BTC < 0))
    test("BTC: смена знака − → + (вход), держать 14д",
         btc(((f7.BTC > 0) & (f7.BTC.shift(1) <= 0)).astype(float).rolling(14, min_periods=1).max() > 0))
    test("BTC: смена знака + → − (вход), держать 14д",
         btc(((f7.BTC < 0) & (f7.BTC.shift(1) >= 0)).astype(float).rolling(14, min_periods=1).max() > 0))
    test("Корзина: лонг монеты, у которой funding растёт", basket(ch > 0))
    test("Корзина: лонг монеты, у которой funding падает", basket(ch < 0))
    test("Кросс-секция: лонг рост funding / шорт падение", F.ls_weights(-ch, C, 4), 7)
    pd.DataFrame(rows).to_csv("improve_results/funding_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
