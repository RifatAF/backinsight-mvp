#!/usr/bin/env python3
"""Случайный вход (монетка: лонг или шорт) + разные системы выхода. Классический опыт Ван Тарпа: можно ли
заработать одним управлением выходом. 19 перпов Binance 2020–2026, дневные свечи, стопы исполняются внутри дня
по high/low (при гэпе — по открытию). Издержки 0.075% за сторону (тейкер + проскальзывание) и funding.

  python exit_lab.py

Для каждой системы выхода 50 случайных прогонов (разные монетки). Портфель: 19 монет поровну, по 1/19 капитала.
Контроль: те же выходы, но вход всегда в сторону тренда (цена выше/ниже SMA100) и всегда лонг.
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F

COST = 0.00075
SEEDS = 50


def load():
    O, H, L, C = {}, {}, {}, {}
    for k in F.COINS:
        d = pd.read_csv(f"data/binance/{k}_1d.csv")
        d.index = pd.to_datetime(d.t, unit="ms")
        O[k], H[k], L[k], C[k] = d.open, d.high, d.low, d.close
    O, H, L, C = (pd.DataFrame(x) for x in (O, H, L, C))
    f = {}
    for k in F.COINS:
        x = pd.read_csv(f"data/binance/{k}_funding.csv")
        x.index = pd.to_datetime(x.t, unit="ms").dt.floor("D")
        f[k] = x.rate.groupby(level=0).sum()
    return O, H, L, C, pd.DataFrame(f).reindex(C.index).fillna(0)


def simulate(o, h, l, c, fund, atr, sma20, sma100, lo10, hi10, rule, entry, rng):
    """Один инструмент. Возвращает дневную доходность на капитал, выделенный монете."""
    n = len(c)
    r = np.zeros(n)
    pos, ep, stop, tp, best, age = 0, 0.0, 0.0, 0.0, 0.0, 0
    kind, a, b = rule
    for t in range(101, n):
        if np.isnan(c[t]) or np.isnan(atr[t - 1]):
            continue
        if pos:
            age += 1
            prev = c[t - 1]
            exit_px = None
            # стоп / тейк внутри дня (сначала худший случай — стоп)
            if pos > 0:
                if stop and l[t] <= stop:
                    exit_px = min(o[t], stop)
                elif tp and h[t] >= tp:
                    exit_px = max(o[t], tp)
            else:
                if stop and h[t] >= stop:
                    exit_px = max(o[t], stop)
                elif tp and l[t] <= tp:
                    exit_px = min(o[t], tp)
            px = exit_px if exit_px is not None else c[t]
            r[t] = pos * (px / prev - 1) - pos * fund[t]
            if exit_px is None:   # выходы по закрытию
                if kind == "time" and age >= a:
                    exit_px = c[t]
                elif kind == "sma" and (c[t] - sma20[t]) * pos < 0:
                    exit_px = c[t]
                elif kind == "donch" and ((pos > 0 and c[t] < lo10[t - 1]) or (pos < 0 and c[t] > hi10[t - 1])):
                    exit_px = c[t]
            if exit_px is not None:
                r[t] -= COST
                pos = 0
                continue
            # подтяжка стопа
            best = max(best, c[t]) if pos > 0 else min(best, c[t])
            if kind == "trail":
                s = best - pos * a * atr[t]
                stop = max(stop, s) if pos > 0 else min(stop, s)
            elif kind == "be_trail" and (c[t] - ep) * pos > a * atr[t]:
                s = best - pos * b * atr[t]
                s = max(s, ep) if pos > 0 else min(s, ep)
                stop = max(stop, s) if pos > 0 else min(stop, s)
        else:
            if entry == "random":
                pos = 1 if rng.random() < 0.5 else -1
            elif entry == "long":
                pos = 1
            else:
                pos = 1 if c[t] > sma100[t] else -1
            ep, best, age = c[t], c[t], 0
            r[t] -= COST
            stop, tp = 0.0, 0.0
            if kind in ("sltp", "trail", "be_trail", "sma", "donch", "time"):
                sl_mult = {"sltp": a, "trail": a, "be_trail": 2.0, "sma": 2.0, "donch": 3.0, "time": 0}[kind]
                if sl_mult:
                    stop = ep - pos * sl_mult * atr[t]
            if kind == "sltp":
                tp = ep + pos * b * atr[t]
    return r


RULES = {
    "Время: выход через 5 дней": ("time", 5, 0),
    "Время: выход через 20 дней": ("time", 20, 0),
    "Стоп 2 ATR / тейк 2 ATR (1:1)": ("sltp", 2, 2),
    "Стоп 1 ATR / тейк 3 ATR (1:3)": ("sltp", 1, 3),
    "Стоп 2 ATR / тейк 6 ATR (1:3)": ("sltp", 2, 6),
    "Трейлинг 3 ATR (Chandelier)": ("trail", 3, 0),
    "Трейлинг 5 ATR": ("trail", 5, 0),
    "Безубыток после +1 ATR, затем трейлинг 2 ATR": ("be_trail", 1, 2),
    "Выход по SMA20 + стоп 2 ATR": ("sma", 0, 0),
    "Выход по 10-дневному канале + стоп 3 ATR": ("donch", 0, 0),
}


def main():
    O, H, L, C, Fd = load()
    tr = pd.concat([H - L, (H - C.shift()).abs(), (L - C.shift()).abs()]).groupby(level=0).max()
    ATR = tr.rolling(14).mean()
    S20, S100 = C.rolling(20).mean(), C.rolling(100).mean()
    LO, HI = L.rolling(10).min(), H.rolling(10).max()
    arr = {k: [x[k].values for x in (O, H, L, C, Fd, ATR, S20, S100, LO, HI)] for k in F.COINS}
    split = C.index.get_loc(pd.Timestamp(F.SPLIT))
    sh = lambda x: x.mean() / (x.std() + 1e-12) * np.sqrt(365)
    rows = []
    print(f"{'система выхода':46s} {'случайный вход: Sharpe ср [5%; 95%]':>36s} {'доля >0':>8s} {'год ср':>7s} | "
          f"{'по тренду':>9s} {'всегда лонг':>11s}")
    for name, rule in RULES.items():
        res = []
        for s in range(SEEDS):
            rng = np.random.default_rng(s)
            port = np.mean([simulate(*arr[k], rule, "random", rng) for k in F.COINS], axis=0)[101:]
            res.append((sh(port), port.mean() * 365, sh(port[:split - 101]), sh(port[split - 101:])))
        res = np.array(res)
        ctrl = {e: np.mean([simulate(*arr[k], rule, e, None) for k in F.COINS], axis=0)[101:] for e in ("trend", "long")}
        print(f"{name:46s} {res[:, 0].mean():6.2f} [{np.quantile(res[:, 0], .05):5.2f};{np.quantile(res[:, 0], .95):5.2f}]"
              f"{'':14s} {(res[:, 0] > 0).mean():7.0%} {res[:, 1].mean():+7.1%} | {sh(ctrl['trend']):9.2f} {sh(ctrl['long']):11.2f}")
        rows.append(dict(rule=name, sharpe_mean=res[:, 0].mean(), q05=np.quantile(res[:, 0], .05),
                         q95=np.quantile(res[:, 0], .95), share_pos=(res[:, 0] > 0).mean(), ret_mean=res[:, 1].mean(),
                         is_mean=res[:, 2].mean(), oos_mean=res[:, 3].mean(),
                         trend=sh(ctrl["trend"]), long=sh(ctrl["long"])))
    pd.DataFrame(rows).to_csv("improve_results/exit_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
