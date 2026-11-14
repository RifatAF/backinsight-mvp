#!/usr/bin/env python3
"""Исследование ончейн-метрик (Coin Metrics community + DefiLlama) и вопроса «разнородные источники и слои».

  python onchain_research.py

Часть 1. Ончейн-сигналы для BTC и ETH: отбор на первой половине истории, проверка на второй.
Часть 2. Ансамбли: сколько сигналов и из каких семейств смешивать; корреляция сигналов.
Часть 3. Слоистая схема (режим цикла -> тренд -> подтверждения -> размер по волатильности) против плоского голосования.
Все ончейн-данные сдвинуты на 1 день (публикуются после закрытия), комиссия 0.1% за смену позиции, только long.
"""
import itertools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import predictive_lab as pl
from strategy_lab import get_json

FEE = 0.001
METRICS = ("PriceUSD,CapMVRVCur,CapMrktCurUSD,AdrActCnt,AdrBalCnt,TxCnt,TxTfrCnt,FeeTotNtv,HashRate,"
           "IssTotUSD,FlowInExUSD,FlowOutExUSD,SplyExNtv,volume_reported_spot_usd_1d")


def load(asset):
    path = Path("data") / f"{asset}_cm_onchain.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    url = (f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets={asset}&metrics={METRICS}"
           f"&frequency=1d&start_time=2014-01-01&page_size=10000&ignore_unsupported_errors=true")
    rows = []
    while url:
        d = get_json(url)
        rows += d["data"]
        url = d.get("next_page_url")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.time).dt.tz_localize(None).dt.floor("D")
    df = df.drop(columns=["asset", "time"] + [c for c in df if "-status" in c]).apply(pd.to_numeric, errors="coerce")
    st = get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    s = pd.Series({pd.Timestamp(int(x["date"]), unit="s").floor("D"): x["totalCirculatingUSD"].get("peggedUSD", np.nan)
                   for x in st})
    df["stables"] = s.reindex(df.index)
    path.parent.mkdir(exist_ok=True)
    df.to_csv(path)
    return df


def zz(s, n=365):
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


def onchain_features(df):
    f = pd.DataFrame(index=df.index)
    lg = lambda c: np.log(df[c].where(df[c] > 0))
    f["активные адреса: 30д к году"] = lg("AdrActCnt").rolling(30).mean() - lg("AdrActCnt").rolling(365).mean()
    f["адреса с балансом: рост 30д"] = lg("AdrBalCnt").diff(30)
    f["транзакции: 30д к году"] = lg("TxCnt").rolling(30).mean() - lg("TxCnt").rolling(365).mean()
    f["комиссии сети: z"] = zz(lg("FeeTotNtv").rolling(7).mean())
    net = (df.FlowInExUSD - df.FlowOutExUSD).rolling(7).sum() / df.CapMrktCurUSD
    f["нетто-приток на биржи 7д: z"] = zz(net)
    f["запас на биржах: изм. 30д"] = lg("SplyExNtv").diff(30)
    if df.HashRate.notna().sum() > 1000 and df.HashRate.iloc[-400:].notna().mean() > 0.5:
        h = df.HashRate
        f["Hash Ribbons: SMA30/SMA60 хешрейта"] = np.log(h.rolling(30).mean() / h.rolling(60).mean())
        hp = df.IssTotUSD / h
        f["hashprice: z"] = zz(np.log(hp.rolling(7).mean()))
    f["NVT: капитализация/спот-объём z"] = zz(np.log(df.CapMrktCurUSD / df.volume_reported_spot_usd_1d.rolling(30).mean()))
    f["стейблкоины: рост 30д"] = np.log(df.stables).diff(30)
    f["MVRV: z"] = zz(df.CapMVRVCur)
    return f.replace([np.inf, -np.inf], np.nan).shift(1)


def run_bt(pos, price, mask=None):
    r = np.log(price).diff().values
    p = np.nan_to_num(np.asarray(pos, dtype=float))
    nxt = np.r_[r[1:], 0]
    lp = np.log1p(p * np.expm1(nxt) - FEE * np.abs(np.diff(np.r_[0, p])))
    if mask is not None:
        lp = lp[mask]
    if len(lp) < 200:
        return None
    eq = np.exp(np.cumsum(lp))
    cagr = eq[-1] ** (365 / len(lp)) - 1
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(cagr=cagr, sharpe=lp.mean() / (lp.std() + 1e-12) * math.sqrt(365), dd=dd)


def part1(assets):
    print("=" * 100 + "\nЧАСТЬ 1. Ончейн-сигналы: long, когда метрика выше (по направлению) или ниже (против) своей медианы за год")
    picked = {}
    for asset, (df, f) in assets.items():
        p = df.PriceUSD
        lp = np.log(p)
        rows = []
        for name in f:
            x = f[name]
            valid = x.notna()
            idx = np.where(valid)[0]
            if len(idx) < 900:
                continue
            cut = idx[len(idx) // 2]
            t = np.arange(len(p))
            m1, m2 = valid.values & (t < cut), valid.values & (t >= cut)
            for h in (30, 90):
                fwd = lp.shift(-h) - lp
                ic1 = x[m1].rank().corr(fwd[m1].rank())
                ic2 = x[m2].rank().corr(fwd[m2].rank())
                rows.append(dict(metric=name, h=h, ic1=ic1, ic2=ic2))
            med = x.rolling(365, min_periods=180).median()
            for d, sgn in (("по направлению", 1), ("против", -1)):
                pos = np.where(valid & med.notna(), (sgn * (x - med) > 0).astype(float), np.nan)
                a, b = run_bt(pos, p, m1), run_bt(pos, p, m2)
                hold1, hold2 = run_bt(np.ones(len(p)), p, m1), run_bt(np.ones(len(p)), p, m2)
                if a and b:
                    rows.append(dict(metric=name, h=f"long {d}", s1=a["sharpe"] - hold1["sharpe"],
                                     s2=b["sharpe"] - hold2["sharpe"], cagr2=b["cagr"], dd2=b["dd"],
                                     split=p.index[cut].strftime("%Y-%m")))
        r = pd.DataFrame(rows)
        ic = r[r.h.isin([30, 90])].pivot_table(index="metric", columns="h", values=["ic1", "ic2"])
        ic.columns = [f"IC{h}д {'1-я' if c == 'ic1' else '2-я'} пол." for c, h in ic.columns]
        print(f"\n######## {asset.upper()}: ранговая корреляция с будущей доходностью (знак должен совпасть в обеих половинах)")
        print(ic[sorted(ic.columns)].to_string(float_format=lambda v: f"{v:+.2f}"))
        st = r[r.h.astype(str).str.startswith("long")].copy()
        print(f"\n{asset.upper()}: Sharpe стратегии минус Sharpe держания, в 1-й (отбор) и 2-й (проверка) половине")
        st["ok"] = np.where((st.s1 > 0) & (st.s2 > 0), "✔ обе", "")
        print(st.sort_values("s1", ascending=False)[["metric", "h", "s1", "s2", "cagr2", "dd2", "split", "ok"]]
              .head(12).to_string(index=False, formatters={"s1": "{:+.2f}".format, "s2": "{:+.2f}".format,
                                                             "cagr2": "{:+.0%}".format, "dd2": "{:+.0%}".format}))
        # отбор только по первой половине: лучшая версия каждой метрики с s1 > 0
        sel = st[st.s1 > 0.1].sort_values("s1", ascending=False).drop_duplicates("metric")
        picked[asset] = [(m, 1 if "направлению" in h else -1) for m, h in zip(sel.metric, sel.h)]
    return picked


def signal_pool(df, f, picked, btc_price):
    """Бинарные сигналы long/вне рынка из разных семейств."""
    p = df.PriceUSD
    btc = btc_price.reindex(p.index).ffill()
    S, fam = {}, {}

    def add(name, family, s):
        S[name] = s.astype(float).where(s.notna() if hasattr(s, "notna") else True)
        fam[name] = family

    add("R3: BTC 30д и RSI>50", "тренд", (np.log(btc).diff(30) > 0) & (pl.rsi(p) > 50))
    add("моментум 30д", "тренд", np.log(p).diff(30) > 0)
    add("цена > SMA50", "тренд", p > p.rolling(50).mean())
    add("цена > SMA200", "тренд", p > p.rolling(200).mean())
    add("моментум 90д", "тренд", np.log(p).diff(90) > 0)
    for m, sgn in picked:
        x = f[m]
        med = x.rolling(365, min_periods=180).median()
        add(f"ончейн: {m} ({'по напр.' if sgn > 0 else 'против'})", "ончейн", (sgn * (x - med) > 0).where(med.notna()))
    mv = df.CapMVRVCur.shift(1)
    pct = mv.expanding(365).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    add("цикл: MVRV ниже 90-го перцентиля", "цикл", (pct < 0.9).where(pct.notna()))
    add("цикл: цена > SMA200 недель", "цикл", (p > p.rolling(1400).mean()).where(p.rolling(1400).mean().notna()))
    return S, fam


def part2(assets, picked, btc_price):
    print("\n" + "=" * 100 + "\nЧАСТЬ 2. Ансамбли: позиция = доля включённых сигналов. Одно семейство против разных, 1..N сигналов")
    for asset, (df, f) in assets.items():
        S, fam = signal_pool(df, f, picked[asset], btc_price)
        D = pd.DataFrame(S).dropna()
        p = df.PriceUSD.reindex(D.index)
        print(f"\n######## {asset.upper()} {D.index[0]:%Y-%m} .. {D.index[-1]:%Y-%m}, сигналов: {len(S)} "
              f"(тренд {sum(v == 'тренд' for v in fam.values())}, ончейн {sum(v == 'ончейн' for v in fam.values())}, "
              f"цикл {sum(v == 'цикл' for v in fam.values())})")
        corr = D.corr()
        famr = pd.Series(fam)
        within = {g: corr.loc[famr[famr == g].index, famr[famr == g].index].values[np.triu_indices((famr == g).sum(), 1)].mean()
                  for g in famr.unique() if (famr == g).sum() > 1}
        between = np.mean([corr.iat[i, j] for i in range(len(corr)) for j in range(i + 1, len(corr))
                           if famr.iloc[i] != famr.iloc[j]])
        print("Средняя корреляция сигналов: внутри семейства " +
              ", ".join(f"{g} {v:+.2f}" for g, v in within.items()) + f"; между семействами {between:+.2f}")
        hold = run_bt(np.ones(len(p)), p)
        rows = []
        names = list(D.columns)
        rng = np.random.default_rng(0)
        for k in range(1, min(8, len(names)) + 1):
            combos = list(itertools.combinations(names, k))
            if len(combos) > 300:
                combos = [tuple(rng.choice(names, k, replace=False)) for _ in range(300)]
            for cmb in combos:
                fams = {fam[c] for c in cmb}
                kind = "одно семейство" if len(fams) == 1 else f"{len(fams)} семейства"
                pos = D[list(cmb)].mean(axis=1).values
                r = run_bt(pos, p)
                rows.append(dict(k=k, kind=kind, sharpe=r["sharpe"], cagr=r["cagr"], dd=r["dd"]))
        r = pd.DataFrame(rows)
        t = r.groupby(["k", "kind"]).agg(n=("sharpe", "size"), sharpe=("sharpe", "median"), cagr=("cagr", "median"),
                                          dd=("dd", "median"), sharpe_p10=("sharpe", lambda s: s.quantile(0.1)))
        print(f"Держать: Sharpe {hold['sharpe']:.2f}, {hold['cagr']:+.0%} в год, просадка {hold['dd']:.0%}")
        print(t.to_string(formatters={"sharpe": "{:.2f}".format, "sharpe_p10": "{:.2f}".format,
                                      "cagr": "{:+.0%}".format, "dd": "{:+.0%}".format}))


def part3(assets, picked, btc_price):
    print("\n" + "=" * 100 + "\nЧАСТЬ 3. Слоистая схема против плоского голосования")
    for asset, (df, f) in assets.items():
        S, fam = signal_pool(df, f, picked[asset], btc_price)
        D = pd.DataFrame(S).dropna()
        p = df.PriceUSD.reindex(D.index)
        vol = np.log(p).diff().rolling(30).std() * math.sqrt(365)
        vt = (0.5 / vol).clip(upper=1).fillna(1)
        onc = [c for c in D if fam[c] == "ончейн"]
        trend = D["R3: BTC 30д и RSI>50"]
        regime = D["цикл: MVRV ниже 90-го перцентиля"]
        conf = D[onc].mean(axis=1) if onc else pd.Series(1.0, index=D.index)
        V = {
            "держать": pd.Series(1.0, index=D.index),
            "только тренд R3": trend,
            "плоское голосование всех сигналов": D.mean(axis=1),
            "слой 1+2: режим цикла × тренд": regime * trend,
            "слой 2+4: тренд × размер по волатильности": trend * vt,
            "слои 1+2+4": regime * trend * vt,
            "слои 1+2+3+4: + размер ×(0.5+0.5·доля ончейн-подтверждений)": regime * trend * vt * (0.5 + 0.5 * conf),
            "слои 2+3: тренд и большинство ончейн «за»": trend * (conf > 0.5),
        }
        t = np.arange(len(p))
        half = len(p) // 2
        rows = []
        for name, pos in V.items():
            a, b, c = run_bt(pos.values, p), run_bt(pos.values, p, t < half), run_bt(pos.values, p, t >= half)
            rows.append(dict(variant=name, cagr=a["cagr"], sharpe=a["sharpe"], dd=a["dd"],
                             sharpe_1=b["sharpe"], sharpe_2=c["sharpe"], in_mkt=float(np.mean(pos))))
        print(f"\n######## {asset.upper()} {D.index[0]:%Y-%m} .. {D.index[-1]:%Y-%m}; ончейн-сигналы, отобранные на 1-й половине: "
              f"{', '.join(onc) if onc else 'нет'}")
        print(pd.DataFrame(rows).set_index("variant").to_string(formatters={
            "cagr": "{:+.0%}".format, "dd": "{:+.0%}".format, "in_mkt": "{:.0%}".format,
            "sharpe": "{:.2f}".format, "sharpe_1": "{:.2f}".format, "sharpe_2": "{:.2f}".format}))


def main():
    assets = {}
    for a in ("btc", "eth"):
        df = load(a)
        assets[a] = (df, onchain_features(df))
    picked = part1(assets)
    btc_price = assets["btc"][0].PriceUSD
    part2(assets, picked, btc_price)
    part3(assets, picked, btc_price)


if __name__ == "__main__":
    sys.exit(main())
