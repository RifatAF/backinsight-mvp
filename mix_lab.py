#!/usr/bin/env python3
"""Чем дополнить FNEG (BTC только при funding 7д < 0). Перпы Binance 2020-03 .. 2026-10, 2020–23 / 2024–26.

  python mix_lab.py

Все варианты заданы до запуска. Базовый FNEG: вес BTC 1 при среднем funding 7д < 0, иначе 0.
Carry: спот BTC + шорт перпа BTC; на связку идёт половина свободного капитала (вторая половина — маржа перпа),
получаем funding (при отрицательном платим), вход/выход 0.1% спот + 0.055% перп.
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F


def carry_returns(Fd, on):
    """Дневная доходность связки на весь капитал, если связка включена (on: Series 0..1 доли капитала)."""
    on = on.reindex(Fd.index).fillna(0)
    held = on.shift(1).fillna(0)
    turn = (on - held).abs()
    return held * 0.5 * Fd.BTC.fillna(0) - turn * 0.5 * (0.001 + 0.00055)


def W_of(C, **w):
    return pd.DataFrame({k: v.reindex(C.index).fillna(0).astype(float) for k, v in w.items()}).reindex(
        columns=C.columns).fillna(0)


def main():
    C, Fd, V = F.load()
    R = C.pct_change(fill_method=None)
    f7 = {k: Fd[k].rolling(7).mean() for k in C}
    neg = (f7["BTC"] < 0).astype(float)
    rows = []
    rep = lambda n, r: rows.append(F.report(n, r))
    rep("Держать BTC", R.BTC.fillna(0))
    base = F.run_weights(W_of(C, BTC=neg), C, Fd)
    rep("FNEG (база)", base)

    # 1. Свободные деньги в carry (связка получает funding, когда он положительный — ровно когда FNEG вне рынка)
    rep("FNEG + carry на свободных деньгах", base + carry_returns(Fd, 1 - neg))
    pos = (f7["BTC"] > 0.0001).astype(float)     # carry только когда funding заметно > 0 (0.01%/день ≈ 3.7% год.)
    rep("FNEG + carry только при funding > 0.01%/день", base + carry_returns(Fd, pos * (1 - neg)))

    # 2. Гистерезис: войти при funding < 0, держать, пока funding 7д не станет > 0.01%/день
    st, cur = [], 0.0
    for v in f7["BTC"]:
        cur = 1.0 if v < 0 else 0.0 if v > 0.0001 else cur
        st.append(cur)
    hyst = pd.Series(st, C.index)
    rep("FNEG с выходом только при funding > 0.01%/день", F.run_weights(W_of(C, BTC=hyst), C, Fd))

    # 3. Плечо при подтверждении трендом: x2, если ещё и цена выше SMA200
    up = (C.BTC > C.BTC.rolling(200).mean()).astype(float)
    rep("FNEG, x2 если цена выше SMA200", F.run_weights(W_of(C, BTC=neg * (1 + up)), C, Fd))
    rep("FNEG x2 всегда", F.run_weights(W_of(C, BTC=neg * 2), C, Fd))

    # 4. Корзина: тот же сигнал на BTC, ETH, BNB, LINK, каждой 1/4
    bask = {k: (f7[k] < 0).astype(float) / 4 for k in ("BTC", "ETH", "BNB", "LINK")}
    rep("FNEG корзина BTC/ETH/BNB/LINK по 1/4", F.run_weights(W_of(C, **bask), C, Fd))
    # 4b. Сигнал рынка: средний funding топ-монет < 0 → BTC
    mkt = pd.concat([f7[k] for k in C], axis=1).median(axis=1)
    rep("BTC при медианном funding 19 монет < 0", F.run_weights(W_of(C, BTC=(mkt < 0).astype(float)), C, Fd))

    # 5. Фильтр ликвидности: только если предложение стейблкоинов за 30д растёт
    oc = pd.read_csv("data/onchain_seed.csv", index_col=0, parse_dates=True)
    sg = (oc.stables.pct_change(30) > 0).astype(float).reindex(C.index).ffill().fillna(0)
    rep("FNEG и стейблкоины растут", F.run_weights(W_of(C, BTC=neg * sg), C, Fd))
    rep("FNEG или стейблкоины растут >2% (x1)", F.run_weights(
        W_of(C, BTC=((neg + (oc.stables.pct_change(30) > 0.02).astype(float).reindex(C.index).ffill().fillna(0)) > 0)
             .astype(float)), C, Fd))

    # 6. Смесь с M1 (текущая система, BTC+ETH, кривая из zones_lab с 2021-06)
    m1 = pd.read_csv("improve_results/zones_curves.csv", index_col=0, parse_dates=True)["M1 (текущая система)"].pct_change()
    m1 = m1.reindex(C.index)
    s = slice("2021-06-02", m1.dropna().index[-1])
    print("\n--- с 2021-06 (где есть кривая M1) ---")
    rep("M1", m1.loc[s].fillna(0))
    rep("FNEG", base.loc[s])
    rep("50% M1 + 50% FNEG", (0.5 * m1 + 0.5 * base).loc[s].fillna(0))
    # FNEG как надстройка: M1 как есть, плюс +1 BTC поверх при funding < 0 (общий вес до 2)
    rep("M1 + надстройка FNEG (+1 BTC при funding < 0)", (m1 + base).loc[s].fillna(0))
    rep("FNEG + carry (с 2021-06)", (base + carry_returns(Fd, 1 - neg)).loc[s])
    print("корреляция M1 и FNEG:", round(m1.loc[s].corr(base.loc[s]), 2))
    pd.DataFrame(rows).to_csv("improve_results/mix_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
