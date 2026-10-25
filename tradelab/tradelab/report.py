"""Milestone 2: benchmark-only report. Full report is milestone 5."""
import pandas as pd

from tradelab import backtest, metrics, strategies


def _row(net):
    return {"sharpe": metrics.sharpe(net), "calmar": metrics.calmar(net),
            "max_dd": metrics.max_drawdown(net), "cagr": metrics.cagr(net), "days": len(net)}


def regime_bounds(cfg, holdout_start):
    out = {}
    for name, (a, b) in cfg["regimes"].items():
        b = holdout_start - pd.Timedelta(days=1) if str(b) == "holdout_start" else pd.Timestamp(b, tz="UTC")
        out[name] = (pd.Timestamp(a, tz="UTC"), pd.Timestamp(b))
    return out


def benchmark_table(bars, cfg, holdout_start, funding=None):
    rows = []
    for name, fn in (("bh", strategies.bh), ("vt", strategies.vt)):
        net = backtest.run(bars, fn(bars, cfg), cfg, funding)["net"]
        rows.append({"bench": name, "period": "all", **_row(net)})
        for reg, (a, b) in regime_bounds(cfg, holdout_start).items():
            rows.append({"bench": name, "period": reg, **_row(net[(net.index >= a) & (net.index <= b)])})
    return pd.DataFrame(rows)


def benchmarks_md(table, symbol, start, end):
    t = table.copy()
    for c in ("sharpe", "calmar"):
        t[c] = t[c].map("{:.2f}".format)
    for c in ("max_dd", "cagr"):
        t[c] = t[c].map("{:.1%}".format)
    head = "| " + " | ".join(t.columns) + " |\n|" + "---|" * len(t.columns) + "\n"
    body = "".join("| " + " | ".join(map(str, r)) + " |\n" for r in t.itertuples(index=False))
    return f"# Эталоны {symbol}, {start.date()} .. {end.date()} (до holdout)\n\n{head}{body}"
