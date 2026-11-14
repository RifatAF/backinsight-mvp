#!/usr/bin/env python3
"""Стратегии на зонах проторговки (профиль объёма) и оценочной карте ликвидаций + простые индикаторы.

  python zones_lab.py            # бэктест всех вариантов, BTC+ETH, 2021-06 .. сейчас

Данные: 4-часовые свечи перпетуалов OKX (data/<COIN>_swap_4h_okx.csv, с 2020).
Профиль объёма: объём свечи в USD поровну по ценовым корзинам 0.25% между её минимумом и максимумом, окно 30 дней.
POC = корзина с наибольшим объёмом, VAH/VAL = границы 70% объёма вокруг POC.
Карта ликвидаций (оценка, без данных открытого интереса): объём каждой 4ч свечи за 60 дней считается новыми
позициями, половина лонги, половина шорты, плечи x10/x25/x50/x100 в долях 40/35/15/10%. Ликвидация лонга
p·(1 − 1/L + 0.5%), шорта p·(1 + 1/L − 0.5%). Уровень снят, если цена после открытия до него дошла.
magnet = доля неснятых ликвидаций в ±10% от цены, лежащих выше (шорты): > 0.5 тянет вверх.
Исполнение раз в день по закрытию дня, спот, комиссия 0.1%, ребаланс при отклонении > 2%.
"""
import math
import sys

import numpy as np
import pandas as pd

import abtest as A
import system as S
from max_lab import load

START = "2021-06-01"
COINS = ["BTC", "ETH"]
BIN = 0.0025
PROFILE_DAYS, LIQ_DAYS, LIQ_RANGE = 30, 60, 0.10
LEVS = {10: 0.40, 25: 0.35, 50: 0.15, 100: 0.10}


def load4h(coin):
    d = pd.read_csv(f"data/{coin}_swap_4h_okx.csv")
    d.index = pd.to_datetime(d.ts, unit="ms")
    return d[["open", "high", "low", "close", "volusd"]]


profile = A.volume_profile


def liq_magnet(h, l, c, v, price):
    return A.liq_magnet(h, l, c, v, price, LIQ_RANGE)[0]


def features(coin):
    x = load4h(coin)
    day_close = x.close.resample("1D").last().dropna()
    days = day_close.loc["2020-12-01":].index[:-1]               # последняя свеча дня может быть незакрытой
    rows = []
    H, Lo, C, V = x.high.values, x.low.values, x.close.values, x.volusd.values
    end_idx = np.searchsorted(x.index.values, (days + pd.Timedelta(days=1)).values)   # свечи строго до конца дня
    for d, e in zip(days, end_idx):
        p = C[e - 1]
        sp, sl = max(0, e - PROFILE_DAYS * 6), max(0, e - LIQ_DAYS * 6)
        poc, val, vah = profile(H[sp:e], Lo[sp:e], V[sp:e])
        mag = liq_magnet(H[sl:e], Lo[sl:e], C[sl:e], V[sl:e], p)
        rows.append(dict(day=d, close=p, poc=poc, val=val, vah=vah, magnet=mag))
    f = pd.DataFrame(rows).set_index("day")
    dc = day_close.reindex(f.index)
    full = day_close
    f["sma50"] = full.rolling(50).mean().reindex(f.index)
    f["sma200"] = full.rolling(200).mean().reindex(f.index)
    f["pct365"] = full.rolling(365, min_periods=200).apply(lambda s: (s[:-1] < s[-1]).mean(), raw=True).reindex(f.index)
    return f


def strategies(f):
    up = (f.close > f.sma200) & (f.sma50 > f.sma200)
    out = {}
    out["MA 50/200"] = up.astype(float)
    out["Перцентиль 365 > 60%"] = (f.pct365 > 0.6).astype(float)

    def hold(enter, exit_):
        s, cur = [], 0.0
        for e, x in zip(enter, exit_):
            cur = 1.0 if e else 0.0 if x else cur
            s.append(cur)
        return pd.Series(s, f.index)
    out["Магнит ликвидаций (в тренде)"] = hold((f.magnet > 0.55) & (f.close > f.sma200),
                                               (f.magnet < 0.45) | (f.close < f.sma200))
    out["Откат в зону стоимости (в тренде)"] = hold(up & (f.close <= f.poc * 1.01), ~up | (f.close < f.val * 0.97))
    out["Пробой зоны проторговки"] = hold((f.close > f.vah) & (f.pct365 > 0.5), f.close < f.poc)
    out["Пробой + магнит"] = hold((f.close > f.vah) & (f.pct365 > 0.5) & (f.magnet > 0.5), f.close < f.poc)
    score = ((f.close > f.sma200).astype(int) + (f.sma50 > f.sma200).astype(int) + (f.pct365 > 0.5).astype(int)
             + (f.close > f.poc).astype(int) + (f.magnet > 0.5).astype(int)) / 5
    out["ZONES: 5 голосов"] = np.select([score >= 0.8, score >= 0.6], [1.0, 0.5], 0.0)
    out["ZONES: 5 голосов"] = pd.Series(out["ZONES: 5 голосов"], f.index)
    return out


def backtest(W, closes, idx):
    """W: {coin: Series весов 0..1}, доля монеты = вес / число монет. Спот, комиссия FEE, порог 2%."""
    rets = closes.pct_change().fillna(0)
    val, cur, eq = 1.0, {}, []
    for day in idx:
        if eq:
            pnl = sum(u * rets.at[day, k] for k, u in cur.items())
            val += pnl
            cur = {k: u * (1 + rets.at[day, k]) for k, u in cur.items()}
        for k in COINS:
            tgt = float(W[k].get(day, 0.0)) / len(COINS) * val
            now = cur.get(k, 0.0)
            if abs(tgt - now) > A.THRESHOLD * val or (tgt == 0 and now):
                val -= abs(tgt - now) * A.FEE
                cur[k] = tgt
        eq.append(val)
    return pd.Series(eq, idx)


def metrics(e):
    r = e.pct_change().dropna()
    yrs = len(r) / 365
    return ((e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1, r.mean() / (r.std() + 1e-12) * math.sqrt(365),
            (e / e.cummax() - 1).min())


def main():
    F = {k: features(k) for k in COINS}
    dfs = {k: load(k) for k in COINS}
    closes = pd.DataFrame({k: v.close for k, v in dfs.items()})
    idx = closes.loc[START:].index.intersection(F["BTC"].index)
    layers = S.market_layers(S.fetch_onchain(), S.Params())
    m1 = {k: S.coin_position(closes[k], closes.BTC, layers, S.Params(mode="A")) for k in COINS}
    variants = {"M1 (текущая система)": m1}
    per = {k: strategies(F[k]) for k in COINS}
    for name in per["BTC"]:
        variants[name] = {k: per[k][name] for k in COINS}
    curves, rows = {}, {}
    for name, W in variants.items():
        e = backtest(W, closes, idx)
        curves[name] = e
        c, s, dd = metrics(e)
        c1, c2 = metrics(e.loc[:"2023-12-31"])[0], metrics(e.loc["2024-01-01":])[0]
        expo = np.mean([W[k].reindex(idx).fillna(0).mean() for k in COINS])
        rows[name] = dict(cagr=c, sharpe=s, dd=dd, c1=c1, c2=c2, exposure=expo)
    base = curves["M1 (текущая система)"].pct_change()
    hold = (closes.loc[idx] / closes.loc[idx].iloc[0]).mean(axis=1)
    curves["держать BTC+ETH"] = hold
    rows["держать BTC+ETH"] = dict(zip(("cagr", "sharpe", "dd"), metrics(hold)), c1=metrics(hold.loc[:"2023-12-31"])[0],
                                   c2=metrics(hold.loc["2024-01-01":])[0], exposure=1.0)
    print(f"{'вариант':38s} {'в год':>6s} {'Sharpe':>6s} {'просадка':>8s} {'2021–23':>8s} {'2024–26':>8s} "
          f"{'в рынке':>7s} {'корр M1':>7s}")
    for name, r in rows.items():
        corr = curves[name].pct_change().corr(base)
        print(f"{name:38s} {r['cagr']:+6.0%} {r['sharpe']:6.2f} {r['dd']:+8.0%} {r['c1']:+8.0%} {r['c2']:+8.0%} "
              f"{r['exposure']:7.0%} {corr:7.2f}")
    if len(sys.argv) > 1:
        pd.DataFrame(curves).to_csv(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
