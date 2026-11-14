#!/usr/bin/env python3
"""Поиск более доходных вариантов: волатильные монеты, ротация в самые сильные монеты, более высокое плечо.

  python max_lab.py

Сигнал на монету тот же, что у M1 (system.py, режим A). Исполнение дневное, порог ребаланса 2%, минимальный
ордер не моделируется (капитал условный). Без плеча спот 0.1%; с плечом перпетуал 0.055% и funding на всю
позицию (Hyperliquid по дням с 05.2023, до этого 0.01% за 8 ч); ликвидация по худшей цене дня.
Окно 2021-06-01 .. последняя закрытая свеча, данные OKX.
"""
import math
import sys

import numpy as np
import pandas as pd

import abtest as A
import system as S

START = "2021-06-01"
BIG = ["BTC", "ETH"]
VOLATILE = ["SOL", "DOGE", "AVAX", "ADA", "LINK", "NEAR"]
ALL = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "AVAX", "LINK", "DOT", "BCH", "TRX", "BNB", "NEAR",
       "ATOM", "UNI", "SUI", "PEPE", "SHIB"]


def load(coin):
    d = pd.read_csv(f"data/{coin}USDT_okx_daily.csv")
    d.index = pd.to_datetime(d.timestamp, unit="ms").dt.normalize()
    d = d[~d.index.duplicated()]
    return d[d.index < pd.Timestamp.now().normalize()]


def main():
    dfs = {k: load(k) for k in ALL}
    idx = dfs["BTC"].loc[START:].index
    C = pd.DataFrame({k: v.close for k, v in dfs.items()}).reindex(dfs["BTC"].index)
    Lo = pd.DataFrame({k: v.low for k, v in dfs.items()}).reindex(C.index)
    Hi = pd.DataFrame({k: v.high for k, v in dfs.items()}).reindex(C.index)
    btc = C.BTC
    layers = S.market_layers(S.fetch_onchain(), S.Params())
    pos = pd.DataFrame({k: S.coin_position(C[k].dropna(), btc, layers, S.Params(mode="A")) for k in ALL}).reindex(C.index)
    age = C.notna().cumsum()
    mom30, mom90 = np.log(C).diff(30), np.log(C).diff(90)
    rets = C.pct_change(fill_method=None).fillna(0)
    fh = pd.read_csv("data/hl_funding_daily.csv", index_col=0, parse_dates=True)
    F = pd.DataFrame({k: (fh[k] if k in fh else pd.Series(dtype=float)).reindex(C.index) for k in ALL}).fillna(0.0003)

    def basket(coins):
        def w(day):
            av = [k for k in coins if age.at[day, k] >= 250 and pd.notna(pos.at[day, k])]
            return {k: pos.at[day, k] / len(av) for k in av} if av else {}
        return w

    def rotation(k, mom, weekly=True):
        state = {"pick": [], "last": None}

        def w(day):
            if state["last"] is None or not weekly or (day - state["last"]).days >= 7:
                el = [c for c in ALL if age.at[day, c] >= 250 and pd.notna(mom.at[day, c]) and pos.at[day, c] > 0]
                state["pick"] = sorted(el, key=lambda c: -mom.at[day, c])[:k]
                state["last"] = day
            return {c: pos.at[day, c] / k for c in state["pick"] if pos.at[day, c] > 0}
        return w

    def bt(wfn, lev="none", tv=0.6, cap=3.0):
        val, cur, eq, liq = 1.0, {}, [], 0
        for day in idx:
            if eq:                                      # движение цен за день + funding + ликвидация
                worst = sum(u * (Lo.at[day, k] if u > 0 else Hi.at[day, k]) / C.at[prev, k] for k, u in cur.items())
                gross = sum(abs(u) for u in cur.values())
                if lev != "none" and gross > 0 and (val - sum(cur.values())) + worst < A.MAINT_MARGIN * gross:
                    val, cur = 0.0, {}
                    liq += 1
                else:
                    pnl = sum(u * rets.at[day, k] for k, u in cur.items())
                    fund = sum(u * F.at[day, k] for k, u in cur.items()) if lev != "none" else 0.0
                    val += pnl - fund
                    cur = {k: u * (1 + rets.at[day, k]) for k, u in cur.items()}
            if val <= 0:
                eq.append(0.0)
                prev = day
                continue
            w = wfn(day)
            if lev == "fixed":
                w = {k: v * cap for k, v in w.items()}
            elif lev == "vol":
                ks = [k for k, v in w.items() if v]
                if ks:
                    r = rets[ks].loc[:day].iloc[-30:].fillna(0)
                    ww = np.array([w[k] for k in ks])
                    pv = math.sqrt(max(ww @ r.cov().values @ ww * 365, 0))
                    L = min(cap, tv / pv) if pv > 0 else 1.0
                    w = {k: v * L for k, v in w.items()}
            fee = A.PERP_FEE if lev != "none" else A.FEE
            for k in set(w) | set(cur):
                tgt, now = w.get(k, 0.0) * val, cur.get(k, 0.0)
                if abs(tgt - now) > 0.02 * val or (tgt == 0 and now != 0):
                    val -= abs(tgt - now) * fee
                    cur[k] = tgt
            cur = {k: u for k, u in cur.items() if u}
            eq.append(val)
            prev = day
        return pd.Series(eq, idx), liq

    def m(e):
        if e.iloc[-1] <= 0:                             # ликвидация: капитал обнулён
            return -1.0, np.nan, -1.0
        r = e.pct_change().dropna()
        yrs = len(r) / 365
        dd = (e / e.cummax() - 1).min()
        return (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1, r.mean() / (r.std() + 1e-12) * math.sqrt(365), dd

    cases = {
        "M1 BTC+ETH": (basket(BIG), "none", 0, 0),
        "M1 BTC+ETH плечо по вол. 0.6 до x3": (basket(BIG), "vol", 0.6, 3),
        "M1 BTC+ETH плечо по вол. 0.8 до x3": (basket(BIG), "vol", 0.8, 3),
        "M1 BTC+ETH плечо по вол. 1.0 до x5": (basket(BIG), "vol", 1.0, 5),
        "M1 BTC+ETH постоянное x3": (basket(BIG), "fixed", 0, 3),
        "M1 BTC+ETH постоянное x10": (basket(BIG), "fixed", 0, 10),
        "M1 BTC+ETH плечо по вол. 1.5 до x10": (basket(BIG), "vol", 1.5, 10),
        "M1 BTC+ETH плечо по вол. 3.0 до x10": (basket(BIG), "vol", 3.0, 10),
        "M1 6 волатильных": (basket(VOLATILE), "none", 0, 0),
        "M1 6 волатильных плечо по вол. 0.6 до x3": (basket(VOLATILE), "vol", 0.6, 3),
        "M1 все 19 монет": (basket(ALL), "none", 0, 0),
        "Ротация топ-3 по 30 дн": (rotation(3, mom30), "none", 0, 0),
        "Ротация топ-3 по 90 дн": (rotation(3, mom90), "none", 0, 0),
        "Ротация топ-5 по 30 дн": (rotation(5, mom30), "none", 0, 0),
        "Ротация топ-5 по 90 дн": (rotation(5, mom90), "none", 0, 0),
        "Ротация топ-3 по 90 дн, плечо по вол. 0.6 до x3": (rotation(3, mom90), "vol", 0.6, 3),
        "Ротация топ-5 по 90 дн, плечо по вол. 0.6 до x3": (rotation(5, mom90), "vol", 0.6, 3),
        "Ротация топ-5 по 90 дн, плечо по вол. 0.8 до x3": (rotation(5, mom90), "vol", 0.8, 3),
    }
    rows = {}
    curves = {}
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for name, (wfn, lev, tv, cap) in cases.items():
        if only and only not in name:
            continue
        e, liq = bt(wfn, lev, tv, cap)
        curves[name] = e
        c, s, dd = m(e)
        c1 = m(e.loc[:"2023-12-31"])[0] if e.loc[:"2023-12-31"].iloc[-1] > 0 else -1
        c2 = m(e.loc["2024-01-01":])[0] if e.iloc[-1] > 0 else -1
        rows[name] = dict(cagr=c, sharpe=s, dd=dd, cagr_2021_23=c1, cagr_2024_26=c2, ликвидаций=liq)
        print(f"{name:52s} {c:+6.0%} в год  Sharpe {s:4.2f}  просадка {dd:+5.0%}  "
              f"2021–23 {c1:+5.0%}  2024–26 {c2:+5.0%}  ликв. {liq}", flush=True)
    hold = (C[BIG].loc[idx] / C[BIG].loc[idx].iloc[0]).mean(axis=1)
    c, s, dd = m(hold)
    print(f"{'держать BTC+ETH':52s} {c:+6.0%} в год  Sharpe {s:4.2f}  просадка {dd:+5.0%}")
    if not only:
        pd.DataFrame(rows).T.to_csv("improve_results/max_lab.csv")
        pd.DataFrame(curves).to_csv("improve_results/max_lab_curves.csv")


if __name__ == "__main__":
    sys.exit(main())
