#!/usr/bin/env python3
"""Strategy Lab: перебор торговых стратегий и их комбинаций на дневках с честной проверкой вне выборки.

  python strategy_lab.py fetch --coin ETH       # цена, деривативы, опционы, ончейн, сентимент, макро -> data/ETH_daily_full.csv
  python strategy_lab.py run                    # перебор одиночных стратегий, пар и фильтров
  python strategy_lab.py run --fee 0.001 --top 25

Каждая стратегия даёт позицию -1/0/+1 по данным на закрытие дня t и держит её день t+1.
Комиссия списывается с каждого изменения позиции. Внешние данные с задержкой публикации сдвинуты на день.
"""
import argparse
import itertools
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import predictive_lab as pl

DAY = 86400000
OUT = Path("data")


# ---------- загрузка ----------

def get_json(url):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-lab"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def get_csv(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strategy-lab"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return pd.read_csv(r)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * 2 ** attempt)


def day_index(ts_ms):
    return pd.to_datetime(np.asarray(ts_ms) // DAY * DAY, unit="ms", utc=True)


def fetch_prices(coin, since):
    cb, okx = pl.exchange("coinbase"), pl.exchange("okx")
    px = pl.fetch_ohlcv(cb, f"{coin}/USD", "1d", since, 300)
    btc = pl.fetch_ohlcv(cb, "BTC/USD", "1d", since, 300)[["timestamp", "close"]].rename(columns={"close": "btc_close"})
    spot = pl.fetch_ohlcv(okx, f"{coin}/USDT", "1d", since, 100)[["timestamp", "close"]].rename(columns={"close": "usdt_close"})
    df = px.merge(btc, on="timestamp", how="left").merge(spot, on="timestamp", how="left")
    df["cb_premium"] = df.close / df.usdt_close - 1
    df.index = day_index(df.timestamp)
    return df.drop(columns=["usdt_close"])


def fetch_deribit_funding(coin, since):
    rows, start, now = [], since, int(time.time() * 1000)
    while start < now:
        end = min(start + 30 * DAY, now)
        r = get_json(f"https://www.deribit.com/api/v2/public/get_funding_rate_history"
                     f"?instrument_name={coin}-PERPETUAL&start_timestamp={start}&end_timestamp={end}")["result"]
        rows += [(x["timestamp"], x["interest_1h"]) for x in r]
        start = end
    s = pd.DataFrame(rows, columns=["ts", "f"]).drop_duplicates("ts")
    return s.groupby(day_index(s.ts)).f.sum().rename("funding")   # сумма почасового funding за день


def fetch_dvol(coin, since):
    rows, end = [], int(time.time() * 1000)
    while True:
        r = get_json(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={coin}"
                     f"&start_timestamp={since}&end_timestamp={end}&resolution=1D")["result"]
        rows += r["data"]
        if not r.get("continuation") or not r["data"]:
            break
        end = r["continuation"]
    s = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c"]).drop_duplicates("ts")
    return s.set_index(day_index(s.ts)).c.rename("dvol").sort_index()


def fetch_fng():
    d = get_json("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    s = pd.Series({int(x["timestamp"]) * 1000: float(x["value"]) for x in d})
    return pd.Series(s.values, index=day_index(s.index), name="fng").sort_index()


def fetch_llama():
    st = get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    stables = pd.Series({int(x["date"]) * 1000: x["totalCirculatingUSD"].get("peggedUSD", np.nan) for x in st})
    tvl = get_json("https://api.llama.fi/v2/historicalChainTvl/Ethereum")
    tvl = pd.Series({int(x["date"]) * 1000: x["tvl"] for x in tvl})
    return pd.DataFrame({"stables": pd.Series(stables.values, index=day_index(stables.index)),
                         "tvl": pd.Series(tvl.values, index=day_index(tvl.index))})


def fetch_coinmetrics(coin):
    metrics = "CapMVRVCur,AdrActCnt,TxCnt,FlowInExUSD,FlowOutExUSD,SplyExNtv"
    url = (f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets={coin.lower()}"
           f"&metrics={metrics}&frequency=1d&start_time=2018-06-01&page_size=10000")
    rows = []
    while url:
        d = get_json(url)
        rows += d["data"]
        url = d.get("next_page_url")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.time, utc=True).dt.floor("D")
    df = df.drop(columns=["asset", "time"] + [c for c in df if "-status" in c]).apply(pd.to_numeric, errors="coerce")
    return df.rename(columns={"CapMVRVCur": "mvrv", "AdrActCnt": "active_addr", "TxCnt": "tx_cnt",
                              "FlowInExUSD": "ex_inflow", "FlowOutExUSD": "ex_outflow", "SplyExNtv": "ex_supply"})


def fetch_fred():
    out = {}
    for sid, name in {"SP500": "spx", "VIXCLS": "vix", "DGS10": "us10y", "DTWEXBGS": "dxy"}.items():
        d = get_csv(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2018-06-01")
        d.columns = ["date", name]
        s = pd.to_numeric(d[name], errors="coerce")
        out[name] = pd.Series(s.values, index=pd.to_datetime(d.date, utc=True))
    return pd.DataFrame(out)


def cmd_fetch(a):
    since = int(pd.Timestamp(a.start, tz="UTC").timestamp() * 1000)
    print("Цены Coinbase (ETH, BTC) и OKX спот для премии...", flush=True)
    df = fetch_prices(a.coin, since)
    idx = df.index
    print("Funding Deribit, DVOL, Fear & Greed, DefiLlama, Coin Metrics, FRED...", flush=True)
    parts = {
        "funding": fetch_deribit_funding(a.coin, since),
        "dvol": fetch_dvol(a.coin, since),
        "fng": fetch_fng(),
    }
    for name, s in parts.items():
        df[name] = s.reindex(idx)
    # данные, которые публикуются после закрытия дня или по календарю США: сдвиг на день и протяжка выходных
    sources = [fetch_llama(), fetch_coinmetrics(a.coin)]
    try:
        sources.append(fetch_fred())
    except Exception as e:
        print(f"FRED недоступен ({e}), макро пропущено", flush=True)
    lagged = pd.concat(sources, axis=1)
    lagged = lagged.reindex(lagged.index.union(idx)).sort_index().ffill(limit=5).shift(1)
    df = df.join(lagged.reindex(idx))
    # OI и long/short с OKX из predictive_lab (1d), если файл уже скачан
    okx_file = OUT / f"{a.coin}_1d.csv"
    if okx_file.exists():
        o = pd.read_csv(okx_file)
        o.index = day_index(o.timestamp)
        for c in ["oi", "ls_ratio", "top_ls_ratio"]:
            if c in o:
                df[c] = o[c].reindex(idx)
    out = OUT / f"{a.coin}_daily_full.csv"
    OUT.mkdir(exist_ok=True)
    df.reset_index(drop=True).to_csv(out, index=False)
    cover = ", ".join(f"{c}: {df[c].notna().sum()}" for c in df.columns if c not in ("timestamp", "open", "high", "low"))
    print(f"{len(df)} дней -> {out}\n{cover}")


# ---------- сигналы ----------

def z(s, n):
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


def hold(entry_long, entry_short, exit_long, exit_short):
    """Позиция с удержанием: вход по условию, выход по своему условию."""
    pos, cur = np.zeros(len(entry_long)), 0
    el, es, xl, xs = (np.asarray(x, dtype=bool) for x in (entry_long, entry_short, exit_long, exit_short))
    for i in range(len(pos)):
        if cur == 1 and xl[i]:
            cur = 0
        elif cur == -1 and xs[i]:
            cur = 0
        if el[i]:
            cur = 1
        elif es[i]:
            cur = -1
        pos[i] = cur
    return pos


def sign_where(cond_long, cond_short, valid):
    p = np.where(cond_long, 1.0, np.where(cond_short, -1.0, 0.0))
    return np.where(valid, p, np.nan)


def build_signals(df):
    """Библиотека стратегий: имя -> позиция (-1/0/+1, NaN где данных нет)."""
    c, h, l = df.close, df.high, df.low
    r = np.log(c).diff()
    S = {}

    # тренд
    for f_, s_ in [(10, 50), (20, 100), (50, 200), (20, 50)]:
        a, b = c.rolling(f_).mean(), c.rolling(s_).mean()
        S[f"trend_sma{f_}/{s_}"] = sign_where(a > b, a < b, b.notna())
    for n in (50, 100, 200):
        m = c.rolling(n).mean()
        S[f"trend_above_sma{n}"] = sign_where(c > m, c < m, m.notna())
    for n in (7, 14, 30, 90):
        m = np.log(c).diff(n)
        S[f"tsmom_{n}d"] = sign_where(m > 0, m < 0, m.notna())
    for n in (20, 55):
        hi, lo = h.rolling(n).max().shift(1), l.rolling(n).min().shift(1)
        lo2, hi2 = l.rolling(n // 2).min().shift(1), h.rolling(n // 2).max().shift(1)
        S[f"donchian_{n}"] = np.where(hi.notna(), hold(c > hi, c < lo, c < lo2, c > hi2), np.nan)
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    sig = macd.ewm(span=9).mean()
    S["macd_cross"] = sign_where(macd > sig, macd < sig, c.index >= 35)
    S["macd_zero"] = sign_where(macd > 0, macd < 0, c.index >= 35)

    # возврат к среднему
    rsi = pl.rsi(c)
    for lo_, hi_ in [(30, 70), (25, 75), (35, 65)]:
        S[f"rsi_rev_{lo_}/{hi_}"] = np.where(c.index >= 20, hold(rsi < lo_, rsi > hi_, rsi > 50, rsi < 50), np.nan)
    bz = z(c, 20)
    for k in (1.5, 2.0):
        S[f"bb_rev_{k}"] = np.where(bz.notna(), hold(bz < -k, bz > k, bz > 0, bz < 0), np.nan)
    S["rsi_trend_50"] = sign_where(rsi > 50, rsi < 50, c.index >= 20)

    # межрыночные
    if "btc_close" in df:
        b = np.log(df.btc_close)
        for n in (7, 30):
            m = b.diff(n)
            S[f"btc_mom_{n}d"] = sign_where(m > 0, m < 0, m.notna())
        ratio = np.log(c) - b
        m = ratio - ratio.rolling(50).mean()
        S["ethbtc_trend_50"] = sign_where(m > 0, m < 0, ratio.rolling(50).mean().notna())

    def add_level(name, s, n, ks, follow):
        """Сигнал по z-score: follow = идти по направлению, иначе против (контртренд)."""
        if s is None or s.notna().sum() < 200:
            return
        zz = z(s, n)
        for k in ks:
            sgn = 1 if follow else -1
            S[f"{name}_{'follow' if follow else 'fade'}_z{k}"] = sign_where(sgn * zz > k, sgn * zz < -k, zz.notna())

    col = lambda name: df[name] if name in df and df[name].notna().sum() > 200 else None
    # деривативы и спот
    add_level("funding", col("funding"), 90, (0.5, 1.0, 1.5), follow=False)
    add_level("funding", col("funding"), 90, (0.5, 1.0), follow=True)
    add_level("cbprem", col("cb_premium"), 90, (0.0, 0.5, 1.0), follow=True)
    add_level("cbprem", col("cb_premium"), 90, (0.5, 1.0), follow=False)
    if col("oi") is not None:
        oc = np.log(df.oi).diff(7)
        add_level("oi7", oc, 90, (0.5, 1.0), follow=False)
        pc = np.log(c).diff(7)
        S["oi_price_confirm"] = sign_where((pc > 0) & (oc > 0), (pc < 0) & (oc > 0), oc.notna())
    add_level("ls", np.log(col("ls_ratio")) if col("ls_ratio") is not None else None, 60, (0.5, 1.0), follow=False)
    add_level("topls", np.log(col("top_ls_ratio")) if col("top_ls_ratio") is not None else None, 60, (0.5, 1.0), follow=True)

    # опционы и сентимент
    add_level("dvol", col("dvol"), 90, (1.0, 1.5), follow=False)   # высокий страх -> покупать
    if col("dvol") is not None:
        dv = df.dvol.diff(7)
        S["dvol_falling"] = sign_where(dv < 0, dv > 0, dv.notna())
    if col("fng") is not None:
        f = df.fng
        S["fng_contra_25/75"] = sign_where(f < 25, f > 75, f.notna())
        S["fng_contra_20/80"] = sign_where(f < 20, f > 80, f.notna())
        S["fng_follow_50"] = sign_where(f > 50, f < 50, f.notna())
        fm = f.rolling(7).mean() - f.rolling(30).mean()
        S["fng_momentum"] = sign_where(fm > 0, fm < 0, fm.notna())

    # ончейн и DeFi
    add_level("mvrv", col("mvrv"), 365, (0.5, 1.0), follow=False)
    if col("mvrv") is not None:
        S["mvrv_below_1"] = sign_where(df.mvrv < 1, df.mvrv > 2.5, df.mvrv.notna())
    if col("ex_inflow") is not None:
        net = (df.ex_inflow - df.ex_outflow).rolling(7).sum()
        add_level("exnetflow", net, 90, (0.5, 1.0), follow=False)   # приток на биржи -> продажи
    if col("ex_supply") is not None:
        d = np.log(df.ex_supply).diff(30)
        S["exsupply_falling"] = sign_where(d < 0, d > 0, d.notna())
    for name, n in [("active_addr", 30), ("tx_cnt", 30), ("stables", 30), ("tvl", 30)]:
        s = col(name)
        if s is not None:
            m = np.log(s.where(s > 0)).diff(n)
            S[f"{name}_growth_{n}d"] = sign_where(m > 0, m < 0, m.notna())
    if col("active_addr") is not None:
        a = df.active_addr.rolling(7).mean()
        S["active_addr_vs_90d"] = sign_where(a > a.rolling(90).mean(), a < a.rolling(90).mean(), a.rolling(90).mean().notna())

    # макро
    if col("spx") is not None:
        m = df.spx.rolling(50).mean()
        S["spx_above_50d"] = sign_where(df.spx > m, df.spx < m, m.notna())
    add_level("vix", col("vix"), 90, (1.0,), follow=False)   # высокий VIX -> рынок в страхе
    if col("dxy") is not None:
        m = np.log(df.dxy).diff(20)
        S["dxy_falling_20d"] = sign_where(m < 0, m > 0, m.notna())
    if col("us10y") is not None:
        m = df.us10y.diff(20)
        S["yields_falling_20d"] = sign_where(m < 0, m > 0, m.notna())

    # сезонность
    dow = df.dt.dt.dayofweek
    S["season_weekend_flat"] = np.where(dow >= 4, 0.0, 1.0)

    return {k: np.asarray(v, dtype=float) for k, v in S.items()}


# ---------- оценка ----------

def backtest(pos, ret, fee):
    """pos[t] держится на доходности t+1. NaN = нет данных = вне рынка, но и не считается."""
    p = np.nan_to_num(pos)
    nxt = np.r_[ret[1:], np.nan]
    turn = np.abs(np.diff(np.r_[0, p]))
    pnl = p * nxt - fee * turn
    return pnl


def stats(pnl, mask):
    x = pnl[mask & ~np.isnan(pnl)]
    if len(x) < 120 or x.std() == 0:
        return None
    sh = x.mean() / x.std() * math.sqrt(365)
    eq = np.cumsum(x)
    dd = np.expm1((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min())   # в процентах от пика
    return dict(sharpe=sh, ann_ret=x.mean() * 365, max_dd=dd, days=len(x))


def deflated_sharpe(sr_daily, n_obs, n_trials, skew, kurt, sr_var):
    """Bailey & López de Prado: вероятность, что лучший Sharpe из n_trials не случайность."""
    if n_trials < 2:
        return np.nan
    e = 0.5772156649
    from statistics import NormalDist
    nd = NormalDist()
    sr0 = math.sqrt(sr_var) * ((1 - e) * nd.inv_cdf(1 - 1 / n_trials) + e * nd.inv_cdf(1 - 1 / (n_trials * math.e)))
    denom = math.sqrt(max(1 - skew * sr_daily + (kurt - 1) / 4 * sr_daily ** 2, 1e-12))
    return nd.cdf((sr_daily - sr0) * math.sqrt(n_obs - 1) / denom)


def evaluate(strats, ret, valid_from, split, fee):
    """Sharpe до split (in-sample) и после (out-of-sample) на собственном окне данных стратегии."""
    rows, pnls = [], {}
    n = len(ret)
    t = np.arange(n)
    for name, pos in strats.items():
        ok = ~np.isnan(pos)
        if ok.sum() < 300:
            continue
        first = np.argmax(ok)
        cut = first + int((n - first) * split)
        pnl = backtest(np.where(ok, pos, 0), ret, fee)
        pnl[~ok] = np.nan
        a, b = stats(pnl, (t >= first) & (t < cut)), stats(pnl, t >= cut)
        if not a or not b:
            continue
        p = np.nan_to_num(pos)
        rows.append(dict(strategy=name, is_sharpe=a["sharpe"], oos_sharpe=b["sharpe"], oos_ann=b["ann_ret"],
                         oos_dd=b["max_dd"], full_sharpe=stats(pnl, t >= first)["sharpe"],
                         trades_y=np.abs(np.diff(p[ok])).sum() / ok.sum() * 365, long_pct=np.mean(p[ok] > 0),
                         short_pct=np.mean(p[ok] < 0), start=valid_from[first]))
        pnls[name] = pnl
    return pd.DataFrame(rows), pnls


def combos(single, top_names, filters):
    """Пары: согласие двух сигналов (иначе вне рынка) и фильтр (сигнал торгуется только в сторону фильтра)."""
    out = {}
    for a, b in itertools.combinations(top_names, 2):
        pa, pb = single[a], single[b]
        out[f"AND[{a} & {b}]"] = np.where(np.isnan(pa) | np.isnan(pb), np.nan, np.where(pa == pb, pa, 0.0))
        out[f"SUM[{a} + {b}]"] = np.where(np.isnan(pa) | np.isnan(pb), np.nan, (pa + pb) / 2)
    for f_ in filters:
        for s in top_names:
            if s == f_:
                continue
            pf, ps = single[f_], single[s]
            out[f"FILTER[{f_}] {s}"] = np.where(np.isnan(pf) | np.isnan(ps), np.nan, np.where(np.sign(ps) == pf, ps, 0.0))
    return out


def walk_forward_selection(strats, ret, fee, train=730, step=90, pick=1):
    """Каждые step дней выбираем лучшую стратегию по Sharpe за прошлые train дней и торгуем её следующий отрезок.
    Это и есть честная оценка всей процедуры «перебрать и выбрать лучшее»."""
    names = list(strats)
    pnl = {k: backtest(np.nan_to_num(v), ret, fee) for k, v in strats.items()}
    valid = {k: ~np.isnan(v) for k, v in strats.items()}
    n, out, chosen = len(ret), np.full(len(ret), np.nan), []
    for start in range(train, n - 1, step):
        best, best_sh = None, -np.inf
        for k in names:
            w = slice(start - train, start)
            if valid[k][w].mean() < 0.95:
                continue
            x = pnl[k][w]
            x = x[~np.isnan(x)]
            sh = x.mean() / (x.std() + 1e-12)
            if sh > best_sh:
                best, best_sh = k, sh
        if best:
            out[start:start + step] = pnl[best][start:start + step]
            chosen.append((start, best))
    return out, chosen


def cmd_run(a):
    df = pd.read_csv(a.csv)
    df["dt"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    ret = np.log(df.close).diff().values
    dates = df.dt.dt.strftime("%Y-%m-%d").values
    single = build_signals(df)
    single["buy_hold"] = np.ones(len(df))
    print(f"{len(df)} дней ({dates[0]} .. {dates[-1]}), одиночных стратегий: {len(single)}, комиссия {a.fee:.2%} за смену позиции")

    variants = dict(single)
    for k, v in single.items():   # long-only версии
        if k != "buy_hold" and np.nanmin(v) < 0:
            variants[k + " [LO]"] = np.where(np.isnan(v), np.nan, np.clip(v, 0, None))
    res1, _ = evaluate(variants, ret, dates, a.split, a.fee)
    res1 = res1.sort_values("is_sharpe", ascending=False)

    # комбинации строятся из лучших по in-sample, проверяются на out-of-sample
    top = [s for s in res1.strategy if s != "buy_hold"][:a.combo_pool]
    filters = [s for s in ["trend_above_sma200", "trend_sma50/200", "trend_above_sma100", "spx_above_50d",
                           "btc_mom_30d"] if s in variants]
    cmb, seen = {}, {np.nan_to_num(v).tobytes() for v in variants.values()}
    for k, v in combos(variants, top, filters).items():   # убираем дубли с одинаковыми позициями
        key = np.nan_to_num(v).tobytes()
        if key not in seen:
            seen.add(key)
            cmb[k] = v
    res2, _ = evaluate(cmb, ret, dates, a.split, a.fee)
    res = pd.concat([res1, res2]).sort_values("is_sharpe", ascending=False)
    n_trials = len(res)
    res.to_csv(a.out, index=False)

    bh = res[res.strategy == "buy_hold"].iloc[0]
    print(f"\nBuy & hold: Sharpe IS {bh.is_sharpe:.2f}, OOS {bh.oos_sharpe:.2f}. Всего проверено стратегий: {n_trials}")

    def show(d, title):
        print(f"\n=== {title} ===")
        t = d[["strategy", "is_sharpe", "oos_sharpe", "oos_ann", "oos_dd", "trades_y", "long_pct", "short_pct", "start"]].copy()
        t["oos_ann"] = t.oos_ann.map("{:+.0%}".format)
        t["oos_dd"] = t.oos_dd.map("{:.0%}".format)
        for c in ("is_sharpe", "oos_sharpe"):
            t[c] = t[c].map("{:+.2f}".format)
        t["trades_y"] = t.trades_y.map("{:.0f}".format)
        t["long_pct"] = t.long_pct.map("{:.0%}".format)
        t["short_pct"] = t.short_pct.map("{:.0%}".format)
        t["strategy"] = t.strategy.str.slice(0, 70)
        print(t.to_string(index=False))

    show(res1.head(a.top), f"Одиночные: топ-{a.top} по in-sample Sharpe (первые {a.split:.0%} истории каждой)")
    show(res1.sort_values("oos_sharpe", ascending=False).head(10), "Одиночные: топ-10 по out-of-sample (для справки, выбирать так нельзя)")
    show(res2.sort_values("is_sharpe", ascending=False).head(a.top), f"Комбинации: топ-{a.top} по in-sample")

    robust = res[(res.is_sharpe > 0.5) & (res.oos_sharpe > 0.5)].copy()
    robust["min_sharpe"] = robust[["is_sharpe", "oos_sharpe"]].min(axis=1)
    show(robust.sort_values("min_sharpe", ascending=False).head(a.top),
         "Устойчивые: Sharpe > 0.5 и в первой, и во второй части истории (по худшей из двух)")

    rho = res[["is_sharpe", "oos_sharpe"]].corr(method="spearman").iloc[0, 1]
    topq = res.head(max(len(res) // 10, 1))
    print(f"\nПереносится ли отбор: ранговая корреляция Sharpe IS и OOS = {rho:+.2f}; "
          f"средний OOS Sharpe у лучших 10% по IS = {topq.oos_sharpe.mean():+.2f}, у всех = {res.oos_sharpe.mean():+.2f}, "
          f"у buy & hold = {bh.oos_sharpe:+.2f}")

    # deflated Sharpe лучшей по полной истории
    best = res.sort_values("full_sharpe", ascending=False).iloc[0]
    allp = {**variants, **cmb}
    pnl = backtest(np.nan_to_num(allp[best.strategy]), ret, a.fee)
    x = pnl[~np.isnan(allp[best.strategy]) & ~np.isnan(pnl)]
    sr = x.mean() / x.std()
    sr_var = (res.full_sharpe / math.sqrt(365)).var()
    skew = ((x - x.mean()) ** 3).mean() / x.std() ** 3
    kurt = ((x - x.mean()) ** 4).mean() / x.std() ** 4
    dsr = deflated_sharpe(sr, len(x), n_trials, skew, kurt, sr_var)
    print(f"Лучшая по всей истории: {best.strategy} (Sharpe {best.full_sharpe:.2f}). "
          f"Deflated Sharpe с учётом {n_trials} попыток: {dsr:.0%} (нужно > 95%)")

    # честная процедура выбора
    pool = {k: v for k, v in allp.items() if np.isnan(v).mean() < 0.5}
    wf, chosen = walk_forward_selection(pool, ret, a.fee)
    m = ~np.isnan(wf)
    sw = wf[m].mean() / wf[m].std() * math.sqrt(365)
    bhp = np.r_[ret[1:], np.nan][m]
    sb = np.nanmean(bhp) / np.nanstd(bhp) * math.sqrt(365)
    print(f"\nWalk-forward выбор лучшей стратегии каждые 90 дней по прошлым 2 годам ({len(pool)} кандидатов, "
          f"{dates[np.argmax(m)]} .. {dates[-1]}): Sharpe {sw:.2f}, годовая {np.nanmean(wf) * 365:+.0%}; "
          f"buy & hold за тот же период: Sharpe {sb:.2f}, годовая {np.nanmean(bhp) * 365:+.0%}")
    print("Что выбиралось: " + "; ".join(f"{dates[s]} {k}" for s, k in chosen[-8:]))
    print(f"\nПолная таблица -> {a.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--coin", default="ETH")
    p.add_argument("--start", default="2019-01-01")
    p = sub.add_parser("run")
    p.add_argument("--csv", default="data/ETH_daily_full.csv")
    p.add_argument("--fee", type=float, default=0.001, help="издержки на единицу смены позиции (0.1% = комиссия + проскальзывание)")
    p.add_argument("--split", type=float, default=0.6)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--combo-pool", type=int, default=20)
    p.add_argument("--out", default="results_strategies.csv")
    a = ap.parse_args()
    (cmd_fetch if a.cmd == "fetch" else cmd_run)(a)


if __name__ == "__main__":
    sys.exit(main())
