"""Synthetic paths A (null) and C (weak AR(1) signal), calibration (CLAUDE.md sections 10, 11.2)."""
from functools import partial

import numpy as np
import pandas as pd

from tradelab import backtest, grid, metrics, parallel, strategies, walkforward


def sb_indices(n, mean_block, rng):
    """Stationary bootstrap (Politis-Romano) indices with wrap-around."""
    start = rng.random(n) < 1.0 / mean_block
    jump = rng.integers(n, size=n)
    idx = np.empty(n, dtype=np.int64)
    idx[0] = jump[0]
    for t in range(1, n):
        idx[t] = jump[t] if start[t] else (idx[t - 1] + 1) % n
    return idx


def path(bars, seed, phi=0.0, mean_block=20):
    """Bootstrap of daily close-to-close returns; OHLC rebuilt with the source bar's relative widths.
    phi > 0 adds AR(1): r'[t] = phi * r'[t-1] + r[t]. Bar 0 is the real first bar; dates are kept."""
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    r = c[1:] / c[:-1] - 1.0
    up = h[1:] / np.maximum(o[1:], c[1:]) - 1.0
    dn = 1.0 - l[1:] / np.minimum(o[1:], c[1:])
    qv = bars["quote_volume"].to_numpy(dtype=float)[1:]
    k = sb_indices(len(r), mean_block, np.random.default_rng(seed))
    rs = r[k].copy()
    if phi:
        for t in range(1, len(rs)):
            rs[t] = phi * rs[t - 1] + rs[t]
    assert (rs > -1).all()
    close = c[0] * np.concatenate([[1.0], np.cumprod(1.0 + rs)])
    open_ = np.concatenate([[o[0]], close[:-1]])
    hi = np.concatenate([[h[0]], np.maximum(open_[1:], close[1:]) * (1 + up[k])])
    lo = np.concatenate([[l[0]], np.minimum(open_[1:], close[1:]) * (1 - dn[k])])
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close,
                         "quote_volume": np.concatenate([[bars["quote_volume"].iloc[0]], qv[k]])},
                        index=bars.index)


def evaluate(bars, cfg, funding_day, confs, nbrs, phi, seed, reps=1000):
    """Selection procedure on one path; finding = CI low of Sharpe diff vs bh > 0 and DSR > dsr_min."""
    p = path(bars, seed, phi, cfg["synth"]["bootstrap_mean_block"])
    s = strategies.config_matrix(p, cfg, funding_day, confs)
    wf = walkforward.run(p, s, cfg, funding_day, nbrs)
    bh_net = backtest.pnl(p, backtest.positions(np.ones(len(p)), cfg), cfg, funding_day)["net"][wf["oos"]]
    proc = wf["net_oos"]
    th = cfg["thresholds"]
    with np.errstate(divide="ignore", invalid="ignore"):
        daily_sr = wf["net_all"].mean(0) / wf["net_all"].std(0, ddof=1)
    daily_sr = daily_sr[np.isfinite(daily_sr)]
    res = {"phi": phi, "seed": seed, "cash_folds": int(sum(j < 0 for j in wf["choices"])),
           "best_is_sharpe": float(walkforward.sharpe_cols(wf["net_all"], cfg["annualization"]).max()),
           "sharpe_bh": metrics.sharpe(bh_net)}
    if np.std(proc) == 0:
        return {**res, "sharpe_oos": np.nan, "ci_lo": np.nan, "dsr": np.nan, "finding": False}
    ci = metrics.sharpe_diff_ci(proc, bh_net, th["ci_level"], cfg["synth"]["bootstrap_mean_block"],
                                reps, seed, cfg["annualization"])
    d = metrics.dsr(proc, daily_sr)
    return {**res, "sharpe_oos": metrics.sharpe(proc), "ci_lo": ci["lo"], "dsr": d,
            "finding": bool(ci["lo"] > 0 and d > th["dsr_min"])}


def _job(args, bars, cfg, funding_day, confs, nbrs):
    phi, seed = args
    return evaluate(bars, cfg, funding_day, confs, nbrs, phi, seed)


def calibrate(bars, cfg, funding_day, jobs=None, n_paths=None):
    """Paths A (phi = 0) and C (each phi) share seeds; returns a per-path frame."""
    n = cfg["synth"]["n_null_paths"] if n_paths is None else n_paths
    confs = grid.configs()
    nbrs = grid.neighbors(confs)
    seeds = parallel.job_seeds(cfg["seed"], n)
    tasks = [(phi, s) for phi in [0.0] + list(cfg["synth"]["ar_phi_grid"]) for s in seeds]
    fn = partial(_job, bars=bars, cfg=cfg, funding_day=np.asarray(funding_day), confs=confs, nbrs=nbrs)
    return pd.DataFrame(parallel.pmap(fn, tasks, jobs))


def verdict(res, cfg):
    fdr = float(res.loc[res.phi == 0, "finding"].mean())
    power = {float(phi): float(g["finding"].mean()) for phi, g in res[res.phi > 0].groupby("phi")}
    p20 = power.get(0.20, 0.0)
    if fdr > cfg["thresholds"]["fdr_max"]:
        status = "stop_fdr"
    elif p20 < 3 * fdr or p20 == 0:
        status = "stop_engine"
    else:
        status = "ok"
    return {"fdr": fdr, "power": power, "status": status}
