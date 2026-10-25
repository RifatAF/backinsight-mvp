"""Single execution model (CLAUDE.md section 5)."""
import numpy as np
import pandas as pd

from tradelab import costs as costs_mod


def apply_band(target, band):
    """Position moves to target only if |target - current| > band. NaN target means 0."""
    target = np.nan_to_num(np.asarray(target, dtype=float), nan=0.0)
    out = np.empty_like(target)
    cur = 0.0
    for i, t in enumerate(target):
        if abs(t - cur) > band:
            cur = t
        out[i] = cur
    return out


def run(bars, signal, cfg, funding=None):
    """bars: DataFrame with open, quote_volume on a daily UTC index.
    signal: s[t] known at close of bar t. Executed at open of t+1.
    Returns a frame indexed by bar k for k with a defined r_oo (last bar dropped).
    """
    c = cfg["costs"]
    s = pd.Series(signal, index=bars.index).astype(float)
    banded = apply_band(s.to_numpy(), c["rebalance_band"])
    pos = np.concatenate([[0.0], banded[:-1]])  # pos[t+1] = band(s)[t]
    assert pos.max() <= cfg["risk"]["hard_leverage_cap"], "position exceeds hard_leverage_cap"

    o = bars["open"].to_numpy(dtype=float)
    r_oo = np.append(o[1:] / o[:-1] - 1.0, np.nan)
    turnover = np.abs(np.diff(pos, prepend=0.0))
    gross = pos * r_oo
    cost = costs_mod.trade_cost(turnover, bars["quote_volume"].to_numpy(dtype=float), c)
    fday = costs_mod.funding_daily(bars.index, c, funding).to_numpy()
    fund = costs_mod.funding_cost(pos, fday)
    net = gross - cost - fund

    out = pd.DataFrame(
        {"pos": pos, "r_oo": r_oo, "turnover": turnover, "gross": gross,
         "cost": cost, "fund": fund, "net": net},
        index=bars.index,
    )
    return out.iloc[:-1]


def n_trades(result):
    return int((result["turnover"] > 0).sum())
