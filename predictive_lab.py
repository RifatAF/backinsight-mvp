#!/usr/bin/env python3
"""Predictive Lab: какие индикаторы, деривативные метрики, связки и сезонность реально предсказывают направление цены.

  python predictive_lab.py fetch --coin ETH --tf 1h,4h,1d     # данные через ccxt -> data/ETH_<tf>.csv
  python predictive_lab.py run data/ETH_4h.csv                # прогнать все признаки и связки
  python predictive_lab.py run data/ETH_1h.csv data/ETH_4h.csv data/ETH_1d.csv --pairs

CSV: timestamp (мс или ISO) и close обязательны. Остальные колонки подхватываются, если есть:
open/high/low/volume, funding, oi, ls_ratio, top_ls_ratio, taker_buy, taker_sell, cb_premium, а любые свои
числовые колонки автоматически становятся признаками.
"""
import argparse
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

TF_MS = {"1h": 3600000, "4h": 4 * 3600000, "1d": 86400000}
OKX_PERIOD = {"1h": "1H", "4h": "4H", "1d": "1D"}
DEFAULT_HORIZONS = {"1h": "1,4,24", "4h": "1,6,42", "1d": "1,3,7"}
DEFAULT_DAYS = {"1h": 730, "4h": 730, "1d": 1460}


# ---------- загрузка данных (ccxt) ----------

def exchange(name):
    import ccxt
    ex = getattr(ccxt, name)({"enableRateLimit": True})
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca:
        ex.validateServerSsl = ca   # ccxt передаёт это значение в requests как verify
    return ex


def retry(fn, *args, **kw):
    for attempt in range(6):
        try:
            return fn(*args, **kw)
        except Exception:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)


def fetch_ohlcv(ex, symbol, tf, since, limit):
    rows, step = [], TF_MS[tf]
    now = ex.milliseconds()
    while since < now:
        batch = retry(ex.fetch_ohlcv, symbol, tf, since=since, limit=limit)
        if not batch:
            since += step * limit
            continue
        rows += batch
        since = batch[-1][0] + step
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df.drop_duplicates("timestamp").sort_values("timestamp")


def fetch_rubik(okx, method, inst, tf, cols, since):
    """OKX trading data (OI, long/short, taker volume): пагинация назад по end, глубина зависит от периода."""
    rows, end = [], None
    while end is None or end > since:
        params = {"instId": inst, "period": OKX_PERIOD[tf], "limit": 100}
        if end:
            params["end"] = end
        data = retry(getattr(okx, method), params)["data"]
        if not data:
            break
        rows += data
        nxt = int(data[-1][0]) - 1
        if end is not None and nxt >= end:
            break
        end = nxt
    if not rows:
        return pd.DataFrame(columns=["timestamp"] + cols)
    df = pd.DataFrame([r[:len(cols) + 1] for r in rows], columns=["timestamp"] + cols).astype(float)
    df["timestamp"] = df.timestamp.astype("int64")
    return df.drop_duplicates("timestamp").sort_values("timestamp")


def fetch_funding(coin, since):
    hl = exchange("hyperliquid")
    rows = []
    while True:
        batch = retry(hl.fetch_funding_rate_history, f"{coin}/USDC:USDC", since=since, limit=500)
        if not batch:
            break
        rows += [(r["timestamp"], r["fundingRate"]) for r in batch]
        if len(batch) < 500:
            break
        since = batch[-1]["timestamp"] + 1
    return pd.DataFrame(rows, columns=["timestamp", "funding"])


def asof(base, other):
    """Присоединяет последнее известное значение на момент открытия бара (без заглядывания вперёд)."""
    if other.empty:
        return base
    return pd.merge_asof(base.sort_values("timestamp"), other.sort_values("timestamp"), on="timestamp",
                         direction="backward")


def cmd_fetch(a):
    okx, cb = exchange("okx"), exchange("coinbase")
    inst = f"{a.coin}-USDT-SWAP"
    tfs = a.tf.split(",")
    max_days = max(a.days or DEFAULT_DAYS[tf] for tf in tfs)
    print(f"Funding Hyperliquid (почасовой) за {max_days} дн...", flush=True)
    funding = fetch_funding(a.coin, okx.milliseconds() - max_days * 86400000)
    for tf in tfs:
        days = a.days or DEFAULT_DAYS[tf]
        since = okx.milliseconds() - days * 86400000
        print(f"[{tf}] свечи перпа OKX за {days} дн...", flush=True)
        df = fetch_ohlcv(okx, f"{a.coin}/USDT:USDT", tf, since, 100)

        print(f"[{tf}] Coinbase {a.coin}/USD и OKX спот {a.coin}/USDT для премии Coinbase...", flush=True)
        cb_tf = "1h" if tf == "4h" else tf   # у Coinbase нет 4h, собираем из часовых
        cbx = fetch_ohlcv(cb, f"{a.coin}/USD", cb_tf, since, 300)
        cbx["timestamp"] = cbx.timestamp // TF_MS[tf] * TF_MS[tf]
        cbx = cbx.groupby("timestamp", as_index=False).close.last().rename(columns={"close": "cb_close"})
        spot = fetch_ohlcv(okx, f"{a.coin}/USDT", tf, since, 100)[["timestamp", "close"]].rename(columns={"close": "spot_close"})
        prem = cbx.merge(spot, on="timestamp")
        prem["cb_premium"] = prem.cb_close / prem.spot_close - 1
        df = df.merge(prem[["timestamp", "cb_premium"]], on="timestamp", how="left")

        print(f"[{tf}] OI, long/short (аккаунты и топ-трейдеры), taker volume с OKX...", flush=True)
        oi = fetch_rubik(okx, "publicGetRubikStatContractsOpenInterestHistory", inst, tf, ["oi_contracts", "oi_coin", "oi"], since)
        ls = fetch_rubik(okx, "publicGetRubikStatContractsLongShortAccountRatioContract", inst, tf, ["ls_ratio"], since)
        top = fetch_rubik(okx, "publicGetRubikStatContractsLongShortPositionRatioContractTopTrader", inst, tf, ["top_ls_ratio"], since)
        tk = fetch_rubik(okx, "publicGetRubikStatTakerVolumeContract", inst, tf, ["taker_sell", "taker_buy"], since)
        tk = tk[(tk.taker_buy + tk.taker_sell) > 0]
        for part in (oi[["timestamp", "oi"]], ls, top, tk):
            df = asof(df, part) if tf == "1d" else df.merge(part, on="timestamp", how="left")

        fr = funding[funding.timestamp >= since].copy()   # сумма почасового funding за бар
        fr["timestamp"] = fr.timestamp // TF_MS[tf] * TF_MS[tf]
        fr = fr.groupby("timestamp", as_index=False).funding.sum()
        df = df.merge(fr, on="timestamp", how="left")

        out = Path(a.out_dir) / f"{a.coin}_{tf}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        cover = ", ".join(f"{c}: {df[c].notna().sum()}" for c in
                          ["cb_premium", "funding", "oi", "ls_ratio", "top_ls_ratio", "taker_buy"])
        print(f"[{tf}] {len(df)} баров -> {out}  ({cover})\n", flush=True)


# ---------- признаки ----------

def z(s, n):
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def has(df, col):
    return col in df and df[col].notna().sum() > 50


def build_features(df, bar_hours):
    """Все признаки считаются только по данным на момент закрытия бара t (без заглядывания вперёд)."""
    c = df.close
    f, cat = pd.DataFrame(index=df.index), pd.DataFrame(index=df.index)
    W = 180 if bar_hours < 24 else 90   # окно z-score
    lr = np.log(c).diff()

    f["mom_1"] = lr
    f["mom_6"] = np.log(c).diff(6)
    f["mom_42"] = np.log(c).diff(42)
    f["rsi_14"] = rsi(c)
    ema12, ema26 = c.ewm(span=12).mean(), c.ewm(span=26).mean()
    macd = ema12 - ema26
    f["macd"] = macd / c
    f["macd_hist"] = (macd - macd.ewm(span=9).mean()) / c
    f["bb_z20"] = z(c, 20)
    f["dist_ma200"] = c / c.rolling(200, min_periods=100).mean() - 1
    f["rvol_42"] = lr.rolling(42).std()
    f["rvol_ratio"] = lr.rolling(6).std() / f.rvol_42
    if has(df, "high"):
        f["range_z"] = z((df.high - df.low) / c, 42)
    if has(df, "volume"):
        f["volume_z"] = z(np.log1p(df.volume), 42)

    if has(df, "funding"):
        f["funding"] = df.funding
        f["funding_z"] = z(df.funding, W)
        f["funding_chg"] = df.funding.diff(6)
    if has(df, "oi"):
        f["oi_chg_1"] = np.log(df.oi).diff()
        f["oi_chg_6"] = np.log(df.oi).diff(6)
        f["oi_z"] = z(np.log(df.oi), W)
    if has(df, "cb_premium"):
        f["cb_premium"] = df.cb_premium
        f["cb_premium_z"] = z(df.cb_premium, W)
        f["cb_premium_chg"] = df.cb_premium.diff(6)
    if has(df, "ls_ratio"):
        f["ls_ratio"] = df.ls_ratio
        f["ls_z"] = z(np.log(df.ls_ratio), W)
        f["ls_chg_6"] = np.log(df.ls_ratio).diff(6)
    if has(df, "top_ls_ratio"):
        f["top_ls_z"] = z(np.log(df.top_ls_ratio), W)
        f["top_ls_chg_6"] = np.log(df.top_ls_ratio).diff(6)
        if has(df, "ls_ratio"):
            f["X_top_vs_crowd"] = np.log(df.top_ls_ratio) - np.log(df.ls_ratio)   # топы против толпы
    if has(df, "taker_buy"):
        imb = (df.taker_buy - df.taker_sell) / (df.taker_buy + df.taker_sell)
        f["taker_imb"] = imb
        f["taker_imb_6"] = imb.rolling(6).mean()

    # связки: зависящие друг от друга данные
    zz = lambda name: z(f[name], W)
    pairs = [("funding_z", "oi_chg_6", "перегрев плеча"),
             ("funding_z", "oi_z", ""),
             ("mom_6", "oi_chg_6", "тренд на притоке позиций"),
             ("funding_z", "mom_6", ""),
             ("cb_premium_z", "funding_z", "спот в США против перпа"),
             ("cb_premium_z", "mom_6", ""),
             ("ls_z", "oi_chg_6", "толпа набирает позиции"),
             ("ls_z", "funding_z", ""),
             ("taker_imb_6", "oi_chg_6", "агрессия на открытии позиций"),
             ("volume_z", "mom_1", "")]
    for a_, b_, _ in pairs:
        if a_ in f and b_ in f:
            f[f"X_{a_}*{b_}"] = (f[a_] if a_.endswith("_z") else zz(a_)) * (f[b_] if b_.endswith("_z") else zz(b_))
    f["X_rsi*rvol_ratio"] = (f.rsi_14 - 50) * f.rvol_ratio
    f["X_macd_hist*volume_z"] = zz("macd_hist") * f.get("volume_z", 1)

    if "oi_chg_6" in f:
        cat["regime_price_oi"] = pd.Series(np.where(f.mom_6 > 0, "P+", "P-"), index=f.index) \
            .str.cat(np.where(f.oi_chg_6 > 0, "OI+", "OI-")).where(f.oi_chg_6.notna())
    if "funding_z" in f:
        cat["regime_funding"] = pd.cut(f.funding_z, [-np.inf, -1, 1, np.inf], labels=["low", "mid", "high"]) \
            .astype(object).where(f.funding_z.notna())
    if "cb_premium_z" in f:
        cat["regime_cb_premium"] = pd.cut(f.cb_premium_z, [-np.inf, -1, 1, np.inf], labels=["low", "mid", "high"]) \
            .astype(object).where(f.cb_premium_z.notna())

    # свои колонки из CSV
    known = {"timestamp", "dt", "open", "high", "low", "close", "volume", "funding", "oi", "cb_premium",
             "ls_ratio", "top_ls_ratio", "taker_buy", "taker_sell"}
    for col in df.columns:
        if col not in known and pd.api.types.is_numeric_dtype(df[col]):
            f[f"user_{col}"] = df[col]

    # сезонность
    ts = df.dt
    if bar_hours < 24:
        cat["season_hour"] = ts.dt.hour.astype(str)
        cat["season_dow_hour"] = ts.dt.dayofweek.astype(str) + "_" + cat.season_hour
    cat["season_dow"] = ts.dt.dayofweek.astype(str)
    cat["season_month"] = ts.dt.month.astype(str)
    return f.replace([np.inf, -np.inf], np.nan), cat


def add_pairs(f, W=180):
    base = [c for c in f.columns if not c.startswith("X_")]
    zs = {c: z(f[c], W) for c in base}
    extra = {f"P_{a}*{b}": zs[a] * zs[b] for i, a in enumerate(base) for b in base[i + 1:]}
    return pd.concat([f, pd.DataFrame(extra, index=f.index)], axis=1)


# ---------- оценка ----------

def folds(n, k, gap):
    """Walk-forward: обучаемся на прошлом, тестируем на следующем куске, между ними зазор gap баров."""
    edges = np.linspace(n * 0.4, n, k + 1).astype(int)
    for i in range(k):
        yield np.arange(0, max(edges[i] - gap, 0)), np.arange(edges[i], edges[i + 1])


def spearman(a, b):
    if len(a) < 10:
        return np.nan
    return np.corrcoef(pd.Series(a).rank(), pd.Series(b).rank())[0, 1]


def score(pred, y, h):
    """pred: +1/-1/0, y: будущая доходность. Перекрывающиеся окна учитываются через N_eff = N/h."""
    m = pred != 0
    p, r = pred[m], y[m]
    n = len(p)
    if n < 30:
        return None
    hit = float(np.mean(np.sign(r) == p))
    n_eff = max(n / h, 1)
    pnl = p * r
    t = pnl.mean() / (pnl.std() + 1e-12) * math.sqrt(n_eff)
    return hit, n, n_eff, pnl.mean(), t


def evaluate(f, cat, fwd, h, k, bar_hours, min_bars):
    """Каждый признак проверяется на своей собственной истории: у OI и long/short она короче, чем у цены."""
    res = []
    y_full = fwd.values
    for name in list(f.columns) + list(cat.columns):
        is_cat = name in cat
        x_full = (cat[name] if is_cat else f[name]).values
        idx = np.where(~pd.isna(x_full) & ~np.isnan(y_full))[0]
        if len(idx) < min_bars:
            continue
        x, y = x_full[idx], y_full[idx]
        n = len(idx)
        preds, ics, fold_edges = np.zeros(n), [], []
        for tr, te in folds(n, k, h):
            if len(tr) < max(50, 5 * h):
                continue
            maj = 1 if np.mean(y[tr] > 0) >= 0.5 else -1
            if is_cat:
                stats = pd.Series(y[tr]).groupby(x[tr]).agg(["mean", "size"])
                means = stats[stats["size"] >= 20]["mean"]
                p = np.array([np.sign(means.get(v, 0.0)) for v in x[te]])
            else:
                s = np.sign(np.nan_to_num(spearman(x[tr], y[tr])))
                p = s * np.sign(x[te] - np.median(x[tr]))
                ics.append(s * spearman(x[te], y[te]))
            preds[te] = p
            ok = p != 0
            if ok.sum() > 10:
                fold_edges.append(np.mean(np.sign(y[te][ok]) == p[ok]) - np.mean(np.sign(y[te][ok]) == maj))
        sc = score(preds, y, h)
        if not sc:
            continue
        hit, cnt, n_eff, mean_r, t = sc
        up = np.mean(y[preds != 0] > 0)
        baseline = max(up, 1 - up)
        edge = hit - baseline
        pval = 0.5 * math.erfc(edge / math.sqrt(baseline * (1 - baseline) / n_eff) / math.sqrt(2))
        res.append(dict(feature=name, horizon_h=h * bar_hours, hit=hit, baseline=baseline, edge=edge,
                        ic=np.nanmean(ics) if ics else np.nan, bps=mean_r * 1e4, t=t, p=pval,
                        stable=f"{sum(e > 0 for e in fold_edges)}/{len(fold_edges)}",
                        days=round(n * bar_hours / 24), n=cnt, n_eff=int(n_eff)))
    return pd.DataFrame(res)


def logit_fit(X, y, lam=1.0, it=25):
    w = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X @ w))
        W = p * (1 - p) + 1e-6
        H = X.T @ (X * W[:, None]) + lam * np.eye(len(w))
        w -= np.linalg.solve(H, X.T @ (p - y) + lam * w)
    return w


def combo(f, fwd, feats, h, k):
    """Логистическая регрессия на лучших признаках, тоже walk-forward, на общей для них истории."""
    X, y = f[feats].values, fwd.values
    ok = ~np.isnan(X).any(1) & ~np.isnan(y)
    X, y = X[ok], y[ok]
    preds = np.zeros(len(y))
    for tr, te in folds(len(y), k, h):
        if len(tr) < max(100, 10 * h):
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-12
        w = logit_fit(np.c_[np.ones(len(tr)), (X[tr] - mu) / sd], (y[tr] > 0).astype(float))
        preds[te] = np.sign(np.c_[np.ones(len(te)), (X[te] - mu) / sd] @ w)
    return score(preds, y, h)


def load(path):
    df = pd.read_csv(path)
    ts = df.timestamp
    df["dt"] = pd.to_datetime(ts, unit="ms", utc=True) if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts, utc=True)
    return df.sort_values("dt").reset_index(drop=True)


def fmt_table(r):
    show = r.copy()
    for c, fmt in {"hit": "{:.1%}", "baseline": "{:.1%}", "edge": "{:+.1%}", "ic": "{:+.3f}",
                   "bps": "{:+.1f}", "t": "{:+.2f}", "p": "{:.4f}"}.items():
        show[c] = show[c].map(lambda v, fmt=fmt: "" if pd.isna(v) else fmt.format(v))
    show["sig"] = np.where(r.significant, "***", "")
    return show[["feature", "hit", "baseline", "edge", "ic", "bps", "t", "p", "stable", "days", "n_eff", "sig"]] \
        .to_string(index=False)


def run_one(path, a):
    df = load(path)
    bar_hours = df.dt.diff().median().total_seconds() / 3600
    tf = {1: "1h", 4: "4h", 24: "1d"}.get(round(bar_hours), "")
    f, cat = build_features(df, bar_hours)
    if a.pairs:
        f = add_pairs(f, 180 if bar_hours < 24 else 90)
    print(f"\n######## {path}: {len(df)} баров ({df.dt.iloc[0]:%Y-%m-%d} .. {df.dt.iloc[-1]:%Y-%m-%d}), "
          f"шаг {bar_hours:g}ч, признаков: {f.shape[1] + cat.shape[1]}")

    out = []
    horizons = a.horizons or DEFAULT_HORIZONS.get(tf, "1,6,24")
    for h in [int(x) for x in horizons.split(",")]:
        fwd = np.log(df.close).shift(-h) - np.log(df.close)
        r = evaluate(f, cat, fwd, h, a.folds, bar_hours, a.min_bars)
        r["significant"] = r.p < 0.05 / max(len(r), 1)   # поправка Бонферрони на число проверок
        r = r.sort_values("edge", ascending=False)
        r.insert(0, "tf", tf or f"{bar_hours:g}h")
        out.append(r)

        print(f"\n=== {tf} | горизонт {h * bar_hours:g}ч ({h} бар) | проверок: {len(r)} ===")
        print(fmt_table(r.head(a.top)))
        top = [x for x in r[r.feature.isin(f.columns) & (r.edge > 0) & (r.days >= 0.5 * r.days.max())]
               .feature.head(a.combo_k)]
        if len(top) >= 2:
            sc = combo(f, fwd, top, h, a.folds)
            if sc:
                hit, n, n_eff, mr, t = sc
                print(f"Комбинация {top}: hit {hit:.1%}, {mr * 1e4:+.1f} bps, t {t:+.2f}, N_eff {n_eff:.0f} "
                      f"(оптимистично: топ выбран на всей выборке)")
    return pd.concat(out)


def cmd_run(a):
    res = pd.concat([run_one(p, a) for p in a.csv])
    res.to_csv(a.out, index=False)
    sig = res[res.significant]
    print(f"\nПолная таблица -> {a.out}. Прошли поправку Бонферрони: {len(sig)} из {len(res)}")
    if len(sig):
        print(fmt_table(sig.sort_values("edge", ascending=False)))
    print("hit: доля угаданных направлений вне выборки; baseline: «всегда угадывать большинство»; edge = hit - baseline;\n"
          "ic: ранговая корреляция с будущей доходностью; stable: в скольких фолдах edge > 0; days: длина истории признака;\n"
          "sig ***: p ниже порога Бонферрони.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--coin", default="ETH")
    p.add_argument("--tf", default="1h,4h,1d")
    p.add_argument("--days", type=int, help="глубина истории; по умолчанию 730 для 1h/4h и 1460 для 1d")
    p.add_argument("--out-dir", default="data")
    p = sub.add_parser("run")
    p.add_argument("csv", nargs="+")
    p.add_argument("--horizons", help="в барах; по умолчанию 1h: 1,4,24; 4h: 1,6,42; 1d: 1,3,7")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--combo-k", type=int, default=5)
    p.add_argument("--min-bars", type=int, default=300, help="минимум баров истории у признака")
    p.add_argument("--pairs", action="store_true", help="перебрать все попарные произведения признаков")
    p.add_argument("--out", default="results.csv")
    a = ap.parse_args()
    (cmd_fetch if a.cmd == "fetch" else cmd_run)(a)


if __name__ == "__main__":
    sys.exit(main())
