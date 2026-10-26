import numpy as np
import pandas as pd
import pytest
from conftest import random_bars

from tradelab import backtest, grid, strategies, synth, walkforward


def _fday(bars, seed=1):
    return np.random.default_rng(seed).normal(1e-4, 3e-4, len(bars))


def test_no_lookahead_all_configs(cfg, bars):
    confs = grid.configs()
    fday = _fday(bars)
    base = strategies.config_matrix(bars, cfg, fday, confs)
    for t in (500, 1100):
        alt = bars.copy()
        alt.iloc[t + 1:] = random_bars(len(bars) - t - 1, seed=t).to_numpy()
        fd = fday.copy()
        fd[t + 1:] = _fday(bars, seed=t)[t + 1:]
        np.testing.assert_array_equal(base[: t + 1], strategies.config_matrix(alt, cfg, fd, confs)[: t + 1])


def test_grid_and_neighbors():
    confs = grid.configs()
    ids = [grid.config_id(c) for c in confs]
    assert len(confs) == 300 and len(set(ids)) == 300
    nbrs = grid.neighbors(confs)
    fam = {b: f for f, bs in strategies.FAMILIES.items() for b in bs}
    for i, nb in enumerate(nbrs):
        assert len(nb) >= 1 and i not in nb
        for j in nb:
            assert i in nbrs[j]
            a, b = confs[i], confs[j]
            assert a["q"] == b["q"] and fam[a["base"]] == fam[b["base"]]
            assert sum(a[k] != b[k] for k in ("base", "v", "p")) == 1


def test_donchian_enter_hold_exit():
    idx = pd.date_range("2020-01-01", periods=8, freq="D", tz="UTC")
    idx = pd.date_range("2020-01-01", periods=9, freq="D", tz="UTC")
    c = pd.Series([10, 10, 10, 10, 12, 11.5, 11.8, 9, 9.5], index=idx, dtype=float)
    bars = pd.DataFrame({"high": c, "low": c, "close": c}, index=idx)
    s = strategies.donchian(bars, [4])
    # entry: close > max(high[t-4..t-1]); exit: close < min(low[t-2..t-1]); otherwise hold
    assert s.tolist() == [0, 0, 0, 0, 1, 1, 1, 0, 0]


def test_tsmom_and_ma(bars):
    c = bars["close"]
    t = strategies.tsmom(c, 20)
    assert t.iloc[:20].eq(0).all() and set(t.unique()) <= {0.0, 1.0}
    m = strategies.ma_cross(c, 10, 50)
    assert m.iloc[:49].eq(0).all()


def test_vol_target_and_filters(cfg, bars):
    inputs = strategies.layer_inputs(bars, cfg, _fday(bars))
    b = pd.Series(1.0, index=bars.index)
    s = strategies.compose(b, {"v": 0.2, "p": None, "q": None}, inputs, cfg)
    assert s.max() <= cfg["risk"]["max_leverage"] and s.iloc[:30].eq(0).all()
    f = strategies.compose(b, {"v": None, "p": 0.8, "q": 0.9}, inputs, cfg)
    off = (inputs["sigma_pct"] > 0.8) | (inputs["funding_pct"] > 0.9)
    assert f[off].eq(0).all() and f[~off].eq(1).all() and off.any()
    assert inputs["sigma_pct"].iloc[:364].isna().all()


def test_band_matrix_equals_columns():
    x = np.random.default_rng(0).uniform(0, 2, (300, 5))
    m = backtest.apply_band(x, 0.1)
    for j in range(5):
        np.testing.assert_array_equal(m[:, j], backtest.apply_band(x[:, j], 0.1))


def test_folds(cfg):
    n = 2989
    f = walkforward.folds(n, cfg)
    w = cfg["walkforward"]
    assert f[0][1] == w["min_train_days"] + w["embargo_days"]
    for tr_end, ts, te in f:
        assert ts - tr_end == w["embargo_days"] and te - ts <= w["test_days"]
    assert all(a[2] == b[1] for a, b in zip(f, f[1:])) and f[-1][2] == n


def test_select_requires_plateau(cfg):
    rng = np.random.default_rng(0)
    net = rng.normal(0, 0.01, (1000, 3))
    net[:, 0] += 0.004          # isolated peak: neighbours are poor
    net[:, 1] += 0.0015
    net[:, 2] += 0.0014
    nbrs = [np.array([1, 2]), np.array([2]), np.array([1])]
    turnover = np.zeros_like(net)
    assert walkforward.sharpe_cols(net).argmax() == 0
    assert walkforward.select(net, turnover, nbrs, cfg) in (1, 2)


def test_walkforward_choices_use_past_only(cfg):
    bars = random_bars(2400, seed=5)
    fday = _fday(bars)
    confs = grid.configs()
    nbrs = grid.neighbors(confs)
    base = walkforward.run(bars, strategies.config_matrix(bars, cfg, fday, confs), cfg, fday, nbrs)
    f = walkforward.folds(len(bars) - 1, cfg)
    k = 4
    cut = f[k][1]  # data after the k-th test start must not change folds 0..k
    alt = bars.copy()
    alt.iloc[cut:] = random_bars(len(bars) - cut, seed=9).to_numpy()
    res = walkforward.run(alt, strategies.config_matrix(alt, cfg, fday, confs), cfg, fday, nbrs)
    assert base["choices"][: k + 1] == res["choices"][: k + 1]


def test_synth_path_shape(bars):
    p = synth.path(bars, seed=3)
    assert (p["open"].iloc[1:].to_numpy() == p["close"].iloc[:-1].to_numpy()).all()
    assert (p["high"] >= p[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (p["low"] <= p[["open", "close"]].min(axis=1) + 1e-9).all()
    src = set(np.round(bars["close"].pct_change().dropna(), 12))
    assert set(np.round(p["close"].pct_change().dropna(), 12)) <= src
    pd.testing.assert_frame_equal(p, synth.path(bars, seed=3))
    c = synth.path(bars, seed=3, phi=0.2)
    assert not np.allclose(c["close"], p["close"])


def test_sb_block_length():
    idx = synth.sb_indices(200_000, 20, np.random.default_rng(0))
    breaks = (np.diff(idx) != 1).sum()
    assert 18 < len(idx) / breaks < 22


def test_calibration_parallel_equals_sequential(cfg, bars):
    fday = _fday(bars)
    a = synth.calibrate(bars, cfg, fday, jobs=1, n_paths=2)
    b = synth.calibrate(bars, cfg, fday, jobs=2, n_paths=2)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 2 * (1 + len(cfg["synth"]["ar_phi_grid"]))


@pytest.mark.parametrize("fdr,p20,status", [(0.10, 0.9, "stop_fdr"), (0.02, 0.05, "stop_engine"),
                                            (0.0, 0.0, "stop_engine"), (0.02, 0.10, "ok")])
def test_verdict(cfg, fdr, p20, status):
    n = 100
    rows = [{"phi": 0.0, "finding": i < fdr * n} for i in range(n)]
    rows += [{"phi": 0.2, "finding": i < p20 * n} for i in range(n)]
    assert synth.verdict(pd.DataFrame(rows), cfg)["status"] == status
