#!/usr/bin/env python3
"""Проверка 5 идей повышения дохода поверх 12 стратегий abtest.py (тот же код решений и исполнения).

  python improve_lab.py --run base   --coins BTC,ETH                    # база
  python improve_lab.py --run base   --coins BTC,ETH,SOL,XRP,LTC,DOGE   # идея 2: больше монет
  python improve_lab.py --run perpfee                                  # идея 5: комиссии перпетуала для _x3/_ls
  python improve_lab.py --run vt                                       # идея 3: плечо по волатильности вместо x3
  python improve_lab.py --run bear                                     # идея 4: шорт только в медвежьем рынке
  Идея 1 (carry свободных денег) считается в каждом прогоне поверх кривых: см. carry_overlay.

Данные: дневные свечи OKX (data/<COIN>USDT_okx_daily.csv), funding Hyperliquid по дням с 05.2023
(data/hl_funding_daily.csv), до этого 0.01% за 8 ч. Окно по умолчанию 2021-06-01 .. последняя закрытая свеча.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import abtest as A
import system as S

SPOT_FEE, PERP_TAKER, PERP_MAKER = 0.001, 0.00055, 0.0002


def load(coin):
    d = pd.read_csv(f"data/{coin}USDT_okx_daily.csv")
    d.index = pd.to_datetime(d.timestamp, unit="ms").dt.normalize()
    d = d[~d.index.duplicated()]
    return d[d.index < pd.Timestamp.now().normalize()]


def funding_daily(coins, index):
    f = pd.read_csv("data/hl_funding_daily.csv", index_col=0, parse_dates=True)
    out = {}
    for k in coins:
        s = f[k].reindex(index) if k in f else pd.Series(np.nan, index)
        out[k] = s.fillna(A.DEFAULT_FUNDING * 3)          # до 2023: 0.01% за 8 ч
    return pd.DataFrame(out)


def port_vol(targets, closes, day, n=30):
    """Годовая волатильность портфеля с весами targets по доходностям за n дней."""
    ks = [k for k, w in targets.items() if w]
    if not ks:
        return 0.0
    r = pd.DataFrame({k: closes[k].loc[:day].pct_change().iloc[-n:] for k in ks}).fillna(0)
    w = np.array([targets[k] for k in ks])
    return float(np.sqrt(w @ r.cov().values @ w * 365))


def run(models, coins, start, fee_margin, vt_target=None, label=""):
    dfs = {k: load(k) for k in coins}
    closes = {k: v.close for k, v in dfs.items()}
    common = sorted(set.intersection(*[set(c.index) for c in closes.values()]))
    days = [d for d in common if d >= pd.Timestamp(start)]
    oc = A.onchain("improve_cache")
    ocf = A.onchain_features(oc)
    layers = S.market_layers(oc, S.Params())
    ctx = A.build_context(closes, ocf, oc, layers, coins, days[0])
    fund = funding_daily(coins, pd.DatetimeIndex(days))
    accts = {m: {"cash": 100.0, "units": {}, "start": 100.0, "base": 100.0} for m in models}
    ms = {m: {} for m in models}
    eqh = {m: {} for m in models}
    idle = {m: {} for m in models}
    stats = {m: dict(fills=0, liq=0, fees=0.0, funding=0.0) for m in models}
    for i, day in enumerate(days):
        ctx["day"] = day
        prices = {k: float(closes[k].loc[day]) for k in coins}
        lows = {k: float(dfs[k].low.loc[day]) for k in coins}
        highs = {k: float(dfs[k].high.loc[day]) for k in coins}
        f8 = {k: float(fund[k].loc[day]) / 3 for k in coins}
        for m in models:
            base, var = m.split("_")[0], (m.split("_")[1] if "_" in m else "")
            lev = 3.0 if var in ("x3", "vt") else 1.0
            short = var in ("ls", "lsb")
            margin = lev > 1 or short
            acct = accts[m]
            liq = A.check_liquidation(acct, lows, highs) if margin else None
            paid = A.apply_funding(acct, prices, f8) if margin else 0.0
            targets, diag = A.decide(base, ctx, ms[m], list(eqh[m].values()), short=short, bear_only=(var == "lsb"))
            eq0 = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in prices)
            if var == "x3":
                targets = {k: (None if v is None else v * 3) for k, v in targets.items()}
            elif var == "vt":
                tv = {k: (v or 0.0) for k, v in targets.items()}
                pv = port_vol(tv, closes, day)
                L = min(3.0, vt_target / pv) if pv > 0 else 1.0
                targets = {k: (None if v is None else v * L) for k, v in targets.items()}
            A.FEE = fee_margin if margin else SPOT_FEE
            fills = A.execute(acct, targets, prices, None, m, day, None, lev=lev, allow_short=short)
            A.FEE = SPOT_FEE
            eq = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in prices)
            gross = sum(abs(acct["units"].get(k, 0)) * prices[k] for k in prices)
            eqh[m][day] = eq
            idle[m][day] = max(0.0, (eq - gross) / eq) if eq > 0 and lev == 1 else 0.0   # свободная доля без плеча
            s = stats[m]
            s["fills"] += len(fills)
            s["liq"] += bool(liq)
            s["fees"] += sum(f["fee"] for f in fills)
            s["funding"] += paid
        if i % 250 == 0:
            print(f"{label} {day.date()} ({i}/{len(days)})", flush=True)
    E = pd.DataFrame(eqh)
    I = pd.DataFrame(idle)
    return E, I, stats, fund


def carry_overlay(E, I, fund_btc, entry_cost=SPOT_FEE + PERP_TAKER, share=0.9, start=None):
    """Идея 1: свободная доля капитала (без плеча) стоит в связке «спот BTC + шорт перпа BTC» и получает funding.
    Включено, только если средний funding за прошлые 7 дней > 0 (сигнал со сдвигом на день). Смена размера
    связки больше чем на 10% капитала оплачивается комиссией входа/выхода по обеим ногам."""
    sig = (fund_btc.rolling(7).mean().shift(1) > 0).reindex(E.index).fillna(False)
    f = fund_btc.reindex(E.index).fillna(0)
    out = {}
    for m in E:
        r = E[m].pct_change().fillna(0)
        cur, adj = 0.0, []
        for d in E.index:
            tgt = share * I[m].loc[d] if sig.loc[d] else 0.0
            cost = 0.0
            if abs(tgt - cur) > 0.10 or (tgt == 0 and cur > 0):
                cost = abs(tgt - cur) * entry_cost
                cur = tgt
            adj.append(cur * f.loc[d] - cost)
        out[m] = (1 + r + pd.Series(adj, E.index)).cumprod() * E[m].iloc[0]
    return pd.DataFrame(out)


def metrics(eq):
    if len(eq) < 3:
        return dict(cagr=np.nan, sharpe=np.nan, dd=np.nan, calmar=np.nan)
    r = eq.pct_change().dropna()
    yrs = len(r) / 365
    dd = (eq / eq.cummax() - 1).min()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1.0
    return dict(cagr=cagr, sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365), dd=dd,
                calmar=cagr / abs(dd) if dd < 0 else np.nan)


def table(E):
    t = pd.DataFrame({m: metrics(E[m]) for m in E}).T
    return t


def halves(E, split="2024-01-01"):
    a, b = E.loc[:split], E.loc[split:]
    return pd.DataFrame({m: {"cagr_1": metrics(a[m])["cagr"], "cagr_2": metrics(b[m])["cagr"],
                             "sharpe_1": metrics(a[m])["sharpe"], "sharpe_2": metrics(b[m])["sharpe"]} for m in E}).T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="base", choices=["base", "perpfee", "maker", "vt", "bear"])
    ap.add_argument("--coins", default="BTC,ETH")
    ap.add_argument("--start", default="2021-06-01")
    ap.add_argument("--vt", default="0.4,0.6,0.8")
    ap.add_argument("--out", default="improve_results")
    a = ap.parse_args()
    coins = a.coins.split(",")
    B = A.BASE_MODELS
    fee_margin = SPOT_FEE
    if a.run == "base":
        models = B + [f"{m}_x3" for m in B] + [f"{m}_ls" for m in B]
    elif a.run in ("perpfee", "maker"):
        models = [f"{m}_x3" for m in B] + [f"{m}_ls" for m in B]
        fee_margin = PERP_TAKER if a.run == "perpfee" else PERP_MAKER
    elif a.run == "bear":
        models = [f"{m}_lsb" for m in B]
    res = {}
    if a.run == "vt":
        for tv in [float(x) for x in a.vt.split(",")]:
            E, I, st, fund = run([f"{m}_vt" for m in B], coins, a.start, SPOT_FEE, vt_target=tv, label=f"vt{tv}")
            E.columns = [f"{c}{tv}" for c in E.columns]
            res[tv] = (E, st)
        E = pd.concat([r[0] for r in res.values()], axis=1)
        stats = {f"{k}{tv}": v for tv, r in res.items() for k, v in r[1].items()}
        I = None
    else:
        E, I, stats, fund = run(models, coins, a.start, fee_margin, label=a.run)
    tag = f"{a.run}_{len(coins)}c"
    Path(a.out).mkdir(exist_ok=True)
    E.to_csv(f"{a.out}/{tag}_equity.csv")
    t = table(E)
    t["сделок"] = pd.Series({m: s["fills"] for m, s in stats.items()})
    t["ликвидаций"] = pd.Series({m: s["liq"] for m, s in stats.items()})
    hold = pd.DataFrame({k: load(k).close.loc[E.index] for k in coins})
    hold = (hold / hold.iloc[0]).mean(axis=1)
    t.loc["держать поровну"] = metrics(hold)
    fmt = {"cagr": "{:+.0%}".format, "dd": "{:+.0%}".format, "sharpe": "{:.2f}".format, "calmar": "{:.2f}".format}
    print(f"\n=== {tag}: {E.index[0].date()} .. {E.index[-1].date()} ({len(E)} дней), монеты {coins}")
    print(t.to_string(formatters=fmt))
    print("\nПо половинам (до/после 2024-01-01):")
    print(halves(E).to_string(float_format=lambda v: f"{v:+.2f}"))
    if I is not None:
        I.to_csv(f"{a.out}/{tag}_idle.csv")
        sub = E.loc["2023-06-01":]
        C = carry_overlay(sub, I.loc[sub.index], fund["BTC"].reindex(sub.index))
        cmp_ = pd.DataFrame({m: {"cagr без": metrics(sub[m])["cagr"], "cagr c carry": metrics(C[m])["cagr"],
                                 "dd без": metrics(sub[m])["dd"], "dd c carry": metrics(C[m])["dd"],
                                 "свободно, %": I[m].loc[sub.index].mean()} for m in E}).T
        print("\nИдея 1, carry свободных денег (с 2023-06, реальный funding Hyperliquid BTC):")
        print(cmp_.to_string(float_format=lambda v: f"{v:+.1%}"))
        cmp_.to_csv(f"{a.out}/{tag}_carry.csv")
    t.to_csv(f"{a.out}/{tag}_table.csv")


if __name__ == "__main__":
    sys.exit(main())
