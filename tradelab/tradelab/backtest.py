"""Single execution model (CLAUDE.md section 5). Works on one signal (T,) or many (T, N)."""
import numpy as np
import pandas as pd

from tradelab import costs as costs_mod


def apply_band(target, band):
    """Position moves to target only if |target - current| > band. NaN target means 0."""
    target = np.nan_to_num(np.asarray(target, dtype=float), nan=0.0)
    out = np.empty_like(target)
    cur = np.zeros(target.shape[1:])
    for i in range(len(target)):
        cur = np.where(np.abs(target[i] - cur) > band, target[i], cur)
        out[i] = cur
    return out


def positions(signal, cfg):
    """pos[t+1] = band(s)[t]; pos[0] = 0."""
    banded = apply_band(signal, cfg["costs"]["rebalance_band"])
    pos = np.concatenate([np.zeros((1,) + banded.shape[1:]), banded[:-1]])
    assert pos.max() <= cfg["risk"]["hard_leverage_cap"], "position exceeds hard_leverage_cap"
    return pos


def pnl(bars, pos, cfg, funding_day):
    """Returns full-length arrays; the last row has no r_oo (NaN)."""
    c = cfg["costs"]
    o = bars["open"].to_numpy(dtype=float)
    r_oo = np.append(o[1:] / o[:-1] - 1.0, np.nan)
    qv = bars["quote_volume"].to_numpy(dtype=float)
    fday = np.asarray(funding_day, dtype=float)
    if pos.ndim == 2:
        r_oo, qv, fday = r_oo[:, None], qv[:, None], fday[:, None]
    turnover = np.abs(np.diff(pos, axis=0, prepend=np.zeros((1,) + pos.shape[1:])))
    gross = pos * r_oo
    cost = costs_mod.trade_cost(turnover, qv, c)
    fund = costs_mod.funding_cost(pos, fday)
    return {"pos": pos, "r_oo": np.broadcast_to(r_oo, pos.shape), "turnover": turnover,
            "gross": gross, "cost": cost, "fund": fund, "net": gross - cost - fund}


def run(bars, signal, cfg, funding=None):
    """bars: DataFrame with open, quote_volume on a daily UTC index.
    signal: s[t] known at close of bar t. Executed at open of t+1.
    Returns a frame indexed by bar k for k with a defined r_oo (last bar dropped).
    """
    s = pd.Series(signal, index=bars.index).astype(float).to_numpy()
    fday = costs_mod.funding_daily(bars.index, cfg["costs"], funding).to_numpy()
    out = pd.DataFrame(pnl(bars, positions(s, cfg), cfg, fday), index=bars.index)
    return out.iloc[:-1]


def n_trades(result):
    return int((result["turnover"] > 0).sum())
