#!/usr/bin/env python3
"""Исторические уровни поддержки и сопротивления: цены, где рынок раньше разворачивался (дневной график).

  python levels_lab.py

Уровень = разворот: максимум или минимум дня, который выше/ниже всех дней в ±N дней (N = 10 и 30).
Разворот становится известен только через N дней (без заглядывания вперёд). Близкие развороты (в пределах 3%)
объединяются, сила уровня = число разворотов в нём (+2 за разворот с N = 30, например пик 2021 года).

1) Проверка «держит ли уровень»: цена подошла к уровню (касание ±1%) после того, как была от него дальше 3%.
   Отскок от поддержки: после касания цена сначала выросла на 8% от уровня, а не упала на 5% ниже него.
   Отбой от сопротивления: сначала упала на 8%, а не выросла на 5% выше. Окно 60 дней.
   Сравнение с ложными уровнями: те же уровни, сдвинутые на ±5..15% (там, где разворотов не было).
2) Стратегии на BTC+ETH (спот, комиссия 0.1%, 2021-06 .. сейчас):
   отскок: в тренде (цена выше SMA200) купить у сильной поддержки, стоп 5% под уровнем, выход у следующего
   сильного уровня выше или по стопу; пробой: закрытие на 3% выше сильного сопротивления, выход при закрытии
   на 5% ниже пробитого уровня.
Данные: дневные свечи Coinbase (BTC с 2015, ETH с 2016) + OKX за последние дни.
"""
import math
import sys

import numpy as np
import pandas as pd

import zones_lab as Z

START = "2021-06-01"
MERGE, TOUCH, AWAY = 0.03, 0.01, 0.03
WIN, LOSE, HORIZON = 0.08, 0.05, 60


def load_cb(coin):
    d = pd.read_csv(f"data/{coin}USD_cb_daily.csv")
    d.index = pd.to_datetime(d.timestamp, unit="ms").dt.normalize()
    o = pd.read_csv(f"data/{coin}USDT_okx_daily.csv")
    o.index = pd.to_datetime(o.timestamp, unit="ms").dt.normalize()
    d = pd.concat([d, o[o.index > d.index[-1]]])[["open", "high", "low", "close"]]
    d = d[~d.index.duplicated()]
    return d[d.index < pd.Timestamp.now().normalize()]


def pivots(d):
    """Список (день подтверждения, цена, вес) разворотов."""
    out = []
    for n, w in ((10, 1), (30, 2)):
        hi = d.high.rolling(2 * n + 1, center=True).max()
        lo = d.low.rolling(2 * n + 1, center=True).min()
        for i in np.where(d.high.values == hi.values)[0]:
            if n <= i < len(d) - n:
                out.append((d.index[i + n], float(d.high.iloc[i]), w))
        for i in np.where(d.low.values == lo.values)[0]:
            if n <= i < len(d) - n:
                out.append((d.index[i + n], float(d.low.iloc[i]), w))
    return sorted(out)


def levels_at(piv, day, min_strength=2):
    """Сильные уровни, известные на день day: кластеры разворотов в пределах MERGE."""
    px = sorted((p, w) for t, p, w in piv if t <= day)
    clusters, cur = [], []
    for p, w in px:
        if cur and p > cur[0][0] * (1 + MERGE):
            clusters.append(cur)
            cur = []
        cur.append((p, w))
    if cur:
        clusters.append(cur)
    lv = []
    for c in clusters:
        s = sum(w for _, w in c)
        if s >= min_strength:
            lv.append((float(np.exp(np.mean([math.log(p) for p, _ in c]))), s))
    return lv


def reaction(d, levels_by_day, kind):
    """Доля успешных реакций у уровней. kind: support / resistance."""
    H, L, C = d.high.values, d.low.values, d.close.values
    res, last_touch = [], {}
    for i in range(1, len(d) - 1):
        for lv, _ in levels_by_day[i]:
            key = round(lv, -1)
            if kind == "support":
                touch = L[i] <= lv * (1 + TOUCH) and C[i - 1] > lv * (1 + AWAY)
            else:
                touch = H[i] >= lv * (1 - TOUCH) and C[i - 1] < lv * (1 - AWAY)
            if not touch or i - last_touch.get(key, -999) < 20:
                continue
            last_touch[key] = i
            ok = None
            for j in range(i + 1, min(i + 1 + HORIZON, len(d))):
                if kind == "support":
                    if L[j] <= lv * (1 - LOSE):
                        ok = False
                        break
                    if H[j] >= lv * (1 + WIN):
                        ok = True
                        break
                else:
                    if H[j] >= lv * (1 + LOSE):
                        ok = False
                        break
                    if L[j] <= lv * (1 - WIN):
                        ok = True
                        break
            if ok is not None:
                res.append(ok)
    return np.mean(res) if res else np.nan, len(res)


def strategy(d, lv_by_day, mode):
    C = d.close.values
    sma = d.close.rolling(200).mean().values
    pos, entry_lv, target, out = 0.0, None, None, []
    for i in range(len(d)):
        lv = sorted(l for l, s in lv_by_day[i])
        p = C[i]
        if pos == 0:
            if mode == "bounce" and i > 200 and p > sma[i]:
                sup = [l for l in lv if p * (1 - 0.03) <= l <= p * (1 + TOUCH) and C[i - 1] > l * (1 + TOUCH)]
                if sup:
                    entry_lv = max(sup)
                    above = [l for l in lv if l > p * 1.05]
                    target = min(above) if above else p * 1.3
                    pos = 1.0
            elif mode == "breakout" and i > 10:
                br = [l for l in lv if p > l * 1.03 and C[i - 10:i].min() < l]
                if br:
                    entry_lv, pos = max(br), 1.0
        else:
            if mode == "bounce" and (p < entry_lv * (1 - LOSE) or p >= target):
                pos = 0.0
            elif mode == "breakout" and p < entry_lv * (1 - LOSE):
                pos = 0.0
        out.append(pos)
    return pd.Series(out, d.index)


def main():
    rng = np.random.default_rng(7)
    curves, W = {}, {"bounce": {}, "breakout": {}}
    print("1) Реакция цены у исторических уровней (окно", HORIZON, "дней, успех +8% до −5%)")
    for coin in ("BTC", "ETH"):
        d = load_cb(coin)
        piv = pivots(d)
        days = d.index
        lv_true = [levels_at(piv, t) for t in days]
        for kind in ("support", "resistance"):
            r, n = reaction(d, lv_true, kind)
            fake = []
            for _ in range(5):
                sh = rng.choice([-1, 1], 1)[0] * rng.uniform(0.05, 0.15)
                fr, fn = reaction(d, [[(l * (1 + sh), s) for l, s in x] for x in lv_true], kind)
                fake.append(fr)
            name = "поддержка" if kind == "support" else "сопротивление"
            print(f"   {coin} {name:14s} настоящие уровни {r:5.0%} (касаний {n:3d})   ложные уровни {np.nanmean(fake):5.0%}")
        for mode in W:
            W[mode][coin] = strategy(d, lv_true, mode)
        cur = levels_at(piv, days[-1])
        p = d.close.iloc[-1]
        near = sorted(cur, key=lambda x: abs(math.log(x[0] / p)))[:8]
        print(f"   {coin} сейчас {p:,.0f}; ближайшие сильные уровни: " +
              ", ".join(f"{l:,.0f} (сила {s})" for l, s in sorted(near)))
    closes = pd.DataFrame({k: load_cb(k).close for k in ("BTC", "ETH")})
    idx = closes.loc[START:].index
    print("\n2) Стратегии, BTC+ETH, 2021-06 .. сейчас")
    base = pd.read_csv("improve_results/zones_curves.csv", index_col=0, parse_dates=True)["M1 (текущая система)"]
    for mode, name in (("bounce", "Отскок от поддержки в тренде"), ("breakout", "Пробой сопротивления")):
        Z.COINS = ["BTC", "ETH"]
        e = Z.backtest(W[mode], closes, idx)
        c, s, dd = Z.metrics(e)
        c1, c2 = Z.metrics(e.loc[:"2023-12-31"])[0], Z.metrics(e.loc["2024-01-01":])[0]
        expo = np.mean([W[mode][k].reindex(idx).mean() for k in W[mode]])
        corr = e.pct_change().corr(base.pct_change())
        print(f"   {name:30s} {c:+5.0%} в год  Sharpe {s:4.2f}  просадка {dd:+4.0%}  2021–23 {c1:+4.0%}  "
              f"2024–26 {c2:+4.0%}  в рынке {expo:3.0%}  корр M1 {corr:4.2f}")


if __name__ == "__main__":
    sys.exit(main())
