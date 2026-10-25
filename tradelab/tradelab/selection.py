"""Real-data search, final candidate and its checks (CLAUDE.md sections 11.3-11.5, 13 without holdout)."""
import numpy as np
import pandas as pd

from tradelab import backtest, costs, grid, journal, metrics, parallel, strategies, stress, synth, walkforward

PBO_SKIP_DAYS = 250  # longest lookback in the grid: rows before it are warm-up zeros


class SearchAlreadyRun(Exception):
    pass


def trial_metrics(net, turnover, cfg):
    sd = net.std(ddof=1)
    return {"sharpe": float(metrics.sharpe(net)) if sd > 0 else None,
            "daily_sr": float(net.mean() / sd) if sd > 0 else None,
            "cagr": float(metrics.cagr(net)), "max_dd": float(metrics.max_drawdown(net)),
            "trades_per_month": float((turnover > 0).sum() / (len(net) / (365 / 12)))}


def _run_grid(bars, cfg, funding_day):
    confs = grid.configs()
    nbrs = grid.neighbors(confs)
    wf = walkforward.run(bars, strategies.config_matrix(bars, cfg, funding_day, confs), cfg, funding_day, nbrs)
    final = walkforward.select(wf["net_all"], wf["turnover_all"], nbrs, cfg)
    return {"confs": confs, "nbrs": nbrs, "wf": wf, "final": final}


def search(bars, cfg, conn, funding_day, dataset_sha256):
    """Every config is a journal trial: 'started' rows first, metrics as events after the run."""
    if any(t["dataset_sha256"] == dataset_sha256 for t in journal.trials(conn)):
        raise SearchAlreadyRun("trials for this dataset are already in the journal; the search runs once")
    rng = [str(bars.index[0].date()), str(bars.index[-1].date())]
    ids = [journal.start_trial(conn, {"id": grid.config_id(c), **c}, rng, dataset_sha256) for c in grid.configs()]
    found = _run_grid(bars, cfg, funding_day)
    wf = found["wf"]
    for j, tid in enumerate(ids):
        journal.finish_trial(conn, tid, trial_metrics(wf["net_all"][:, j], wf["turnover_all"][:, j], cfg))
    return found


def replay(bars, cfg, conn, funding_day, dataset_sha256):
    """Recompute the journaled search without adding trials; every trial's metrics must match the journal."""
    trials = [t for t in journal.trials(conn) if t["dataset_sha256"] == dataset_sha256]
    if len(trials) != len(grid.configs()):
        raise SearchAlreadyRun(f"expected {len(grid.configs())} journaled trials, found {len(trials)}")
    logged = {e["payload"]["trial_id"]: e["payload"]["metrics"] for e in journal.events(conn, "trial_metrics")}
    found = _run_grid(bars, cfg, funding_day)
    wf = found["wf"]
    for j, t in enumerate(trials):
        assert t["config"]["id"] == grid.config_id(found["confs"][j])
        if trial_metrics(wf["net_all"][:, j], wf["turnover_all"][:, j], cfg) != logged[t["id"]]:
            raise AssertionError(f"replay differs from journal for trial {t['id']}")
    return found


def real_trial_srs(conn, dataset_sha256):
    tids = {t["id"] for t in journal.trials(conn) if t["dataset_sha256"] == dataset_sha256}
    srs = [e["payload"]["metrics"]["daily_sr"] for e in journal.events(conn, "trial_metrics")
           if e["payload"]["trial_id"] in tids]
    return np.array([s for s in srs if s is not None]), len(tids)


def ablate(cand, confs, net_all, oos):
    """Switch each layer off; keep the layer only if it strictly improves the Sharpe on OOS days."""
    idx = {grid.config_id(c): i for i, c in enumerate(confs)}
    log = []
    for layer in ("v", "p", "q"):
        if cand[layer] is None:
            continue
        alt = dict(cand, **{layer: None})
        s_c = metrics.sharpe(net_all[oos, idx[grid.config_id(cand)]])
        s_a = metrics.sharpe(net_all[oos, idx[grid.config_id(alt)]])
        keep = bool(s_c > s_a)
        log.append({"layer": layer, "with": float(s_c), "without": float(s_a), "kept": keep})
        if not keep:
            cand = alt
    return cand, log


def transfer(basket_bars, cfg, conf, funding_by_sym):
    out = {}
    for sym, bars in basket_bars.items():
        fday = costs.funding_daily(bars.index, cfg["costs"], funding_by_sym.get(sym)).to_numpy()
        net = stress.candidate_net(bars, cfg, fday, conf)
        out[sym] = float(np.prod(1 + net) - 1)
    return out


def monthly_flags(net, index, limit):
    m = (1 + pd.Series(net, index=index)).groupby(index.tz_localize(None).to_period("M")).prod() - 1
    return [str(p) for p, v in m.items() if v > limit]


def _curve(net):
    return {"sharpe": float(metrics.sharpe(net)), "calmar": float(metrics.calmar(net)),
            "max_dd": float(metrics.max_drawdown(net)), "cagr": float(metrics.cagr(net))}


def evaluate(bars, cfg, conn, funding_day, found, dataset_sha256, fdr, basket_bars, funding_by_sym):
    th = cfg["thresholds"]
    confs, nbrs, wf = found["confs"], found["nbrs"], found["wf"]
    oos = wf["oos"]
    index = bars.index[oos]
    proc = wf["net_oos"]
    bench = {}
    for name, fn in (("bh", strategies.bh), ("vt", strategies.vt)):
        p = backtest.pnl(bars, backtest.positions(fn(bars, cfg).to_numpy(), cfg), cfg, funding_day)
        bench[name] = p["net"][oos]
    srs, n_trials = real_trial_srs(conn, dataset_sha256)
    level, block = th["ci_level"], cfg["synth"]["bootstrap_mean_block"]
    r = {"n_trials": n_trials, "oos_days": len(oos), "oos_start": str(index[0].date()),
         "procedure": _curve(proc), "bh": _curve(bench["bh"]), "vt": _curve(bench["vt"]),
         "ci_vs_bh": metrics.sharpe_diff_ci(proc, bench["bh"], level, block, 1000, cfg["seed"]),
         "ci_vs_vt": metrics.sharpe_diff_ci(proc, bench["vt"], level, block, 1000, cfg["seed"]),
         "dsr": metrics.dsr(proc, srs), "pbo": metrics.pbo(wf["net_all"][PBO_SKIP_DAYS:]), "fdr": fdr,
         "fold_choices": [grid.config_id(confs[j]) if j >= 0 else "cash" for j in wf["choices"]],
         "procedure_trades_per_month": float((np.abs(np.diff(wf["pos"][oos], prepend=wf["pos"][oos[0] - 1])) > 0)
                                             .sum() / (len(oos) / (365 / 12))),
         "monthly_flags": monthly_flags(proc, index, th["monthly_return_flag"])}

    checks = {
        "oos_sharpe_above_bh_vt": r["procedure"]["sharpe"] > max(r["bh"]["sharpe"], r["vt"]["sharpe"]),
        "oos_calmar_above_bh_vt": r["procedure"]["calmar"] > max(r["bh"]["calmar"], r["vt"]["calmar"]),
        "ci_low_above_0_vs_bh": r["ci_vs_bh"]["lo"] > 0,
        "ci_low_above_0_vs_vt": r["ci_vs_vt"]["lo"] > 0,
        "dsr": r["dsr"] > th["dsr_min"], "pbo": r["pbo"] < th["pbo_max"], "fdr": fdr <= th["fdr_max"],
        "oos_max_dd": abs(r["procedure"]["max_dd"]) <= th["max_drawdown"],
        "oos_trades_per_month": r["procedure_trades_per_month"] <= th["max_trades_per_month"],
    }

    if found["final"] < 0:
        r["candidate"] = None
        checks["candidate_exists"] = False
        prm = synth.fit_b(bars)
        seeds = parallel.job_seeds(cfg["seed"] + 1, cfg["synth"]["n_null_paths"])
        r["facts"] = synth.facts_table(bars, [synth.path_b(bars, prm, s) for s in seeds])
    else:
        cand, ablation = ablate(confs[found["final"]], confs, wf["net_all"], oos)
        j = {grid.config_id(c): i for i, c in enumerate(confs)}[grid.config_id(cand)]
        sr_full = walkforward.sharpe_cols(wf["net_all"], cfg["annualization"])
        plat = float(walkforward.plateau(sr_full, nbrs)[j])
        tr = transfer(basket_bars, cfg, cand, funding_by_sym)
        st = stress.run(bars, cfg, funding_day, cand, [confs[k] for k in nbrs[j]])
        net_c = wf["net_all"][:, j]
        r["candidate"] = {"selected": grid.config_id(confs[found["final"]]), "after_ablation": grid.config_id(cand),
                          "ablation": ablation, "full_sample": _curve(net_c),
                          "trades_per_month": trial_metrics(net_c, wf["turnover_all"][:, j], cfg)["trades_per_month"],
                          "plateau": plat, "transfer": tr,
                          "transfer_share": float(np.mean(np.array(list(tr.values())) > 0)),
                          "stress": {k: v for k, v in st.items() if k != "facts"}}
        r["facts"] = st["facts"]
        checks.update({
            "candidate_exists": True,
            "plateau": plat >= th["plateau_min_ratio"],
            "transfer": r["candidate"]["transfer_share"] >= th["basket_profitable_share"],
            "stress": st["passed"],
            "candidate_max_dd": abs(r["candidate"]["full_sample"]["max_dd"]) <= th["max_drawdown"],
            "candidate_trades_per_month": r["candidate"]["trades_per_month"] <= th["max_trades_per_month"],
        })
    r["checks"] = {k: bool(v) for k, v in checks.items()}
    r["passed"] = all(r["checks"].values())
    r["verdict"] = "кандидат допущен к holdout" if r["passed"] else "не найдена"
    return r
