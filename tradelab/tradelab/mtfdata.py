"""Stage 6 data: 5-minute bars of liquid USDT-M perpetuals from the Binance archive, with 5-minute open
interest, long/short ratios and taker ratio (archive "metrics", from 2021-12), settled funding, and
hourly Coinbase closes of BTC and ETH (Coinbase premium). Same holdout cut as the rest of the project.

Per coin, a 5m frame indexed by bar open time (UTC):
  open, high, low, close, quote_volume, taker_buy_quote      5m klines
  oi, ls_global, ls_top_acc, taker_ratio                     metrics snapshot taken at time S, placed on the
                                                             bar that opens at S (usable at its close, S + 5m)
  funding, settled, funding_hours                            settlement at T charged to the bar ending at T
"""
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from tradelab import data, features, perpdata

ROOT = data.ROOT / "data" / "mtf"
RAW, HOLD = ROOT / "raw", ROOT / "holdout"
DAILY = "data/futures/um/daily"


def klines_5m(sym, start, pool):
    keys = perpdata._months(f"{perpdata.UM}/klines/{sym}/5m/", start)
    kl = pd.concat(pool.map(perpdata._zip_csv, keys), ignore_index=True)
    out = pd.DataFrame({"open": kl[1].to_numpy(), "high": kl[2].to_numpy(), "low": kl[3].to_numpy(),
                        "close": kl[4].to_numpy(), "quote_volume": kl[7].to_numpy(),
                        "taker_buy_quote": kl[10].to_numpy()}, index=perpdata._ms(kl[0]))
    out = out[~out.index.duplicated()].sort_index()
    return out.reindex(pd.date_range(out.index[0], out.index[-1], freq="5min", tz="UTC"))


def metrics_5m(sym, start, pool):
    keys = [k for k in perpdata._list(f"{DAILY}/metrics/{sym}/") if k.endswith(".zip") and k[-14:-4] >= str(start)[:10]]
    frames = [f for f in pool.map(features._archive_zip, sorted(keys)) if len(f)]
    if not frames:
        return pd.DataFrame()
    m = pd.concat(frames, ignore_index=True)
    t = pd.to_datetime(m["create_time"], utc=True).dt.floor("5min")
    out = pd.DataFrame({"oi": m["sum_open_interest_value"].astype(float).to_numpy(),
                        "ls_global": m["count_long_short_ratio"].astype(float).to_numpy(),
                        "ls_top_acc": m["count_toptrader_long_short_ratio"].astype(float).to_numpy(),
                        "taker_ratio": m["sum_taker_long_short_vol_ratio"].astype(float).to_numpy()}, index=t)
    return out[~out.index.duplicated(keep="last")].sort_index()


def funding_5m(sym, index, pool):
    keys = perpdata._months(f"{perpdata.UM}/fundingRate/{sym}/", index[0])
    out = pd.DataFrame({"funding": 0.0, "settled": False, "funding_hours": np.nan}, index=index)
    if not keys:
        return out
    fr = pd.concat(pool.map(perpdata._zip_csv, keys), ignore_index=True)
    t = perpdata._ms(fr[0]).floor("5min") - pd.Timedelta(minutes=5)
    rate = pd.Series(fr[2].to_numpy(), index=t).groupby(level=0).sum().reindex(index)
    hours = pd.Series(fr[1].to_numpy(), index=t).groupby(level=0).last().reindex(index)
    out["funding"], out["settled"], out["funding_hours"] = rate.fillna(0.0).to_numpy(), rate.notna().to_numpy(), hours.ffill().to_numpy()
    return out


def coinbase_hourly(base, start):
    """Hourly Coinbase USD closes (public, ccxt). Bar h is usable from h + 1h."""
    import ccxt

    ex = data._ca_bundle(ccxt.coinbaseexchange({"enableRateLimit": True}))
    rows, since = [], int(pd.Timestamp(start).timestamp() * 1000)
    now = int(time.time() * 1000)
    while since < now:
        batch = ex.fetch_ohlcv(f"{base}/USD", "1h", since=since, limit=300)
        if not batch:
            since += 300 * 3_600_000
            continue
        rows += batch
        since = batch[-1][0] + 3_600_000
    s = pd.Series([r[4] for r in rows], index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True), name=base)
    return s[~s.index.duplicated()].sort_index()


def build(sym, mcfg, pool):
    k = klines_5m(sym, mcfg["data"]["klines_start"], pool)
    m = metrics_5m(sym, mcfg["data"]["start"], pool)
    out = k.join(m.reindex(k.index)) if len(m) else k.assign(oi=np.nan, ls_global=np.nan, ls_top_acc=np.nan, taker_ratio=np.nan)
    out = out.join(funding_5m(sym, k.index, pool))
    out.index.name = "timestamp"
    return out


def download(cfg, mcfg, conn, threads=48):
    from tradelab import journal

    start = pd.Timestamp(data.dataset_info(conn)["holdout_start"])
    files, report = {}, {}
    with ThreadPoolExecutor(threads) as pool:
        for sym in mcfg["coins"]:
            f = build(sym, mcfg, pool)
            report[sym] = {"bars": int(f["open"].notna().sum()), "oi_from": str(f["oi"].first_valid_index())}
            name = f"{sym}.parquet"
            files[f"raw/{name}"] = data.save(f[f.index < start], name, RAW)[1]
            files[f"holdout/{name}"] = data.save(f[f.index >= start], name, HOLD)[1]
            print(f"{sym}: {report[sym]}", flush=True)
    cb = pd.DataFrame({b: coinbase_hourly(b, mcfg["data"]["start"]) for b in ("BTC", "ETH")})
    cb.index.name = "timestamp"
    files["raw/coinbase_1h.parquet"] = data.save(cb[cb.index < start], "coinbase_1h.parquet", RAW)[1]
    files["holdout/coinbase_1h.parquet"] = data.save(cb[cb.index >= start], "coinbase_1h.parquet", HOLD)[1]
    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    payload = {"mtf_sha256": combined, "coins": report, "holdout_start": str(start)}
    journal.log_event(conn, "dataset_mtf", payload)
    return payload


def load(directory=RAW):
    frames = {p.stem: pd.read_parquet(p) for p in sorted(directory.glob("*USDT.parquet"))}
    cb = directory / "coinbase_1h.parquet"
    return frames, (pd.read_parquet(cb) if cb.exists() else None)
