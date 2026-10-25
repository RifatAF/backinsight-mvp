"""Stress tests of one fixed config (CLAUDE.md section 11.5)."""
import copy

import numpy as np

from tradelab import backtest, metrics, strategies, synth


def candidate_net(bars, cfg, funding_day, conf, delay=0):
    s = strategies.config_matrix(bars, cfg, funding_day, [conf])[:, 0]
    if delay:
        s = np.concatenate([np.zeros(delay), s[:-delay]])  # executes at open of t + 1 + delay
    return backtest.pnl(bars, backtest.positions(s, cfg), cfg, funding_day)["net"][:-1]


def _sharpe(x):
    x = np.asarray(x, dtype=float)
    return float(metrics.sharpe(x)) if x.std() > 0 else 0.0


def bootstrap_share(net, cfg, n=None, seed=None):
    n = cfg["stress"]["n_boot_paths"] if n is None else n
    rng = np.random.default_rng(cfg["seed"] if seed is None else seed)
    srs = [_sharpe(net[synth.sb_indices(len(net), cfg["synth"]["bootstrap_mean_block"], rng)]) for _ in range(n)]
    return float(np.mean(np.array(srs) > 0)), srs


def double_costs(cfg):
    c = copy.deepcopy(cfg)
    for k in ("fee_per_side", "slippage_base", "slippage_size_coef"):
        c["costs"][k] *= 2
    return c


def run(bars, cfg, funding_day, conf, neighbor_confs, n_b=None):
    from tradelab import parallel

    base = candidate_net(bars, cfg, funding_day, conf)
    share, _ = bootstrap_share(base, cfg)
    out = {"sharpe": _sharpe(base), "boot_positive_share": share,
           "double_costs_sharpe": _sharpe(candidate_net(bars, double_costs(cfg), funding_day, conf)),
           "delay2_sharpe": _sharpe(candidate_net(bars, cfg, funding_day, conf, delay=2))}
    nb = [_sharpe(candidate_net(bars, cfg, funding_day, c)) for c in neighbor_confs]
    out["neighbor_sharpes"] = nb
    out["neighbor_positive_share"] = float(np.mean(np.array(nb) > 0)) if nb else 0.0
    prm = synth.fit_b(bars)
    seeds = parallel.job_seeds(cfg["seed"] + 1, cfg["synth"]["n_null_paths"] if n_b is None else n_b)
    paths = [synth.path_b(bars, prm, s) for s in seeds]
    b = [_sharpe(candidate_net(p, cfg, funding_day, conf)) for p in paths]
    out["synth_b_positive_share"] = float(np.mean(np.array(b) > 0))
    out["garch_params"] = {k: float(v) for k, v in prm.items() if k != "jumps"}
    out["facts"] = synth.facts_table(bars, paths)
    th = cfg["thresholds"]["stress_positive_share"]
    out["passed"] = bool(share >= th and out["double_costs_sharpe"] > 0 and out["delay2_sharpe"] > 0
                         and out["neighbor_positive_share"] >= th and out["synth_b_positive_share"] >= th)
    return out
