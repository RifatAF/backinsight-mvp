import numpy as np
import pandas as pd


def trade_cost(turnover, quote_volume, costs):
    """cost[k] = turnover * (fee + slip_base + coef * turnover * notional / quote_volume)."""
    turnover = np.asarray(turnover, dtype=float)
    qv = np.asarray(quote_volume, dtype=float)
    impact = np.zeros_like(turnover)
    if costs["slippage_size_coef"] != 0:
        with np.errstate(divide="ignore", invalid="ignore"):
            impact = costs["slippage_size_coef"] * turnover * costs["notional_usd"] / qv
        impact = np.where(turnover > 0, impact, 0.0)
    return turnover * (costs["fee_per_side"] + costs["slippage_base"] + impact)


def funding_daily(index, costs, funding=None):
    """Daily sum of funding rates aligned to `index` (UTC days).

    `funding` is a Series of per-event rates indexed by UTC timestamp. Days before
    the first funding event, and days with no events inside the history, get
    3 * funding_fallback_per_8h.
    """
    fallback = 3 * costs["funding_fallback_per_8h"]
    if funding is None or len(funding) == 0:
        return pd.Series(fallback, index=index, dtype=float)
    daily = funding.groupby(funding.index.floor("D")).sum()
    return daily.reindex(index).fillna(fallback).astype(float)


def funding_cost(pos, funding_day):
    """fund[k] = max(pos[k] - 1, 0) * funding_day[k]; positive rate means long pays."""
    return np.maximum(np.asarray(pos, dtype=float) - 1.0, 0.0) * np.asarray(funding_day, dtype=float)
