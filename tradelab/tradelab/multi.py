"""Multi-strategy: one portfolio of every procedure run so far (stage 1 grid, generator, every
experiment, stage 5 perps), built only from their stitched out-of-sample daily returns.

Each component is the honest OOS curve of a whole selection procedure, so no component was picked by
its own future. The combination rules are fixed in advance and use only past component returns:
  A  inverse volatility over all components (vol of the last 90 OOS days, at least 90 days of history)
  B  as A, but only components whose last-365-day OOS Sharpe (at least 90 days) is positive; else cash
  C  50% ETH with volatility target (vt) + 50% rule B
Weights are reset on the first day of each month; moving capital between components costs
REALLOC_COST per unit of weight changed. Rule B is the primary rule for the verdict; all three are
journal trials, so the DSR counts them.
"""
import hashlib
import json

import numpy as np
import pandas as pd

from tradelab import journal, metrics, selection

RULES = ("A", "B", "C")
PRIMARY = "B"
MIN_DAYS = 90
REALLOC_COST = 0.0015
STAGE = "multi"


def load(oos_dir):
    comps = {}
    for p in sorted(oos_dir.glob("*.parquet")):
        s = pd.read_parquet(p)["net"]
        comps[p.stem] = s[~s.index.duplicated(keep="last")].sort_index()
    return comps


def matrix(comps):
    idx = None
    for s in comps.values():
        idx = s.index if idx is None else idx.union(s.index)
    days = pd.date_range(idx[0], idx[-1], freq="D", tz="UTC")
    return pd.DataFrame({k: s.reindex(days) for k, s in comps.items()})  # NaN before a component's OOS start


def weights(x, rule):
    """Monthly weights from data strictly before each month's first day."""
    n = x.notna().cumsum()
    vol = x.rolling(MIN_DAYS, min_periods=MIN_DAYS).std().shift(1)
    m = x.rolling(365, min_periods=MIN_DAYS).mean().shift(1)
    sd = x.rolling(365, min_periods=MIN_DAYS).std().shift(1)
    ok = (n.shift(1) >= MIN_DAYS) & (vol > 0)
    if rule in ("B", "C"):
        ok &= (m / sd) > 0
    inv = (1 / vol).where(ok)
    w = inv.div(inv.sum(axis=1), axis=0).fillna(0.0)
    first = w.index.day == 1
    w = w.where(pd.Series(first, index=w.index), np.nan)
    w.iloc[0] = 0.0
    return w.ffill()


def combine(x, rule, vt=None):
    w = weights(x, rule)
    net = (w * x.fillna(0.0)).sum(axis=1)
    cost = w.diff().abs().sum(axis=1).fillna(0.0) * REALLOC_COST
    net = net - cost
    if rule == "C":
        net = 0.5 * vt.reindex(x.index).fillna(0.0) + 0.5 * net
    return net, w


def run(comps, cfg, conn, bench):
    """bench: {"bh": Series, "vt": Series} daily ETH benchmarks on a full calendar."""
    x = matrix(comps)
    th = cfg["thresholds"]
    key = hashlib.sha256(json.dumps(sorted(comps)).encode()).hexdigest()[:16]
    nets = {r: combine(x, r, bench["vt"])[0] for r in RULES}
    w_b = combine(x, "B")[1]
    live = (w_b.sum(axis=1) > 0)
    start = live.idxmax() if live.any() else x.index[-1]
    days = x.index[x.index >= start]
    mine = {t["config"]["rule"]: t for t in journal.trials(conn)
            if t["config"].get("stage") == STAGE and t["config"].get("components") == key}
    rng = [str(days[0].date()), str(days[-1].date())]
    for r in RULES:
        m = selection.trial_metrics(nets[r][days].to_numpy(), np.ones(len(days)), cfg)
        if r not in mine:
            tid = journal.start_trial(conn, {"stage": STAGE, "rule": r, "components": key, "names": sorted(comps)},
                                      rng, key)
            journal.finish_trial(conn, tid, m)
    srs = np.array([s for s in (e["payload"]["metrics"]["daily_sr"] for e in journal.events(conn, "trial_metrics"))
                    if s is not None])
    b = {k: v.reindex(days).fillna(0.0).to_numpy() for k, v in bench.items()}
    out = {"components": sorted(comps), "start": str(days[0].date()), "days": len(days), "n_trials_total": len(srs),
           "rules": {}, "component_stats": {}}
    for r in RULES:
        p = nets[r][days].to_numpy()
        out["rules"][r] = {**selection._curve(p), "dsr": metrics.dsr(p, srs),
                           "ci_vs_bh": metrics.sharpe_diff_ci(p, b["bh"], th["ci_level"], cfg["synth"]["bootstrap_mean_block"], 1000, cfg["seed"]),
                           "ci_vs_vt": metrics.sharpe_diff_ci(p, b["vt"], th["ci_level"], cfg["synth"]["bootstrap_mean_block"], 1000, cfg["seed"])}
    out["bh"], out["vt"] = selection._curve(b["bh"]), selection._curve(b["vt"])
    for k in comps:
        s = x[k].dropna()
        out["component_stats"][k] = {"start": str(s.index[0].date()), "days": len(s), **selection._curve(s.to_numpy()),
                                     "avg_weight_B": float(w_b[k][days].mean())}
    p = out["rules"][PRIMARY]
    checks = {"sharpe_above_bh_vt": p["sharpe"] > max(out["bh"]["sharpe"], out["vt"]["sharpe"]),
              "calmar_above_bh_vt": p["calmar"] > max(out["bh"]["calmar"], out["vt"]["calmar"]),
              "ci_low_above_0_vs_bh": p["ci_vs_bh"]["lo"] > 0, "ci_low_above_0_vs_vt": p["ci_vs_vt"]["lo"] > 0,
              "dsr": p["dsr"] > th["dsr_min"], "max_dd": abs(p["max_dd"]) <= th["max_drawdown"]}
    out["checks"] = {k: bool(v) for k, v in checks.items()}
    out["passed"] = all(out["checks"].values())
    out["verdict"] = ("мульти-стратегия бьёт удержание вне выборки (holdout не проверялся)" if out["passed"]
                      else "не найдена")
    curves = pd.DataFrame({**{f"rule_{r}": nets[r][days] for r in RULES},
                           "bh": pd.Series(b["bh"], index=days), "vt": pd.Series(b["vt"], index=days)})
    return out, curves
