#!/usr/bin/env python3
"""Свечной анализ: классические паттерны японских свечей. Решение только на закрытии свечи, вход по цене закрытия,
выход через H свечей. 19 перпов Binance, дневные свечи 2020–2026 и 4-часовые BTC/ETH (из часовых).

  python candle_lab.py

Для каждого паттерна: число сигналов, средний результат сделки после издержек (0.15% круг) в сторону паттерна,
то же у случайных свечей той же монеты (плацебо: 1000 перемешиваний дат), доля плацебо хуже паттерна,
и результат отдельно 2020–23 / 2024–26. Паттерн «работает», если лучше 95% плацебо и в плюсе в обеих половинах.
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F

COST = 0.0015


def patterns(o, h, l, c):
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    up, dn = c > o, c < o
    lw = np.minimum(o, c) - l
    uw = h - np.maximum(o, c)
    trend_dn = c.shift(1) < c.shift(6)          # перед свечой 5 свечей падения
    trend_up = c.shift(1) > c.shift(6)
    big = body > body.rolling(20).mean()
    P = {
        "Бычье поглощение (после падения)": (+1, trend_dn & up & dn.shift(1) & (c > o.shift(1)) & (o < c.shift(1))),
        "Медвежье поглощение (после роста)": (-1, trend_up & dn & up.shift(1) & (c < o.shift(1)) & (o > c.shift(1))),
        "Молот (после падения)": (+1, trend_dn & (lw > 2 * body) & (uw < body + 1e-12) & (body / rng > 0.05)),
        "Падающая звезда (после роста)": (-1, trend_up & (uw > 2 * body) & (lw < body + 1e-12) & (body / rng > 0.05)),
        "Пин-бар вниз (длинная нижняя тень > 2/3)": (+1, lw / rng > 0.66),
        "Пин-бар вверх (длинная верхняя тень > 2/3)": (-1, uw / rng > 0.66),
        "Доджи после роста → вниз": (-1, trend_up & (body / rng < 0.1)),
        "Доджи после падения → вверх": (+1, trend_dn & (body / rng < 0.1)),
        "Три белых солдата": (+1, up & up.shift(1) & up.shift(2) & (c > c.shift(1)) & (c.shift(1) > c.shift(2))),
        "Три чёрные вороны": (-1, dn & dn.shift(1) & dn.shift(2) & (c < c.shift(1)) & (c.shift(1) < c.shift(2))),
        "Утренняя звезда": (+1, dn.shift(2) & big.shift(2) & (body.shift(1) < 0.3 * body.shift(2)) & up
                            & (c > (o.shift(2) + c.shift(2)) / 2)),
        "Вечерняя звезда": (-1, up.shift(2) & big.shift(2) & (body.shift(1) < 0.3 * body.shift(2)) & dn
                            & (c < (o.shift(2) + c.shift(2)) / 2)),
        "Пробой внутреннего бара вверх": (+1, (h.shift(1) < h.shift(2)) & (l.shift(1) > l.shift(2)) & (c > h.shift(1))),
        "Пробой внутреннего бара вниз": (-1, (h.shift(1) < h.shift(2)) & (l.shift(1) > l.shift(2)) & (c < l.shift(1))),
        "Большая зелёная свеча (тело > 2× среднего) → вверх": (+1, up & (body > 2 * body.rolling(20).mean())),
        "Большая красная свеча → вниз": (-1, dn & (body > 2 * body.rolling(20).mean())),
    }
    return {k: (s, v.fillna(False)) for k, (s, v) in P.items()}


def collect(frames, H):
    """frames: {монета: DataFrame o,h,l,c}. Возвращает {паттерн: DataFrame(t, монета, доход)} и все доходы для плацебо."""
    out, base = {}, []
    for k, d in frames.items():
        fwd = d.c.shift(-H) / d.c - 1
        base.append(pd.DataFrame({"t": d.index, "coin": k, "fwd": fwd.values}).dropna())
        for name, (side, m) in patterns(d.o, d.h, d.l, d.c).items():
            x = pd.DataFrame({"t": d.index[m.values], "coin": k, "r": side * fwd[m].values - COST, "side": side}).dropna()
            out.setdefault(name, []).append(x)
    return {k: pd.concat(v) for k, v in out.items()}, pd.concat(base)


def report(frames, H, label, split, rng):
    ev, base = collect(frames, H)
    print(f"\n=== {label}, выход через {H} свеч. ===")
    print(f"{'паттерн':52s} {'сигн':>5s} {'сделка':>7s} {'случ.':>7s} {'хуже':>5s} {'20–23':>7s} {'24–26':>7s}")
    rows = []
    by_coin = {k: g.fwd.values for k, g in base.groupby("coin")}
    for name, x in ev.items():
        if len(x) < 30:
            continue
        side = x.side.iloc[0]
        # плацебо: столько же случайных свечей тех же монет, та же сторона
        cnt = x.coin.value_counts()
        pl = np.array([np.mean(np.concatenate([side * rng.choice(by_coin[c], n) for c, n in cnt.items()])) - COST
                       for _ in range(1000)])
        m = x.r.mean()
        a, b = x[x.t < split].r.mean(), x[x.t >= split].r.mean()
        share = (pl < m).mean()
        ok = share > 0.95 and a > 0 and b > 0
        print(f"{name:52s} {len(x):5d} {m:+7.2%} {pl.mean():+7.2%} {share:5.0%} {a:+7.2%} {b:+7.2%} {'РАБОТАЕТ' if ok else ''}")
        rows.append(dict(tf=label, H=H, pattern=name, n=len(x), mean=m, placebo=pl.mean(), beats=share, is_=a, oos=b, ok=ok))
    return rows


def main():
    rng = np.random.default_rng(3)
    split = pd.Timestamp(F.SPLIT)
    daily = {}
    for k in F.COINS:
        d = pd.read_csv(f"data/binance/{k}_1d.csv")
        d.index = pd.to_datetime(d.t, unit="ms")
        daily[k] = d.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})[["o", "h", "l", "c"]]
    h4 = {}
    for k in ("BTC", "ETH"):
        d = pd.read_csv(f"data/binance/{k}_1h.csv")
        d.index = pd.to_datetime(d.t, unit="ms")
        h4[k] = d.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna() \
            .rename(columns={"open": "o", "high": "h", "low": "l", "close": "c"})
    rows = []
    for H in (1, 3, 5):
        rows += report(daily, H, "дневные, 19 монет", split, rng)
    for H in (1, 6):
        rows += report(h4, H, "4 часа, BTC+ETH", split, rng)
    pd.DataFrame(rows).to_csv("improve_results/candle_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
