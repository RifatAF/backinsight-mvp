#!/usr/bin/env python3
"""Проверка устойчивости итоговой системы (system.py): параметры, комиссии, задержка исполнения, дата старта, бутстрэп.

  python robustness.py

Портфель исполняется так же, как в paper_bot.py: каждый день целевые веса = 1/N × позиция монеты,
сделка только если вес отклонился больше порога (2%), комиссия + проскальзывание с оборота.
"""
import math
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

import system as S
from coins_test import load as load_cb

FEE = 0.001
BASKETS = {"BTC + ETH": ["BTC", "ETH"], "6 монет с ETF": ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE"]}


def closes_for(coins):
    out = {}
    for k in coins:
        c = load_cb(k)
        c.index = c.index.tz_localize(None)
        out[k] = c
    return out


def portfolio_bt(closes, W, fee=FEE, threshold=0.02, delay=0):
    px = pd.DataFrame(closes).reindex(W.index).ffill()
    W = W.shift(delay) if delay else W
    rets = px.pct_change().fillna(0).values
    w_t = W.fillna(0).values
    cur = np.zeros(px.shape[1])
    eq, val = np.empty(len(px)), 1.0
    for i in range(len(px)):
        if i > 0:
            gross = cur * (1 + rets[i])
            val *= 1 + (gross.sum() - cur.sum())
            cur = gross / (1 + (gross.sum() - cur.sum()))   # веса после движения цены
        diff = w_t[i] - cur
        trade = np.where(np.abs(diff) > threshold, diff, 0.0)
        val *= 1 - fee * np.abs(trade).sum()
        cur = cur + trade
        eq[i] = val
    return pd.Series(eq, index=px.index)


def metrics(eq):
    r = np.log(eq).diff().dropna()
    yrs = len(r) / 365
    dd = (eq / eq.cummax() - 1).min()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    return dict(cagr=cagr, sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365), dd=dd, calmar=cagr / abs(dd))


def run_case(closes, layers, p, start, **kw):
    btc = closes["BTC"]
    W = S.targets(closes, btc, layers, p)
    W = W.loc[start:]
    return portfolio_bt(closes, W, **kw)


def fmt(df):
    return df.to_string(formatters={"cagr": "{:+.0%}".format, "dd": "{:+.0%}".format, "sharpe": "{:.2f}".format,
                                    "calmar": "{:.2f}".format})


def main():
    print("Загрузка ончейн-слоёв...", flush=True)
    layers = S.market_layers(S.fetch_onchain(), S.Params())
    for bname, coins in BASKETS.items():
        closes = closes_for(coins)
        first = max(c.index[0] for c in closes.values()) + pd.Timedelta(days=400)
        first = max(first, pd.Timestamp("2016-12-01"))
        for mode in ("A", "B"):
            base = S.Params(mode=mode)
            print(f"\n{'#' * 90}\n{bname}, режим {mode}, с {first:%Y-%m}")
            rows = {"база": metrics(run_case(closes, layers, base, first))}
            hold = pd.DataFrame(closes).loc[first:].ffill()
            rows["держать (для сравнения)"] = metrics((hold / hold.iloc[0]).mean(axis=1))

            # 1. параметры по одному
            grid = {"mom_days": [20, 45, 60], "rsi_th": [45, 55], "rsi_len": [10, 21], "vol_target": [0.4, 0.6, 0.8],
                    "sma_regime": [150, 250], "sma_slope": [15, 60], "mvrv_pct": [0.85, 0.95, 1.01]}
            for k, vals in grid.items():
                for v in vals:
                    rows[f"{k}={v}"] = metrics(run_case(closes, layers, replace(base, **{k: v}), first))
            t = pd.DataFrame(rows).T
            print("1) Чувствительность к параметрам (меняется один, остальные базовые):")
            print(fmt(t))
            pv = t.drop(["база", "держать (для сравнения)"])
            print(f"   по всем вариантам параметров: CAGR от {pv.cagr.min():+.0%} до {pv.cagr.max():+.0%}, "
                  f"медиана {pv.cagr.median():+.0%}; просадка от {pv.dd.min():+.0%} до {pv.dd.max():+.0%}; "
                  f"Sharpe медиана {pv.sharpe.median():.2f}")

            # 2. издержки и задержка
            rows = {}
            for fee in (0.001, 0.002, 0.003):
                rows[f"издержки {fee:.1%} за сделку"] = metrics(run_case(closes, layers, base, first, fee=fee))
            rows["исполнение на 1 день позже"] = metrics(run_case(closes, layers, base, first, delay=1))
            rows["исполнение на 2 дня позже"] = metrics(run_case(closes, layers, base, first, delay=2))
            rows["порог ребаланса 5%"] = metrics(run_case(closes, layers, base, first, threshold=0.05))
            rows["без порога (каждый день)"] = metrics(run_case(closes, layers, base, first, threshold=0.0))
            print("\n2) Издержки, задержка исполнения, порог ребаланса:")
            print(fmt(pd.DataFrame(rows).T))

            # 3. дата старта
            eq = run_case(closes, layers, base, first)
            rows = {}
            for y in range(first.year + (first.month > 1), 2026):
                st = pd.Timestamp(f"{y}-01-01")
                if st < first:
                    continue
                e = eq.loc[st:]
                h = hold.loc[st:]
                m = metrics(e / e.iloc[0])
                mh = metrics((h / h.iloc[0]).mean(axis=1))
                rows[f"старт {y}"] = dict(cagr=m["cagr"], dd=m["dd"], hold_cagr=mh["cagr"], hold_dd=mh["dd"])
            print("\n3) Если бы начали в начале года X (до сегодня):")
            print(pd.DataFrame(rows).T.to_string(float_format=lambda v: f"{v:+.0%}"))

            # 4. блочный бутстрэп: 3-летние траектории из 30-дневных блоков реальных доходностей
            r = eq.pct_change().dropna().values
            rng = np.random.default_rng(42)
            sims_c, sims_d = [], []
            for _ in range(2000):
                path = np.concatenate([r[s:s + 30] for s in rng.integers(0, len(r) - 30, 37)])[:1095]
                e = np.cumprod(1 + path)
                sims_c.append(e[-1] ** (1 / 3) - 1)
                sims_d.append((e / np.maximum.accumulate(e) - 1).min())
            q = lambda a: np.percentile(a, [5, 25, 50, 75, 95])
            qc, qd = q(sims_c), q(sims_d)
            print("\n4) Бутстрэп 2000 трёхлетних траекторий (перцентили 5/25/50/75/95):")
            print("   годовая доходность: " + " / ".join(f"{v:+.0%}" for v in qc))
            print("   макс. просадка:     " + " / ".join(f"{v:+.0%}" for v in qd))
            print(f"   доля траекторий с убытком за 3 года: {np.mean(np.array(sims_c) < 0):.0%}")


if __name__ == "__main__":
    sys.exit(main())
