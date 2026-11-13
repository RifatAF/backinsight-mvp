#!/usr/bin/env python3
"""Как читать открытый интерес (OI) и funding: OI в USD или в монетах? Перпы Binance (data.binance.vision metrics).

  python tools_fetch_metrics.py BTC ETH SOL XRP DOGE BNB
  python oi_lab.py

1) Насколько OI в USD «загрязнён» ценой: корреляция дневного изменения OI с доходностью дня.
2) Предсказательная сила: ранговая корреляция (IC) сигнала с доходностью следующих 7 дней, по монетам и половинам.
3) Классическая матрица «цена × OI» (за 7 дней) отдельно для OI в монетах и в USD: средний рост следующих 7 дней.
4) OI + funding: набор плеча при высоком / отрицательном funding.
5) Лучшие сигналы как стратегии (корзина монет, издержки, funding) против плацебо (сдвиг сигнала).
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F
from new_lab import placebo

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
SPLIT = "2024-01-01"


def load():
    C, Fd, _ = F.load()
    M = {}
    for k in COINS:
        m = pd.read_csv(f"data/binance/{k}_metrics.csv", parse_dates=["day"]).set_index("day")
        M[k] = m
    oc = pd.DataFrame({k: M[k].oi_coin for k in COINS}).reindex(C.index)
    ou = pd.DataFrame({k: M[k].oi_usd for k in COINS}).reindex(C.index)
    tk = pd.DataFrame({k: M[k].taker_ls for k in COINS}).reindex(C.index)
    ls = pd.DataFrame({k: M[k].acc_ls for k in COINS}).reindex(C.index)
    # битые дни (OI = 0 или скачок > 3x) убрать
    for X in (oc, ou):
        X[(X <= 0) | (X / X.shift(1) > 3) | (X / X.shift(1) < 1 / 3)] = np.nan
    return C[COINS], Fd[COINS], oc, ou, tk, ls


def _sp(a, b):
    m = a.notna() & b.notna()
    return a[m].rank().corr(b[m].rank()) if m.sum() > 30 else np.nan


def ic(sig, fwd):
    out = {}
    for name, sl in (("всё", slice(None)), ("2020–23", slice(None, "2023-12-31")), ("2024–26", slice(SPLIT, None))):
        v = [_sp(sig[k].loc[sl], fwd[k].loc[sl]) for k in sig]
        out[name] = np.nanmean(v)
    out["монет с IC>0"] = sum(_sp(sig[k], fwd[k]) > 0 for k in sig)
    return out


def main():
    C, Fd, oc, ou, tk, ls = load()
    R = C.pct_change(fill_method=None)
    fwd = C.shift(-7) / C - 1
    start = oc.dropna(how="all").index[0]
    print(f"данные OI: {start.date()} .. {oc.dropna(how='all').index[-1].date()}, монеты {COINS}\n")

    print("1) Корреляция дневного изменения OI с доходностью ТОГО ЖЕ дня (загрязнение ценой):")
    for k in COINS:
        a = np.log(oc[k]).diff().corr(R[k]); b = np.log(ou[k]).diff().corr(R[k])
        print(f"   {k:5s} OI в монетах {a:+.2f}   OI в USD {b:+.2f}")

    d7c, d7u = np.log(oc).diff(7), np.log(ou).diff(7)
    z = lambda x: (x - x.rolling(90, min_periods=60).mean()) / x.rolling(90, min_periods=60).std()
    lev_c = np.log(oc / oc.rolling(90, min_periods=60).mean())     # OI относительно своей средней за 90 дней
    lev_u = np.log(ou / ou.rolling(90, min_periods=60).mean())
    f7 = Fd.rolling(7).mean()
    p7 = np.log(C).diff(7)
    sigs = {
        "Изм. OI за 7д, в монетах": d7c, "Изм. OI за 7д, в USD": d7u,
        "z изм. OI 7д, в монетах": z(d7c), "z изм. OI 7д, в USD": z(d7u),
        "OI к средней 90д, в монетах": lev_c, "OI к средней 90д, в USD": lev_u,
        "Funding 7д": f7, "Taker buy/sell объём 7д": tk.rolling(7).mean(), "Доля лонг-аккаунтов 7д": ls.rolling(7).mean(),
        "OI в монетах растёт × funding (знак)": np.sign(d7c) * f7 / f7.abs().rolling(90, min_periods=60).mean(),
    }
    print("\n2) IC с доходностью следующих 7 дней (среднее по 6 монетам; |IC| > 0.05 уже заметно):")
    print(f"   {'сигнал':40s} {'всё':>6s} {'20–23':>6s} {'24–26':>6s} {'IC>0':>5s}")
    for n, s in sigs.items():
        r = ic(s.loc[start:], fwd.loc[start:])
        print(f"   {n:40s} {r['всё']:+6.3f} {r['2020–23']:+6.3f} {r['2024–26']:+6.3f} {r['монет с IC>0']:4d}/6")

    print("\n3) Матрица «цена × OI» за 7 дней → средний рост следующих 7 дней (все монеты, дни после старта OI):")
    for label, d in (("OI в монетах", d7c), ("OI в USD", d7u)):
        cells = {"цена↑ OI↑ (новые лонги)": (p7 > 0) & (d > 0), "цена↑ OI↓ (закрытие шортов)": (p7 > 0) & (d < 0),
                 "цена↓ OI↑ (новые шорты)": (p7 < 0) & (d > 0), "цена↓ OI↓ (ликвидация лонгов)": (p7 < 0) & (d < 0)}
        base = fwd.loc[start:][oc.loc[start:].notna()].stack().dropna().mean()
        print(f"   {label}: (средний 7д рост всех дней {base:+.2%})")
        for cn, m in cells.items():
            v = fwd.loc[start:][m.loc[start:]].stack().dropna()
            a = fwd.loc[start:"2023-12-31"][m.loc[start:"2023-12-31"]].stack().mean()
            b = fwd.loc[SPLIT:][m.loc[SPLIT:]].stack().mean()
            print(f"     {cn:34s} {v.mean():+6.2%}  дней {len(v):5d} | 20–23 {a:+6.2%} | 24–26 {b:+6.2%}")

    print("\n4) OI + funding (7 дней) → средний рост следующих 7 дней:")
    hi = f7 > f7.rolling(365, min_periods=180).quantile(0.8)
    neg = f7 < 0
    for label, d in (("монеты", d7c), ("USD", d7u)):
        up = d > d.rolling(90, min_periods=60).quantile(0.8)
        dn = d < d.rolling(90, min_periods=60).quantile(0.2)
        for cn, m in {"OI резко растёт + funding высокий (толпа в лонгах)": up & hi,
                      "OI резко растёт + funding < 0 (толпа в шортах)": up & neg,
                      "OI резко падает + funding < 0 (капитуляция)": dn & neg,
                      "OI резко падает + funding высокий": dn & hi}.items():
            v = fwd.loc[start:][m.loc[start:]].stack().dropna()
            b = fwd.loc[SPLIT:][m.loc[SPLIT:]].stack().mean()
            print(f"   [{label:6s}] {cn:50s} {v.mean():+6.2%}  дней {len(v):4d} | 24–26 {b:+6.2%}")

    print("\n5) Стратегии (лонг монеты по сигналу, корзина из 6, 1/6 на монету), против плацебо:")
    Cf, Ff = C.loc[start:], Fd.loc[start:]
    alive = Cf.notna().astype(float)
    tests = {
        "Лонг: цена↓ OI↓ в монетах (после ликвидаций)": ((p7 < 0) & (d7c < 0)),
        "Лонг: цена↓ OI↓ в USD": ((p7 < 0) & (d7u < 0)),
        "Лонг: OI в монетах ниже средней 90д (плечо сброшено)": lev_c < 0,
        "Лонг: OI в USD ниже средней 90д": lev_u < 0,
        "Лонг: OI в монетах резко растёт + funding < 0": (d7c > d7c.rolling(90, min_periods=60).quantile(0.8)) & neg,
        "Вне рынка, когда OI в монетах растёт и funding высокий": ~((d7c > 0) & hi),
    }
    for n, m in tests.items():
        W = (m.loc[start:].astype(float) * alive) / len(COINS)
        W = W.reindex(columns=Cf.columns).fillna(0)
        r = F.run_weights(W, Cf, Ff)
        row = F.report(n, r)
        print(f"{'':52s} плацебо 95%: {placebo(W, Cf, Ff, 0):5.2f}")
    F.report("Держать 6 монет поровну", R.loc[start:].mean(axis=1).fillna(0))


if __name__ == "__main__":
    sys.exit(main())
