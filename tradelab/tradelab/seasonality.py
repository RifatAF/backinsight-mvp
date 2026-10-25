"""Stage 4 research: calendar effects (weekday, month, hour) on pre-holdout data, before any trading rule.

For every group (e.g. "Thursday" on ETH 1d) the open-to-open bar returns inside the group are compared
with all other bars: mean, share of up bars, Welch t-test p-value. All p-values of one report are
corrected together by Benjamini-Hochberg (q-value), so 7 weekdays x 12 months x 24 hours x coins do
not produce "discoveries" by chance. Stability: the sign of the group's excess mean in the first and
second half of the history. Descriptive only: no strategy is chosen here and nothing enters the trial journal.
"""
import numpy as np
import pandas as pd
from scipy import stats

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def bh_qvalues(p):
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    q = p[order] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


def bar_returns(bars):
    """Open-to-open return of each bar, labelled by the bar it is held in (as the backtest holds it)."""
    r = bars["open"].shift(-1) / bars["open"] - 1
    return r.dropna()


def groups(r, kind):
    idx = r.index
    if kind == "weekday":
        return idx.dayofweek, lambda g: WEEKDAYS[g]
    if kind == "month":
        return idx.month, lambda g: f"{g:02d}"
    return idx.hour, lambda g: f"{g:02d}:00"


def table(r, label, kind):
    key, name = groups(r, kind)
    half = r.index < r.index[len(r) // 2]
    rows = []
    for g in np.unique(key):
        inside = key == g
        a, b = r[inside], r[~inside]
        t = stats.ttest_ind(a, b, equal_var=False)
        h1 = r[inside & half].mean() - r[~inside & half].mean()
        h2 = r[inside & ~half].mean() - r[~inside & ~half].mean()
        rows.append({"ряд": label, "тип": kind, "группа": name(g), "n": int(inside.sum()),
                     "среднее %": 100 * a.mean(), "прочие %": 100 * b.mean(), "доля роста": float((a > 0).mean()),
                     "p": float(t.pvalue), "обе половины": bool(np.sign(h1) == np.sign(h2) and h1 != 0)})
    return rows


def run(series):
    """series: {label: (bars, kinds)}. Returns all rows with BH q-values over the whole report."""
    rows = []
    for label, (bars, kinds) in series.items():
        r = bar_returns(bars)
        for kind in kinds:
            rows += table(r, label, kind)
    df = pd.DataFrame(rows)
    df["q"] = bh_qvalues(df["p"])
    return df


def report(df, cost):
    f = df.copy()
    sig = f[(f["q"] < 0.05)]
    for c in ("среднее %", "прочие %"):
        f[c] = f[c].map(lambda x: f"{x:+.3f}")
    f["доля роста"] = f["доля роста"].map(lambda x: f"{x:.1%}")
    f["p"] = f["p"].map(lambda x: f"{x:.3f}")
    f["q"] = f["q"].map(lambda x: f"{x:.3f}")
    f["обе половины"] = f["обе половины"].map(lambda x: "да" if x else "нет")
    top = f.loc[df.sort_values("p").index[:20]]
    from tradelab import report as rep

    md = ("# Сезонность до holdout\n\n"
          f"Проверено групп: {len(df)}. Значимы после поправки на множественность (q < 0.05): {len(sig)}. "
          f"Издержки одной сделки туда и обратно около {2 * cost:.2%}, поэтому эффект, который нужно ловить "
          "входом и выходом каждый период, должен быть больше этой величины.\n\n"
          "Колонки: «среднее %» доходность бара в группе, «прочие %» остальных баров, «доля роста» доля баров "
          "в плюс, p без поправки, q с поправкой Бенджамини-Хохберга, «обе половины» знак эффекта совпал в "
          "первой и второй половине истории.\n\n## 20 групп с наименьшим p\n\n" + rep.md_table(top)
          + "\n\n## Все группы\n\n" + rep.md_table(f))
    return md
