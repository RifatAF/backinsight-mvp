import copy

import numpy as np
import pandas as pd
import pytest
import yaml

from tradelab import data, mtf

MCFG = yaml.safe_load((data.ROOT / "mtf.yaml").read_text(encoding="utf-8"))


def _frames(days=420, coins=("AAAUSDT", "BBBUSDT"), seed=0, start="2021-01-01"):
    """Test fixture only: random 5m perp frames, never a stand-in for market data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=288 * days, freq="5min", tz="UTC")
    out = {}
    for i, s in enumerate(coins):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
        op = np.concatenate([[100.0], close[:-1]])
        qv = rng.uniform(1e5, 1e6, len(idx))
        settled = (idx.hour % 8 == 7) & (idx.minute == 55)
        out[s] = pd.DataFrame({
            "open": op, "high": np.maximum(op, close) * 1.001, "low": np.minimum(op, close) * 0.999, "close": close,
            "quote_volume": qv, "taker_buy_quote": qv * rng.uniform(0.3, 0.7, len(idx)),
            "oi": 1e8 * np.exp(np.cumsum(rng.normal(0, 0.003, len(idx)))), "ls_global": rng.uniform(0.5, 3, len(idx)),
            "ls_top_acc": rng.uniform(0.5, 3, len(idx)), "taker_ratio": rng.uniform(0.7, 1.3, len(idx)),
            "funding": np.where(settled, rng.normal(1e-4, 3e-4, len(idx)), 0.0), "settled": settled,
            "funding_hours": 8.0}, index=idx)
    return out


def _cb(frames, seed=1):
    idx = pd.date_range(next(iter(frames.values())).index[0], periods=24 * 420, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"BTC": 100 * np.exp(np.cumsum(rng.normal(0, 0.004, len(idx)))), "ETH": 50.0}, index=idx)


def _mcfg():
    m = copy.deepcopy(MCFG)
    m["data"]["start"] = "2021-06-01"
    return m


def test_grid():
    confs = mtf.configs()
    assert len(confs) == len({mtf.config_id(c) for c in confs}) == 45
    for c, nb in zip(confs, mtf.neighbors(confs)):
        assert nb and all(sum(c[k] != confs[j][k] for k in c) == 1 for j in nb)


def test_align_uses_closed_bars_only():
    idx = pd.date_range("2022-01-03", periods=288 * 15, freq="5min", tz="UTC")  # starts on a Monday
    x = pd.DataFrame({"A": np.arange(len(idx), dtype=float)}, index=idx)
    wk = mtf.higher(x, "W", "last")
    al = mtf.align(wk, "W", idx)[:, 0]
    first_close = idx.get_loc(pd.Timestamp("2022-01-09 23:55", tz="UTC"))
    assert np.isnan(al[: first_close]).all() and al[first_close] == wk["A"].iloc[0]
    h4 = mtf.higher(x, "4h", "last")
    a4 = mtf.align(h4, "4h", idx)[:, 0]
    assert np.isnan(a4[46]) and a4[47] == h4["A"].iloc[0] and a4[47 + 48] == h4["A"].iloc[1]


def test_features_causal():
    """Every layer's value up to bar t is unchanged when all data after t is replaced."""
    fr = _frames()
    m = _mcfg()
    cut = pd.Timestamp("2021-12-15 10:00", tz="UTC")
    other = _frames(seed=5)
    alt = {}
    for s, f in fr.items():
        g = f.copy()
        g.loc[g.index > cut] = other[s].loc[other[s].index > cut].to_numpy()
        alt[s] = g
    cb = _cb(fr)
    cb2 = cb.copy()
    cb2.loc[cb2.index > cut - pd.Timedelta(hours=1)] = 77.0
    a1, a2 = mtf.prepare(fr, cb, m), mtf.prepare(alt, cb2, m)
    t = a1["index"].get_loc(cut)
    for k, v in a1["f"].items():
        x, y = np.asarray(v)[: t + 1], np.asarray(a2["f"][k])[: t + 1]
        assert np.array_equal(np.nan_to_num(x.astype(float), nan=-9), np.nan_to_num(y.astype(float), nan=-9)), k
    for c in mtf.configs()[::7]:
        assert np.array_equal(mtf.weights(a1, c, m)[: t + 1], mtf.weights(a2, c, m)[: t + 1])


def _kernel(sdir, close, allow_l=None, allow_s=None, trig=None, time_stop=1000, trend=False, cooldown=5):
    n = len(close)
    t = np.ones(n, bool) if trig is None else trig
    return mtf._positions(np.ones(n, bool) if allow_l is None else allow_l, np.ones(n, bool) if allow_s is None else allow_s,
                          np.asarray(sdir, float), t, t, np.asarray(close, float), np.ones(n), np.full(n, 0.5),
                          np.ones(n, bool), 0, 2.0, time_stop, trend, cooldown)


def test_kernel_stop_and_cooldown():
    close = [100, 100, 99, 97.9, 98, 98, 98, 98, 98, 98, 98, 98]
    pos = _kernel([1] * 12, close)
    assert pos[0] == 0.5 and pos[2] == 0.5 and pos[3] == 0  # 97.9 <= 100 - 2 x ATR(1): stopped
    assert (pos[3:8] == 0).all() and pos[8] == 0.5  # blocked for 5 bars on the long side


def test_kernel_regime_exit_time_stop_and_opposite_setup():
    n = 10
    allow = np.ones(n, bool)
    allow[4] = False
    pos = _kernel([1] * n, [100] * n, allow_l=allow)
    assert pos[3] == 0.5 and pos[4] == 0 and pos[5] == 0.5  # permission withdrawn, then back
    pos = _kernel([1] * n, [100] * n, time_stop=3)
    assert list(pos[:5]) == [0.5, 0.5, 0.5, 0, 0.5]
    pos = _kernel([-1, -1, 1, 1], [100] * 4)
    assert list(pos) == [-0.5, -0.5, 0, 0.5]  # opposite setup closes; a new trade opens a bar later


def test_kernel_trend_follows_setup():
    pos = _kernel([1, 1, 0, -1, -1], [100] * 5, trend=True)
    assert list(pos) == [0.5, 0.5, 0, -0.5, -0.5]


def test_liquidation_map():
    n = 6
    close = np.array([100.0, 100, 100, 95, 95, 95])
    high, low = close * 1.001, close * 0.999
    oi = np.array([1e6, 2e6, 2e6, 2e6, 2e6, 2e6])
    buy = np.ones(n)  # new positions are all longs: liquidations below
    r = mtf._liq_map(close, high, low, oi, buy, np.array([10.0, 25, 50]), np.array([0.5, 0.3, 0.2]), 0.05, 0.0025)
    assert r[1] < 1  # clusters at about 96.5 and 98.5 below, nothing above
    assert np.isnan(r[3]) or r[3] >= r[1]  # the drop to 95 liquidated the 96.5 / 98.5 longs


def test_shifted_features_detach_signals():
    fr = _frames(days=300)
    m = _mcfg()
    m["search"]["min_shift_days"] = 20
    a = mtf.prepare(fr, _cb(fr), m)
    f = mtf.shifted_features(a, _cb(fr), m, 3)
    assert set(f) == set(a["f"]) and f["atr"].shape == a["f"]["atr"].shape
    assert not np.array_equal(np.nan_to_num(f["w"]), np.nan_to_num(a["f"]["w"]))
