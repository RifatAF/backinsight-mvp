#!/usr/bin/env python3
"""Индикаторы цикла (MVRV, MVRV-Z, Mayer, 200-недельная средняя, Puell, Pi Cycle): помогают ли понять,
где начало и конец бычьего и медвежьего рынка. BTC с 2011, ETH с 2016 (Coin Metrics, бесплатный API).

  python cycle_test.py

1) Будущая доходность через 90/180/365 дней по классическим зонам каждого индикатора.
2) Значения индикаторов на исторических вершинах и днах циклов.
3) Стратегии: R3, R3 с фильтром цикла, накопление по MVRV, их смесь. Данные Coin Metrics сдвинуты на 1 день.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import predictive_lab as pl
from strategy_lab import get_json

FEE = 0.001


def load(asset):
    path = Path("data") / f"{asset}_cm_cycle.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        url = (f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets={asset}"
               f"&metrics=PriceUSD,CapMVRVCur,CapMrktCurUSD,IssTotUSD&frequency=1d&start_time=2010-07-01&page_size=10000")
        rows = []
        while url:
            d = get_json(url)
            rows += d["data"]
            url = d.get("next_page_url")
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df.time).dt.tz_localize(None).dt.floor("D")
        df = df.drop(columns=["asset", "time"]).apply(pd.to_numeric, errors="coerce")
        path.parent.mkdir(exist_ok=True)
        df.to_csv(path)
    df = df.rename(columns={"PriceUSD": "price", "CapMVRVCur": "mvrv", "CapMrktCurUSD": "mcap", "IssTotUSD": "iss"})
    return df.dropna(subset=["price"])


def indicators(df):
    p = df.price
    rc = df.mcap / df.mvrv                                   # реализованная капитализация
    ind = pd.DataFrame(index=df.index)
    ind["MVRV"] = df.mvrv
    ind["MVRV-Z"] = (df.mcap - rc) / df.mcap.expanding(365).std()
    ind["Mayer (цена/SMA200)"] = p / p.rolling(200).mean()
    ind["цена/SMA200 недель"] = p / p.rolling(1400).mean()
    if df.iss.notna().sum() > 1000:
        ind["Puell"] = df.iss / df.iss.rolling(365).mean()
    ind["Pi Cycle (SMA111/2·SMA350)"] = p.rolling(111).mean() / (2 * p.rolling(350).mean())
    return ind.shift(1)   # данные публикуются после закрытия дня


ZONES = {   # классические пороги из аналитики on-chain
    "MVRV": [0, 1, 2, 3, 3.7, 99],
    "MVRV-Z": [-9, 0, 2, 5, 7, 99],
    "Mayer (цена/SMA200)": [0, 0.8, 1.0, 1.5, 2.4, 99],
    "цена/SMA200 недель": [0, 1, 2, 4, 6, 99],
    "Puell": [0, 0.5, 1, 2, 4, 99],
    "Pi Cycle (SMA111/2·SMA350)": [0, 0.5, 0.75, 0.9, 1.0, 99],
}


def zone_table(df, ind):
    lp = np.log(df.price)
    fwd = {h: np.expm1(lp.shift(-h) - lp) for h in (90, 180, 365)}
    for name, edges in ZONES.items():
        if name not in ind:
            continue
        z = pd.cut(ind[name], edges)
        t = pd.DataFrame({"z": z, **{f"f{h}": f for h, f in fwd.items()}}).dropna(subset=["z"])
        g = t.groupby("z", observed=True)
        out = g.agg(дней=("f90", "size"), **{f"ср {h}д": (f"f{h}", "median") for h in (90, 180, 365)},
                    рост365=("f365", lambda x: (x.dropna() > 0).mean()))
        out["эпизодов"] = [((t.z == k) & (t.z.shift() != k)).sum() for k in out.index]
        for c in out:
            if c.startswith("ср"):
                out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:+.0%}")
        out["рост365"] = out["рост365"].map("{:.0%}".format)
        print(f"\n{name}: медианная будущая доходность по зонам")
        print(out.to_string())


def cycle_extremes(p, drop=0.6):
    """Вершины, после которых было падение > drop, и дна между ними."""
    tops, bottoms = [], []
    i, n = 0, len(p)
    v = p.values
    while i < n:
        j = i + np.argmax(v[i:])            # максимум до конца
        # ищем первую вершину, после которой было падение > drop
        peak, k = v[i], i
        found = False
        for t in range(i, n):
            if v[t] > peak:
                peak, k = v[t], t
            if v[t] < peak * (1 - drop):
                tops.append(p.index[k])
                b = k + np.argmin(v[k:min(n, k + 900)])
                bottoms.append(p.index[b])
                i = b + 1
                found = True
                break
        if not found:
            break
    return tops, bottoms


def extremes_table(df, ind):
    tops, bottoms = cycle_extremes(df.price)
    rows = []
    for kind, dates in (("вершина", tops), ("дно", bottoms)):
        for d in dates:
            r = {"тип": kind, "дата": d.strftime("%Y-%m-%d"), "цена": f"{df.price[d]:,.0f}"}
            for c in ind:
                r[c] = f"{ind[c].get(d, np.nan):.2f}"
            rows.append(r)
    print("\nЗначения индикаторов на вершинах (перед падением > 60%) и днах циклов:")
    print(pd.DataFrame(rows).sort_values("дата").to_string(index=False))


def backtest(pos, price, start):
    r = np.log(price).diff().values
    p = np.nan_to_num(np.asarray(pos, dtype=float))
    nxt = np.r_[r[1:], 0]
    lp = np.log1p(p * np.expm1(nxt) - FEE * np.abs(np.diff(np.r_[0, p])))[start:]
    eq = np.exp(np.cumsum(lp))
    yrs = len(lp) / 365
    cagr = eq[-1] ** (1 / yrs) - 1
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(cagr=cagr, sharpe=lp.mean() / (lp.std() + 1e-12) * math.sqrt(365), dd=dd, calmar=cagr / abs(dd),
                in_mkt=p[start:].mean())


def strategies(df, ind, btc_price):
    p = df.price
    btc = btc_price.reindex(p.index).ffill()
    r3 = ((np.log(btc).diff(30) > 0) & (pl.rsi(p) > 50)).astype(float)
    mv = ind["MVRV"]
    # фильтр цикла: перцентиль MVRV по истории до текущего дня (без заглядывания вперёд)
    pct = mv.expanding(365).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    # накопление: держать, пока MVRV не перегрет; выход выше 3.5, обратно ниже 2.5 (гистерезис)
    acc, state = np.zeros(len(mv)), 1.0
    for i, v in enumerate(mv.values):
        if np.isnan(v):
            acc[i] = np.nan
            continue
        if v > 3.5:
            state = 0.0
        elif v < 2.5:
            state = 1.0
        acc[i] = state
    acc = pd.Series(acc, index=mv.index)
    sizing = (1.5 - pct).clip(0, 1)   # 100% при перцентиле < 50%, плавно до 0 при перцентиле 100%
    S = {
        "держать": pd.Series(1.0, index=p.index),
        "R3 (тренд)": r3,
        "R3 + не в лонге при MVRV > 90-го перцентиля": r3 * (pct < 0.9),
        "R3 × размер по перцентилю MVRV": r3 * sizing,
        "MVRV-цикл: держать, выход > 3.5, вход < 2.5": acc,
        "смесь: 50% MVRV-цикл + 50% R3": 0.5 * acc + 0.5 * r3,
        "R3 или (MVRV < 1)": ((r3 > 0) | (mv < 1)).astype(float),
    }
    return S


def main():
    btc = load("btc")
    for asset in ("btc", "eth"):
        df = load(asset) if asset != "btc" else btc
        ind = indicators(df)
        print(f"\n################ {asset.upper()} {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}")
        zone_table(df, ind)
        extremes_table(df, ind)
        start = max(1400 if asset == "btc" else 400, 365)
        rows = []
        for name, pos in strategies(df, ind, btc.price).items():
            rows.append(dict(strategy=name, **backtest(pos.values, df.price, start)))
        t = pd.DataFrame(rows).set_index("strategy")
        print(f"\nСтратегии с {df.index[start]:%Y-%m-%d}, комиссия 0.1%:")
        print(t.to_string(formatters={"cagr": "{:+.0%}".format, "dd": "{:+.0%}".format, "in_mkt": "{:.0%}".format,
                                      "sharpe": "{:.2f}".format, "calmar": "{:.2f}".format}))
        last = ind.dropna(how="all").iloc[-1]
        print(f"\nСейчас ({ind.dropna(how='all').index[-1]:%Y-%m-%d}): " +
              ", ".join(f"{k} {v:.2f}" for k, v in last.items() if not np.isnan(v)))


if __name__ == "__main__":
    sys.exit(main())
