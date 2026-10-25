"""Strategy generator (stage 2): random long-only specs from indicator families, screened
("does not lose") and selected inside walk-forward training windows only.

A spec: {"family", "params", "confirm": None | {"family", "params"}, "trend": None | 100 | 200,
         "v": None | vol target, "p": None | regime filter}.
Every spec is a journal trial, so the deflated Sharpe ratio counts all of them.
"""
import hashlib
import json
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from tradelab import backtest, indicators, journal, metrics, parallel, strategies, walkforward

GEN_PATH = Path(__file__).resolve().parent.parent / "generator.yaml"
V_AXIS = [None, 0.10, 0.15, 0.20, 0.25]
P_AXIS = [0.80, 0.90, None]
TREND_AXIS = [None, 100, 200]


class GeneratorConfigChanged(Exception):
    pass


def load_config(path=None):
    p = Path(path or GEN_PATH)
    return yaml.safe_load(p.read_bytes()), hashlib.sha256(p.read_bytes()).hexdigest()


def lock(conn, gsha):
    ev = journal.events(conn, "generator_config_sha256")
    if not ev:
        journal.log_event(conn, "generator_config_sha256", {"sha256": gsha})
    elif ev[0]["payload"]["sha256"] != gsha:
        raise GeneratorConfigChanged("generator.yaml changed after lock")


def spec_id(spec):
    return json.dumps(spec, sort_keys=True)


def _rand_params(rng, family):
    grid = indicators.grid(family)
    while True:
        p = {k: v[rng.integers(len(v))] for k, v in grid.items()}
        p = {k: (float(v) if isinstance(v, float) else v if isinstance(v, str) else int(v)) for k, v in p.items()}
        if indicators.valid(family, p):
            return p


def generate(gcfg):
    """Deterministic list of distinct random specs."""
    rng = np.random.default_rng(gcfg["seed"])
    fams = list(gcfg.get("families") or indicators.FAMILIES)  # stage 2 default: the 13 price families
    seen, out = set(), []
    tries = 0
    while len(out) < gcfg["n_strategies"] and tries < 100 * gcfg["n_strategies"]:
        tries += 1
        f = fams[rng.integers(len(fams))]
        spec = {"family": f, "params": _rand_params(rng, f), "confirm": None, "trend": None,
                "v": V_AXIS[rng.integers(len(V_AXIS))], "p": P_AXIS[rng.integers(len(P_AXIS))]}
        if rng.random() < gcfg["combo_share"]:
            g = fams[rng.integers(len(fams))]
            if g != f:
                spec["confirm"] = {"family": g, "params": _rand_params(rng, g)}
        if rng.random() < gcfg["trend_filter_share"]:
            spec["trend"] = TREND_AXIS[1 + rng.integers(2)]
        if "direction" in gcfg:  # stage 3 only, so stage 2 spec ids stay unchanged
            spec["direction"] = gcfg["direction"]
        k = spec_id(spec)
        if k not in seen:
            seen.add(k)
            out.append(spec)
    return out


def neighbors(spec):
    """Specs one step away on one parameter of the main family, or on the v / p / trend axes."""
    out = []
    grid = indicators.grid(spec["family"])
    for k, vals in grid.items():
        i = vals.index(spec["params"][k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                p = dict(spec["params"], **{k: vals[j]})
                if indicators.valid(spec["family"], p):
                    out.append(dict(spec, params=p))
    for key, axis in (("v", V_AXIS[1:]), ("p", P_AXIS), ("trend", TREND_AXIS)):
        if spec[key] in axis:
            i = axis.index(spec[key])
            out += [dict(spec, **{key: axis[j]}) for j in (i - 1, i + 1) if 0 <= j < len(axis)]
    return out


def target(bars, cfg, funding_day, spec, inputs=None):
    """Target position series for one spec."""
    b = indicators.signal(bars, spec["family"], spec["params"])
    if spec["confirm"]:
        b = b * indicators.signal(bars, spec["confirm"]["family"], spec["confirm"]["params"])
    if spec["trend"]:
        b = b * (bars["close"] > bars["close"].rolling(spec["trend"]).mean()).astype(float)
    inputs = strategies.layer_inputs(bars, cfg, funding_day) if inputs is None else inputs
    return strategies.compose(b, {"v": spec["v"], "p": spec["p"], "q": None}, inputs, cfg).to_numpy()


def spec_fn(spec):
    """Callable form for stress tests and the holdout run."""
    return partial(_call_target, spec=spec)


def _call_target(bars, cfg, funding_day, spec):
    return target(bars, cfg, funding_day, spec)


def _chunk(specs, bars, cfg, funding_day):
    inputs = strategies.layer_inputs(bars, cfg, funding_day)
    s = np.column_stack([target(bars, cfg, funding_day, sp, inputs) for sp in specs])
    pos = backtest.positions(s, cfg)
    p = backtest.pnl(bars, pos, cfg, funding_day)
    return pos, p["net"][:-1], p["turnover"][:-1] > 0


def evaluate_all(bars, cfg, funding_day, specs, jobs=None, chunk=50):
    """Positions (T x N), net and trade flags (T-1 x N) for all specs, computed in parallel chunks."""
    parts = [specs[i:i + chunk] for i in range(0, len(specs), chunk)]
    res = parallel.pmap(partial(_chunk, bars=bars, cfg=cfg, funding_day=np.asarray(funding_day)), parts, jobs)
    return (np.concatenate([r[0] for r in res], axis=1), np.concatenate([r[1] for r in res], axis=1),
            np.concatenate([r[2] for r in res], axis=1))


def screen_metrics(net, trades, ann=365):
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = net.mean(0) / net.std(0, ddof=1) * np.sqrt(ann)
    eq = np.cumprod(1 + net, axis=0)
    dd = (eq / np.maximum.accumulate(eq, axis=0) - 1).min(0)
    cagr = eq[-1] ** (ann / len(net)) - 1
    n_tr = trades.sum(0)
    return {"sharpe": np.where(np.isfinite(sr), sr, -np.inf), "cagr": cagr, "max_dd": dd,
            "trades": n_tr, "tpm": n_tr / (len(net) / (365 / 12))}


def screen_mask(m, gcfg, cfg):
    s = gcfg["screen"]
    return ((m["sharpe"] > s["min_sharpe"]) & (m["cagr"] > s["min_cagr"]) & (-m["max_dd"] <= s["max_drawdown"])
            & (m["trades"] >= s["min_trades"]) & (m["tpm"] <= cfg["thresholds"]["max_trades_per_month"]))


class NeighborCache:
    """Net returns of neighbour specs, computed on demand over the full path (signals are causal)."""

    def __init__(self, bars, cfg, funding_day, specs, net):
        self.bars, self.cfg, self.fday = bars, cfg, np.asarray(funding_day)
        self.inputs = strategies.layer_inputs(bars, cfg, funding_day)
        self.cache = {spec_id(s): net[:, j] for j, s in enumerate(specs)}

    def net(self, spec):
        k = spec_id(spec)
        if k not in self.cache:
            s = target(self.bars, self.cfg, self.fday, spec, self.inputs)
            self.cache[k] = backtest.pnl(self.bars, backtest.positions(s, self.cfg), self.cfg, self.fday)["net"][:-1]
        return self.cache[k]


def plateau(spec, rows, sr_own, nc, ann=365):
    if not sr_own > 0:
        return -np.inf
    srs = []
    for nb in neighbors(spec):
        x = nc.net(nb)[rows]
        sd = x.std(ddof=1)
        srs.append(x.mean() / sd * np.sqrt(ann) if sd > 0 else 0.0)
    return float(np.mean(srs) / sr_own) if srs else -np.inf


def choose(net, trades, rows, specs, nc, gcfg, cfg):
    """Best train Sharpe among screened specs whose plateau >= threshold; -1 if none."""
    m = screen_metrics(net[rows], trades[rows], cfg["annualization"])
    ok = screen_mask(m, gcfg, cfg)
    order = [j for j in np.argsort(-m["sharpe"]) if ok[j]][: gcfg["plateau_top_k"]]
    for j in order:
        if plateau(specs[j], rows, m["sharpe"][j], nc, cfg["annualization"]) >= cfg["thresholds"]["plateau_min_ratio"]:
            return int(j), int(ok.sum())
    return -1, int(ok.sum())


def walk_forward(bars, cfg, funding_day, specs, pos, net, trades, gcfg):
    nc = NeighborCache(bars, cfg, funding_day, specs, net)
    stitched = np.zeros(len(bars))
    choices, survivors, oos = [], [], []
    for tr_end, ts, te in walkforward.folds(len(net), cfg):
        j, n_ok = choose(net, trades, np.arange(tr_end), specs, nc, gcfg, cfg)
        choices.append(j)
        survivors.append(n_ok)
        if j >= 0:
            stitched[ts:te] = pos[ts:te, j]
        oos.append(np.arange(ts, te))
    oos = np.concatenate(oos)
    proc = backtest.pnl(bars, stitched, cfg, funding_day)
    final, n_final = choose(net, trades, np.arange(len(net)), specs, nc, gcfg, cfg)
    return {"choices": choices, "survivors": survivors, "oos": oos, "net_oos": proc["net"][oos],
            "pos": stitched, "final": final, "final_survivors": n_final, "nc": nc}


# ---------- calibration: how often the whole generator "finds" something in pure noise ----------

def evaluate_path(bars, cfg, funding_day, specs, gcfg, seed, phi=0.0):
    from tradelab import synth

    p = synth.path(bars, seed, phi, cfg["synth"]["bootstrap_mean_block"])
    pos, net, trades = evaluate_all(p, cfg, funding_day, specs, jobs=1)
    wf = walk_forward(p, cfg, funding_day, specs, pos, net, trades, gcfg)
    bh = backtest.pnl(p, backtest.positions(np.ones(len(p)), cfg), cfg, funding_day)["net"][wf["oos"]]
    proc = wf["net_oos"]
    th = cfg["thresholds"]
    with np.errstate(divide="ignore", invalid="ignore"):
        dsr_ = net.mean(0) / net.std(0, ddof=1)
    dsr_ = dsr_[np.isfinite(dsr_)]
    if np.std(proc) == 0:
        return {"phi": phi, "seed": seed, "finding": False, "ci_lo": np.nan, "dsr": np.nan}
    ci = metrics.sharpe_diff_ci(proc, bh, th["ci_level"], cfg["synth"]["bootstrap_mean_block"], 1000, seed)
    d = metrics.dsr(proc, dsr_)
    return {"phi": phi, "seed": seed, "ci_lo": ci["lo"], "dsr": d, "sharpe_oos": metrics.sharpe(proc),
            "finding": bool(ci["lo"] > 0 and d > th["dsr_min"])}


def calibrate(bars, cfg, funding_day, specs, gcfg, n_paths=None, phis=(0.0, 0.20), jobs=None):
    n = gcfg["n_null_paths"] if n_paths is None else n_paths
    seeds = parallel.job_seeds(gcfg["seed"], n)
    tasks = [(phi, s) for phi in phis for s in seeds]
    fn = partial(_cal_job, bars=bars, cfg=cfg, funding_day=np.asarray(funding_day), specs=specs, gcfg=gcfg)
    return pd.DataFrame(parallel.pmap(fn, tasks, jobs))


def _cal_job(task, bars, cfg, funding_day, specs, gcfg):
    phi, seed = task
    return evaluate_path(bars, cfg, funding_day, specs, gcfg, seed, phi)


# ---------- real-data search and checks ----------

def search(bars, cfg, conn, funding_day, specs, sha, gsha, jobs=None):
    """Journals every spec as a trial once (stage 'generator'); later runs replay and verify."""
    from tradelab import selection

    mine = [t for t in journal.trials(conn) if t["dataset_sha256"] == sha and t["config"].get("stage") == "generator"]
    rng = [str(bars.index[0].date()), str(bars.index[-1].date())]
    ids = None
    if not mine:
        ids = [journal.start_trial(conn, {"stage": "generator", "generator_sha256": gsha, "spec": sp}, rng, sha)
               for sp in specs]
    elif len(mine) != len(specs) or any(t["config"]["spec"] != sp for t, sp in zip(mine, specs)):
        raise GeneratorConfigChanged("journaled generator trials do not match the current specs")
    pos, net, trades = evaluate_all(bars, cfg, funding_day, specs, jobs)
    logged = {e["payload"]["trial_id"]: e["payload"]["metrics"] for e in journal.events(conn, "trial_metrics")}
    for j in range(len(specs)):
        m = selection.trial_metrics(net[:, j], trades[:, j].astype(float), cfg)
        if ids is not None:
            journal.finish_trial(conn, ids[j], m)
        elif logged[mine[j]["id"]] != m:
            raise AssertionError(f"replay differs from journal for trial {mine[j]['id']}")
    return pos, net, trades, ids is None


def ablate(spec, net_of, oos):
    log = []
    for key in ("confirm", "trend", "v", "p"):
        if spec[key] is None:
            continue
        alt = dict(spec, **{key: None})
        a, b = metrics.sharpe(net_of(spec)[oos]), metrics.sharpe(net_of(alt)[oos])
        keep = bool(a > b)
        log.append({"layer": key, "with": float(a), "without": float(b), "kept": keep})
        if not keep:
            spec = alt
    return spec, log


def check(bars, cfg, conn, funding_day, specs, net, wf, sha, fdr, basket_bars, funding_by_sym):
    from tradelab import costs as _c, selection, stress

    th = cfg["thresholds"]
    oos, proc = wf["oos"], wf["net_oos"]
    index = bars.index[oos]
    bench = {name: backtest.pnl(bars, backtest.positions(fn(bars, cfg).to_numpy(), cfg), cfg, funding_day)["net"][oos]
             for name, fn in (("bh", strategies.bh), ("vt", strategies.vt))}
    srs, n_trials = selection.real_trial_srs(conn, sha)
    lvl, blk = th["ci_level"], cfg["synth"]["bootstrap_mean_block"]
    turn = np.abs(np.diff(wf["pos"][oos], prepend=wf["pos"][oos[0] - 1])) > 0
    r = {"n_trials_total": n_trials, "n_generated": len(specs), "oos_days": len(oos), "oos_start": str(index[0].date()),
         "procedure": selection._curve(proc), "bh": selection._curve(bench["bh"]), "vt": selection._curve(bench["vt"]),
         "ci_vs_bh": metrics.sharpe_diff_ci(proc, bench["bh"], lvl, blk, 1000, cfg["seed"]),
         "ci_vs_vt": metrics.sharpe_diff_ci(proc, bench["vt"], lvl, blk, 1000, cfg["seed"]),
         "dsr": metrics.dsr(proc, srs), "pbo": metrics.pbo(net[250:]), "fdr": fdr,
         "fold_choices": [spec_id(specs[j]) if j >= 0 else "cash" for j in wf["choices"]],
         "fold_survivors": wf["survivors"],
         "procedure_trades_per_month": float(turn.sum() / (len(oos) / (365 / 12))),
         "monthly_flags": selection.monthly_flags(proc, index, th["monthly_return_flag"])}
    checks = {
        "oos_sharpe_above_bh_vt": r["procedure"]["sharpe"] > max(r["bh"]["sharpe"], r["vt"]["sharpe"]),
        "oos_calmar_above_bh_vt": r["procedure"]["calmar"] > max(r["bh"]["calmar"], r["vt"]["calmar"]),
        "ci_low_above_0_vs_bh": r["ci_vs_bh"]["lo"] > 0, "ci_low_above_0_vs_vt": r["ci_vs_vt"]["lo"] > 0,
        "dsr": r["dsr"] > th["dsr_min"], "pbo": r["pbo"] < th["pbo_max"], "fdr": fdr <= th["fdr_max"],
        "oos_max_dd": abs(r["procedure"]["max_dd"]) <= th["max_drawdown"],
        "oos_trades_per_month": r["procedure_trades_per_month"] <= th["max_trades_per_month"],
    }
    if wf["final"] < 0:
        r["candidate"] = None
        checks["candidate_exists"] = False
    else:
        nc = wf["nc"]
        cand, ablation = ablate(specs[wf["final"]], nc.net, oos)
        full = np.arange(len(net))
        x = nc.net(cand)
        plat = plateau(cand, full, metrics.sharpe(x) if x.std() > 0 else 0.0, nc, cfg["annualization"])
        fn = spec_fn(cand)
        tr = {}
        for sym, b in basket_bars.items():
            fd = _c.funding_daily(b.index, cfg["costs"], funding_by_sym.get(sym)).to_numpy()
            tr[sym] = float(np.prod(1 + stress.candidate_net(b, cfg, fd, fn)) - 1)
        st = stress.run(bars, cfg, funding_day, fn, [spec_fn(n) for n in neighbors(cand)])
        tpm = float((np.abs(np.diff(backtest.positions(fn(bars, cfg, funding_day), cfg), axis=0)) > 0).sum()
                    / (len(x) / (365 / 12)))
        r["candidate"] = {"selected": spec_id(specs[wf["final"]]), "after_ablation": spec_id(cand), "spec": cand,
                          "ablation": ablation, "full_sample": selection._curve(x), "trades_per_month": tpm,
                          "plateau": plat, "transfer": tr, "transfer_share": float(np.mean(np.array(list(tr.values())) > 0)),
                          "stress": {k: v for k, v in st.items() if k != "facts"}}
        checks.update({"candidate_exists": True, "plateau": plat >= th["plateau_min_ratio"],
                       "transfer": r["candidate"]["transfer_share"] >= th["basket_profitable_share"],
                       "stress": st["passed"],
                       "candidate_max_dd": abs(r["candidate"]["full_sample"]["max_dd"]) <= th["max_drawdown"],
                       "candidate_trades_per_month": tpm <= th["max_trades_per_month"]})
    r["checks"] = {k: bool(v) for k, v in checks.items()}
    r["passed"] = all(r["checks"].values())
    r["verdict"] = "кандидат допущен к holdout" if r["passed"] else "не найдена"
    return r


def leaderboard(specs, net, trades, gcfg, cfg, index):
    """In-sample table of all generated specs on the whole pre-holdout history (for looking, not for choosing)."""
    m = screen_metrics(net, trades, cfg["annualization"])
    ok = screen_mask(m, gcfg, cfg)
    df = pd.DataFrame({"spec": [spec_id(s) for s in specs], "family": [s["family"] for s in specs],
                       "sharpe_is": m["sharpe"], "cagr_is": m["cagr"], "max_dd_is": m["max_dd"],
                       "trades_per_month": m["tpm"], "does_not_lose": ok})
    return df.sort_values("sharpe_is", ascending=False)
