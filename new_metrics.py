#!/usr/bin/env python3
"""Новые метрики со скриншотов биржи: базис, OI к капитализации, long/short топ-трейдеров по аккаунтам.
Проверяются отдельно и как фильтры к лучшим базам (премия Coinbase, моментум 30 дней). Дневки ETH и BTC.

  python new_metrics.py            # нужен data/<COIN>_daily_full.csv из strategy_lab.py fetch
"""
import itertools
import sys
import time

import numpy as np
import pandas as pd

import predictive_lab as pl
import strategy_lab as sl
from oi_funding_premium import evaluate, z


def okx_index(coin, since):
    okx = pl.exchange("okx")
    rows, after = [], ""
    while True:
        d = pl.retry(okx.publicGetMarketHistoryIndexCandles, {"instId": f"{coin}-USDT", "bar": "1Dutc", "limit": 100,
                                                             **({"after": after} if after else {})})["data"]
        if not d:
            break
        rows += d
        after = d[-1][0]
        if int(after) < since:
            break
        time.sleep(0.12)
    s = pd.DataFrame(rows).iloc[:, [0, 4]].astype(float)
    return pd.Series(s[4].values, index=sl.day_index(s[0].astype("int64")), name="index_close").sort_index()


def extra(coin, since):
    okx = pl.exchange("okx")
    swap = pl.fetch_ohlcv(okx, f"{coin}/USDT:USDT", "1d", since, 100)
    swap = pd.Series(swap.close.values, index=sl.day_index(swap.timestamp), name="perp_close")
    top_acc = pl.fetch_rubik(okx, "publicGetRubikStatContractsLongShortAccountRatioContractTopTrader",
                             f"{coin}-USDT-SWAP", "1d", ["top_acc_ratio"], since)
    top_acc = pd.Series(top_acc.top_acc_ratio.values, index=sl.day_index(top_acc.timestamp))
    mc = sl.get_json(f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets={coin.lower()}"
                     f"&metrics=CapMrktCurUSD&frequency=1d&start_time=2018-06-01&page_size=10000")["data"]
    mc = pd.Series({pd.Timestamp(x["time"]).floor("D"): float(x["CapMrktCurUSD"]) for x in mc}, name="mcap")
    return pd.concat([swap, okx_index(coin, since), top_acc.rename("top_acc_ratio"), mc], axis=1, sort=True)


def signals(df, k, pk):
    basis = df.perp_close / df.index_close - 1
    bz, bchg = z(basis), basis.diff(7)
    lev = df.oi / df.mcap.shift(1)                     # капитализация публикуется с задержкой
    lz, lchg = z(lev), np.log(lev).diff(7)
    tz = z(np.log(df.top_acc_ratio), 60)
    gap = z(np.log(df.top_acc_ratio) - np.log(df.ls_ratio), 60)   # топы против толпы
    prem = z(df.cb_premium)
    mom = np.log(df.close).diff(30)
    base = {
        "премия [LO]": (prem > pk).astype(float),
        "моментум 30д [LO]": (mom > 0).astype(float),
        "моментум 30д + премия [LO]": ((mom > 0) & (prem > pk)).astype(float),
    }
    metric = {
        "базис z": bz, "базис 7д изм.": bchg, "OI/капитализация z": lz, "OI/капитализация 7д изм.": lchg,
        "топ-трейдеры аккаунты z": tz, "топы против толпы z": gap,
    }
    S = {}
    for name, m in metric.items():
        mm = z(m) if "изм." in name else m
        S[f"{name}: по направлению"] = np.sign(mm.where(mm.abs() > k, 0))
        S[f"{name}: против"] = -np.sign(mm.where(mm.abs() > k, 0))
        S[f"{name}: по направлению [LO]"] = (mm > k).astype(float)
        S[f"{name}: против [LO]"] = (mm < -k).astype(float)
        for bname, b in base.items():
            # фильтр: база торгуется, только если метрика не перегрета вверх (против) или подтверждает (по направлению)
            S[f"{bname} + {name} не перегрет"] = b * (mm < k)
            S[f"{bname} + {name} подтверждает"] = b * (mm > -k)
    out = {}
    for name, v in {**base, **S}.items():
        valid = v.notna() & df.oi.notna() & df.top_acc_ratio.notna() & prem.notna() & bz.notna() & lz.notna()
        out[name] = np.where(valid, v.astype(float), np.nan)
    return out


def run(coin, fee=0.001):
    df = pd.read_csv(f"data/{coin}_daily_full.csv")
    df.index = sl.day_index(df.timestamp)
    since = int(df.timestamp.iloc[0])
    df = df.join(extra(coin, since).drop(columns=[], errors="ignore"), how="left")
    ret = np.log(df.close).diff().values
    rows = []
    for k, pk in itertools.product((0.5, 1.0), (0.0, 0.5)):
        for name, pos in signals(df, k, pk).items():
            r = evaluate(pos, ret, fee)
            if r:
                rows.append(dict(strategy=name, k=k, pk=pk, **r))
    res = pd.DataFrame(rows)
    valid = ~np.isnan(signals(df, 0.5, 0.0)["премия [LO]"])
    bh = evaluate(np.where(valid, 1.0, np.nan), ret, fee)
    first = df.index[np.argmax(valid)]
    print(f"\n################ {coin}: окно {first:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}, комиссия {fee:.2%}")
    print(f"Buy & hold: {bh['cagr']:+.0%} в год, Sharpe {bh['sharpe']:.2f}, просадка {bh['dd']:.0%}, "
          f"1-я половина {bh['h1']:+.0%}, 2-я {bh['h2']:+.0%}")
    agg = res.groupby("strategy").agg(cagr=("cagr", "median"), sharpe=("sharpe", "median"), dd=("dd", "median"),
                                      h1=("h1", "median"), h2=("h2", "median"), in_mkt=("in_mkt", "median"),
                                      trades_y=("trades_y", "median"))
    agg = agg.sort_values("sharpe", ascending=False)
    f = agg.copy()
    for c in ("cagr", "dd", "h1", "h2"):
        f[c] = agg[c].map("{:+.0%}".format)
    f["in_mkt"] = agg.in_mkt.map("{:.0%}".format)
    f["sharpe"] = agg.sharpe.map("{:.2f}".format)
    f["trades_y"] = agg.trades_y.map("{:.0f}".format)
    bases = ["премия [LO]", "моментум 30д [LO]", "моментум 30д + премия [LO]"]
    print("\nБазы:")
    print(f.loc[bases].to_string())
    print("\nТоп-15 по медианному Sharpe (медиана по порогам k и pk):")
    print(f.head(15).to_string())
    alone = f[~f.index.str.contains(r"\+")]
    print("\nНовые метрики сами по себе, лучшие 6:")
    print(alone.drop(bases, errors="ignore").head(6).to_string())
    return agg


if __name__ == "__main__":
    for c in ("ETH", "BTC"):
        run(c)
    sys.exit(0)
