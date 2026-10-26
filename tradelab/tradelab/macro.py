"""Stage 4: market-wide macro series (public Yahoo Finance daily closes), cut at the holdout start.

Columns, daily by the exchange-local trading date D (value = close of D):
  spx     S&P 500 (^GSPC)            ndx     Nasdaq Composite (^IXIC)
  nikkei  Nikkei 225 (^N225)          gold    gold futures (GC=F)
  dxy     US dollar index (DX-Y.NYB)  usdjpy  USD/JPY (JPY=X), yen carry proxy
  us3m    US 13-week T-bill yield (^IRX)
  us10y   US 10-year yield (^TNX)
  vix     equity volatility (^VIX)    move    bond volatility (^MOVE)
Every session of D ends by 00:00 UTC of D+1 (US about 20:00-22:00, Tokyo 06:00, London FX
midnight), so like the other daily features it is
known from 00:00 UTC of D+1. Non-trading days carry the last close forward.
"""
import hashlib
import json
import time
import urllib.request

import pandas as pd

from tradelab import data

TICKERS = {"spx": "^GSPC", "ndx": "^IXIC", "nikkei": "^N225", "gold": "GC=F", "dxy": "DX-Y.NYB",
           "usdjpy": "JPY=X", "us3m": "^IRX", "us10y": "^TNX", "vix": "^VIX", "move": "^MOVE"}
COLUMNS = list(TICKERS)
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1={a}&period2={b}&interval=1d"
START = pd.Timestamp("2015-01-01", tz="UTC")
NAME = "macro.parquet"


def _get(url, tries=4):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def parse(raw):
    """Yahoo chart JSON -> close by exchange-local trading date. Timestamps are session starts in UTC;
    the exchange time zone (with its daylight saving) gives the date: USD/JPY's London day D starts
    at 23:00 UTC of D-1 in summer."""
    res = json.loads(raw)["chart"]["result"][0]
    tz = res["meta"].get("exchangeTimezoneName", "UTC")
    ts = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(tz)
    day = pd.DatetimeIndex(ts.tz_localize(None).floor("D")).tz_localize("UTC")
    s = pd.Series(res["indicators"]["quote"][0]["close"], index=day, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def fetch(ticker, end):
    url = URL.format(t=urllib.request.quote(ticker), a=int(START.timestamp()), b=int(end.timestamp()))
    return parse(_get(url))


def download(cfg, conn):
    """All series up to the last closed UTC day, split at the holdout start; sha256 to the journal."""
    from tradelab import journal

    info = data.dataset_info(conn)
    start = pd.Timestamp(info["holdout_start"])
    end = pd.Timestamp.now(tz="UTC").floor("D")
    m = pd.DataFrame({k: fetch(t, end) for k, t in TICKERS.items()})
    m = m[m.index < end]
    m.index.name = "timestamp"
    files = {"raw/" + NAME: data.save(m[m.index < start], NAME, data.RAW_DIR)[1],
             "holdout/" + NAME: data.save(m[m.index >= start], NAME, data.HOLDOUT_DIR)[1]}
    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    cover = {c: str(m[c].first_valid_index().date()) if m[c].notna().any() else None for c in COLUMNS}
    payload = {"files": files, "macro_sha256": combined, "first_valid": cover, "holdout_start": str(start)}
    journal.log_event(conn, "dataset_macro", payload)
    return payload


def load():
    p = data.RAW_DIR / NAME
    return pd.read_parquet(p) if p.exists() else None
