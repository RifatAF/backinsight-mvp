"""Long-only signal families for the strategy generator. Every signal uses data up to close of bar t.

Each family is a function (bars, **params) -> Series in [0, 1]; GRIDS lists the allowed parameter
values in order, so +-1 step on one parameter defines plateau neighbours.
"""
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def ema(x, n):
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(x, n):
    return x.rolling(n).mean()


def wilder(x, n):
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def latch(enter, exit_):
    """1 after an entry bar until an exit bar, else 0 (entry wins on a tie)."""
    s = pd.Series(np.nan, index=enter.index)
    s[exit_.fillna(False)] = 0.0
    s[enter.fillna(False)] = 1.0
    return s.ffill().fillna(0.0)


def rsi(close, n):
    d = close.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder(-d.clip(upper=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(bars):
    pc = bars["close"].shift(1)
    return pd.concat([bars["high"] - bars["low"], (bars["high"] - pc).abs(), (bars["low"] - pc).abs()], axis=1).max(axis=1)


# ---------- families ----------

def macd(bars, fast, slow, signal):
    c = bars["close"]
    m = ema(c, fast) - ema(c, slow)
    return (m > ema(m, signal)).astype(float)


def macd_zero(bars, fast, slow, signal):
    c = bars["close"]
    m = ema(c, fast) - ema(c, slow)
    return ((m > ema(m, signal)) & (m > 0)).astype(float)


def rsi_reversion(bars, n, low, exit):
    r = rsi(bars["close"], n)
    return latch(r < low, r > exit)


def rsi_momentum(bars, n, level):
    return (rsi(bars["close"], n) > level).astype(float)


def bb_reversion(bars, n, k):
    c = bars["close"]
    m, sd = sma(c, n), c.rolling(n).std()
    return latch(c < m - k * sd, c > m)


def bb_breakout(bars, n, k):
    c = bars["close"]
    m, sd = sma(c, n), c.rolling(n).std()
    return latch(c > m + k * sd, c < m)


def stoch_reversion(bars, n, low, high):
    ll, hh = bars["low"].rolling(n).min(), bars["high"].rolling(n).max()
    k = 100 * (bars["close"] - ll) / (hh - ll).replace(0, np.nan)
    return latch(k < low, k > high)


def ema_cross(bars, fast, slow):
    c = bars["close"]
    return (ema(c, fast) > ema(c, slow)).astype(float)


def donchian(bars, n):
    c = bars["close"]
    return latch(c > bars["high"].shift(1).rolling(n).max(), c < bars["low"].shift(1).rolling(n // 2).min())


def roc(bars, n):
    c = bars["close"]
    return (c / c.shift(n) - 1 > 0).astype(float)


def cci(bars, n, level):
    tp = ((bars["high"] + bars["low"] + bars["close"]) / 3).to_numpy()
    out = np.full(len(tp), np.nan)
    if len(tp) >= n:
        w = sliding_window_view(tp, n)
        m = w.mean(1)
        mad = np.abs(w - m[:, None]).mean(1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[n - 1:] = (tp[n - 1:] - m) / (0.015 * mad)
    return pd.Series(out > level, index=bars.index).astype(float)


def adx_trend(bars, n, level):
    h, l = bars["high"], bars["low"]
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    atr = wilder(true_range(bars), n)
    pdi, ndi = 100 * wilder(pdm, n) / atr, 100 * wilder(ndm, n) / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return ((wilder(dx, n) > level) & (pdi > ndi)).astype(float)


def supertrend(bars, n, mult):
    h, l, c = (bars[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    atr = wilder(true_range(bars), n).to_numpy()
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    out = np.zeros(len(c))
    fu, fl, trend = np.nan, np.nan, 0.0
    for t in range(len(c)):
        if np.isnan(atr[t]):
            continue
        fu = ub[t] if np.isnan(fu) or ub[t] < fu or c[t - 1] > fu else fu
        fl = lb[t] if np.isnan(fl) or lb[t] > fl or c[t - 1] < fl else fl
        if c[t] > fu:
            trend = 1.0
        elif c[t] < fl:
            trend = 0.0
        out[t] = trend
    return pd.Series(out, index=bars.index)


FAMILIES = {
    "macd": (macd, {"fast": [8, 12, 16], "slow": [21, 26, 35], "signal": [5, 9, 12]}),
    "macd_zero": (macd_zero, {"fast": [8, 12, 16], "slow": [21, 26, 35], "signal": [5, 9, 12]}),
    "rsi_reversion": (rsi_reversion, {"n": [7, 14, 21], "low": [20, 25, 30, 35], "exit": [50, 55, 60, 65, 70]}),
    "rsi_momentum": (rsi_momentum, {"n": [7, 14, 21, 30], "level": [50, 55, 60]}),
    "bb_reversion": (bb_reversion, {"n": [10, 20, 30, 50], "k": [1.5, 2.0, 2.5]}),
    "bb_breakout": (bb_breakout, {"n": [10, 20, 30, 50], "k": [1.0, 1.5, 2.0]}),
    "stoch_reversion": (stoch_reversion, {"n": [9, 14, 21], "low": [10, 20, 30], "high": [70, 80, 90]}),
    "ema_cross": (ema_cross, {"fast": [5, 10, 20, 30], "slow": [50, 100, 150, 200]}),
    "donchian": (donchian, {"n": [10, 20, 40, 80, 160]}),
    "roc": (roc, {"n": [10, 20, 40, 60, 90, 120, 180, 250]}),
    "cci": (cci, {"n": [14, 20, 40], "level": [0, 50, 100]}),
    "adx_trend": (adx_trend, {"n": [10, 14, 20], "level": [15, 20, 25, 30]}),
    "supertrend": (supertrend, {"n": [7, 10, 14, 20], "mult": [2.0, 2.5, 3.0, 4.0]}),
}


def valid(family, params):
    if "fast" in params and "slow" in params:
        return params["fast"] < params["slow"]
    if family == "rsi_reversion":
        return params["low"] < params["exit"]
    return True


def signal(bars, family, params):
    return FAMILIES[family][0](bars, **params)


# ---------- stage 3: shorts and derivatives / sentiment families ----------

def invert(bars):
    """Price series 1/p: an uptrend in the inverted bars is a downtrend in the original, so any
    long rule applied to invert(bars) is the mirrored short rule."""
    out = bars.copy()
    out["open"], out["close"] = 1 / bars["open"], 1 / bars["close"]
    out["high"], out["low"] = 1 / bars["low"], 1 / bars["high"]
    return out


def _pct(x, feats, min_days=180):
    return x.expanding(min_days * feats.attrs.get("bpd", 1)).rank(pct=True)


def _days(n, feats):
    return int(n * feats.attrs.get("bpd", 1))


def _pair(long, short):
    return long.fillna(False).astype(float), short.fillna(False).astype(float)


def funding_contra(bars, feats, n, q):
    p = _pct(feats["funding_day"].rolling(_days(n, feats)).mean(), feats)
    return _pair(p < q, p > 1 - q)


def oi_trend(bars, feats, n):
    k = _days(n, feats)
    pr = bars["close"] / bars["close"].shift(k) - 1
    oi = feats["oi_value"] / feats["oi_value"].shift(k) - 1
    return _pair((pr > 0) & (oi > 0), (pr < 0) & (oi > 0))


def ls_contra(bars, feats, kind, q):
    p = _pct(feats[kind], feats)
    return _pair(p < q, p > 1 - q)


def taker_momentum(bars, feats, n, level):
    t = feats["taker_ratio"].rolling(_days(n, feats)).mean()
    return _pair(t > level, t < 1 / level)


def cb_premium(bars, feats, n, q):
    p = _pct(feats["cb_premium"].rolling(_days(n, feats)).mean(), feats)
    return _pair(p > 1 - q, p < q)


def basis_contra(bars, feats, n, q):
    p = _pct(feats["basis"].rolling(_days(n, feats)).mean(), feats)
    return _pair(p < q, p > 1 - q)


def fng_contra(bars, feats, low, high):
    f = feats["fng"]
    return latch(f < low, f > high), latch(f > 100 - low, f < 100 - high)


def oi_flush(bars, feats, drop, ret, hold):
    """Liquidation proxy: a one-day open-interest drop with a large price move. A flush down
    (longs liquidated) opens a long for `hold` days, a flush up (shorts liquidated) a short."""
    k = _days(1, feats)
    d_oi = feats["oi_value"] / feats["oi_value"].shift(k) - 1
    d_p = bars["close"] / bars["close"].shift(k) - 1
    h = _days(hold, feats)
    down = ((d_oi < -drop) & (d_p < -ret)).astype(float).rolling(h, min_periods=1).max()
    up = ((d_oi < -drop) & (d_p > ret)).astype(float).rolling(h, min_periods=1).max()
    return _pair(down > 0, up > 0)


FEATURE_FAMILIES = {
    "funding_contra": (funding_contra, {"n": [1, 3, 7, 14], "q": [0.1, 0.2, 0.3]}),
    "oi_trend": (oi_trend, {"n": [3, 7, 14, 30]}),
    "ls_contra": (ls_contra, {"kind": ["global_ls", "top_ls_acc", "top_ls_pos"], "q": [0.1, 0.2, 0.3]}),
    "taker_momentum": (taker_momentum, {"n": [1, 3, 7, 14], "level": [1.0, 1.02, 1.05]}),
    "cb_premium": (cb_premium, {"n": [1, 3, 7, 14], "q": [0.2, 0.3, 0.5]}),
    "basis_contra": (basis_contra, {"n": [1, 3, 7, 14], "q": [0.1, 0.2, 0.3]}),
    "fng_contra": (fng_contra, {"low": [15, 20, 25, 30], "high": [50, 60, 70]}),
    "oi_flush": (oi_flush, {"drop": [0.05, 0.08, 0.12], "ret": [0.03, 0.05, 0.08], "hold": [3, 7, 14]}),
}


# ---------- stage 4: macro, calendar and pure-statistics families ----------
# A family and its mirror ("_inv", "_down") are separate families, so plateau neighbours never
# flip the sign of a rule.

MACRO_SERIES = ["spx", "ndx", "nikkei", "gold", "dxy", "usdjpy", "us3m", "us10y", "vix", "move"]


def _trend(feats, series, n):
    x = feats[series]
    return x - x.shift(_days(n, feats))


def macro_trend(bars, feats, series, n):
    """Crypto follows the macro series: long while it rose over n days, short while it fell."""
    d = _trend(feats, series, n)
    return _pair(d > 0, d < 0)


def macro_trend_inv(bars, feats, series, n):
    d = _trend(feats, series, n)
    return _pair(d < 0, d > 0)


def _regime(feats, series, q):
    p = _pct(feats[series], feats)
    return p > 1 - q, p < q


def macro_regime(bars, feats, series, q):
    """Long while the series is in its top q of history so far, short in its bottom q."""
    hi, lo = _regime(feats, series, q)
    return _pair(hi, lo)


def macro_regime_inv(bars, feats, series, q):
    hi, lo = _regime(feats, series, q)
    return _pair(lo, hi)


def _shock(feats, series, z, hold):
    """One-day move of the series beyond z rolling (60-day) standard deviations, held `hold` days."""
    k = _days(1, feats)
    d = feats[series] - feats[series].shift(k)
    sd = d.rolling(_days(60, feats)).std()
    h = _days(hold, feats)
    up = (d > z * sd).astype(float).rolling(h, min_periods=1).max() > 0
    dn = (d < -z * sd).astype(float).rolling(h, min_periods=1).max() > 0
    return up, dn


def macro_shock(bars, feats, series, z, hold):
    """React with the shock: long after a jump up of the series, short after a drop."""
    up, dn = _shock(feats, series, z, hold)
    return _pair(up & ~dn, dn & ~up)


def macro_shock_inv(bars, feats, series, z, hold):
    up, dn = _shock(feats, series, z, hold)
    return _pair(dn & ~up, up & ~dn)


def _next_bar(bars):
    """Calendar rules decide at the close of t about the bar held next (executed at open of t+1)."""
    step = bars.index[1] - bars.index[0] if len(bars) > 1 else pd.Timedelta(days=1)
    return bars.index + step


def _cal(bars, flag):
    return pd.Series(flag, index=bars.index)


def weekday_up(bars, feats, day):
    return _pair(_cal(bars, _next_bar(bars).dayofweek == day), _cal(bars, False))


def weekday_down(bars, feats, day):
    return _pair(_cal(bars, False), _cal(bars, _next_bar(bars).dayofweek == day))


def month_up(bars, feats, month):
    return _pair(_cal(bars, _next_bar(bars).month == month), _cal(bars, False))


def month_down(bars, feats, month):
    return _pair(_cal(bars, False), _cal(bars, _next_bar(bars).month == month))


def hour_up(bars, feats, hour):
    return _pair(_cal(bars, _next_bar(bars).hour == hour), _cal(bars, False))


def hour_down(bars, feats, hour):
    return _pair(_cal(bars, False), _cal(bars, _next_bar(bars).hour == hour))


def _logret(bars):
    return np.log(bars["close"]).diff()


def _zret(bars, feats, n):
    r = _logret(bars)
    k = _days(n, feats)
    return r.rolling(k).sum() / (r.rolling(_days(60, feats)).std() * np.sqrt(k))


def ret_reversion(bars, feats, n, z):
    """Fade an n-day move larger than z volatility-scaled standard deviations."""
    x = _zret(bars, feats, n)
    return _pair(x < -z, x > z)


def ret_momentum(bars, feats, n, z):
    x = _zret(bars, feats, n)
    return _pair(x > z, x < -z)


def autocorr(bars, feats, w, n):
    """Follow the last n-day move while returns were positively autocorrelated over w days, fade it otherwise."""
    r = _logret(bars)
    ac = r.rolling(_days(w, feats)).corr(r.shift(1))
    m = r.rolling(_days(n, feats)).sum()
    sign = np.sign(ac) * np.sign(m)
    return _pair(sign > 0, sign < 0)


def _vol_pct(bars, feats, n):
    v = _logret(bars).rolling(_days(n, feats)).std()
    return _pct(v, feats)


def vol_regime(bars, feats, n, q):
    """Long in calm markets (realized vol in its bottom q so far), short in turbulent ones."""
    p = _vol_pct(bars, feats, n)
    return _pair(p < q, p > 1 - q)


def vol_regime_inv(bars, feats, n, q):
    p = _vol_pct(bars, feats, n)
    return _pair(p > 1 - q, p < q)


def btc_lead(bars, feats, n):
    """Follow bitcoin's n-day move (daily BTC close)."""
    d = _trend(feats, "btc_close", n)
    return _pair(d > 0, d < 0)


def hold(bars, feats):
    """Always long: a base for strategies driven only by a higher-timeframe filter."""
    return _pair(_cal(bars, True), _cal(bars, False))


_SERIES = {"series": MACRO_SERIES}
EXTRA_FAMILIES = {
    "hold": (hold, {}),
    "macro_trend": (macro_trend, {**_SERIES, "n": [5, 10, 20, 60]}),
    "macro_trend_inv": (macro_trend_inv, {**_SERIES, "n": [5, 10, 20, 60]}),
    "macro_regime": (macro_regime, {**_SERIES, "q": [0.2, 0.3, 0.5]}),
    "macro_regime_inv": (macro_regime_inv, {**_SERIES, "q": [0.2, 0.3, 0.5]}),
    "macro_shock": (macro_shock, {**_SERIES, "z": [1.5, 2.0, 2.5], "hold": [1, 3, 5]}),
    "macro_shock_inv": (macro_shock_inv, {**_SERIES, "z": [1.5, 2.0, 2.5], "hold": [1, 3, 5]}),
    "weekday_up": (weekday_up, {"day": list(range(7))}),
    "weekday_down": (weekday_down, {"day": list(range(7))}),
    "month_up": (month_up, {"month": list(range(1, 13))}),
    "month_down": (month_down, {"month": list(range(1, 13))}),
    "hour_up": (hour_up, {"hour": list(range(24))}),
    "hour_down": (hour_down, {"hour": list(range(24))}),
    "ret_reversion": (ret_reversion, {"n": [1, 3, 5, 10], "z": [1.0, 1.5, 2.0]}),
    "ret_momentum": (ret_momentum, {"n": [1, 3, 5, 10], "z": [1.0, 1.5, 2.0]}),
    "autocorr": (autocorr, {"w": [30, 60, 120], "n": [1, 3, 5]}),
    "vol_regime": (vol_regime, {"n": [10, 20, 60], "q": [0.2, 0.3, 0.5]}),
    "vol_regime_inv": (vol_regime_inv, {"n": [10, 20, 60], "q": [0.2, 0.3, 0.5]}),
    "btc_lead": (btc_lead, {"n": [1, 3, 7, 14]}),
}
ALL_FAMILIES = {**FAMILIES, **FEATURE_FAMILIES, **EXTRA_FAMILIES}


def grid(family):
    return ALL_FAMILIES[family][1]


def long_short(bars, feats, family, params, inverted=None):
    """(long, short) signals in {0, 1}; price families get their short side from inverted bars."""
    if family in FEATURE_FAMILIES:
        return FEATURE_FAMILIES[family][0](bars, feats, **params)
    if family in EXTRA_FAMILIES:
        return EXTRA_FAMILIES[family][0](bars, feats, **params)
    inv = invert(bars) if inverted is None else inverted
    return FAMILIES[family][0](bars, **params), FAMILIES[family][0](inv, **params)
