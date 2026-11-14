#!/usr/bin/env python3
"""Новые идеи (2026-11), ни одна не пробовалась в прошлых лабораториях. Правила заданы до запуска.

  python new_lab.py

Проверка: 2020–23 / 2024–26 и плацебо — тот же сигнал, сдвинутый по кругу на случайный срок (200 раз,
сохраняет частоту и длину сделок). Идея проходит, если Sharpe выше 95-го процентиля плацебо и > 0 в обеих половинах.
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F


def placebo(W, C, Fd, sh, n=200, seed=7):
    rng = np.random.default_rng(seed)
    out = []
    for k in rng.integers(90, len(W) - 90, n):
        Ws = pd.DataFrame(np.roll(W.values, k, axis=0), W.index, W.columns)
        out.append(F.stats(F.run_weights(Ws, C, Fd).loc["2020-03-01":])["sharpe"])
    return np.nanquantile(out, 0.95)


def main():
    C, Fd, V = F.load()
    R = C.pct_change(fill_method=None)
    rows = []

    def test(name, W, ls=False):
        r = F.run_weights(W.reindex(C.index).fillna(0), C, Fd)
        row = F.report(name, r)
        p = placebo(W.reindex(C.index).fillna(0), C, Fd, row["all_sharpe"])
        ok = row["all_sharpe"] > p and row["is_sharpe"] > 0 and row["oos_sharpe"] > 0
        print(f"{'':52s} плацебо 95%: {p:5.2f}  {'ПРОШЛА' if ok else '—'}")
        rows.append(dict(**row, placebo95=p, passed=ok))
        return r

    def btc(on):
        return pd.DataFrame({"BTC": on.astype(float)}).reindex(columns=C.columns).fillna(0)

    F.report("Держать BTC", R.BTC.fillna(0))

    # 1. Ширина рынка: доля из 19 монет выше своей SMA50. BTC пропорционально ширине (0..1)
    alive = C.notna().cumsum() >= 50
    br = ((C > C.rolling(50).mean()) & alive).sum(axis=1) / alive.sum(axis=1).clip(lower=1)
    test("Ширина: BTC × доля монет выше SMA50", btc(br))
    # 1b. Всплеск ширины (Zweig): ширина 10д прыгнула с < 0.3 до > 0.7 → BTC 60 дней
    br10 = br.rolling(5).mean()
    thrust = (br10 > 0.7) & (br10.rolling(15).min() < 0.3)
    test("Всплеск ширины <0.3 → >0.7 за 15д, BTC 60д", btc(thrust.astype(float).rolling(60, min_periods=1).max() > 0))

    # 2. Смена месяца: BTC с последних 2 дней месяца по 3-й день следующего
    d = C.index
    eom = (d + pd.offsets.MonthEnd(0) - d).days <= 1
    tom = pd.Series(eom | (d.day <= 3), d)
    test("Смена месяца: BTC с −2 по +3 день", btc(tom.shift(-1).fillna(False)))

    # 3. Сжатие волатильности: 20д волатильность в нижних 20% за год, затем пробой 20д максимума → BTC 30 дней
    v20 = R.BTC.rolling(20).std()
    squeeze = v20 < v20.rolling(365, min_periods=180).quantile(0.2)
    brk = (C.BTC >= C.BTC.rolling(20).max()) & squeeze.shift(1).rolling(10).max().astype(bool)
    test("Сжатие волатильности + пробой вверх, BTC 30д", btc(brk.astype(float).rolling(30, min_periods=1).max() > 0))

    # 4. Премия за волатильность (Bollerslev): IV² − RV² высокая → рынок платит за страховку → BTC растёт
    iv = pd.read_csv("data/dvol_daily.csv", index_col=0, parse_dates=True).BTC.reindex(C.index).ffill() / 100
    rv = R.BTC.rolling(30).std() * np.sqrt(365)
    vrp = iv ** 2 - rv ** 2
    test("VRP > медианы года → BTC", btc(vrp > vrp.rolling(365, min_periods=180).median()))
    test("VRP < 0 (страх в реализованной) → BTC 14д", btc((vrp < 0).astype(float).rolling(14, min_periods=1).max() > 0))

    # 5. Пара ETH/BTC: z-score лог-отношения 60д, рыночно-нейтрально 0.5/0.5
    lr = np.log(C.ETH / C.BTC)
    z = (lr - lr.rolling(60).mean()) / lr.rolling(60).std()
    pos = pd.Series(np.where(z < -1.5, 1, np.where(z > 1.5, -1, np.nan)), C.index).ffill()
    pos = pos.where(z.abs() > 0.25, 0).fillna(0)  # выход у среднего
    test("Пара ETH/BTC: возврат к среднему z 60д ±1.5",
         pd.DataFrame({"ETH": 0.5 * pos, "BTC": -0.5 * pos}).reindex(columns=C.columns).fillna(0))

    # 6. Моментум альтов только при низкой средней корреляции (разброс = есть кого отбирать)
    cor = R.rolling(30, min_periods=20).corr().groupby(level=0).apply(
        lambda m: m.values[np.triu_indices(len(m), 1)].mean() if m.notna().sum().sum() > 50 else np.nan)
    lowc = cor < cor.rolling(365, min_periods=180).median()
    mom = F.ls_weights(-R.rolling(30, min_periods=20).sum(), C, 4)
    test("Моментум 30д 4/4 только при корреляции ниже медианы", mom.mul(lowc.astype(float), axis=0))

    # 7. Сброс плеча: funding BTC 7д упал больше чем на 0.03%/день за 14 дней и цена выше SMA100 → BTC 20д
    f7 = Fd.BTC.rolling(7).mean()
    reset = (f7.diff(14) < -0.0003) & (C.BTC > C.BTC.rolling(100).mean())
    test("Сброс funding в тренде, BTC 20д", btc(reset.astype(float).rolling(20, min_periods=1).max() > 0))

    # 8. Кросс-секция: лонг монеты с наибольшим падением funding за 7д / шорт с наибольшим ростом
    test("Изменение funding 7д: лонг сброс / шорт разгон", F.ls_weights(Fd.rolling(7).mean().diff(7), C, 4))

    pd.DataFrame(rows).to_csv("improve_results/new_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
