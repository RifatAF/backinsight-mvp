"""Stage 5: portfolio strategies on low-liquidity perpetuals that trade against crowded positioning
(funding) and forced liquidations, hourly bars, costs of the target venue.

Execution model (hourly version of CLAUDE.md section 5): weights W[t] are decided at the close of
bar t from data up to that close and held over bar t+1 (open to open). Per coin and bar:
  gross   = pos * r                 r = open[t+1] / open[t] - 1 (last bar of a delisted coin: close / open)
  cost    = |pos - pos_prev| * (fee + slippage_base + coef * |pos - pos_prev| * capital / quote_volume)
  funding = pos * funding_rate      settled at the end of the bar: longs pay a positive rate
  net     = max(gross, -|pos|) - cost - funding   (a coin cannot lose more than the capital behind it)
The delta-neutral carry adds a spot leg (spot return = perp return corrected by the premium change,
spot fees, no funding). Portfolio bar return = sum over coins; daily return = compounded bars of a UTC day.
"""
import itertools
import json
from functools import partial

import numpy as np
import pandas as pd

from tradelab import parallel

REB_HOUR = 7  # rebalance at the close of the bars 07:00, 15:00, 23:00 = right after each 8h settlement


# ---------- inputs ----------

def prepare(panels, pcfg):
    """Numpy inputs (T x N, float64) for every coin that is eligible at least once."""
    u = pcfg["universe"]
    op = panels["open"]
    keep = [c for c in op.columns if c not in u["exclude"]]
    pn = {k: v[keep] for k, v in panels.items()}
    idx = op.index
    qv = pn["quote_volume"]
    day = qv.groupby(qv.index.floor("D")).sum(min_count=1)
    med = day.rolling(u["volume_window_days"], min_periods=u["volume_window_days"] // 2).median()
    med = med.shift(1)  # known at 00:00 of day D: full days up to D-1
    first = op.notna().idxmax()
    age_days = (day.index.values[:, None] - first.values[None, :]) / np.timedelta64(1, "D")
    ok_day = (med >= u["min_daily_volume_usd"]) & (med <= u["max_daily_volume_usd"]) & (age_days >= u["min_history_days"])
    elig = ok_day.reindex(idx.floor("D")).set_axis(idx).fillna(False) & op.notna()
    cols = elig.columns[elig.any()]
    a = {k: pn[k][cols].to_numpy(dtype=float) for k in ("open", "close", "quote_volume", "taker_buy_quote",
                                                         "premium_open", "funding", "funding_hours")}
    a["funding"] = np.nan_to_num(a["funding"])
    a["settled"] = pn["settled"][cols].fillna(False).to_numpy(dtype=bool)
    a["elig"] = elig[cols].to_numpy(dtype=bool)
    a["listed"] = ~np.isnan(a["open"])
    a["index"], a["symbols"] = idx, list(cols)
    a["r"], a["spot_r"] = returns(a["open"], a["close"], a["premium_open"])
    a["f8"] = funding8(a["funding"], a["funding_hours"], a["settled"])
    a["shock"] = shock_inputs(a["open"], a["close"], a["quote_volume"], a["taker_buy_quote"])
    return a


def returns(op, cl, prem):
    nxt = np.vstack([op[1:], np.full((1, op.shape[1]), np.nan)])
    r = np.where(np.isnan(nxt), cl / op - 1, nxt / op - 1)
    pn = np.vstack([prem[1:], np.full((1, op.shape[1]), np.nan)])
    spot = (1 + r) * (1 + prem) / (1 + pn) - 1  # spot index moves with the perp minus the premium change
    spot = np.where(np.isfinite(spot), spot, r)
    return np.nan_to_num(r), np.nan_to_num(spot)


def funding8(funding, hours, settled):
    """Settled rates scaled to an 8h interval, NaN between settlements."""
    with np.errstate(invalid="ignore", divide="ignore"):
        f = funding * 8.0 / np.where(np.isnan(hours), 8.0, hours)
    return np.where(settled, f, np.nan)


def funding_signal(f8, n):
    """Mean of the last n settled rates (8h scale), carried forward to every later bar."""
    out = np.full(f8.shape, np.nan)
    for j in range(f8.shape[1]):
        ev = np.where(~np.isnan(f8[:, j]))[0]
        if len(ev) < n:
            continue
        m = pd.Series(f8[ev, j]).rolling(n).mean().to_numpy()
        col = np.full(len(f8), np.nan)
        col[ev] = m
        out[:, j] = pd.Series(col).ffill().to_numpy()
    return out


def shock_inputs(op, cl, qv, tbq, window=168):
    """Bar return z-score vs the previous week's hourly volatility, volume vs its previous-week mean,
    taker buy share of the bar."""
    r = pd.DataFrame(cl / op - 1)
    sd = r.rolling(window, min_periods=window // 2).std().shift(1)
    vol = pd.DataFrame(qv)
    vm = vol.rolling(window, min_periods=window // 2).mean().shift(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {"z": (r / sd).to_numpy(), "vr": (vol / vm).to_numpy(), "buy": tbq / qv}


# ---------- strategies ----------

GRIDS = {
    "spread": {"n": [1, 3, 9], "k": [3, 5, 10]},
    "crowd_short": {"n": [1, 3, 9], "thr": [0.0005, 0.001, 0.002], "k": [3, 5, 10]},
    "squeeze_long": {"n": [1, 3, 9], "thr": [0.0003, 0.0005, 0.001], "k": [3, 5, 10]},
    "carry": {"n": [1, 3, 9], "thr": [0.0003, 0.0005, 0.001], "k": [3, 5, 10]},
    "cascade_long": {"z": [3.0, 4.0, 5.0], "m": [3.0, 5.0], "h": [4, 12, 24]},
    "cascade_short": {"z": [3.0, 4.0, 5.0], "m": [3.0, 5.0], "h": [4, 12, 24]},
}
CASCADE_MAX = 10  # at most 10 cascade positions at once, 1/10 of capital each
SHARE = 0.55      # taker sell (buy) share that marks a forced flush down (up)


def configs():
    out = []
    for fam, g in GRIDS.items():
        for vals in itertools.product(*g.values()):
            out.append({"family": fam, **dict(zip(g, vals))})
    return out


def config_id(c):
    return json.dumps(c, sort_keys=True)


def neighbors(confs):
    """Plateau neighbours: +-1 step on one parameter inside the family."""
    pos = {config_id(c): i for i, c in enumerate(confs)}
    out = []
    for c in confs:
        nb = []
        for k, vals in GRIDS[c["family"]].items():
            i = vals.index(c[k])
            for j in (i - 1, i + 1):
                if 0 <= j < len(vals):
                    nb.append(pos[config_id(dict(c, **{k: vals[j]}))])
        out.append(nb)
    return out


def _reb_rows(index):
    return np.where(index.hour % 8 == REB_HOUR)[0]


def _hold(w_reb, rows, t, n):
    """Weights set at rebalance rows, held until the next one."""
    w = np.full((t, n), np.nan)
    w[rows] = w_reb
    w = pd.DataFrame(w).ffill().fillna(0.0).to_numpy()
    return w


def _rank_pick(f, ok, k, top):
    """Boolean mask of the k highest (top) or lowest valid values per row."""
    v = np.where(ok & ~np.isnan(f), f, np.nan)
    pick = np.zeros(f.shape, dtype=bool)
    for i in range(len(v)):
        good = np.where(~np.isnan(v[i]))[0]
        if not len(good):
            continue
        order = good[np.argsort(v[i, good], kind="stable")]
        sel = order[-k:] if top else order[:k]
        pick[i, sel] = True
    return pick


def weights(a, c, sig=None):
    """(perp weights, spot weights) T x N for config c. sig: signal inputs (shifted ones in the null)."""
    sig = sig or a
    t, n = a["open"].shape
    fam = c["family"]
    zero = np.zeros((t, n))
    if fam.startswith("cascade"):
        s = sig["shock"]
        with np.errstate(invalid="ignore"):
            if fam == "cascade_long":
                trig = (s["z"] < -c["z"]) & (s["vr"] > c["m"]) & (1 - s["buy"] > SHARE)
            else:
                trig = (s["z"] > c["z"]) & (s["vr"] > c["m"]) & (s["buy"] > SHARE)
        trig &= a["elig"]
        active = pd.DataFrame(trig.astype(float)).rolling(c["h"], min_periods=1).max().to_numpy() > 0
        active &= a["listed"]
        cnt = active.sum(1, keepdims=True)
        w = np.where(active, np.minimum(1.0 / CASCADE_MAX, 1.0 / np.maximum(cnt, 1)), 0.0)
        return (w if fam == "cascade_long" else -w), zero
    rows = _reb_rows(a["index"])
    f = (sig["fsig"][c["n"]] if "fsig" in sig else funding_signal(sig["f8"], c["n"]))[rows]
    ok = a["elig"][rows]
    k = c["k"]
    if fam == "spread":
        enough = ((ok & ~np.isnan(f)).sum(1) >= 2 * k)[:, None]
        w = 0.5 / k * (_rank_pick(f, ok, k, False).astype(float) - _rank_pick(f, ok, k, True)) * enough
        return _hold(w, rows, t, n) * a["listed"], zero
    if fam == "crowd_short":
        w = -1.0 / k * _rank_pick(f, ok & (f > c["thr"]), k, True)
        return _hold(w, rows, t, n) * a["listed"], zero
    if fam == "squeeze_long":
        w = 1.0 / k * _rank_pick(f, ok & (f < -c["thr"]), k, False)
        return _hold(w, rows, t, n) * a["listed"], zero
    # carry: enter the top k with f > thr, keep a coin while f > thr / 2 and it stays eligible
    held = np.zeros(n, dtype=bool)
    w = np.zeros((len(rows), n))
    for i in range(len(rows)):
        valid = ok[i] & ~np.isnan(f[i])
        held &= valid & (f[i] > c["thr"] / 2)
        free = k - held.sum()
        if free > 0:
            cand = np.where(valid & ~held & (f[i] > c["thr"]))[0]
            cand = cand[np.argsort(-f[i, cand], kind="stable")][:free]
            held[cand] = True
        w[i, held] = 0.5 / k
    sp = _hold(w, rows, t, n) * a["listed"]
    return -sp, sp


# ---------- backtest ----------

def pnl(a, wp, ws, pcfg, cost_mult=1.0, delay=0):
    """Hourly portfolio net returns and the per-bar turnover."""
    v = pcfg["venue"]
    if delay:
        wp = np.vstack([np.zeros((delay, wp.shape[1])), wp[:-delay]])
        ws = np.vstack([np.zeros((delay, ws.shape[1])), ws[:-delay]])
    pos_p = np.vstack([np.zeros((1, wp.shape[1])), wp[:-1]]) * a["listed"]
    pos_s = np.vstack([np.zeros((1, ws.shape[1])), ws[:-1]]) * a["listed"]
    qv = np.where(np.isnan(a["quote_volume"]) | (a["quote_volume"] <= 0), np.inf, a["quote_volume"])

    def leg(pos, r, fee):
        d = np.abs(np.diff(pos, axis=0, prepend=0.0))
        cost = d * (fee + v["slippage_base"] + v["slippage_size_coef"] * d * v["capital_usd"] / qv) * cost_mult
        return np.maximum(pos * r, -np.abs(pos)) - cost, d

    net_p, dp = leg(pos_p, a["r"], v["taker_fee"])
    net_s, ds = leg(pos_s, a["spot_r"], v["spot_taker_fee"])
    fund = pos_p * a["funding"]
    bar = (net_p + net_s - fund).sum(1)
    return np.maximum(bar, -0.9999), (dp + ds).sum(1)


def daily(a, bar, turnover):
    day = a["index"].floor("D")
    d = pd.Series(np.log1p(bar)).groupby(day).sum()
    tr = pd.Series(turnover > 0).groupby(day).any()
    return np.expm1(d.to_numpy()), tr.to_numpy(), d.index


def run_config(c, a, pcfg, sig=None, cost_mult=1.0, delay=0):
    wp, ws = weights(a, c, sig)
    bar, tr = pnl(a, wp, ws, pcfg, cost_mult, delay)
    net, trades, _ = daily(a, bar, tr)
    return net, trades


def with_fsig(sig, confs):
    """Funding signals for every window n used by confs, computed once and shared by all configs."""
    return dict(sig, fsig={n: funding_signal(sig["f8"], n) for n in sorted({c["n"] for c in confs if "n" in c})})


def evaluate(a, pcfg, confs, sig=None, jobs=None):
    """Daily net (D x N) and daily trade flags for all configs."""
    sig = with_fsig(a if sig is None else sig, confs)
    res = parallel.pmap(partial(run_config, a=a, pcfg=pcfg, sig=sig), confs, jobs)
    return np.column_stack([r[0] for r in res]), np.column_stack([r[1] for r in res])


def days(a):
    return pd.DatetimeIndex(a["index"].floor("D").unique())


# ---------- null: signals detached from future returns ----------

def shifted_signals(a, seed, min_shift_hours):
    """Each coin's signal inputs (funding, shock features) circularly shifted inside its listed span by
    its own random offset; prices, eligibility and the funding actually paid stay real."""
    rng = np.random.default_rng(seed)
    f8 = np.full(a["f8"].shape, np.nan)
    shock = {k: np.full(v.shape, np.nan) for k, v in a["shock"].items()}
    for j in range(a["f8"].shape[1]):
        live = np.where(a["listed"][:, j])[0]
        if len(live) < 3 * min_shift_hours:
            continue
        lo, hi = live[0], live[-1] + 1
        k = int(rng.integers(min_shift_hours, hi - lo - min_shift_hours))
        f8[lo:hi, j] = np.roll(a["f8"][lo:hi, j], k)
        for name, v in a["shock"].items():
            shock[name][lo:hi, j] = np.roll(v[lo:hi, j], k)
    return {"f8": f8, "shock": shock}
