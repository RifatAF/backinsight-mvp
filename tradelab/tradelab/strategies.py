"""Signals use data up to close of bar t only (CLAUDE.md sections 7, 8)."""
import numpy as np
import pandas as pd

DON_WINDOWS = (10, 20, 40, 80, 160)
DON = {"don_no160": 160, "don_all": None, "don_no10": 10}  # fast -> slow
TSMOM = {"tsmom_20": 20, "tsmom_60": 60, "tsmom_120": 120, "tsmom_250": 250}
MA = {"ma_10_50": (10, 50), "ma_20_100": (20, 100), "ma_50_200": (50, 200)}
FAMILIES = {"don": list(DON), "tsmom": list(TSMOM), "ma": list(MA)}
BASES = [b for fam in FAMILIES.values() for b in fam]
PCT_MIN_DAYS = 365


def sigma(close, span=30, ann=365):
    """Annualized EWMA std of daily log returns of close."""
    lr = np.log(close).diff()
    return lr.ewm(span=span, min_periods=span).std() * np.sqrt(ann)


def bh(bars, cfg):
    return pd.Series(1.0, index=bars.index)


def vt(bars, cfg):
    b = cfg["benchmarks"]
    sig = sigma(bars["close"], b["vol_ewma_span"], cfg["annualization"])
    return np.minimum(cfg["risk"]["max_leverage"], b["voltarget_annual"] / sig).fillna(0.0)


def donchian(bars, windows):
    h, l, c = bars["high"], bars["low"], bars["close"]
    states = []
    for n in windows:
        st = pd.Series(np.nan, index=bars.index)
        st[c > h.shift(1).rolling(n).max()] = 1.0
        st[c < l.shift(1).rolling(n // 2).min()] = 0.0
        states.append(st.ffill().fillna(0.0))
    return sum(states) / len(states)


def tsmom(close, lag):
    return (close / close.shift(lag) - 1 > 0).astype(float)


def ma_cross(close, fast, slow):
    return (close.rolling(fast).mean() > close.rolling(slow).mean()).astype(float)


def base_signals(bars):
    c = bars["close"]
    out = {}
    for k, drop in DON.items():
        out[k] = donchian(bars, [w for w in DON_WINDOWS if w != drop])
    for k, lag in TSMOM.items():
        out[k] = tsmom(c, lag)
    for k, (f, s) in MA.items():
        out[k] = ma_cross(c, f, s)
    return out


def expanding_pct(x, min_days=PCT_MIN_DAYS):
    """Percentile of x[t] among x[0..t]; NaN until min_days observations."""
    return x.expanding(min_days).rank(pct=True)


def layer_inputs(bars, cfg, funding_day, bpd=1):
    """bpd = bars per day: windows defined in days scale with it (bpd = 4 for 6h bars)."""
    sig = sigma(bars["close"], cfg["benchmarks"]["vol_ewma_span"] * bpd, cfg["annualization"] * bpd)
    fsum = pd.Series(np.asarray(funding_day, dtype=float), index=bars.index).rolling(7 * bpd).sum()
    return {"sigma": sig, "sigma_pct": expanding_pct(sig, PCT_MIN_DAYS * bpd),
            "funding_pct": expanding_pct(fsum, PCT_MIN_DAYS * bpd)}


def compose(b, conf, inputs, cfg):
    """s = b * vol scale, zeroed by the regime and funding filters (NaN percentile = filter off)."""
    s = b.copy()
    if conf["v"] is not None:
        s = b * np.minimum(cfg["risk"]["max_leverage"], conf["v"] / inputs["sigma"]).fillna(0.0)
    if conf["p"] is not None:
        s = s.where(~(inputs["sigma_pct"] > conf["p"]), 0.0)
    if conf["q"] is not None:
        s = s.where(~(inputs["funding_pct"] > conf["q"]), 0.0)
    return s


def config_matrix(bars, cfg, funding_day, configs):
    """T x N target positions for the given grid configs."""
    base = base_signals(bars)
    inputs = layer_inputs(bars, cfg, funding_day)
    return np.column_stack([compose(base[c["base"]], c, inputs, cfg).to_numpy() for c in configs])
