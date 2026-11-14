#!/usr/bin/env python3
"""Статистика по правилам аудита (AUDIT.md, PREREG.md): дефлированный Sharpe, Sharpe без 10 лучших дней, блочный
бутстрэп, а через 3 месяца — сверка форвард-теста с replay и проверка «поломки».

  python stats_lab.py replay rp/replay_equity.csv            # оценки по истории
  python stats_lab.py forward abtest_export.zip|папка rp_fwd/replay_equity.csv
        # форвард: data_lake/equity/equity.csv из экспорта (дни catchup и дубли исключаются) против replay тех же дней

N_TRIALS — число испробованных вариантов (журнал в PREREG.md), от него зависит дефлированный Sharpe.
"""
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

N_TRIALS = (21, 100, 500)
EULER = 0.5772156649


def sharpe(r):
    r = pd.Series(r).dropna()
    return r.mean() / (r.std() + 1e-12) * math.sqrt(365) if len(r) > 2 else np.nan


def deflated_sharpe(r, n_trials, sr_std):
    """Bailey & López de Prado (2014): вероятность, что истинный Sharpe выше ожидаемого максимума n_trials пустых."""
    from statistics import NormalDist
    r = pd.Series(r).dropna()
    T = len(r)
    sr = r.mean() / (r.std() + 1e-12)                  # дневной
    g3, g4 = r.skew(), r.kurt() + 3
    z = NormalDist().inv_cdf
    sr0 = sr_std / math.sqrt(365) * ((1 - EULER) * z(1 - 1 / n_trials) + EULER * z(1 - 1 / (n_trials * math.e)))
    den = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    return NormalDist().cdf((sr - sr0) * math.sqrt(T - 1) / den)


def block_boot(r, n=2000, block=20, seed=1):
    r = pd.Series(r).dropna().values
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        idx = np.concatenate([np.arange(s, s + block) % len(r) for s in rng.integers(0, len(r), len(r) // block + 1)])
        out.append(sharpe(r[idx[:len(r)]]))
    return np.quantile(out, [0.05, 0.95])


def replay_report(path):
    E = pd.read_csv(path, index_col=0, parse_dates=True)
    R = E.pct_change().iloc[1:]
    sr_std = float(np.nanstd([sharpe(R[c]) for c in R]))
    print(f"{'стратегия':16s} {'Sharpe':>6s} {'90% ДИ':>13s} {'без 10 лучших':>13s} " +
          " ".join(f"DSR N={n:<4d}" for n in N_TRIALS))
    for c in R.columns:
        r = R[c].dropna()
        lo, hi = block_boot(r)
        top = r.sort_values().iloc[:-10]
        dsr = " ".join(f"{deflated_sharpe(r, n, sr_std):10.2f}" for n in N_TRIALS)
        print(f"{c:16s} {sharpe(r):6.2f} [{lo:5.2f};{hi:5.2f}] {sharpe(top):13.2f} {dsr}")


def windows(path, days=90, q=0.01):
    """Квантиль q доходности за окно `days` дней по replay: порог «поломки» (PREREG.md)."""
    E = pd.read_csv(path, index_col=0, parse_dates=True)
    return (E / E.shift(days) - 1).quantile(q)


def load_forward(src):
    p = Path(src)
    if p.suffix == ".zip":
        with zipfile.ZipFile(p) as z:
            name = next(n for n in z.namelist() if n.endswith("equity/equity.csv"))
            df = pd.read_csv(z.open(name))
    else:
        df = pd.read_csv(next(p.rglob("equity/equity.csv")))
    df = df.drop_duplicates(["day", "model"], keep="last")
    catch = df[df.get("catchup", False) == True].day.unique()
    df = df[~df.day.isin(catch)]
    E = df.pivot(index="day", columns="model", values="equity")
    E.index = pd.to_datetime(E.index)
    return E.sort_index(), len(catch)


def forward_report(src, replay_path):
    F, n_catch = load_forward(src)
    Rp = pd.read_csv(replay_path, index_col=0, parse_dates=True)
    q = windows(replay_path, days=max(len(F) - 1, 2))
    print(f"форвард {F.index[0].date()} .. {F.index[-1].date()}, {len(F)} дн, исключено catchup-дней: {n_catch}")
    print(f"{'стратегия':16s} {'итог':>7s} {'replay':>7s} {'корр дн':>7s} {'расхожд':>8s} {'порог 1%':>8s}  вывод")
    for m in F.columns:
        f = F[m].dropna()
        if len(f) < 5:
            continue
        r = Rp[m].reindex(f.index) if m in Rp else None
        tot = f.iloc[-1] / f.iloc[0] - 1
        if r is not None and r.notna().sum() > 5:
            rt = r.iloc[-1] / r.dropna().iloc[0] - 1
            corr = f.pct_change().corr(r.pct_change())
            gap = tot - rt
        else:
            rt = corr = gap = np.nan
        thr = q.get(m, np.nan)
        verdict = []
        if not np.isnan(corr) and (corr < 0.95 or abs(gap) > 0.03):
            verdict.append("НЕ совпадает с моделью")
        if not np.isnan(thr) and tot < thr:
            verdict.append("ПОЛОМКА или смена режима")
        print(f"{m:16s} {tot:+7.1%} {rt:+7.1%} {corr:7.2f} {gap:+8.1%} {thr:+8.1%}  {'; '.join(verdict) or 'ок'}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "replay":
        replay_report(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "forward":
        forward_report(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
