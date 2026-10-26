"""Stage 5 selection: all configs of perps.GRIDS are journal trials; walk-forward selection on daily
portfolio returns, the same checks as before (DSR over every trial in the journal, PBO, CIs against
ETH bh and vt, FDR from a signal-shift null), stress and a one-time holdout."""
import numpy as np
import pandas as pd

from tradelab import backtest, data, journal, metrics, parallel, perps, selection, strategies, walkforward

STAGE = "perps"


def eth_benchmarks(cfg, days, bars=None, funding=None):
    """Daily net of ETH bh and vt (stage 1 cost model) on the given UTC days; 0 before ETH data."""
    bars = data.load_raw("ETH/USDT") if bars is None else bars
    from tradelab import costs

    fday = costs.funding_daily(bars.index, cfg["costs"], data.load_funding("ETH/USDT") if funding is None else funding).to_numpy()
    out = {}
    for name, fn in (("bh", strategies.bh), ("vt", strategies.vt)):
        net = backtest.pnl(bars, backtest.positions(fn(bars, cfg).to_numpy(), cfg), cfg, fday)["net"][:-1]
        out[name] = pd.Series(net, index=bars.index[:-1]).reindex(days).fillna(0.0).to_numpy()
    return out


def select(net, nbrs, cfg, rows):
    """Max Sharpe on the rows among configs whose plateau ratio passes; -1 if none."""
    sr = walkforward.sharpe_cols(net[rows], cfg["annualization"])
    ok = (walkforward.plateau(sr, nbrs) >= cfg["thresholds"]["plateau_min_ratio"]) & np.isfinite(sr)
    return int(np.argmax(np.where(ok, sr, -np.inf))) if ok.any() else -1


def walk_forward(net, nbrs, cfg):
    fl = walkforward.folds(len(net), cfg)
    choices = [select(net, nbrs, cfg, np.arange(tr)) for tr, _, _ in fl]
    oos = np.concatenate([np.arange(ts, te) for _, ts, te in fl])
    return {"folds": fl, "choices": choices, "oos": oos, "final": select(net, nbrs, cfg, np.arange(len(net)))}


def stitched(a, pcfg, confs, wf, day_index, cost_mult=1.0):
    """Daily net of the stitched procedure: each test window holds its fold winner's weights, one
    backtest over all hours, so switching between winners pays costs."""
    hour_day = a["index"].floor("D")
    wp = np.zeros(a["open"].shape)
    ws = np.zeros(a["open"].shape)
    cache = {}
    sig = perps.with_fsig(a, [confs[j] for j in set(wf["choices"]) if j >= 0])
    for j, (_, ts, te) in zip(wf["choices"], wf["folds"]):
        if j < 0:
            continue
        if j not in cache:
            cache[j] = perps.weights(a, confs[j], sig)
        m = (hour_day >= day_index[ts]) & (hour_day <= day_index[te - 1])
        wp[m], ws[m] = cache[j][0][m], cache[j][1][m]
    bar, tr = perps.pnl(a, wp, ws, pcfg, cost_mult)
    return perps.daily(a, bar, tr)[0]


def search(a, cfg, pcfg, conn, sha, jobs=None):
    """Journal every config (once per dataset); later runs recompute and compare with the journal."""
    confs = perps.configs()
    mine = [t for t in journal.trials(conn) if t["config"].get("stage") == STAGE]
    rng = [str(a["index"][0].date()), str(a["index"][-1].date())]
    ids = None
    if not mine:
        ids = [journal.start_trial(conn, {"stage": STAGE, "id": perps.config_id(c), **c}, rng, sha) for c in confs]
    elif [t["config"]["id"] for t in mine] != [perps.config_id(c) for c in confs]:
        raise SystemExit("журнальные пробы perps не совпадают с текущей сеткой")
    net, trades = perps.evaluate(a, pcfg, confs, jobs=jobs)
    logged = {e["payload"]["trial_id"]: e["payload"]["metrics"] for e in journal.events(conn, "trial_metrics")}
    pairs = []
    for j, c in enumerate(confs):
        m = selection.trial_metrics(net[:, j], trades[:, j].astype(float), cfg)
        if ids is not None:
            journal.finish_trial(conn, ids[j], m)
        else:
            pairs.append((mine[j]["id"], m, logged[mine[j]["id"]]))
    if pairs:
        selection.verify_replay(conn, pairs, STAGE)
    return confs, net, trades


def _sharpe(x):
    x = np.asarray(x, dtype=float)
    return float(metrics.sharpe(x)) if x.std() > 0 else 0.0


def stress(a, cfg, pcfg, c, nb_confs, rows):
    net = perps.run_config(c, a, pcfg)[0][rows]
    out = {"sharpe": _sharpe(net)}
    from tradelab import stress as st

    out["boot_positive_share"], _ = st.bootstrap_share(net, cfg, n=pcfg["search"]["n_boot_paths"])
    out["double_costs_sharpe"] = _sharpe(perps.run_config(c, a, pcfg, cost_mult=2.0)[0][rows])
    out["delay1h_sharpe"] = _sharpe(perps.run_config(c, a, pcfg, delay=1)[0][rows])
    out["delay8h_sharpe"] = _sharpe(perps.run_config(c, a, pcfg, delay=8)[0][rows])
    nb = [_sharpe(perps.run_config(x, a, pcfg)[0][rows]) for x in nb_confs]
    out["neighbor_sharpes"] = nb
    out["neighbor_positive_share"] = float(np.mean(np.array(nb) > 0)) if nb else 0.0
    th = cfg["thresholds"]["stress_positive_share"]
    out["passed"] = bool(out["boot_positive_share"] >= th and out["double_costs_sharpe"] > 0 and out["delay1h_sharpe"] > 0
                         and out["delay8h_sharpe"] > 0 and out["neighbor_positive_share"] >= th)
    return out


def check(a, cfg, pcfg, conn, confs, net, fdr):
    th = cfg["thresholds"]
    di = perps.days(a)
    nbrs = perps.neighbors(confs)
    wf = walk_forward(net, nbrs, cfg)
    oos = wf["oos"]
    proc = stitched(a, pcfg, confs, wf, di)[oos]
    bench = {k: v[oos] for k, v in eth_benchmarks(cfg, di).items()}
    srs = np.array([s for s in (e["payload"]["metrics"]["daily_sr"] for e in journal.events(conn, "trial_metrics"))
                    if s is not None])
    lvl, blk = th["ci_level"], cfg["synth"]["bootstrap_mean_block"]
    r = {"n_trials_total": len(srs), "n_configs": len(confs), "coins": len(a["symbols"]),
         "oos_days": len(oos), "oos_start": str(di[oos[0]].date()),
         "procedure": selection._curve(proc), "bh": selection._curve(bench["bh"]), "vt": selection._curve(bench["vt"]),
         "ci_vs_bh": metrics.sharpe_diff_ci(proc, bench["bh"], lvl, blk, 1000, cfg["seed"]),
         "ci_vs_vt": metrics.sharpe_diff_ci(proc, bench["vt"], lvl, blk, 1000, cfg["seed"]),
         "dsr": metrics.dsr(proc, srs), "pbo": metrics.pbo(net[np.any(net != 0, axis=1)]), "fdr": fdr,
         "fold_choices": [perps.config_id(confs[j]) if j >= 0 else "cash" for j in wf["choices"]],
         "monthly_flags": selection.monthly_flags(proc, di[oos], th["monthly_return_flag"])}
    checks = {
        "oos_sharpe_above_bh_vt": r["procedure"]["sharpe"] > max(r["bh"]["sharpe"], r["vt"]["sharpe"]),
        "oos_calmar_above_bh_vt": r["procedure"]["calmar"] > max(r["bh"]["calmar"], r["vt"]["calmar"]),
        "ci_low_above_0_vs_bh": r["ci_vs_bh"]["lo"] > 0, "ci_low_above_0_vs_vt": r["ci_vs_vt"]["lo"] > 0,
        "dsr": r["dsr"] > th["dsr_min"], "pbo": r["pbo"] < th["pbo_max"], "fdr": fdr <= th["fdr_max"],
        "oos_max_dd": abs(r["procedure"]["max_dd"]) <= th["max_drawdown"],
    }
    j = wf["final"]
    if j < 0:
        r["candidate"] = None
        checks["candidate_exists"] = False
    else:
        c = confs[j]
        sr = walkforward.sharpe_cols(net, cfg["annualization"])
        plat = float(walkforward.plateau(sr, nbrs)[j])
        live = np.arange(len(net))
        st = stress(a, cfg, pcfg, c, [confs[k] for k in nbrs[j]], live)
        r["candidate"] = {"config": c, "id": perps.config_id(c), "full_sample": selection._curve(net[:, j]),
                          "plateau": plat, "stress": st}
        checks.update({"candidate_exists": True, "plateau": plat >= th["plateau_min_ratio"], "stress": st["passed"],
                       "candidate_max_dd": abs(r["candidate"]["full_sample"]["max_dd"]) <= th["max_drawdown"]})
    r["checks"] = {k: bool(v) for k, v in checks.items()}
    r["passed"] = all(r["checks"].values())
    r["verdict"] = "кандидат допущен к holdout" if r["passed"] else "не найдена"
    r["wf"] = {"oos": oos, "proc": proc, "bh": bench["bh"], "vt": bench["vt"], "days": di[oos]}
    return r


def leaderboard(confs, net, trades, cfg):
    rows = []
    for j, c in enumerate(confs):
        x = net[:, j]
        m = selection.trial_metrics(x, trades[:, j].astype(float), cfg)
        rows.append({"family": c["family"], "config": perps.config_id(c), "sharpe_is": m["sharpe"] or 0.0,
                     "cagr_is": m["cagr"], "max_dd_is": m["max_dd"], "active_days_per_month": m["trades_per_month"]})
    return pd.DataFrame(rows).sort_values("sharpe_is", ascending=False)


# ---------- null calibration ----------

def null_path(seed, a, cfg, pcfg, confs, bh):
    sig = perps.with_fsig(perps.shifted_signals(a, seed, 24 * pcfg["search"]["min_shift_days"]), confs)
    net = np.column_stack([perps.run_config(c, a, pcfg, sig)[0] for c in confs])
    nbrs = perps.neighbors(confs)
    wf = walk_forward(net, nbrs, cfg)
    proc = np.zeros(len(net))
    for j, (_, ts, te) in zip(wf["choices"], wf["folds"]):
        if j >= 0:
            proc[ts:te] = net[ts:te, j]
    proc = proc[wf["oos"]]
    th = cfg["thresholds"]
    if np.std(proc) == 0:
        return {"seed": seed, "finding": False, "ci_lo": np.nan, "dsr": np.nan, "sharpe_oos": 0.0}
    with np.errstate(divide="ignore", invalid="ignore"):
        dsr_ = net.mean(0) / net.std(0, ddof=1)
    ci = metrics.sharpe_diff_ci(proc, bh[wf["oos"]], th["ci_level"], cfg["synth"]["bootstrap_mean_block"], 1000, seed)
    d = metrics.dsr(proc, dsr_[np.isfinite(dsr_)])
    return {"seed": seed, "ci_lo": ci["lo"], "dsr": d, "sharpe_oos": _sharpe(proc),
            "finding": bool(ci["lo"] > 0 and d > th["dsr_min"])}


def calibrate(a, cfg, pcfg, jobs=None, n_paths=None):
    """Signal-shift null: the whole procedure on paths where each coin's signals are detached from its
    future returns. FDR = share of paths with a finding. Configs run serially inside a path, paths in parallel."""
    confs = perps.configs()
    bh = eth_benchmarks(cfg, perps.days(a))["bh"]
    seeds = parallel.job_seeds(pcfg["seed"] + 1000, pcfg["search"]["n_null_paths"] if n_paths is None else n_paths)
    from functools import partial

    return pd.DataFrame(parallel.pmap(partial(null_path, a=a, cfg=cfg, pcfg=pcfg, confs=confs, bh=bh), seeds, jobs))
