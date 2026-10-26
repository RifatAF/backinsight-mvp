"""Stage 6 selection: every three-layer config is a journal trial; walk-forward selection, the usual
checks, stress, a signal-shift null for the FDR, and a report on what each layer adds."""
from functools import partial

import numpy as np
import pandas as pd

from tradelab import journal, metrics, mtf, parallel, perps, perpsearch, selection, walkforward

STAGE = "mtf"


def search(a, cfg, mcfg, conn, sha, jobs=None):
    confs = mtf.configs()
    mine = [t for t in journal.trials(conn) if t["config"].get("stage") == STAGE]
    rng = [str(mtf.days(a, mcfg)[0].date()), str(mtf.days(a, mcfg)[-1].date())]
    ids = None
    if not mine:
        ids = [journal.start_trial(conn, {"stage": STAGE, "id": mtf.config_id(c), **c}, rng, sha) for c in confs]
    elif [t["config"]["id"] for t in mine] != [mtf.config_id(c) for c in confs]:
        raise SystemExit("журнальные пробы mtf не совпадают с текущей сеткой")
    net, trades = mtf.evaluate(a, mcfg, confs, jobs=jobs)
    logged = {e["payload"]["trial_id"]: e["payload"]["metrics"] for e in journal.events(conn, "trial_metrics")}
    pairs = []
    for j in range(len(confs)):
        m = selection.trial_metrics(net[:, j], trades[:, j].astype(float), cfg)
        if ids is not None:
            journal.finish_trial(conn, ids[j], m)
        else:
            pairs.append((mine[j]["id"], m, logged[mine[j]["id"]]))
    if pairs:
        selection.verify_replay(conn, pairs, STAGE)
    return confs, net, trades


def stitched(a, mcfg, confs, wf, di):
    hour_day = a["index"].floor("D")
    w = np.zeros(a["close"].shape)
    cache = {}
    for j, (_, ts, te) in zip(wf["choices"], wf["folds"]):
        if j < 0:
            continue
        if j not in cache:
            cache[j] = mtf.weights(a, confs[j], mcfg)
        m = (hour_day >= di[ts]) & (hour_day <= di[te - 1])
        w[m] = cache[j][m]
    bar, tr = perps.pnl(a, w, np.zeros_like(w), mcfg)
    net, _, d = perps.daily(a, bar, tr)
    return net[d >= di[0]]


def layer_effects(confs, net, rows):
    """For each layer and option: Sharpe with the option minus Sharpe with 'none', the other two layers
    held fixed, over the given days. Median and share of pairs where the option helps."""
    sr = walkforward.sharpe_cols(net[rows])
    sr = np.where(np.isfinite(sr), sr, 0.0)
    idx = {mtf.config_id(c): j for j, c in enumerate(confs)}
    out = []
    for layer, opts in (("regime", mtf.REGIMES), ("setup", mtf.SETUPS), ("trigger", mtf.TRIGGERS)):
        for o in opts[1:]:
            d = []
            for c in confs:
                if c[layer] != o:
                    continue
                base = idx.get(mtf.config_id(dict(c, **{layer: "none"})))
                if base is not None:
                    d.append(sr[idx[mtf.config_id(c)]] - sr[base])
            if d:
                out.append({"слой": layer, "вариант": o, "пар": len(d), "медиана ΔШарп": float(np.median(d)),
                            "доля пар, где лучше": float(np.mean(np.array(d) > 0))})
    return out


def stress(a, cfg, mcfg, c, nb, rows):
    from tradelab import stress as st

    sh = lambda x: perpsearch._sharpe(x[rows])
    net = mtf.run_config(c, a, mcfg)[0]
    out = {"sharpe": sh(net)}
    out["boot_positive_share"], _ = st.bootstrap_share(net[rows], cfg, n=mcfg["search"]["n_boot_paths"])
    out["double_costs_sharpe"] = sh(mtf.run_config(c, a, mcfg, cost_mult=2.0)[0])
    out["delay5m_sharpe"] = sh(mtf.run_config(c, a, mcfg, delay=1)[0])
    out["delay1h_sharpe"] = sh(mtf.run_config(c, a, mcfg, delay=12)[0])
    nbs = [sh(mtf.run_config(x, a, mcfg)[0]) for x in nb]
    out["neighbor_sharpes"] = nbs
    out["neighbor_positive_share"] = float(np.mean(np.array(nbs) > 0)) if nbs else 0.0
    th = cfg["thresholds"]["stress_positive_share"]
    out["passed"] = bool(out["boot_positive_share"] >= th and out["double_costs_sharpe"] > 0 and out["delay5m_sharpe"] > 0
                         and out["delay1h_sharpe"] > 0 and out["neighbor_positive_share"] >= th)
    return out


def check(a, cfg, mcfg, conn, confs, net, fdr):
    th = cfg["thresholds"]
    di = mtf.days(a, mcfg)
    nbrs = mtf.neighbors(confs)
    wf = perpsearch.walk_forward(net, nbrs, cfg)
    oos = wf["oos"]
    proc = stitched(a, mcfg, confs, wf, di)[oos]
    bench = {k: v[oos] for k, v in perpsearch.eth_benchmarks(cfg, di).items()}
    srs = np.array([s for s in (e["payload"]["metrics"]["daily_sr"] for e in journal.events(conn, "trial_metrics"))
                    if s is not None])
    lvl, blk = th["ci_level"], cfg["synth"]["bootstrap_mean_block"]
    r = {"n_trials_total": len(srs), "n_configs": len(confs), "coins": a["symbols"], "oos_days": len(oos),
         "oos_start": str(di[oos[0]].date()) if len(oos) else "-",
         "procedure": selection._curve(proc), "bh": selection._curve(bench["bh"]), "vt": selection._curve(bench["vt"]),
         "ci_vs_bh": metrics.sharpe_diff_ci(proc, bench["bh"], lvl, blk, 1000, cfg["seed"]),
         "ci_vs_vt": metrics.sharpe_diff_ci(proc, bench["vt"], lvl, blk, 1000, cfg["seed"]),
         "dsr": metrics.dsr(proc, srs), "pbo": metrics.pbo(net), "fdr": fdr,
         "fold_choices": [mtf.config_id(confs[j]) if j >= 0 else "cash" for j in wf["choices"]],
         "layers_oos": layer_effects(confs, net, oos), "layers_all": layer_effects(confs, net, np.arange(len(net)))}
    checks = {
        "oos_sharpe_above_bh_vt": r["procedure"]["sharpe"] > max(r["bh"]["sharpe"], r["vt"]["sharpe"]),
        "oos_calmar_above_bh_vt": r["procedure"]["calmar"] > max(r["bh"]["calmar"], r["vt"]["calmar"]),
        "ci_low_above_0_vs_bh": r["ci_vs_bh"]["lo"] > 0, "ci_low_above_0_vs_vt": r["ci_vs_vt"]["lo"] > 0,
        "dsr": r["dsr"] > th["dsr_min"], "pbo": r["pbo"] < th["pbo_max"], "fdr": fdr <= th["fdr_max"],
        "oos_max_dd": abs(r["procedure"]["max_dd"]) <= th["max_drawdown"]}
    j = wf["final"]
    if j < 0:
        r["candidate"] = None
        checks["candidate_exists"] = False
    else:
        c = confs[j]
        sr = walkforward.sharpe_cols(net, cfg["annualization"])
        plat = float(walkforward.plateau(sr, nbrs)[j])
        st = stress(a, cfg, mcfg, c, [confs[k] for k in nbrs[j]], np.arange(len(net)))
        r["candidate"] = {"config": c, "id": mtf.config_id(c), "full_sample": selection._curve(net[:, j]),
                          "plateau": plat, "stress": st}
        checks.update({"candidate_exists": True, "plateau": plat >= th["plateau_min_ratio"], "stress": st["passed"],
                       "candidate_max_dd": abs(r["candidate"]["full_sample"]["max_dd"]) <= th["max_drawdown"]})
    r["checks"] = {k: bool(v) for k, v in checks.items()}
    r["passed"] = all(r["checks"].values())
    r["verdict"] = "кандидат допущен к holdout" if r["passed"] else "не найдена"
    r["wf"] = {"proc": proc, "bh": bench["bh"], "vt": bench["vt"], "days": di[oos]}
    return r


def leaderboard(confs, net, trades, cfg):
    rows = []
    for j, c in enumerate(confs):
        m = selection.trial_metrics(net[:, j], trades[:, j].astype(float), cfg)
        rows.append({**c, "sharpe_is": m["sharpe"] or 0.0, "cagr_is": m["cagr"], "max_dd_is": m["max_dd"],
                     "active_days_per_month": m["trades_per_month"]})
    return pd.DataFrame(rows).sort_values("sharpe_is", ascending=False)


def null_path(seed, a, cb, cfg, mcfg, confs, bh):
    f = mtf.shifted_features(a, cb, mcfg, seed)
    net = np.column_stack([mtf.run_config(c, mtf.lean(a), mcfg, f)[0] for c in confs])
    wf = perpsearch.walk_forward(net, mtf.neighbors(confs), cfg)
    proc = np.zeros(len(net))
    for j, (_, ts, te) in zip(wf["choices"], wf["folds"]):
        if j >= 0:
            proc[ts:te] = net[ts:te, j]
    proc = proc[wf["oos"]]
    th = cfg["thresholds"]
    if not len(proc) or np.std(proc) == 0:
        return {"seed": seed, "finding": False, "ci_lo": np.nan, "dsr": np.nan, "sharpe_oos": 0.0}
    with np.errstate(divide="ignore", invalid="ignore"):
        dsr_ = net.mean(0) / net.std(0, ddof=1)
    ci = metrics.sharpe_diff_ci(proc, bh[wf["oos"]], th["ci_level"], cfg["synth"]["bootstrap_mean_block"], 1000, seed)
    d = metrics.dsr(proc, dsr_[np.isfinite(dsr_)])
    return {"seed": seed, "ci_lo": ci["lo"], "dsr": d, "sharpe_oos": perpsearch._sharpe(proc),
            "finding": bool(ci["lo"] > 0 and d > th["dsr_min"])}


def calibrate(a, cb, cfg, mcfg, jobs=None, n_paths=None):
    """Paths run in parallel; each rebuilds every layer from shifted inputs (pandas panels go along)."""
    confs = mtf.configs()
    bh = perpsearch.eth_benchmarks(cfg, mtf.days(a, mcfg))["bh"]
    seeds = parallel.job_seeds(mcfg["seed"] + 1000, mcfg["search"]["n_null_paths"] if n_paths is None else n_paths)
    return pd.DataFrame(parallel.pmap(partial(null_path, a=a, cb=cb, cfg=cfg, mcfg=mcfg, confs=confs, bh=bh), seeds, jobs))
