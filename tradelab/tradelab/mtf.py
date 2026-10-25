"""Stage 6: three-layer top-down strategy on liquid perpetuals, decided on 5-minute bars.

  layer 1, regime (1M / 1W): which side is allowed and in which direction the market trends
     none   both sides allowed, no direction
     w      weekly close vs its 20-week EMA
     m      monthly close vs its 10-month SMA
     w_cb   weekly trend confirmed by the 30-day Coinbase premium (US spot demand); otherwise flat
  layer 2, setup (1D / 4H): where and in which direction to look for an entry
     none        the regime direction itself (pure trend)
     crowd       against a crowded side: funding and long/short ratio both in the top (bottom) 20% of
                 their history while open interest grew over 3 days -> short (long)
     trend_flow  with a healthy trend: 4h close above (below) the 20-day EMA, open interest growing,
                 takers buying (selling) over the last day, funding not extreme
     liq_magnet  toward the larger cluster of estimated liquidations within +-5% of price
  layer 3, trigger (1H / 15m / 5m): when to enter
     none     at the next bar
     break15  15m close beyond the high (low) of the previous four 15m bars
     flush5   a 5m flush (taker selling > 60% while open interest drops > 0.3%) in the last three 5m
              bars and then an up bar; mirrored for shorts
Higher layers gate lower ones: an entry needs the setup direction, permission of the regime and the
trigger on that side. Exits: the regime withdraws permission, the opposite setup appears, a close beyond
2 x ATR(1h) from entry (then no new trade on that side for a day), or 3 days in the trade (not for the
pure trend setup). Every value is used only
after the bar that produced it has closed. Size: 1/N of capital per coin x min(2, 20% / annual vol).
"""
import itertools
import json

import numpy as np
import pandas as pd

from tradelab import parallel, perps

try:
    from numba import njit
except ImportError:  # pure-Python fallback, much slower
    def njit(*a, **k):
        return (lambda f: f) if not a or not callable(a[0]) else a[0]

REGIMES = ["none", "w", "m", "w_cb"]
SETUPS = ["none", "crowd", "trend_flow", "liq_magnet"]
TRIGGERS = ["none", "break15", "flush5"]
BAR = pd.Timedelta(minutes=5)


def configs():
    return [{"regime": r, "setup": s, "trigger": t} for r, s, t in itertools.product(REGIMES, SETUPS, TRIGGERS)
            if not (r == "none" and s == "none")]  # no regime and no setup: no direction at all


def config_id(c):
    return json.dumps(c, sort_keys=True)


def neighbors(confs):
    """Plateau neighbours: the configs that differ in exactly one layer."""
    out = []
    for i, c in enumerate(confs):
        out.append([j for j, d in enumerate(confs) if j != i and sum(c[k] != d[k] for k in c) == 1])
    return out


# ---------- alignment ----------

def _close_times(labels, rule):
    if rule == "MS":
        return labels + pd.offsets.MonthBegin(1)
    return labels + (pd.Timedelta(days=7) if rule == "W" else pd.Timedelta(rule))


def higher(df, rule, how):
    """Higher-timeframe bars of a 5m frame (columns = coins): how = last / max / min / sum / mean."""
    if rule == "W":
        g = df.groupby(df.index.normalize() - pd.to_timedelta(df.index.dayofweek, unit="D"))
    else:
        g = df.resample(rule, label="left", closed="left")
    return getattr(g, how)()


def align(hdf, rule, index):
    """A higher bar's value becomes usable at the close of the 5m bar that ends at its close time."""
    s = hdf.copy()
    s.index = _close_times(s.index, rule)
    close_t = index + BAR
    out = s.reindex(s.index.union(close_t)).ffill().reindex(close_t)
    out.index = index
    return out.to_numpy(dtype=float)


def _pct(x, min_bars):
    return x.expanding(min_bars).rank(pct=True)


# ---------- liquidation map ----------

@njit(cache=True)
def _bin(x, lo, width, nb):
    k = int((np.log(x) - lo) / width)
    return min(max(k, 0), nb - 1)


@njit(cache=True)
def _liq_map(close, high, low, oi, buy, lev, w, band, width):
    """Price grid anchored at the first close (known from the start), +-6 in log price."""
    n = len(close)
    out = np.full(n, np.nan)
    first = -1
    for t in range(n):
        if not np.isnan(close[t]):
            first = t
            break
    if first < 0:
        return out
    lo = np.log(close[first]) - 6.0
    nb = int(12.0 / width) + 1
    lm = np.zeros(nb)
    sm = np.zeros(nb)
    l_hi, s_lo = -1, nb  # highest bin holding long levels, lowest bin holding short levels
    prev = np.nan
    for t in range(first, n):
        c = close[t]
        if np.isnan(c):
            continue
        if not np.isnan(oi[t]) and not np.isnan(prev) and prev > 0:
            d = oi[t] - prev
            if d > 0:
                b = buy[t] if not np.isnan(buy[t]) else 0.5
                for i in range(len(lev)):
                    kl = _bin(c * (1 - 1 / lev[i] + 0.005), lo, width, nb)
                    ks = _bin(c * (1 + 1 / lev[i] - 0.005), lo, width, nb)
                    lm[kl] += d * b * w[i]
                    sm[ks] += d * (1 - b) * w[i]
                    l_hi = max(l_hi, kl)
                    s_lo = min(s_lo, ks)
            elif d < 0:
                f = oi[t] / prev
                lm *= f
                sm *= f
        if not np.isnan(oi[t]):
            prev = oi[t]
        kl = _bin(low[t], lo, width, nb)
        if kl <= l_hi:  # longs whose liquidation price the low reached are gone
            lm[kl: l_hi + 1] = 0.0
            l_hi = kl - 1
        kh = _bin(high[t], lo, width, nb)
        if kh >= s_lo:
            sm[s_lo: kh + 1] = 0.0
            s_lo = kh + 1
        kc = _bin(c, lo, width, nb)
        above = sm[kc + 1: _bin(c * (1 + band), lo, width, nb) + 1].sum()
        below = lm[_bin(c * (1 - band), lo, width, nb): kc].sum()
        if above + below > 0:
            out[t] = (above + 1e-9) / (below + 1e-9)
    return out


def liquidation_ratio(c15, h15, l15, oi15, buy15, rules):
    """Estimated short liquidations above / long liquidations below within the band, per 15m bar."""
    lev = np.array(rules["liq_leverage"], dtype=float)
    w = np.array(rules["liq_weights"], dtype=float)
    out = np.full(c15.shape, np.nan)
    for j in range(c15.shape[1]):
        out[:, j] = _liq_map(c15[:, j], h15[:, j], l15[:, j], oi15[:, j], buy15[:, j], lev, w,
                             rules["liq_band"], 0.0025)
    return out


# ---------- inputs and layer features ----------

def panels(frames, start_5m=None):
    idx = None
    for f in frames.values():
        idx = f.index if idx is None else idx.union(f.index)
    idx = pd.date_range(idx[0], idx[-1], freq="5min", tz="UTC")
    cols = ["open", "high", "low", "close", "quote_volume", "taker_buy_quote", "oi", "ls_global", "taker_ratio",
            "funding", "settled", "funding_hours"]
    return {k: pd.DataFrame({s: f[k] for s, f in frames.items()}).reindex(idx) for k in cols}


def features(p, cb, mcfg):
    """Every layer's signals on the 5m grid (T x N), each usable at the close of its row."""
    rules = mcfg["rules"]
    idx = p["close"].index
    close = p["close"]
    f = {}
    # regime
    wk = higher(close, "W", "last")
    ema = wk.ewm(span=20, adjust=False, min_periods=20).mean()
    f["w"] = align(np.sign(wk - ema), "W", idx)
    mo = higher(close, "MS", "last")
    f["m"] = align(np.sign(mo - mo.rolling(10).mean()), "MS", idx)
    h1 = higher(close, "1h", "last")
    # Coinbase premium: ETH's own for ETH, BTC's for every other coin (US spot demand for crypto)
    prem = {}
    for b, ref in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        if cb is not None and b in cb and ref in h1:
            prem[b] = (cb[b].reindex(h1.index) / h1[ref] - 1).rolling(720, min_periods=360).mean()
    if prem:
        pr = pd.DataFrame({s: prem.get("ETH" if s == "ETHUSDT" and "ETH" in prem else "BTC", prem[next(iter(prem))])
                           for s in close.columns})
        f["cb"] = align(np.sign(pr), "1h", idx)
    else:
        f["cb"] = np.full(close.shape, np.nan)
    # setup (4h decisions)
    f8 = perps.funding8(np.nan_to_num(p["funding"].to_numpy()), p["funding_hours"].to_numpy(),
                        p["settled"].fillna(False).to_numpy(dtype=bool))
    fs = pd.DataFrame(perps.funding_signal(f8, 3), index=idx, columns=close.columns)
    c4 = higher(close, "4h", "last")
    pf = _pct(higher(fs, "4h", "last"), 6 * 90)
    pl = _pct(higher(p["ls_global"], "4h", "last"), 6 * 90)
    oi4 = higher(p["oi"], "4h", "last")
    dO = oi4 / oi4.shift(18) - 1
    tk = higher(p["taker_ratio"], "4h", "mean").rolling(6, min_periods=6).mean()
    d1 = higher(close, "1D", "last")
    ema20d = d1.ewm(span=20, adjust=False, min_periods=20).mean()
    e4 = pd.DataFrame(align(ema20d, "1D", c4.index - BAR + pd.Timedelta(hours=4)), index=c4.index, columns=c4.columns)
    crowd = np.where((pf > 0.8) & (pl > 0.8) & (dO > 0), -1.0, np.where((pf < 0.2) & (pl < 0.2) & (dO > 0), 1.0, 0.0))
    flow = np.where((c4 > e4) & (dO > 0) & (tk > 1) & (pf < 0.8), 1.0,
                    np.where((c4 < e4) & (dO > 0) & (tk < 1) & (pf > 0.2), -1.0, 0.0))
    f["crowd"] = align(pd.DataFrame(crowd, index=c4.index, columns=c4.columns), "4h", idx)
    f["trend_flow"] = align(pd.DataFrame(flow, index=c4.index, columns=c4.columns), "4h", idx)
    c15, h15, l15 = higher(close, "15min", "last"), higher(p["high"], "15min", "max"), higher(p["low"], "15min", "min")
    oi15 = higher(p["oi"], "15min", "last")
    with np.errstate(invalid="ignore", divide="ignore"):
        buy15 = higher(p["taker_buy_quote"], "15min", "sum") / higher(p["quote_volume"], "15min", "sum")
    lr = liquidation_ratio(c15.to_numpy(), h15.to_numpy(), l15.to_numpy(), oi15.to_numpy(), buy15.to_numpy(), rules)
    lr = pd.DataFrame(lr, index=c15.index, columns=c15.columns)
    lr4 = higher(lr, "4h", "last")
    f["liq_magnet"] = align(pd.DataFrame(np.where(lr4 > 2, 1.0, np.where(lr4 < 0.5, -1.0, 0.0)), index=lr4.index,
                                         columns=lr4.columns), "4h", idx)
    # trigger
    up15 = c15 > h15.shift(1).rolling(4).max()
    dn15 = c15 < l15.shift(1).rolling(4).min()
    f["break15_up"] = align(up15.astype(float), "15min", idx) > 0
    f["break15_dn"] = align(dn15.astype(float), "15min", idx) > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        sell = 1 - p["taker_buy_quote"] / p["quote_volume"]
        doi = p["oi"] / p["oi"].shift(1) - 1
    flush_dn = ((sell > 0.6) & (doi < -0.003)).astype(float).rolling(3, min_periods=1).max() > 0
    flush_up = ((sell < 0.4) & (doi < -0.003)).astype(float).rolling(3, min_periods=1).max() > 0
    f["flush5_up"] = (flush_dn & (close > p["open"])).to_numpy()
    f["flush5_dn"] = (flush_up & (close < p["open"])).to_numpy()
    # risk
    tr = pd.concat([higher(p["high"], "1h", "max") - higher(p["low"], "1h", "min"),
                    (higher(p["high"], "1h", "max") - h1.shift(1)).abs(),
                    (higher(p["low"], "1h", "min") - h1.shift(1)).abs()]).groupby(level=0).max()
    f["atr"] = align(tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean(), "1h", idx)
    vol = np.log(h1).diff().rolling(720, min_periods=360).std() * np.sqrt(24 * 365)
    n = close.shape[1]
    f["size"] = np.nan_to_num(align(np.minimum(rules["max_leverage"], rules["vol_target"] / vol) / n, "1h", idx))
    return f


def prepare(frames, cb, mcfg, cfg_venue=None):
    p = panels(frames)
    a = {"index": p["close"].index, "symbols": list(p["close"].columns)}
    for k in ("open", "high", "low", "close", "quote_volume"):
        a[k] = p[k].to_numpy(dtype=float)
    a["funding"] = np.nan_to_num(p["funding"].to_numpy(dtype=float))
    a["listed"] = ~np.isnan(a["open"])
    a["r"], a["spot_r"] = perps.returns(a["open"], a["close"], np.full(a["open"].shape, np.nan))
    a["start_row"] = int(np.searchsorted(a["index"], pd.Timestamp(mcfg["data"]["start"], tz="UTC")))
    a["f"] = features(p, cb, mcfg)
    a["p"] = {k: v.to_numpy(dtype=float) for k, v in p.items()}  # numpy, so worker processes share it
    return a


def _frames_of(a):
    return {k: pd.DataFrame(v, index=a["index"], columns=a["symbols"]) for k, v in a["p"].items()}


# ---------- positions ----------

@njit(cache=True)
def _positions(allow_l, allow_s, sdir, trig_u, trig_d, close, atr, size, listed, start, stop_atr, time_stop, trend,
               cooldown):
    n = len(close)
    out = np.zeros(n)
    pos, w, stop, t0 = 0, 0.0, 0.0, 0
    blocked_side, blocked_until = 0, -1  # after a stop, no new trade on that side for `cooldown` bars
    for t in range(start, n):
        if not listed[t] or np.isnan(close[t]):
            pos = 0
            continue
        exited = False
        if pos != 0:
            c = close[t]
            hit = (pos > 0 and c <= stop) or (pos < 0 and c >= stop)
            if (hit or (pos > 0 and not allow_l[t]) or (pos < 0 and not allow_s[t])
                    or (not trend and (sdir[t] == -pos or t - t0 >= time_stop)) or (trend and sdir[t] != pos)):
                if hit:
                    blocked_side, blocked_until = pos, t + cooldown
                pos = 0
                exited = True
        if pos == 0 and not exited and not np.isnan(atr[t]) and size[t] > 0:
            if sdir[t] > 0 and allow_l[t] and trig_u[t] and not (blocked_side > 0 and t < blocked_until):
                pos, w, stop, t0 = 1, size[t], close[t] - stop_atr * atr[t], t
            elif sdir[t] < 0 and allow_s[t] and trig_d[t] and not (blocked_side < 0 and t < blocked_until):
                pos, w, stop, t0 = -1, size[t], close[t] + stop_atr * atr[t], t
        out[t] = pos * w
    return out


def layers(f, c):
    """Regime permission, setup direction and trigger arrays for config c (T x N each)."""
    shape = f["atr"].shape
    if c["regime"] == "none":
        rdir = np.zeros(shape)
        al, as_ = np.ones(shape, bool), np.ones(shape, bool)
    else:
        rdir = np.nan_to_num(f["w"] if c["regime"] in ("w", "w_cb") else f["m"])
        if c["regime"] == "w_cb":
            rdir = np.where(rdir == np.nan_to_num(f["cb"]), rdir, 0.0)
        al, as_ = rdir > 0, rdir < 0
    sdir = rdir if c["setup"] == "none" else np.nan_to_num(f[c["setup"]])
    if c["trigger"] == "none":
        tu = td = np.ones(shape, bool)
    else:
        tu, td = np.asarray(f[c["trigger"] + "_up"], bool), np.asarray(f[c["trigger"] + "_dn"], bool)
    return al, as_, sdir, tu, td


def weights(a, c, mcfg, f=None):
    f = a["f"] if f is None else f
    al, as_, sdir, tu, td = layers(f, c)
    r = mcfg["rules"]
    w = np.zeros(a["close"].shape)
    for j in range(w.shape[1]):
        w[:, j] = _positions(al[:, j], as_[:, j], sdir[:, j].astype(np.float64), tu[:, j], td[:, j], a["close"][:, j],
                             f["atr"][:, j], f["size"][:, j], a["listed"][:, j], a["start_row"], r["stop_atr"],
                             int(r["time_stop_days"] * 288), c["setup"] == "none", 288)
    return w


def run_config(c, a, mcfg, f=None, cost_mult=1.0, delay=0):
    w = weights(a, c, mcfg, f)
    bar, tr = perps.pnl(a, w, np.zeros_like(w), mcfg, cost_mult, delay)
    net, trades, days = perps.daily(a, bar, tr)
    keep = days >= pd.Timestamp(mcfg["data"]["start"], tz="UTC")
    return net[keep], trades[keep]


def days(a, mcfg):
    d = pd.DatetimeIndex(a["index"].floor("D").unique())
    return d[d >= pd.Timestamp(mcfg["data"]["start"], tz="UTC")]


def lean(a):
    """The arrays a worker needs (the pandas panels stay in the parent process)."""
    return {k: v for k, v in a.items() if k != "p"}


def evaluate(a, mcfg, confs, f=None, jobs=None):
    from functools import partial

    res = parallel.pmap(partial(run_config, a=lean(a), mcfg=mcfg, f=f), confs, jobs)
    return np.column_stack([r[0] for r in res]), np.column_stack([r[1] for r in res])


# ---------- null ----------

def shifted_features(a, cb, mcfg, seed):
    """All signal inputs of each coin circularly shifted by its own random offset (>= min_shift_days)
    inside its listed span, the Coinbase series by one more; returns and funding paid stay real."""
    rng = np.random.default_rng(seed)
    p = _frames_of(a)
    m = int(mcfg["search"]["min_shift_days"] * 288)
    for j, s in enumerate(a["symbols"]):
        live = np.where(a["listed"][:, j])[0]
        if len(live) < 3 * m:
            continue
        lo, hi = live[0], live[-1] + 1
        k = int(rng.integers(m, hi - lo - m))
        for name, df in p.items():
            col = df[s].to_numpy().copy()
            col[lo:hi] = np.roll(col[lo:hi], k)
            df[s] = col
    if cb is not None:
        cb = cb.copy()
        k = int(rng.integers(m // 12, max(m // 12 + 1, len(cb) - m // 12)))
        cb[:] = np.roll(cb.to_numpy(), k, axis=0)
    return features(p, cb, mcfg)
