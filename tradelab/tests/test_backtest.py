import numpy as np
import pandas as pd
import pytest
from conftest import random_bars

from tradelab import backtest, strategies

SIGNALS = [strategies.bh, strategies.vt]


@pytest.mark.parametrize("fn", SIGNALS)
def test_no_lookahead(fn, cfg, bars):
    base = fn(bars, cfg)
    res = backtest.run(bars, base, cfg)
    for t in (400, 800, 1200):
        alt = bars.copy()
        noise = random_bars(len(bars) - t - 1, seed=t)
        alt.iloc[t + 1:] = noise.to_numpy()
        sig = fn(alt, cfg)
        pd.testing.assert_series_equal(base.iloc[: t + 1], sig.iloc[: t + 1])
        # pos up to t+1 is decided by data through t
        res_alt = backtest.run(alt, sig, cfg)
        np.testing.assert_array_equal(res["pos"].iloc[: t + 2], res_alt["pos"].iloc[: t + 2])


def test_costs_exact(cfg):
    n = 200
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    bars = pd.DataFrame({"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0,
                         "quote_volume": 1e9}, index=idx)
    cfg["costs"]["rebalance_band"] = 0.0
    cfg["costs"]["slippage_size_coef"] = 0.0
    sig = pd.Series(np.arange(n) % 2, index=idx, dtype=float)  # 0,1,0,1,...
    res = backtest.run(bars, sig, cfg)
    trades = backtest.n_trades(res)
    assert trades == n - 3  # pos[0..1] = 0, flips on bars 2..n-2 (last bar has no r_oo)
    expected = -trades * (cfg["costs"]["fee_per_side"] + cfg["costs"]["slippage_base"])
    assert abs(res["net"].sum() - expected) < 1e-12


def test_leverage_cap(cfg, bars):
    with pytest.raises(AssertionError):
        backtest.run(bars, pd.Series(5.0, index=bars.index), cfg)


def test_band_holds_position():
    pos = backtest.apply_band([1.0, 1.05, 0.95, 1.2, 0.0], 0.10)
    np.testing.assert_array_equal(pos, [1.0, 1.0, 1.0, 1.2, 0.0])


def test_funding_only_above_one(cfg, bars):
    r1 = backtest.run(bars, pd.Series(1.0, index=bars.index), cfg)
    r2 = backtest.run(bars, pd.Series(2.0, index=bars.index), cfg)
    assert (r1["fund"] == 0).all()
    np.testing.assert_allclose(r2["fund"].iloc[1:], 3 * cfg["costs"]["funding_fallback_per_8h"])


def test_seed_bitwise(cfg, bars):
    a = backtest.run(bars, strategies.vt(bars, cfg), cfg)
    b = backtest.run(bars, strategies.vt(bars, cfg), cfg)
    assert a.to_numpy().tobytes() == b.to_numpy().tobytes()


def test_short_pays_negative_funding_and_cap(cfg, bars):
    r = backtest.run(bars, pd.Series(-1.0, index=bars.index), cfg)
    np.testing.assert_allclose(r["fund"].iloc[1:], -3 * cfg["costs"]["funding_fallback_per_8h"])
    up = bars.copy()
    up["open"] = np.linspace(100, 200, len(bars))
    assert backtest.run(up, pd.Series(-1.0, index=bars.index), cfg)["gross"].iloc[1:].lt(0).all()
    with pytest.raises(AssertionError):
        backtest.run(bars, pd.Series(-5.0, index=bars.index), cfg)
