#!/usr/bin/env python3
"""Поиск «экспертной» стратегии, ортогональной тренду: покупка просадок в бычьем рынке, премия Coinbase,
перекос толпы по funding. Выбор по 2021-06..2023-12, проверка на 2024..2026 (вне выборки)."""
import math, sys
import numpy as np, pandas as pd
import system as S
from max_lab import load

FEE = 0.001

def cb(coin):
    d = pd.read_csv(f"data/{coin}USD_cb_daily.csv"); d.index = pd.to_datetime(d.timestamp, unit="ms").dt.normalize()
    return d.close[~d.index.duplicated()]

def bt(W, C):
    W = W.reindex(C.index).fillna(0); R = C.pct_change().fillna(0)
    val, cur, out = 1.0, pd.Series(0.0, C.columns), []
    for d in C.index:
        val += (cur * R.loc[d]).sum(); cur = cur * (1 + R.loc[d])
        tgt = W.loc[d] * val
        tr = (tgt - cur).where((tgt - cur).abs() > 0.02 * val, 0.0)
        val -= tr.abs().sum() * FEE; cur = cur + tr; out.append(val)
    return pd.Series(out, C.index)

def m(e):
    r = e.pct_change().dropna(); y = len(r) / 365
    return (e.iloc[-1]/e.iloc[0])**(1/y)-1, r.mean()/(r.std()+1e-12)*math.sqrt(365), (e/e.cummax()-1).min()

def main():
    coins = ["BTC", "ETH"]
    C = pd.DataFrame({k: load(k).close for k in coins}).loc["2020-01-01":]
    P = pd.DataFrame({k: np.log(cb(k).reindex(C.index) / C[k]) for k in coins})   # премия Coinbase к OKX
    btc = C.BTC; sma = btc.rolling(200).mean(); bull = (btc > sma) & (sma > sma.shift(30))
    r7 = np.log(C).diff(7); z7 = (r7 - r7.rolling(365).mean()) / r7.rolling(365).std()
    fh = pd.read_csv("data/hl_funding_daily.csv", index_col=0, parse_dates=True)
    F = pd.DataFrame({k: fh[k] for k in coins}).reindex(C.index)
    layers = S.market_layers(S.fetch_onchain(), S.Params())
    m1 = pd.DataFrame({k: S.coin_position(C[k], btc, layers, S.Params(mode="A")) for k in coins})
    def dip(th, hold):
        out = pd.DataFrame(0.0, C.index, coins)
        for k in coins:
            left = 0
            for i, d in enumerate(C.index):
                if bull.loc[d] and z7.at[d, k] < th: left = hold
                if left > 0 and r7.at[d, k] > 0.05: left = 0
                out.at[d, k] = 1.0 if left > 0 else 0.0; left -= 1
        return out
    prem = lambda n: (P.rolling(n).mean() > 0).astype(float)
    cases = {}
    for th in (-1.0, -1.5, -2.0):
        for hold in (7, 14, 21):
            cases[f"Покупка просадок в быке z<{th}, держать до {hold} дн"] = dip(th, hold)
    for n in (3, 7, 14):
        cases[f"Премия Coinbase {n} дн > 0"] = prem(n)
        cases[f"Просадки z<-1.5/14д + премия {n} дн"] = (dip(-1.5, 14) * prem(n))
    cases["Перекос толпы: funding 3д < 0 и цена выше SMA50"] = ((F.rolling(3).mean() < 0) & (C > C.rolling(50).mean())).astype(float)
    cases["M1 (для сравнения)"] = m1
    rows = {}
    for name, w in cases.items():
        e = bt(w.shift(0).loc["2021-06-01":] / len(coins), C.loc["2021-06-01":])
        a, b = e.loc[:"2023-12-31"], e.loc["2024-01-01":]
        corr = e.pct_change().corr(bt(m1.loc["2021-06-01":] / 2, C.loc["2021-06-01":]).pct_change())
        rows[name] = dict(cagr=m(e)[0], sharpe=m(e)[1], dd=m(e)[2], cagr_2021_23=m(a)[0], sharpe_2021_23=m(a)[1],
                          cagr_2024_26=m(b)[0], sharpe_2024_26=m(b)[1], corr_M1=corr, в_рынке=(w.loc["2021-06-01":].mean(axis=1) > 0).mean())
        print(f"{name:52s} {rows[name]['cagr']:+5.0%}  Sh {rows[name]['sharpe']:4.2f}  dd {rows[name]['dd']:+4.0%}  "
              f"21–23 {rows[name]['cagr_2021_23']:+5.0%}/{rows[name]['sharpe_2021_23']:4.2f}  "
              f"24–26 {rows[name]['cagr_2024_26']:+5.0%}/{rows[name]['sharpe_2024_26']:4.2f}  корр с M1 {corr:+.2f}  в рынке {rows[name]['в_рынке']:.0%}", flush=True)
    pd.DataFrame(rows).T.to_csv("improve_results/expert_lab.csv")

if __name__ == "__main__":
    sys.exit(main())
