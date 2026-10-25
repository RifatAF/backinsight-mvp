"""Derivatives and sentiment features (stage 3). Public sources only, cut at the same holdout start.

Per coin, a daily frame indexed by UTC day D whose values are known at the end of D:
  funding_day   sum of perpetual funding rates in D (Binance archive)
  oi_value      open interest in USDT at the last 5-min snapshot of D (Binance metrics archive, from 2021-12)
  top_ls_acc    top traders long/short account ratio, last snapshot of D
  top_ls_pos    top traders long/short position ratio, last snapshot of D
  global_ls     all accounts long/short ratio, last snapshot of D
  taker_ratio   taker buy/sell volume ratio, mean of D
  basis         perpetual premium index close of D (Binance premiumIndexKlines)
  cb_close      Coinbase USD close of D (own pair; NaN if not listed). The premium
                cb_close / Binance close - 1 is computed later from the spot bars, so building
                features never reads holdout prices
  fng           Crypto Fear & Greed index (alternative.me), market-wide
Liquidation history has no free source (CoinGlass needs a paid key); strategies use an
open-interest-flush proxy built from oi_value and price instead.
"""
import hashlib
import io
import json
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from tradelab import data

COLUMNS = ["funding_day", "oi_value", "top_ls_acc", "top_ls_pos", "global_ls", "taker_ratio",
           "basis", "cb_close", "fng"]


def _get(url, tries=4):
    for i in range(tries):
        try:
            return data._get(url)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def _archive_zip(key):
    blob = _get(f"{data.ARCHIVE}/{key}")
    expected = _get(f"{data.ARCHIVE}/{key}.CHECKSUM").decode().split()[0]
    if hashlib.sha256(blob).hexdigest() != expected:
        raise data.DataUnavailable(f"checksum mismatch: {key}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return pd.read_csv(z.open(z.namelist()[0]))


def _archive_frames(prefix, threads=32):
    keys = sorted(k for k in data._archive_keys(prefix) if k.endswith(".zip"))
    with ThreadPoolExecutor(threads) as pool:
        return list(pool.map(_archive_zip, keys))


def metrics_daily(market_id):
    frames = _archive_frames(f"data/futures/um/daily/metrics/{market_id}/")
    if not frames:
        return pd.DataFrame()
    m = pd.concat(frames, ignore_index=True)
    m["create_time"] = pd.to_datetime(m["create_time"], utc=True)
    m = m.sort_values("create_time").drop_duplicates("create_time")
    day = m["create_time"].dt.floor("D")
    last = m.groupby(day).last()
    return pd.DataFrame({"oi_value": last["sum_open_interest_value"].astype(float),
                         "top_ls_acc": last["count_toptrader_long_short_ratio"].astype(float),
                         "top_ls_pos": last["sum_toptrader_long_short_ratio"].astype(float),
                         "global_ls": last["count_long_short_ratio"].astype(float),
                         "taker_ratio": m.groupby(day)["sum_taker_long_short_vol_ratio"].mean().astype(float)})


def basis_daily(market_id):
    frames = _archive_frames(f"data/futures/um/monthly/premiumIndexKlines/{market_id}/1d/")
    if not frames:
        return pd.Series(dtype=float, name="basis")
    # older monthly files have no header row, newer ones do: read all positionally
    k = pd.concat([f.set_axis(range(f.shape[1]), axis=1) if str(f.columns[0]).startswith("open")
                   else pd.concat([f.columns.to_frame().T, f]).set_axis(range(f.shape[1]), axis=1)
                   for f in frames], ignore_index=True)
    t = pd.to_numeric(k.iloc[:, 0], errors="coerce")
    ok = t.notna()
    t = t[ok].astype("int64")
    t = np.where(t > 1e14, t // 1000, t)  # some archives use microseconds
    s = pd.Series(pd.to_numeric(k.iloc[:, 4][ok]).to_numpy(dtype=float),
                  index=pd.to_datetime(t, unit="ms", utc=True).floor("D"), name="basis")
    return s[~s.index.duplicated()].sort_index()


def funding_daily(market_id):
    rows = data.fetch_funding_archive(market_id)
    if not rows:
        return pd.Series(dtype=float, name="funding_day")
    s = pd.Series([r for _, r in rows], index=pd.to_datetime([t for t, _ in rows], unit="ms", utc=True))
    return s.groupby(s.index.floor("D")).sum().rename("funding_day")


def coinbase_close(base):
    """Daily USD closes from Coinbase via ccxt (public); empty if the pair is not listed."""
    import ccxt

    ex = data._ca_bundle(ccxt.coinbaseexchange({"enableRateLimit": True}))
    sym = f"{base}/USD"
    try:
        ex.load_markets()
        if sym not in ex.markets:
            return pd.Series(dtype=float)
    except ccxt.BaseError:
        return pd.Series(dtype=float)
    rows, since = [], int(pd.Timestamp("2016-01-01", tz="UTC").timestamp() * 1000)
    now = int(time.time() * 1000)
    while since < now:
        batch = ex.fetch_ohlcv(sym, "1d", since=since, limit=300)
        if not batch:
            since += 300 * 86_400_000
            continue
        rows += batch
        since = batch[-1][0] + 86_400_000
    s = pd.Series([r[4] for r in rows], index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True))
    s = s[~s.index.duplicated()].sort_index()
    return s[s.index < pd.Timestamp(now, unit="ms", tz="UTC").floor("D")]  # closed days only


def fear_greed():
    raw = json.loads(_get("https://api.alternative.me/fng/?limit=0&format=json"))["data"]
    s = pd.Series([float(r["value"]) for r in raw],
                  index=pd.to_datetime([int(r["timestamp"]) for r in raw], unit="s", utc=True).floor("D"))
    return s[~s.index.duplicated()].sort_index().rename("fng")


def build(symbol, index, fng):
    mid = symbol.replace("/", "")
    out = pd.DataFrame(index=index)
    out["funding_day"] = funding_daily(mid)
    out = out.join(metrics_daily(mid))
    out["basis"] = basis_daily(mid)
    out["cb_close"] = coinbase_close(symbol.split("/")[0])
    out["fng"] = fng
    out.index.name = "timestamp"
    return out[COLUMNS]


def download(cfg, conn):
    """Features for ETH, BTC and the basket on a daily calendar up to the last closed day;
    the same holdout cut as the price data; sha256 to the journal."""
    from tradelab import journal

    info = data.dataset_info(conn)
    start = pd.Timestamp(info["holdout_start"])
    symbols = list(dict.fromkeys(cfg["data"]["symbols_main"] + info["basket"]))
    end = start + pd.Timedelta(days=cfg["data"]["holdout_days"] - 1)
    fng = fear_greed()
    files, cover = {}, {}
    for sym in symbols:
        first = data.load_raw(sym).index[0]
        f = build(sym, pd.date_range(first, end, freq="D", tz="UTC"), fng)
        name = sym.replace("/", "_") + "_features.parquet"
        files[f"raw/{name}"] = data.save(f[f.index < start], name, data.RAW_DIR)[1]
        files[f"holdout/{name}"] = data.save(f[f.index >= start], name, data.HOLDOUT_DIR)[1]
        cover[sym] = {c: (str(f[c].first_valid_index().date()) if f[c].notna().any() else None) for c in COLUMNS}
    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    payload = {"files": files, "features_sha256": combined, "first_valid": cover, "holdout_start": str(start)}
    journal.log_event(conn, "dataset_features", payload)
    return payload


def load(symbol):
    p = data.RAW_DIR / (symbol.replace("/", "_") + "_features.parquet")
    return pd.read_parquet(p) if p.exists() else None


def align(daily, bars, tf):
    """Daily features (known at the end of day D) onto bars: each bar sees the last day that closed by its close."""
    if daily is None:
        return pd.DataFrame(np.nan, index=bars.index, columns=COLUMNS)
    step = pd.Timedelta(milliseconds=data.TF_MS[tf])
    shifted = daily.copy()
    shifted.index = shifted.index + pd.Timedelta(days=1)  # available at 00:00 of D+1
    close_t = bars.index + step
    out = shifted.reindex(shifted.index.union(close_t)).ffill().reindex(close_t)
    out.index = bars.index
    return out
