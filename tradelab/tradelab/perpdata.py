"""Stage 5 data: Binance USDT-M perpetuals from the public archive, hourly, every symbol including
delisted ones (no survivorship filter), cut at the same holdout start as the rest of the project.

Per symbol, an hourly frame indexed by bar open time (UTC):
  open, high, low, close, quote_volume, taker_buy_quote   1h klines
  premium      premium index close of the bar (perp vs spot index); premium_open at the bar open
  funding      funding rate settled at the end of the bar (charged to the position held into the
               settlement instant), 0 elsewhere; settled marks those bars; funding_hours the interval
               (8, 4 or 1). Premium index values of exactly 0 (frequent in 2020-2021 files) are gaps.
"""
import hashlib
import io
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from tradelab import data

ROOT = data.ROOT / "data" / "perps"
RAW, HOLD = ROOT / "raw", ROOT / "holdout"
UM = "data/futures/um/monthly"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def _get(url, tries=5):
    for i in range(tries):
        try:
            return data._get(url)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def _list(prefix, delimiter=False):
    out, marker = [], ""
    while True:
        q = f"{data.ARCHIVE_LIST}?prefix={prefix}&marker={marker}" + ("&delimiter=/" if delimiter else "")
        root = ET.fromstring(_get(q))
        tag = "s3:CommonPrefixes/s3:Prefix" if delimiter else "s3:Contents/s3:Key"
        page = [p.text for p in root.findall(tag, NS)]
        out += page
        if root.findtext("s3:IsTruncated", namespaces=NS) != "true" or not page:
            return out
        marker = page[-1]


def symbols():
    """All USDT-margined perpetual symbols ever listed (delivery and settled contracts dropped)."""
    names = [p.rstrip("/").split("/")[-1] for p in _list(f"{UM}/klines/", delimiter=True)]
    return sorted(n for n in names if n.endswith("USDT") and "_" not in n)


def _zip_csv(key):
    blob = _get(f"{data.ARCHIVE}/{key}")
    expected = _get(f"{data.ARCHIVE}/{key}.CHECKSUM").decode().split()[0]
    if hashlib.sha256(blob).hexdigest() != expected:
        raise data.DataUnavailable(f"checksum mismatch: {key}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        f = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(f.iloc[0, 0]).lstrip("-").replace(".", "").isdigit():  # header row in newer files
        f = f.iloc[1:]
    return f.apply(pd.to_numeric, errors="coerce")


def _months(prefix, start):
    keys = [k for k in _list(prefix) if k.endswith(".zip")]
    return [k for k in sorted(keys) if k[-11:-4] >= str(start)[:7]]


def _ms(x):
    x = x.astype("int64")
    return pd.to_datetime(np.where(x > 1e14, x // 1000, x), unit="ms", utc=True)  # some files in microseconds


def build(sym, start, pool):
    k_keys = _months(f"{UM}/klines/{sym}/1h/", start)
    if not k_keys:
        return None
    p_keys = _months(f"{UM}/premiumIndexKlines/{sym}/1h/", start)
    f_keys = _months(f"{UM}/fundingRate/{sym}/", start)
    kl = pd.concat(pool.map(_zip_csv, k_keys), ignore_index=True)
    out = pd.DataFrame({"open": kl[1].to_numpy(), "high": kl[2].to_numpy(), "low": kl[3].to_numpy(),
                        "close": kl[4].to_numpy(), "quote_volume": kl[7].to_numpy(),
                        "taker_buy_quote": kl[10].to_numpy()}, index=_ms(kl[0]))
    out = out[~out.index.duplicated()].sort_index()
    out = out.reindex(pd.date_range(out.index[0], out.index[-1], freq="1h", tz="UTC"))
    out["missing"] = out["open"].isna()
    if p_keys:
        pk = pd.concat(pool.map(_zip_csv, p_keys), ignore_index=True)
        pr = pd.DataFrame({"premium_open": pk[1].to_numpy(), "premium": pk[4].to_numpy()}, index=_ms(pk[0]))
        out = out.join(pr[~pr.index.duplicated()].replace(0.0, np.nan))
    else:
        out["premium_open"] = out["premium"] = np.nan
    out["funding"], out["funding_hours"], out["settled"] = 0.0, np.nan, False
    if f_keys:
        fr = pd.concat(pool.map(_zip_csv, f_keys), ignore_index=True)
        t = _ms(fr[0]).floor("1h")  # settlement instant; the bar ending there is t - 1h
        rate = pd.Series(fr[2].to_numpy(), index=t - pd.Timedelta(hours=1)).groupby(level=0).sum()
        hours = pd.Series(fr[1].to_numpy(), index=t - pd.Timedelta(hours=1)).groupby(level=0).last()
        out["funding"] = rate.reindex(out.index).fillna(0.0).to_numpy()
        out["settled"] = rate.reindex(out.index).notna().to_numpy()
        out["funding_hours"] = hours.reindex(out.index).ffill().to_numpy()
    out.index.name = "timestamp"
    return out


def download(cfg, pcfg, conn, only=None, threads=64, symbols_at_once=8):
    """Every symbol (or `only`), split at the holdout start, sha256 of every file to the journal."""
    from tradelab import journal

    info = data.dataset_info(conn)
    start = pd.Timestamp(info["holdout_start"])
    syms = only or symbols()
    files, report = {}, {"symbols": 0, "empty": [], "missing_hours": 0}

    def one(sym):
        f = build(sym, pcfg["data"]["start"], pool)
        if f is None or f["open"].notna().sum() < 24:
            return sym, None
        name = f"{sym}.parquet"
        raw, hold = f[f.index < start], f[f.index >= start]
        out = {}
        if len(raw):
            out[f"raw/{name}"] = data.save(raw, name, RAW)[1]
        if len(hold):
            out[f"holdout/{name}"] = data.save(hold, name, HOLD)[1]
        return sym, (out, int(f["missing"].sum()))

    with ThreadPoolExecutor(threads) as pool, ThreadPoolExecutor(symbols_at_once) as outer:
        for i, (sym, res) in enumerate(outer.map(one, syms)):
            if res is None:
                report["empty"].append(sym)
            else:
                files.update(res[0])
                report["symbols"] += 1
                report["missing_hours"] += res[1]
            if (i + 1) % 25 == 0:
                print(f"{i + 1}/{len(syms)} symbols", flush=True)
    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    payload = {"perps_sha256": combined, "n_files": len(files), "holdout_start": str(start), **report}
    journal.log_event(conn, "dataset_perps", payload)
    return payload


FIELDS = ["open", "close", "quote_volume", "taker_buy_quote", "premium", "premium_open", "funding", "funding_hours",
          "settled"]


def load_panels(directory=RAW, frames=None):
    """{field: DataFrame hours x symbols} on the union hourly index, float64; NaN where not listed."""
    if frames is None:
        frames = {p.stem: pd.read_parquet(p) for p in sorted(directory.glob("*.parquet"))}
    idx = None
    for f in frames.values():
        idx = f.index if idx is None else idx.union(f.index)
    idx = pd.date_range(idx[0], idx[-1], freq="1h", tz="UTC")
    return {k: pd.DataFrame({s: f[k] for s, f in frames.items()}).reindex(idx) for k in FIELDS}
