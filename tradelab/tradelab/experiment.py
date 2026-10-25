"""Stage 3 experiments: derivatives / sentiment features, long-short, 6h bars, basket portfolios.

An experiment is experiments/<name>.yaml. Its strategies are generator specs (price and feature
families). Bars may be 1d or 6h and the universe one coin or the basket; every strategy is run per coin
at bar level with the section 5 cost model, compounded to daily returns and averaged with equal
weights over the coins available that day. Walk-forward selection, screening, plateau, metrics, DSR,
PBO and CIs then work on the daily portfolio series exactly as in stage 2. Every strategy is a journal
trial; the DSR counts all trials of all stages.
"""
import copy
import hashlib
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from tradelab import (backtest, data, features, generator, indicators, journal, metrics, parallel,
                      strategies, synth, walkforward)

EXP_DIR = Path(__file__).resolve().parent.parent / "experiments"
BPD = {"1d": 1, "6h": 4}
WARMUP_DAYS = 250  # a coin enters the equal-weight portfolio 250 days after its first bar
FAMILY_SETS = {"price": list(indicators.FAMILIES), "features": list(indicators.FEATURE_FAMILIES),
               "all": list(indicators.ALL_FAMILIES)}


class ExperimentChanged(Exception):
    pass


def load(name):
    p = EXP_DIR / f"{name}.yaml"
    raw = p.read_bytes()
    e = yaml.safe_load(raw)
    e["name"] = name
    return e, hashlib.sha256(raw).hexdigest()


def gen_config(e):
    g = {k: e[k] for k in ("seed", "n_strategies", "combo_share", "trend_filter_share", "screen", "plateau_top_k")}
    g["families"] = FAMILY_SETS[e["families"]]
    g["direction"] = e["direction"]
    return g


def universe(e, info):
    if e["universe"] == "basket":
        return list(dict.fromkeys(["ETH/USDT"] + info["basket"]))
    return [e["universe"]]


# ---------- per-coin context ----------

def bar_funding(bars, tf, events, daily, costs):
    """Funding per bar: archive events summed per bar (0 inside the history when a bar has none),
    the fallback 3 x 0.0001 per day outside it."""
    bpd = BPD[tf]
    fb = 3 * costs["funding_fallback_per_8h"] / bpd
    if tf == "1d":
        f = daily["funding_day"] if daily is not None else pd.Series(dtype=float)
        return f.reindex(bars.index).fillna(fb).to_numpy()
    if events is None or not len(events):
        return np.full(len(bars), fb)
    per = events.groupby(events.index.floor(f"{24 // bpd}h")).sum().reindex(bars.index).fillna(0.0)
    inside = (bars.index >= events.index[0].floor("D")) & (bars.index <= events.index[-1])
    return np.where(inside, per.to_numpy(), fb)


def coin_context(sym, tf, cfg, bars, daily_close, feats_daily, events):
    bpd = BPD[tf]
    fd = feats_daily.copy() if feats_daily is not None else pd.DataFrame(index=daily_close.index, columns=features.COLUMNS)
    fd["cb_premium"] = fd["cb_close"] / daily_close.reindex(fd.index) - 1
    feats = features.align(fd, bars, tf)
    feats.attrs["bpd"] = bpd
    fday = bar_funding(bars, tf, events, feats_daily, cfg["costs"])
    return {"sym": sym, "tf": tf, "bpd": bpd, "bars": bars, "inv": indicators.invert(bars), "feats": feats,
            "fday": fday, "inputs": strategies.layer_inputs(bars, cfg, fday, bpd),
            "day": bars.index[:-1].floor("D"), "start": bars.index[0].floor("D") + pd.Timedelta(days=WARMUP_DAYS)}


def load_coins(e, cfg, info):
    tf = e["timeframe"]
    out = []
    for sym in universe(e, info):
        bars = data.load_raw(sym, tf)
        out.append(coin_context(sym, tf, cfg, bars, data.load_raw(sym, "1d")["close"], features.load(sym),
                                data.load_funding(sym)))
    return out


def with_bars(coin, cfg, bars):
    """Same coin with other price bars (synthetic paths): features and funding stay real."""
    c = dict(coin, bars=bars, inv=indicators.invert(bars))
    c["inputs"] = strategies.layer_inputs(bars, cfg, c["fday"], c["bpd"])
    return c


def days_index(coins):
    lo = min(c["day"][0] for c in coins)
    hi = max(c["day"][-1] for c in coins)
    return pd.date_range(lo, hi, freq="D", tz="UTC")


# ---------- targets ----------

def target(coin, cfg, spec):
    bars, feats = coin["bars"], coin["feats"]
    lo, sh = indicators.long_short(bars, feats, spec["family"], spec["params"], coin["inv"])
    if spec["confirm"]:
        l2, s2 = indicators.long_short(bars, feats, spec["confirm"]["family"], spec["confirm"]["params"], coin["inv"])
        lo, sh = lo * l2, sh * s2
    if spec["trend"]:
        sma = bars["close"].rolling(spec["trend"] * coin["bpd"]).mean()
        lo, sh = lo * (bars["close"] > sma), sh * (bars["close"] < sma)
    b = lo if spec.get("direction", "long") == "long" else lo - sh
    return strategies.compose(b.astype(float), {"v": spec["v"], "p": spec["p"], "q": None}, coin["inputs"], cfg).to_numpy()


def bh_target(coin, cfg):
    return np.ones(len(coin["bars"]))


def vt_target(coin, cfg):
    c = copy.deepcopy(cfg)
    c["annualization"] *= coin["bpd"]
    c["benchmarks"]["vol_ewma_span"] *= coin["bpd"]
    return strategies.vt(coin["bars"], c).to_numpy()


def spec_target(spec):
    return partial(_spec_target, spec=spec)


def _spec_target(coin, cfg, spec):
    return target(coin, cfg, spec)


def delayed(fn, k):
    return partial(_delayed, fn=fn, k=k)


def _delayed(coin, cfg, fn, k):
    s = fn(coin, cfg)
    return np.concatenate([np.zeros(k), s[:-k]])


# ---------- evaluation ----------

def _daily(coin, net, trades, days):
    d = pd.DataFrame(np.log1p(net)).groupby(coin["day"]).sum()
    t = pd.DataFrame(trades).groupby(coin["day"]).any()
    live = (d.index >= coin["start"])
    d, t = d[live], t[live]
    return np.expm1(d).reindex(days).to_numpy(), t.reindex(days, fill_value=False).to_numpy(dtype=bool)


def evaluate(coins, cfg, fns, days):
    """Daily equal-weight portfolio net (D x n) and trade flags for target functions fn(coin, cfg)."""
    total = np.zeros((len(days), len(fns)))
    count = np.zeros((len(days), 1))
    trades = np.zeros((len(days), len(fns)), dtype=bool)
    for coin in coins:
        s = np.column_stack([fn(coin, cfg) for fn in fns])
        p = backtest.pnl(coin["bars"], backtest.positions(s, cfg), cfg, coin["fday"])
        net, tr = _daily(coin, p["net"][:-1], p["turnover"][:-1] > 0, days)
        have = ~np.isnan(net[:, 0])
        total[have] += net[have]
        count[have] += 1
        trades |= tr
    return np.where(count > 0, total / np.maximum(count, 1), 0.0), trades


def _chunk(specs, coins, cfg, days):
    return evaluate(coins, cfg, [spec_target(s) for s in specs], days)


def evaluate_specs(coins, cfg, specs, days, jobs=None, chunk=25):
    parts = [specs[i:i + chunk] for i in range(0, len(specs), chunk)]
    res = parallel.pmap(partial(_chunk, coins=coins, cfg=cfg, days=days), parts, jobs)
    return np.concatenate([r[0] for r in res], axis=1), np.concatenate([r[1] for r in res], axis=1)


class Cache:
    """generator.choose / plateau interface: daily portfolio net of any spec, computed on demand."""

    def __init__(self, coins, cfg, days, specs=(), net=None):
        self.coins, self.cfg, self.days = coins, cfg, days
        self.cache = {generator.spec_id(s): net[:, j] for j, s in enumerate(specs)} if net is not None else {}

    def net(self, spec):
        k = generator.spec_id(spec)
        if k not in self.cache:
            self.cache[k] = evaluate(self.coins, self.cfg, [spec_target(spec)], self.days)[0][:, 0]
        return self.cache[k]


def stitched(coins, cfg, days, specs, choices, folds):
    """Portfolio net of the stitched position: each coin holds the fold winner's position in its test
    window and nothing elsewhere, run through the same cost model (switches pay costs)."""
    total = np.zeros(len(days))
    count = np.zeros(len(days))
    for coin in coins:
        pos = np.zeros(len(coin["bars"]))
        cache = {}
        bar_day = coin["bars"].index.floor("D")
        for j, (_, ts, te) in zip(choices, folds):
            if j < 0:
                continue
            if j not in cache:
                cache[j] = backtest.positions(target(coin, cfg, specs[j]), cfg)
            m = (bar_day >= days[ts]) & (bar_day <= days[te - 1])
            pos[m] = cache[j][m]
        p = backtest.pnl(coin["bars"], pos, cfg, coin["fday"])
        net, _ = _daily(coin, p["net"][:-1, None], (p["turnover"][:-1] > 0)[:, None], days)
        have = ~np.isnan(net[:, 0])
        total[have] += net[have, 0]
        count[have] += 1
    return np.where(count > 0, total / np.maximum(count, 1), 0.0)


def walk_forward(coins, cfg, days, specs, net, trades, gcfg):
    nc = Cache(coins, cfg, days, specs, net)
    folds = walkforward.folds(len(days), cfg)
    choices, survivors = [], []
    for tr_end, ts, te in folds:
        j, n_ok = generator.choose(net, trades, np.arange(tr_end), specs, nc, gcfg, cfg)
        choices.append(j)
        survivors.append(n_ok)
    oos = np.concatenate([np.arange(ts, te) for _, ts, te in folds])
    proc = stitched(coins, cfg, days, specs, choices, folds)
    final, n_final = generator.choose(net, trades, np.arange(len(net)), specs, nc, gcfg, cfg)
    return {"choices": choices, "survivors": survivors, "folds": folds, "oos": oos, "net_oos": proc[oos],
            "final": final, "final_survivors": n_final, "nc": nc}


def benchmarks(coins, cfg, days):
    net, _ = evaluate(coins, cfg, [bh_target, vt_target], days)
    return {"bh": net[:, 0], "vt": net[:, 1]}


# ---------- journal ----------

def all_trial_srs(conn):
    srs = [e["payload"]["metrics"]["daily_sr"] for e in journal.events(conn, "trial_metrics")]
    return np.array([s for s in srs if s is not None])


def search(e, esha, coins, cfg, conn, specs, days, sha, jobs=None):
    from tradelab import selection

    stage = f"experiment:{e['name']}"
    mine = [t for t in journal.trials(conn) if t["config"].get("stage") == stage]
    rng = [str(days[0].date()), str(days[-1].date())]
    ids = None
    if not mine:
        ids = [journal.start_trial(conn, {"stage": stage, "experiment_sha256": esha, "spec": sp}, rng, sha)
               for sp in specs]
    elif len(mine) != len(specs) or any(t["config"]["spec"] != sp for t, sp in zip(mine, specs)):
        raise ExperimentChanged(f"journaled trials of {stage} do not match the current specs")
    net, trades = evaluate_specs(coins, cfg, specs, days, jobs)
    logged = {ev["payload"]["trial_id"]: ev["payload"]["metrics"] for ev in journal.events(conn, "trial_metrics")}
    for j in range(len(specs)):
        m = selection.trial_metrics(net[:, j], trades[:, j].astype(float), cfg)
        if ids is not None:
            journal.finish_trial(conn, ids[j], m)
        elif logged[mine[j]["id"]] != m:
            raise AssertionError(f"replay differs from journal for trial {mine[j]['id']}")
    return net, trades, ids is None


# ---------- checks ----------

def _sharpe(x):
    x = np.asarray(x, dtype=float)
    return float(metrics.sharpe(x)) if x.std() > 0 else 0.0


def stress(coins, cfg, days, fn, neighbor_fns, n_b, seed):
    from tradelab import stress as st

    base = evaluate(coins, cfg, [fn], days)[0][:, 0]
    share, _ = st.bootstrap_share(base, cfg)
    out = {"sharpe": _sharpe(base), "boot_positive_share": share,
           "double_costs_sharpe": _sharpe(evaluate(coins, st.double_costs(cfg), [fn], days)[0][:, 0]),
           "delay2_sharpe": _sharpe(evaluate(coins, cfg, [delayed(fn, 2)], days)[0][:, 0])}
    nb = [_sharpe(x) for x in evaluate(coins, cfg, neighbor_fns, days)[0].T] if neighbor_fns else []
    out["neighbor_sharpes"] = nb
    out["neighbor_positive_share"] = float(np.mean(np.array(nb) > 0)) if nb else 0.0
    prms = [synth.fit_b(c["bars"]) for c in coins]
    srs = []
    for s in parallel.job_seeds(seed, n_b):
        sub = parallel.job_seeds(s, len(coins))
        paths = [with_bars(c, cfg, synth.path_b(c["bars"], prm, k)) for c, prm, k in zip(coins, prms, sub)]
        srs.append(_sharpe(evaluate(paths, cfg, [fn], days)[0][:, 0]))
    out["synth_b_positive_share"] = float(np.mean(np.array(srs) > 0))
    th = cfg["thresholds"]["stress_positive_share"]
    out["passed"] = bool(share >= th and out["double_costs_sharpe"] > 0 and out["delay2_sharpe"] > 0
                         and out["neighbor_positive_share"] >= th and out["synth_b_positive_share"] >= th)
    return out


def check(e, coins, cfg, conn, days, specs, net, wf, fdr, transfer_coins):
    from tradelab import selection

    th = cfg["thresholds"]
    oos, proc = wf["oos"], wf["net_oos"]
    index = days[oos]
    bench = {k: v[oos] for k, v in benchmarks(coins, cfg, days).items()}
    srs = all_trial_srs(conn)
    lvl, blk = th["ci_level"], cfg["synth"]["bootstrap_mean_block"]
    r = {"experiment": e["name"], "n_trials_total": len(srs), "n_generated": len(specs),
         "coins": [c["sym"] for c in coins], "timeframe": e["timeframe"], "direction": e["direction"],
         "oos_days": len(oos), "oos_start": str(index[0].date()),
         "procedure": selection._curve(proc), "bh": selection._curve(bench["bh"]), "vt": selection._curve(bench["vt"]),
         "ci_vs_bh": metrics.sharpe_diff_ci(proc, bench["bh"], lvl, blk, 1000, cfg["seed"]),
         "ci_vs_vt": metrics.sharpe_diff_ci(proc, bench["vt"], lvl, blk, 1000, cfg["seed"]),
         "dsr": metrics.dsr(proc, srs), "pbo": metrics.pbo(net[WARMUP_DAYS:]), "fdr": fdr,
         "fold_choices": [generator.spec_id(specs[j]) if j >= 0 else "cash" for j in wf["choices"]],
         "fold_survivors": wf["survivors"],
         "monthly_flags": selection.monthly_flags(proc, index, th["monthly_return_flag"])}
    checks = {
        "oos_sharpe_above_bh_vt": r["procedure"]["sharpe"] > max(r["bh"]["sharpe"], r["vt"]["sharpe"]),
        "oos_calmar_above_bh_vt": r["procedure"]["calmar"] > max(r["bh"]["calmar"], r["vt"]["calmar"]),
        "ci_low_above_0_vs_bh": r["ci_vs_bh"]["lo"] > 0, "ci_low_above_0_vs_vt": r["ci_vs_vt"]["lo"] > 0,
        "dsr": r["dsr"] > th["dsr_min"], "pbo": r["pbo"] < th["pbo_max"], "fdr": fdr <= th["fdr_max"],
        "oos_max_dd": abs(r["procedure"]["max_dd"]) <= th["max_drawdown"],
    }
    if wf["final"] < 0:
        r["candidate"] = None
        checks["candidate_exists"] = False
    else:
        nc = wf["nc"]
        cand, ablation = generator.ablate(specs[wf["final"]], nc.net, oos)
        x = nc.net(cand)
        plat = generator.plateau(cand, np.arange(len(x)), _sharpe(x), nc, cfg["annualization"])
        fn = spec_target(cand)
        per_coin = {}
        for c in transfer_coins:
            d = days_index([c])
            per_coin[c["sym"]] = float(np.prod(1 + np.nan_to_num(evaluate([c], cfg, [fn], d)[0][:, 0])) - 1)
        st = stress(coins, cfg, days, fn, [spec_target(n) for n in generator.neighbors(cand)],
                    e["n_synth_b"], cfg["seed"] + 2)
        _, tr = evaluate(coins, cfg, [fn], days)
        tpm = float(tr.sum() / (len(x) / (365 / 12)))
        r["candidate"] = {"selected": generator.spec_id(specs[wf["final"]]), "after_ablation": generator.spec_id(cand),
                          "spec": cand, "ablation": ablation, "full_sample": selection._curve(x),
                          "trades_per_month": tpm, "plateau": plat, "transfer": per_coin,
                          "transfer_share": float(np.mean(np.array(list(per_coin.values())) > 0)) if per_coin else 0.0,
                          "stress": st}
        checks.update({"candidate_exists": True, "plateau": plat >= th["plateau_min_ratio"],
                       "transfer": r["candidate"]["transfer_share"] >= th["basket_profitable_share"],
                       "stress": st["passed"],
                       "candidate_max_dd": abs(r["candidate"]["full_sample"]["max_dd"]) <= th["max_drawdown"],
                       "candidate_trades_per_month": tpm <= th["max_trades_per_month"]})
    r["checks"] = {k: bool(v) for k, v in checks.items()}
    r["passed"] = all(r["checks"].values())
    r["verdict"] = "кандидат допущен к holdout" if r["passed"] else "не найдена"
    return r


def transfer_coins(e, cfg, info, coins):
    """Coins to test the candidate on: the universe itself for a basket, else the other coins at this timeframe."""
    if e["universe"] == "basket":
        return coins
    tf = e["timeframe"]
    out = []
    for sym in dict.fromkeys(cfg["data"]["symbols_main"] + info["basket"]):
        if sym == e["universe"] or not (data.RAW_DIR / data.fname(sym, tf)).exists():
            continue
        out.append(coin_context(sym, tf, cfg, data.load_raw(sym, tf), data.load_raw(sym, "1d")["close"],
                                features.load(sym), data.load_funding(sym)))
    return out


# ---------- calibration ----------

def evaluate_path(coins, cfg, days, specs, gcfg, e, seed, phi):
    sub = parallel.job_seeds(seed, len(coins))
    block = cfg["synth"]["bootstrap_mean_block"]
    paths = [with_bars(c, cfg, synth.path(c["bars"], k, phi, block * c["bpd"])) for c, k in zip(coins, sub)]
    net, trades = evaluate(paths, cfg, [spec_target(s) for s in specs], days)
    wf = walk_forward(paths, cfg, days, specs, net, trades, gcfg)
    bh = benchmarks(paths, cfg, days)["bh"][wf["oos"]]
    proc = wf["net_oos"]
    th = cfg["thresholds"]
    if np.std(proc) == 0:
        return {"phi": phi, "seed": seed, "finding": False, "ci_lo": np.nan, "dsr": np.nan, "sharpe_oos": 0.0}
    with np.errstate(divide="ignore", invalid="ignore"):
        dsr_ = net.mean(0) / net.std(0, ddof=1)
    ci = metrics.sharpe_diff_ci(proc, bh, th["ci_level"], cfg["synth"]["bootstrap_mean_block"], 1000, seed)
    d = metrics.dsr(proc, dsr_[np.isfinite(dsr_)])
    return {"phi": phi, "seed": seed, "ci_lo": ci["lo"], "dsr": d, "sharpe_oos": metrics.sharpe(proc),
            "finding": bool(ci["lo"] > 0 and d > th["dsr_min"])}


def _cal_job(task, coins, cfg, days, specs, gcfg, e):
    phi, seed = task
    return evaluate_path(coins, cfg, days, specs, gcfg, e, seed, phi)


def calibrate(e, coins, cfg, days, specs, gcfg, jobs=None, n_paths=None):
    n = e["n_null_paths"] if n_paths is None else n_paths
    seeds = parallel.job_seeds(e["seed"] + 1000, n)
    tasks = [(phi, s) for phi in (0.0, 0.20) for s in seeds]
    fn = partial(_cal_job, coins=coins, cfg=cfg, days=days, specs=specs, gcfg=gcfg, e=e)
    return pd.DataFrame(parallel.pmap(fn, tasks, jobs))


# ---------- holdout ----------

def holdout_run(e, cfg, conn, info, spec):
    """One-time holdout read: candidate and portfolio bh / vt on holdout days, signals warmed up on history."""
    from tradelab import holdout

    hold = holdout.load(allow=True, conn=conn)
    tf = e["timeframe"]
    start = pd.Timestamp(info["holdout_start"])
    coins = []
    for sym in universe(e, info):
        stem = sym.replace("/", "_")
        bars = pd.concat([data.load_raw(sym, tf), hold[f"{stem}_{tf}"]])
        close = pd.concat([data.load_raw(sym, "1d")["close"], hold[f"{stem}_1d"]["close"]])
        fr = features.load(sym)
        fh = hold.get(f"{stem}_features")
        fd = pd.concat([fr, fh]) if fr is not None and fh is not None else fr
        ev = data.load_funding(sym)
        eh = hold.get(f"{stem}_funding")
        if ev is not None and eh is not None:
            ev = pd.concat([ev, eh["funding_rate"]])
        coins.append(coin_context(sym, tf, cfg, bars, close, fd, ev))
    days = days_index(coins)
    net, _ = evaluate(coins, cfg, [spec_target(spec), bh_target, vt_target], days)
    m = days >= start
    from tradelab import selection

    cur = {k: {**selection._curve(net[m, i]), "days": int(m.sum())} for i, k in enumerate(("candidate", "bh", "vt"))}
    c = cur["candidate"]
    ok = all(c[k] >= cur[b][k] for b in ("bh", "vt") for k in ("sharpe", "calmar"))
    return {"verdict": holdout.FOUND if ok else holdout.NOT_FOUND, "holdout_read": True,
            "candidate": generator.spec_id(spec), "experiment": e["name"], "holdout": cur}
