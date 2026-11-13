#!/usr/bin/env python3
"""Скачать дневные метрики перпов Binance (OI в монетах и в USD, соотношения лонг/шорт) с data.binance.vision.
  python tools_fetch_metrics.py BTC ETH ... -> data/binance/<COIN>_metrics.csv (одна строка на день, срез 00:00 UTC
  следующего дня = последний за день, плюс средние за день по соотношениям)."""
import io, sys, zipfile
from concurrent.futures import ThreadPoolExecutor
import pandas as pd, requests

URL = "https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip"


def one(sym, d):
    for _ in range(3):
        try:
            r = requests.get(URL.format(s=sym, d=d), timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            x = pd.read_csv(zipfile.ZipFile(io.BytesIO(r.content)).open(zipfile.ZipFile(io.BytesIO(r.content)).namelist()[0]))
            if x.empty:
                return None
            last = x.iloc[-1]
            return dict(day=d, oi_coin=last.sum_open_interest, oi_usd=last.sum_open_interest_value,
                        top_ls=x.sum_toptrader_long_short_ratio.mean(), acc_ls=x.count_long_short_ratio.mean(),
                        taker_ls=x.sum_taker_long_short_vol_ratio.mean())
        except Exception:
            continue
    return None


for coin in sys.argv[1:]:
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range("2020-09-01", pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=2))]
    with ThreadPoolExecutor(32) as ex:
        rows = [r for r in ex.map(lambda d: one(f"{coin}USDT", d), days) if r]
    pd.DataFrame(rows).sort_values("day").to_csv(f"data/binance/{coin}_metrics.csv", index=False)
    print(coin, len(rows), rows[0]["day"] if rows else None, flush=True)
