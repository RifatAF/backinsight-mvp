"""Public market data: Binance spot OHLCV via ccxt, perpetual funding, basket, holdout cut.

Only public endpoints. No keys, no orders.
"""
import hashlib
import io
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
HOLDOUT_DIR = ROOT / "data" / "holdout"

MIRROR_SPOT = "https://data-api.binance.vision/api/v3"
ARCHIVE = "https://data.binance.vision"
ARCHIVE_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

TF_MS = {"1d": 86_400_000, "6h": 21_600_000}
BIG_MOVE = 0.50
KLINE_LIMIT = 1000

STABLE_OR_WRAPPED = {
    "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "PAX", "UST", "USTC", "USDE", "USD1",
    "BFUSD", "RLUSD", "XUSD", "AEUR", "EUR", "EURI", "GBP", "AUD", "TRY", "BRL",
    "PAXG", "WBTC", "WBETH", "BETH", "SUSD",
}


class DataUnavailable(Exception):
    pass


CSV_HINT = ("Биржа недоступна. Пришлите CSV по каждому символу со столбцами "
            "timestamp,open,high,low,close,volume,quote_volume (UTC, дневные свечи).")


def _ca_bundle(ex):
    # ccxt passes `verify and validateServerSsl` to requests; a CA path set here reaches requests
    ca = os.environ.get("REQUESTS_CA_BUNDLE")
    if ca:
        ex.session.verify = ca
        ex.validateServerSsl = ca
    return ex


def spot_exchange(cfg):
    """ccxt Binance spot. Falls back to Binance's public market-data mirror on 451 / network error."""
    import ccxt

    errors = []
    for url in (None, MIRROR_SPOT):
        ex = _ca_bundle(getattr(ccxt, cfg["data"]["exchange"])(
            {"enableRateLimit": True, "options": {"fetchMarkets": {"types": ["spot"]}}}))
        if url:
            ex.urls["api"]["public"] = url
        try:
            ex.load_markets()
            return ex
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
            errors.append(f"{url or 'default'}: {type(e).__name__}")
    raise DataUnavailable(CSV_HINT + " Ошибки: " + "; ".join(errors))


def fetch_klines(ex, market_id, tf, now_ms=None):
    """All closed klines from the earliest date, paginated. Returns a raw frame."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rows, start = [], 0
    while True:
        batch = ex.publicGetKlines(
            {"symbol": market_id, "interval": tf, "startTime": start, "limit": KLINE_LIMIT})
        if not batch:
            break
        rows.extend(batch)
        start = int(batch[-1][0]) + TF_MS[tf]
        if len(batch) < KLINE_LIMIT or start > now_ms:
            break
    rows = [r for r in rows if int(r[6]) < now_ms]  # closed bars only
    df = pd.DataFrame(
        {"timestamp": pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True),
         "open": [float(r[1]) for r in rows], "high": [float(r[2]) for r in rows],
         "low": [float(r[3]) for r in rows], "close": [float(r[4]) for r in rows],
         "volume": [float(r[5]) for r in rows], "quote_volume": [float(r[7]) for r in rows]})
    return df


def check(df, tf):
    """Sort, drop duplicate timestamps, flag |r| > 50%, report missing bars. Index is UTC."""
    df = df.copy()
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    if df.index.tz is None or str(df.index.tz) != "UTC":
        raise ValueError("index must be UTC")
    df = df.sort_index()
    dup = int(df.index.duplicated().sum())
    df = df[~df.index.duplicated(keep="first")]
    full = pd.date_range(df.index[0], df.index[-1], freq=pd.Timedelta(milliseconds=TF_MS[tf]))
    missing = full.difference(df.index)
    r = df["close"].pct_change()
    df["flag_big_move"] = (r.abs() > BIG_MOVE).fillna(False)
    df.index.name = "timestamp"
    report = {"bars": len(df), "start": str(df.index[0]), "end": str(df.index[-1]),
              "duplicates": dup, "missing": [str(t) for t in missing],
              "big_moves": [str(t) for t in df.index[df["flag_big_move"]]]}
    return df, report


def holdout_start(last_day, holdout_days):
    """Holdout is the last `holdout_days` daily bars ending at `last_day` inclusive."""
    return pd.Timestamp(last_day).normalize() - pd.Timedelta(days=holdout_days - 1)


def split(df, start):
    return df[df.index < start], df[df.index >= start]


# ---------- funding ----------

def _get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def _archive_keys(prefix):
    keys, marker = [], ""
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    while True:
        root = ET.fromstring(_get(f"{ARCHIVE_LIST}?prefix={prefix}&marker={marker}"))
        page = [k.text for k in root.findall("s3:Contents/s3:Key", ns)]
        keys += page
        if root.findtext("s3:IsTruncated", namespaces=ns) != "true" or not page:
            return keys
        marker = page[-1]


def fetch_funding_ccxt(symbol):
    import ccxt

    ex = _ca_bundle(ccxt.binanceusdm({"enableRateLimit": True}))
    rows, since = [], 0
    while True:
        batch = ex.fetch_funding_rate_history(symbol, since=since, limit=1000)
        if not batch:
            break
        rows += [(b["timestamp"], b["fundingRate"]) for b in batch]
        since = batch[-1]["timestamp"] + 1
        if len(batch) < 1000:
            break
    return rows


def fetch_funding_archive(market_id):
    """Binance public monthly funding archive with CHECKSUM verification."""
    prefix = f"data/futures/um/monthly/fundingRate/{market_id}/"
    zips = sorted(k for k in _archive_keys(prefix) if k.endswith(".zip"))
    rows = []
    for key in zips:
        blob = _get(f"{ARCHIVE}/{key}")
        expected = _get(f"{ARCHIVE}/{key}.CHECKSUM").decode().split()[0]
        if hashlib.sha256(blob).hexdigest() != expected:
            raise DataUnavailable(f"checksum mismatch: {key}")
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            f = pd.read_csv(z.open(z.namelist()[0]))
        rows += list(zip(f["calc_time"].astype("int64"), f["last_funding_rate"].astype(float)))
    return rows


def fetch_funding(symbol, market_id):
    """Returns (Series of per-event funding rates indexed by UTC time, source name)."""
    import ccxt

    try:
        rows, source = fetch_funding_ccxt(f"{symbol}:USDT"), "ccxt binanceusdm"
    except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
        rows, source = fetch_funding_archive(market_id), "data.binance.vision archive"
    s = pd.Series([r for _, r in rows], index=pd.to_datetime([t for t, _ in rows], unit="ms", utc=True),
                  name="funding_rate").sort_index()
    s = s[~s.index.duplicated(keep="first")]
    s.index.name = "timestamp"
    return s, source


# ---------- basket ----------

def basket_candidates(ex, exclude, top=80):
    tick = ex.publicGetTicker24hr()
    markets = {m["id"]: m for m in ex.markets.values()
               if m["spot"] and m["quote"] == "USDT" and m["active"]}
    out = []
    for t in tick:
        m = markets.get(t["symbol"])
        if m is None or m["symbol"] in exclude or m["base"] in STABLE_OR_WRAPPED:
            continue
        out.append((float(t["quoteVolume"]), m["symbol"], m["id"]))
    return [(s, i) for _, s, i in sorted(out, reverse=True)[:top]]


def select_basket(histories, start, size, min_years=3):
    """histories: {symbol: daily frame}. Pairs with >= min_years before holdout start,
    ranked by mean quote volume over the 365 days before holdout start."""
    scores = {}
    for sym, df in histories.items():
        pre = df[df.index < start]
        if len(pre) == 0 or pre.index[0] > start - pd.Timedelta(days=365 * min_years):
            continue
        scores[sym] = pre["quote_volume"].iloc[-365:].mean()
    return [s for s, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:size]]


# ---------- storage ----------

def fname(symbol, tf):
    return symbol.replace("/", "_") + f"_{tf}.parquet"


def save(df, name, directory):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    df.to_parquet(p)
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def load_raw(symbol, tf="1d"):
    """Pre-holdout data only. Holdout files are read exclusively by holdout.load."""
    return pd.read_parquet(RAW_DIR / fname(symbol, tf))


def load_funding(symbol):
    p = RAW_DIR / (symbol.replace("/", "_") + "_funding.parquet")
    return pd.read_parquet(p)["funding_rate"] if p.exists() else None


def download(cfg, conn, now_ms=None):
    """Full download, checks, holdout cut, parquet, sha256 to the journal."""
    from tradelab import journal

    d = cfg["data"]
    ex = spot_exchange(cfg)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    files, reports = {}, {}

    def store(sym, tf, raw_df, start):
        df, rep = check(raw_df, tf)
        pre, hold = split(df, start)
        files[f"raw/{fname(sym, tf)}"] = save(pre, fname(sym, tf), RAW_DIR)[1]
        files[f"holdout/{fname(sym, tf)}"] = save(hold, fname(sym, tf), HOLDOUT_DIR)[1]
        reports[f"{sym} {tf}"] = rep
        return df

    eth = check(fetch_klines(ex, ex.market(d["symbols_main"][0])["id"], "1d", now_ms), "1d")[0]
    start = holdout_start(eth.index[-1], d["holdout_days"])

    daily = {}
    for sym in d["symbols_main"]:
        mid = ex.market(sym)["id"]
        daily[sym] = store(sym, "1d", fetch_klines(ex, mid, "1d", now_ms), start)
        store(sym, d["timeframe_extra"], fetch_klines(ex, mid, d["timeframe_extra"], now_ms), start)
        fr, source = fetch_funding(sym, mid)
        name = sym.replace("/", "_") + "_funding.parquet"
        frame = fr.to_frame()
        files[f"raw/{name}"] = save(frame[frame.index < start], name, RAW_DIR)[1]
        files[f"holdout/{name}"] = save(frame[frame.index >= start], name, HOLDOUT_DIR)[1]
        reports[f"{sym} funding"] = {"source": source, "events": len(fr),
                                     "start": str(fr.index[0]), "end": str(fr.index[-1])}

    cands = basket_candidates(ex, exclude={d["symbols_main"][0]})
    hist = {sym: daily[sym] if sym in daily else check(fetch_klines(ex, mid, "1d", now_ms), "1d")[0]
            for sym, mid in cands}
    basket = select_basket(hist, start, d["basket_size"])
    for sym in basket:
        if sym not in daily:
            raw = hist[sym].reset_index().drop(columns="flag_big_move")
            store(sym, "1d", raw, start)

    combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    payload = {"holdout_start": str(start), "basket": basket, "files": files,
               "dataset_sha256": combined, "reports": reports}
    journal.log_event(conn, "dataset", payload)
    return payload


def dataset_info(conn):
    from tradelab import journal

    ev = journal.events(conn, "dataset")
    if not ev:
        raise DataUnavailable("no dataset in journal: run `python run_pipeline.py data` first")
    return ev[-1]["payload"]

