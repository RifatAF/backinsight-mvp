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
