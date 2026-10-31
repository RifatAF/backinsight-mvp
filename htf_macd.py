#!/usr/bin/env python3
"""MACD старших таймфреймов: внизу на пределе покупать смелее, наверху на пределе шорт или выход.

  python htf_macd.py                 # ETH с 2016 и BTC с 2015 (Coinbase), недельный и месячный MACD
  python htf_macd.py --fee 0.001

Как избегается заглядывание вперёд:
- недельная или месячная свеча учитывается только после её закрытия, позиция меняется со следующего дня;
- «низ» и «верх» MACD определяются по перцентилю на всей истории ДО текущего момента (expanding), а не по всей выборке.
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import predictive_lab as pl

LEVER_COST = 0.10   # годовая стоимость плеча для части позиции сверх 1x (funding/займ)


def load(symbol, start):
    path = Path("data") / f"{symbol.replace('/', '')}_cb_daily.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        cb = pl.exchange("coinbase")
        df = pl.fetch_ohlcv(cb, symbol, "1d", int(pd.Timestamp(start, tz="UTC").timestamp() * 1000), 300)
        path.parent.mkdir(exist_ok=True)
        df.to_csv(path, index=False)
    df["dt"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    return df.set_index("dt")


def htf_macd(close, rule):
    """MACD на закрытых свечах старшего ТФ, нормированный на цену; протянут на дни со сдвигом на 1 день."""
    c = close.resample(rule).last()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    norm = macd / c
    min_hist = 26 if rule.startswith("W") else 24   # прогрев
    pct = norm.expanding(min_hist).apply(lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
    out = pd.DataFrame({"norm": norm, "pct": pct, "hist": hist, "hist_up": (hist.diff() > 0).astype(float)})
    out.iloc[:min_hist] = np.nan
    # метка resample = конец периода (воскресенье или последний день месяца); известна после закрытия -> со следующего дня
    out.index = out.index + pd.Timedelta(days=1)
    return out.reindex(close.index, method="ffill")


def strategies(m, lo, hi):
    p, up = m.pct, m.hist_up.astype(float)
    valid = p.notna()
    S = {}
    # 1. чистый контртренд на экстремумах: лонг в нижней зоне, шорт в верхней, иначе вне рынка
    S[f"зоны: long <{lo:.0%}, short >{hi:.0%}"] = np.where(p < lo, 1, np.where(p > hi, -1, 0))
    # 2. то же, но вход только после разворота гистограммы (подтверждение)
    S["зоны + разворот гистограммы"] = np.where((p < lo) & (up == 1), 1, np.where((p > hi) & (up == 0), -1, 0))
    # 3. «смелее внизу»: базово 1x лонг, внизу 2x, наверху 0 (без шорта)
    S["размер: 2x внизу, 1x середина, 0 наверху"] = np.where(p < lo, 2, np.where(p > hi, 0, 1))
    # 4. то же с шортом наверху
    S["размер: 2x внизу, 1x середина, -1 наверху"] = np.where(p < lo, 2, np.where(p > hi, -1, 1))
    # 5. плавный размер по перцентилю: от 2x на самом дне до 0 на вершине
    S["плавно: 2x * (1 - перцентиль)"] = 2 * (1 - p)
    # 6. для сравнения: классика, следовать за MACD (выше нуля лонг, иначе вне рынка)
    S["тренд: MACD > 0 -> long, иначе 0"] = np.where(m.norm > 0, 1, 0)
    S["тренд: гистограмма растёт -> long"] = np.where(up == 1, 1, 0)
    return {k: np.where(valid, np.asarray(v, dtype=float), np.nan) for k, v in S.items()}


def run_bt(pos, ret, fee):
    p = np.nan_to_num(pos)
    nxt = np.r_[ret[1:], 0.0]
    lev = np.clip(np.abs(p) - 1, 0, None) * LEVER_COST / 365
    # простая доходность, чтобы плечо и шорт считались корректно
    pnl = p * (np.exp(nxt) - 1) - fee * np.abs(np.diff(np.r_[0, p])) - lev
    return np.log1p(np.clip(pnl, -0.99, None))


def metrics(lp, mask):
    x = lp[mask]
    if len(x) < 200:
        return None
    eq = np.exp(np.cumsum(x))
    return dict(cagr=np.exp(x.sum() * 365 / len(x)) - 1, sharpe=x.mean() / (x.std() + 1e-12) * math.sqrt(365),
                dd=(eq / np.maximum.accumulate(eq) - 1).min(), mult=eq[-1])


def analyze(name, df, fee, zones):
    ret = np.log(df.close).diff().fillna(0).values
    yrs = df.index.year.values
    print(f"\n################ {name}: {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}")
    for rule, label in (("W-SUN", "недельный"), ("ME", "месячный")):
        m = htf_macd(df.close, rule)
        first = int(np.argmax(m.pct.notna().values))
        t = np.arange(len(df))
        cut = first + (len(df) - first) // 2
        print(f"\n=== MACD {label}, сигналы с {df.index[first]:%Y-%m-%d}, вторая половина с {df.index[cut]:%Y-%m-%d} ===")
        rows = []
        bh = np.ones(len(df))
        cands = {"buy & hold": bh}
        for lo, hi in zones:
            for k, v in strategies(m, lo, hi).items():
                if k.startswith(("тренд", "плавно")):
                    cands.setdefault(k, v)
                elif k.startswith("зоны:"):
                    cands[k] = v
                else:
                    cands[f"{k} [{lo:.0%}/{hi:.0%}]"] = v
        for k, v in cands.items():
            lp = run_bt(v, ret, fee)
            a, h1, h2 = metrics(lp, t >= first), metrics(lp, (t >= first) & (t < cut)), metrics(lp, t >= cut)
            if not a:
                continue
            row = {"стратегия": k, "CAGR": a["cagr"], "Sharpe": a["sharpe"], "просадка": a["dd"], "x": a["mult"],
                   "CAGR 1-я пол.": h1["cagr"], "CAGR 2-я пол.": h2["cagr"],
                   "в рынке": np.mean(np.nan_to_num(v[first:]) != 0)}
            for y in sorted(set(yrs[first:])):
                row[str(y)] = np.exp(lp[(yrs == y) & (t >= first)].sum()) - 1
            rows.append(row)
        r = pd.DataFrame(rows).set_index("стратегия")
        f = r.copy()
        for c in f:
            f[c] = r[c].map({"Sharpe": "{:.2f}".format, "x": "{:.1f}x".format}.get(c, "{:+.0%}".format))
        print(f[["CAGR", "Sharpe", "просадка", "x", "CAGR 1-я пол.", "CAGR 2-я пол.", "в рынке"]].to_string())
        print(f[[c for c in f if c.isdigit()]].to_string())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fee", type=float, default=0.001)
    a = ap.parse_args()
    zones = [(0.2, 0.8), (0.1, 0.9)]
    for sym, start in (("ETH/USD", "2016-05-01"), ("BTC/USD", "2015-01-01")):
        analyze(sym, load(sym, start), a.fee, zones)


if __name__ == "__main__":
    sys.exit(main())
