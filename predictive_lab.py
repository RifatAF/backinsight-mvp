#!/usr/bin/env python3
"""Predictive Lab: какие индикаторы, метрики и сезонность реально предсказывают направление цены.

  python predictive_lab.py fetch --coin BTC            # скачать 4H свечи + OI (OKX) + funding (Hyperliquid)
  python predictive_lab.py run data/BTC_4H.csv         # прогнать все признаки и связки
  python predictive_lab.py run my.csv --horizons 1,6,42 --pairs

CSV: timestamp (мс или ISO), close, обязательно; open/high/low/volume/funding/oi и любые свои колонки по желанию.
Свои колонки автоматически становятся признаками.
"""
import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BAR_HOURS = 4


# ---------- загрузка данных ----------

def _get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "predictive-lab"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch_okx_candles(inst, days):
    stop = int(time.time() * 1000) - days * 86400000
    rows, after = [], ""
    while True:
        d = _get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=4H&limit=100{after}")["data"]
        if not d:
            break
        rows += d
        last = int(d[-1][0])
        if last < stop:
            break
        after = f"&after={last}"
        time.sleep(0.12)
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    return df.astype(float).assign(timestamp=lambda x: x.timestamp.astype("int64"))


def fetch_okx_oi(inst):
    rows, end = [], ""
    while True:
        d = _get(f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
                 f"?instId={inst}&period=4H&limit=100{end}")["data"]
        if not d:
            break
        rows += d
        end = f"&end={int(d[-1][0]) - 1}"
        time.sleep(0.25)
    df = pd.DataFrame(rows).iloc[:, [0, 3]]
    df.columns = ["timestamp", "oi"]
    return df.astype(float).assign(timestamp=lambda x: x.timestamp.astype("int64"))


def fetch_hl_funding(coin, days):
    start = int(time.time() * 1000) - days * 86400000
    rows = []
    while True:
        d = _get("https://api.hyperliquid.xyz/info", {"type": "fundingHistory", "coin": coin, "startTime": start})
        if not d:
            break
        rows += d
        nxt = d[-1]["time"] + 1
        if nxt <= start or len(d) < 400:
            break
        start = nxt
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    df["timestamp"] = df.time.astype("int64")
    df["funding"] = df.fundingRate.astype(float)
    return df[["timestamp", "funding"]]


def cmd_fetch(a):
    inst = f"{a.coin}-USDT-SWAP"
    print(f"Свечи {inst} 4H за {a.days} дн...")
    px = fetch_okx_candles(inst, a.days)
    print("Open interest (OKX, сколько отдаёт API)...")
    oi = fetch_okx_oi(inst)
    print("Funding (Hyperliquid, почасовой, сумма за 4H)...")
    fr = fetch_hl_funding(a.coin, a.days)
    fr["timestamp"] = fr.timestamp // (BAR_HOURS * 3600000) * (BAR_HOURS * 3600000)
    fr = fr.groupby("timestamp", as_index=False).funding.sum()

    df = px.merge(oi, on="timestamp", how="left").merge(fr, on="timestamp", how="left")
    df = df.sort_values("timestamp").drop_duplicates("timestamp")
    out = Path(a.out or f"data/{a.coin}_4H.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Сохранено {len(df)} баров -> {out}  (OI: {df.oi.notna().sum()}, funding: {df.funding.notna().sum()})")


# ---------- признаки ----------

def z(s, n):
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def build_features(df):
    """Все признаки считаются только по данным на момент закрытия бара t (без заглядывания вперёд)."""
    c = df.close
    f, cat = pd.DataFrame(index=df.index), pd.DataFrame(index=df.index)
    lr = np.log(c).diff()

    f["mom_1"] = lr
    f["mom_6"] = np.log(c).diff(6)
    f["mom_42"] = np.log(c).diff(42)
    f["rsi_14"] = rsi(c)
    ema12, ema26 = c.ewm(span=12).mean(), c.ewm(span=26).mean()
    macd = ema12 - ema26
    f["macd_hist"] = (macd - macd.ewm(span=9).mean()) / c
    f["bb_z20"] = z(c, 20)
    f["dist_ma200"] = c / c.rolling(200, min_periods=100).mean() - 1
    f["rvol_42"] = lr.rolling(42).std()
    f["rvol_ratio"] = lr.rolling(6).std() / f.rvol_42
    if {"high", "low"} <= set(df):
        f["range_z"] = z((df.high - df.low) / c, 42)
    if "volume" in df:
        f["volume_z"] = z(np.log1p(df.volume), 42)

    has_fr, has_oi = "funding" in df and df.funding.notna().any(), "oi" in df and df.oi.notna().any()
    if has_fr:
        f["funding"] = df.funding
        f["funding_z"] = z(df.funding, 180)
        f["funding_chg"] = df.funding.diff(6)
    if has_oi:
        f["oi_chg_1"] = np.log(df.oi).diff()
        f["oi_chg_6"] = np.log(df.oi).diff(6)
        f["oi_z"] = z(np.log(df.oi), 180)

    # связки: зависящие друг от друга данные
    if has_fr and has_oi:
        f["X_funding_z*oi_chg_6"] = f.funding_z * z(f.oi_chg_6, 180)   # перегрев плеча
        f["X_funding_z*oi_z"] = f.funding_z * f.oi_z
    if has_oi:
        f["X_mom_6*oi_chg_6"] = z(f.mom_6, 180) * z(f.oi_chg_6, 180)   # тренд на притоке позиций
        cat["regime_price_oi"] = np.where(f.mom_6 > 0, "P+", "P-") + np.where(f.oi_chg_6 > 0, "OI+", "OI-")
    if has_fr:
        f["X_funding_z*mom_6"] = f.funding_z * z(f.mom_6, 180)
        cat["regime_funding"] = pd.cut(f.funding_z, [-np.inf, -1, 1, np.inf], labels=["low", "mid", "high"]).astype(str)
    if "volume" in df:
        f["X_volume_z*mom_1"] = f.volume_z * z(f.mom_1, 180)
    f["X_rsi*rvol_ratio"] = (f.rsi_14 - 50) * f.rvol_ratio

    # свои колонки из CSV
    base = {"timestamp", "open", "high", "low", "close", "volume", "funding", "oi"}
    for col in df.columns:
        if col not in base and pd.api.types.is_numeric_dtype(df[col]):
            f[f"user_{col}"] = df[col]

    # сезонность
    ts = df.dt
    cat["season_hour"] = ts.dt.hour.astype(str)
    cat["season_dow"] = ts.dt.dayofweek.astype(str)
    cat["season_month"] = ts.dt.month.astype(str)
    cat["season_dow_hour"] = cat.season_dow + "_" + cat.season_hour
    return f.replace([np.inf, -np.inf], np.nan), cat


def add_pairs(f):
    base = [c for c in f.columns if not c.startswith("X_")]
    zs = {c: z(f[c], 180) for c in base}
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


def evaluate(f, cat, fwd, h, k):
    n = len(fwd)
    res = []
    y_all = fwd.values
    for name in list(f.columns) + list(cat.columns):
        is_cat = name in cat
        x = (cat[name] if is_cat else f[name]).values
        preds, ics, fold_edges = np.zeros(n), [], []
        for tr, te in folds(n, k, h):
            tr = tr[~pd.isna(x[tr]) & ~np.isnan(y_all[tr])]
            if len(tr) < 100:
                continue
            maj = 1 if np.mean(y_all[tr] > 0) >= 0.5 else -1
            if is_cat:
                means = pd.Series(y_all[tr]).groupby(x[tr]).agg(["mean", "size"])
                means = means[means["size"] >= 20]["mean"]
                p = np.array([np.sign(means.get(v, 0.0)) for v in x[te]])
            else:
                s = np.sign(spearman(x[tr], y_all[tr]) or 0)
                med = np.nanmedian(x[tr])
                p = s * np.sign(x[te] - med)
                p = np.nan_to_num(p)
                ok = ~np.isnan(x[te]) & ~np.isnan(y_all[te])
                ics.append(s * spearman(x[te][ok], y_all[te][ok]))
            preds[te] = p
            ok = (p != 0) & ~np.isnan(y_all[te])
            if ok.sum() > 10:
                fold_edges.append(np.mean(np.sign(y_all[te][ok]) == p[ok]) - np.mean(np.sign(y_all[te][ok]) == maj))
        valid = ~np.isnan(y_all)
        sc = score(preds[valid], y_all[valid], h)
        if not sc:
            continue
        hit, cnt, n_eff, mean_r, t = sc
        test_mask = valid & (preds != 0)
        base_up = np.mean(y_all[test_mask] > 0)
        baseline = max(base_up, 1 - base_up)
        edge = hit - baseline
        pval = 0.5 * math.erfc(edge / math.sqrt(baseline * (1 - baseline) / n_eff) / math.sqrt(2))
        res.append(dict(feature=name, horizon_h=h * BAR_HOURS, hit=hit, baseline=baseline, edge=edge,
                        ic=np.nanmean(ics) if ics else np.nan, bps=mean_r * 1e4, t=t, p=pval,
                        stable=f"{sum(e > 0 for e in fold_edges)}/{len(fold_edges)}", n=cnt, n_eff=int(n_eff)))
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
    """Логистическая регрессия на лучших признаках, тоже walk-forward."""
    X = f[feats].values
    y = fwd.values
    preds = np.zeros(len(y))
    for tr, te in folds(len(y), k, h):
        ok = ~np.isnan(X[tr]).any(1) & ~np.isnan(y[tr])
        if ok.sum() < 200:
            continue
        mu, sd = X[tr][ok].mean(0), X[tr][ok].std(0) + 1e-12
        Xt = np.c_[np.ones(ok.sum()), (X[tr][ok] - mu) / sd]
        w = logit_fit(Xt, (y[tr][ok] > 0).astype(float))
        Xe = np.nan_to_num((X[te] - mu) / sd)
        preds[te] = np.sign(np.c_[np.ones(len(te)), Xe] @ w)
    valid = ~np.isnan(y)
    return score(preds[valid], y[valid], h), preds


def load(path):
    df = pd.read_csv(path)
    ts = df.timestamp
    df["dt"] = pd.to_datetime(ts, unit="ms", utc=True) if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts, utc=True)
    return df.sort_values("dt").reset_index(drop=True)


def cmd_run(a):
    global BAR_HOURS
    df = load(a.csv)
    BAR_HOURS = (df.dt.diff().median().total_seconds() / 3600) or BAR_HOURS
    f, cat = build_features(df)
    if a.pairs:
        f = add_pairs(f)
    print(f"Баров: {len(df)}  ({df.dt.iloc[0]:%Y-%m-%d} .. {df.dt.iloc[-1]:%Y-%m-%d}), шаг {BAR_HOURS:g}ч, "
          f"признаков: {f.shape[1] + cat.shape[1]}, фолдов: {a.folds}")

    all_res = []
    for h in [int(x) for x in a.horizons.split(",")]:
        fwd = np.log(df.close).shift(-h) - np.log(df.close)
        r = evaluate(f, cat, fwd, h, a.folds)
        m = len(r)
        r["significant"] = r.p < 0.05 / max(m, 1)   # поправка Бонферрони на число проверок
        r = r.sort_values("edge", ascending=False)
        all_res.append(r)

        print(f"\n=== Горизонт {h * BAR_HOURS:g}ч ({h} бар) | проверок: {m} ===")
        show = r.head(a.top).copy()
        for c, fmt in {"hit": "{:.1%}", "baseline": "{:.1%}", "edge": "{:+.1%}", "ic": "{:+.3f}",
                       "bps": "{:+.1f}", "t": "{:+.2f}", "p": "{:.4f}"}.items():
            show[c] = show[c].map(lambda v, fmt=fmt: "" if pd.isna(v) else fmt.format(v))
        print(show[["feature", "hit", "baseline", "edge", "ic", "bps", "t", "p", "stable", "n_eff", "significant"]]
              .to_string(index=False))

        top = [x for x in r[r.feature.isin(f.columns) & (r.edge > 0)].feature.head(a.combo_k)]
        if len(top) >= 2:
            sc, _ = combo(f, fwd, top, h, a.folds)
            if sc:
                hit, n, n_eff, mr, t = sc
                print(f"Комбинация топ-{len(top)} (логистическая регрессия): hit {hit:.1%}, {mr * 1e4:+.1f} bps/сделку, "
                      f"t {t:+.2f}, N_eff {n_eff:.0f}")
                print("  внимание: топ выбран на всей выборке, поэтому оценка комбинации оптимистична")

    out = Path(a.out)
    pd.concat(all_res).to_csv(out, index=False)
    print(f"\nПолная таблица -> {out}")
    print("hit: доля угаданных направлений вне выборки; baseline: «всегда угадывать большинство»; edge = hit - baseline;\n"
          "ic: ранговая корреляция с будущей доходностью; stable: в скольких фолдах edge > 0; "
          "significant: p ниже порога Бонферрони.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--coin", default="BTC")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--out")
    p = sub.add_parser("run")
    p.add_argument("csv")
    p.add_argument("--horizons", default="1,6,42", help="в барах: 1,6,42 = 4ч, сутки, неделя на 4H")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--combo-k", type=int, default=5)
    p.add_argument("--pairs", action="store_true", help="перебрать все попарные произведения признаков")
    p.add_argument("--out", default="results.csv")
    a = ap.parse_args()
    (cmd_fetch if a.cmd == "fetch" else cmd_run)(a)


if __name__ == "__main__":
    sys.exit(main())
