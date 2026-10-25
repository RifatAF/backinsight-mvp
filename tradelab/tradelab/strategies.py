"""Signals use data up to close of bar t only. Milestone 1: benchmarks bh and vt."""
import numpy as np
import pandas as pd


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
