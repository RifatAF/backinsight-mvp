#!/usr/bin/env python3
"""Простые трендовые правила на многих монетах: какое правило лучше в среднем и где оно работает.

  python coins_test.py              # Coinbase спот, дневки, только long, комиссия 0.1% за смену позиции

Правила заданы заранее и одинаковы для всех монет, проверка раз в день по закрытию:
  R1  цена выше SMA50
  R2  доходность за 30 дней > 0
  R3  BTC за 30 дней > 0 и RSI14 монеты > 50
  R4  BTC за 30 дней > 0 и монета за 30 дней > 0
  R5  гистограмма недельного MACD растёт
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import predictive_lab as pl
from htf_macd import htf_macd

COINS = {   # группа: есть спотовые ETF в США / крупные институциональные держатели или нет
    "BTC": "ETF", "ETH": "ETF", "SOL": "ETF", "XRP": "ETF", "LTC": "ETF", "DOGE": "ETF",
    "LINK": "нет", "ADA": "нет", "AVAX": "нет", "DOT": "нет", "BCH": "нет", "UNI": "нет",
    "AAVE": "нет", "ATOM": "нет", "XLM": "нет", "NEAR": "нет",
}
FEE = 0.001


def load(coin):
    path = Path("data") / f"{coin}USD_cb_daily.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        cb = pl.exchange("coinbase")
        df = pl.fetch_ohlcv(cb, f"{coin}/USD", "1d", int(pd.Timestamp("2017-01-01", tz="UTC").timestamp() * 1000), 300)
        path.parent.mkdir(exist_ok=True)
        df.to_csv(path, index=False)
    df["dt"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    return df.set_index("dt").close


def rules(c, btc):
    btc = btc.reindex(c.index).ffill()
    btc_up = np.log(btc).diff(30) > 0
    mom = np.log(c).diff(30) > 0
    m = htf_macd(c, "W-SUN")
    return {
        "R1 цена > SMA50": c > c.rolling(50).mean(),
        "R2 моментум 30д": mom,
        "R3 BTC 30д + RSI>50": btc_up & (pl.rsi(c) > 50),
        "R4 BTC 30д + моментум 30д": btc_up & mom,
        "R5 недельный MACD растёт": m.hist_up == 1,
        "Держать": pd.Series(True, index=c.index),
    }


def stats(pos, c, start):
    ret = np.log(c).diff().values
    p = pos.astype(float).values
    nxt = np.r_[ret[1:], 0.0]
    lp = np.log1p(p * np.expm1(nxt) - FEE * np.abs(np.diff(np.r_[0, p])))[start:]
    eq = np.exp(np.cumsum(lp))
    half = len(lp) // 2
    cagr = lambda x: np.exp(x.sum() * 365 / len(x)) - 1
    return dict(cagr=cagr(lp), sharpe=lp.mean() / (lp.std() + 1e-12) * math.sqrt(365),
                dd=(eq / np.maximum.accumulate(eq) - 1).min(), h1=cagr(lp[:half]), h2=cagr(lp[half:]),
                in_mkt=p[start:].mean(), trades_y=np.abs(np.diff(p[start:])).sum() / len(lp) * 365)


def main():
    btc = load("BTC")
    rows = []
    for coin, grp in COINS.items():
        try:
            c = load(coin)
        except Exception as e:
            print(f"{coin}: нет данных ({e})")
            continue
        start = 200   # прогрев индикаторов
        if len(c) < start + 365:
            continue
        for name, pos in rules(c, btc).items():
            rows.append(dict(coin=coin, group=grp, rule=name, since=c.index[start].strftime("%Y-%m"),
                             **stats(pos.fillna(False), c, start)))
    r = pd.DataFrame(rows)
    r.to_csv("results_coins.csv", index=False)
    pct = lambda v: f"{v:+.0%}"

    print("=== Медиана по монетам ===")
    agg = r.groupby("rule").agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"),
                                h1=("h1", "median"), h2=("h2", "median"), in_mkt=("in_mkt", "median"),
                                trades_y=("trades_y", "median"))
    hold = r[r.rule == "Держать"].set_index("coin")
    beat = {}
    for rule in agg.index:
        x = r[r.rule == rule].set_index("coin")
        beat[rule] = f"{(x.sharpe > hold.sharpe).mean():.0%} / {(x.dd > hold.dd).mean():.0%}"
    agg["лучше держания: Sharpe / просадка"] = pd.Series(beat)
    agg = agg.sort_values("sharpe", ascending=False)
    print(agg.to_string(formatters={"cagr": pct, "dd": pct, "h1": pct, "h2": pct, "sharpe": "{:.2f}".format,
                                    "in_mkt": "{:.0%}".format, "trades_y": "{:.0f}".format}))

    best = agg.index[0] if agg.index[0] != "Держать" else agg.index[1]
    print(f"\n=== По монетам: {best} против держания ===")
    b = r[r.rule == best].set_index("coin")
    t = pd.DataFrame({"группа": b.group, "с": b.since, "CAGR": b.cagr.map(pct), "держать": hold.cagr.map(pct),
                      "Sharpe": b.sharpe.map("{:.2f}".format), "Sharpe держ.": hold.sharpe.map("{:.2f}".format),
                      "просадка": b.dd.map(pct), "просадка держ.": hold.dd.map(pct),
                      "2-я половина": b.h2.map(pct), "держ. 2-я": hold.h2.map(pct)})
    print(t.sort_values("группа").to_string())

    print("\n=== ETF/институциональные против остальных (медиана) ===")
    g = r[r.rule.isin([best, "Держать"])].groupby(["group", "rule"]).agg(
        cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"), h2=("h2", "median"), n=("coin", "size"))
    print(g.to_string(formatters={"cagr": pct, "dd": pct, "h2": pct, "sharpe": "{:.2f}".format}))


if __name__ == "__main__":
    sys.exit(main())
