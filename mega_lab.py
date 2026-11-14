#!/usr/bin/env python3
"""Разбор всех стратегий форвард-теста по одной истории (abtest.py replay) и сборка портфеля из них.

  python abtest.py replay --exchange okx --days 2100 --dir rp     # кривые всех стратегий тем же кодом, что на ноутбуке
  python mega_lab.py rp/replay_equity.csv

1) Каждая стратегия: доходность в год, Sharpe, просадка, по годам, худший месяц, корреляция с M1 и с BTC.
2) Портфели из стратегий. Веса только по прошлому (без заглядывания вперёд), правила заданы до запуска:
   равные доли; обратная волатильность за 90 дней; «лидеры» (3 лучших по Sharpe за 180 дней, ребаланс раз в месяц);
   плюс общий контроль риска: волатильность портфеля к цели 35% годовых (плечо до 2), тормоз при просадке > 25%.
Проверка: первая половина истории / вторая половина отдельно.
"""
import math
import sys

import numpy as np
import pandas as pd


def stats(r):
    r = r.dropna()
    if len(r) < 30:
        return dict(cagr=np.nan, sharpe=np.nan, dd=np.nan)
    e = (1 + r).cumprod()
    return dict(cagr=e.iloc[-1] ** (365 / len(r)) - 1, sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365),
                dd=(e / e.cummax() - 1).min())


def line(name, r, split):
    a, b, c = stats(r), stats(r.loc[:split]), stats(r.loc[split:])
    print(f"{name:44s} {a['cagr']:+6.0%} {a['sharpe']:5.2f} {a['dd']:+5.0%} | 1-я пол. {b['cagr']:+5.0%} Sh {b['sharpe']:5.2f}"
          f" | 2-я пол. {c['cagr']:+5.0%} Sh {c['sharpe']:5.2f} {c['dd']:+5.0%}")
    return dict(name=name, **a, sh1=b["sharpe"], sh2=c["sharpe"], c1=b["cagr"], c2=c["cagr"])


def vol_target(r, target=0.35, cap=2.0, look=30):
    v = r.rolling(look, min_periods=10).std().shift(1) * math.sqrt(365)
    return r * (target / v).clip(upper=cap).fillna(1.0)


def dd_brake(r, limit=0.25, cut=0.5):
    out, eq, peak = [], 1.0, 1.0
    for x in r.fillna(0):
        k = cut if eq / peak - 1 < -limit else 1.0
        y = k * x
        out.append(y)
        eq *= 1 + y
        peak = max(peak, eq)
    return pd.Series(out, r.index)


def blend(R, weights_fn, every=30):
    W, cur = [], None
    for i, t in enumerate(R.index):
        if cur is None or i % every == 0:
            cur = weights_fn(R.iloc[:i])
        W.append(cur)
    W = pd.DataFrame(W, index=R.index).reindex(columns=R.columns).fillna(0)
    return (W * R.fillna(0)).sum(axis=1)


def main():
    E = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else "rp/replay_equity.csv", index_col=0, parse_dates=True)
    hold = [c for c in E.columns if c.startswith("держать")]
    R = E.pct_change()
    R = R.iloc[1:]
    split = R.index[len(R) // 2]
    print(f"история {R.index[0].date()} .. {R.index[-1].date()}, раздел {split.date()}\n")
    print(f"{'стратегия':44s} {'в год':>6s} {'Sh':>5s} {'просад':>5s}")
    rows = [line(c, R[c], split) for c in R.columns]
    pd.DataFrame(rows).to_csv("improve_results/mega_lab_models.csv", index=False)

    models = [c for c in R.columns if c not in hold]
    print("\nпо годам:")
    print((R[models + hold].groupby(R.index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(0).to_string())
    print("\nхудший месяц:")
    print((R[models].groupby(R.index.to_period("M")).apply(lambda x: (1 + x).prod() - 1).min() * 100).round(0).to_string())
    base = R[hold[0]] if hold else R["M1"]
    print("\nкорреляция с M1 и с «держать»:")
    print(pd.DataFrame({"M1": R[models].corrwith(R["M1"]), "держать": R[models].corrwith(base)}).round(2).to_string())

    print("\n--- портфели из стратегий (веса по прошлому) ---")
    sleeves = ["M1", "ROT3", "EXP", "FMIX"]            # разные источники дохода: тренд, ротация, паника, funding
    sleeves = [s for s in sleeves if s in R]
    ew = lambda past: {k: 1 / len(models) for k in models}
    rows = [line("Все стратегии поровну", blend(R[models], ew), split)]
    rows.append(line("Тренд+ротация+паника+funding поровну", blend(R[sleeves], lambda p: {k: 1 / len(sleeves) for k in sleeves}), split))

    def inv_vol(past, cols=sleeves):
        v = past[cols].iloc[-90:].std()
        if len(past) < 30 or v.isna().all():
            return {k: 1 / len(cols) for k in cols}
        iv = (1 / v.replace(0, np.nan)).fillna(0)
        return (iv / iv.sum()).to_dict()
    ivr = blend(R[sleeves], inv_vol)
    rows.append(line("Тот же набор, обратная волатильность 90д", ivr, split))

    def leaders(past, k=3):
        if len(past) < 180:
            return {c: 1 / len(models) for c in models}
        p = past[models].iloc[-180:]
        sh = p.mean() / (p.std() + 1e-12)
        top = sh.sort_values().index[-k:]
        return {c: 1 / k for c in top}
    rows.append(line("Лидеры: 3 лучших по Sharpe за 180д", blend(R[models], leaders), split))
    rows.append(line("Обр. волатильность + цель 35% (до x2)", vol_target(ivr), split))
    rows.append(line("Обр. волатильность + цель 35% + тормоз −25%", dd_brake(vol_target(ivr)), split))
    rows.append(line("COMBO + цель 35%", vol_target(R["COMBO"]) if "COMBO" in R else ivr * 0, split))
    pd.DataFrame(rows).to_csv("improve_results/mega_lab_portfolios.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
