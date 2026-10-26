import copy

import numpy as np
import pandas as pd
import pytest
from conftest import random_bars

from tradelab import generator, indicators, journal


@pytest.fixture
def gcfg():
    g, _ = generator.load_config()
    g = copy.deepcopy(g)
    g["n_strategies"] = 60
    g["n_null_paths"] = 2
    return g


def _fday(bars):
    return np.full(len(bars), 3e-4)


def _first_params(family):
    grid = indicators.FAMILIES[family][1]
    for i in range(10):
        p = {k: v[min(i, len(v) - 1)] if k != "slow" and k != "exit" else v[-1] for k, v in grid.items()}
        if indicators.valid(family, p):
            return p
    raise AssertionError(family)


@pytest.mark.parametrize("family", list(indicators.FAMILIES))
def test_family_causal_and_bounded(family, bars):
    p = _first_params(family)
    base = indicators.signal(bars, family, p)
    assert base.between(0, 1).all() and base.index.equals(bars.index)
    t = 900
    alt = bars.copy()
    alt.iloc[t + 1:] = random_bars(len(bars) - t - 1, seed=t).to_numpy()
    pd.testing.assert_series_equal(base.iloc[: t + 1], indicators.signal(alt, family, p).iloc[: t + 1])


def test_rsi_and_latch():
    idx = pd.date_range("2020-01-01", periods=40, freq="D", tz="UTC")
    up = pd.Series(np.arange(1.0, 41.0), index=idx)
    assert indicators.rsi(up, 14).dropna().eq(100).all()
    e = pd.Series([False, True, False, False, False], dtype=bool)
    x = pd.Series([False, False, False, True, False], dtype=bool)
    assert indicators.latch(e, x).tolist() == [0, 1, 1, 0, 0]


def test_supertrend_follows_trend():
    idx = pd.date_range("2020-01-01", periods=200, freq="D", tz="UTC")
    c = pd.Series(np.r_[np.linspace(100, 200, 100), np.linspace(200, 100, 100)], index=idx)
    bars = pd.DataFrame({"high": c * 1.01, "low": c * 0.99, "close": c}, index=idx)
    s = indicators.supertrend(bars, 10, 3.0)
    assert s.iloc[60:100].eq(1).all() and s.iloc[160:].eq(0).all()


def test_generate_deterministic_distinct_valid(gcfg):
    a, b = generator.generate(gcfg), generator.generate(gcfg)
    assert a == b and len({generator.spec_id(s) for s in a}) == len(a) == 60
    for s in a:
        assert indicators.valid(s["family"], s["params"])
        for nb in generator.neighbors(s):
            diff = [k for k in ("params", "v", "p", "trend") if nb[k] != s[k]]
            assert len(diff) == 1 and indicators.valid(nb["family"], nb["params"])


def test_spec_target_causal(cfg, bars, gcfg):
    specs = [s for s in generator.generate(gcfg) if s["confirm"] or s["trend"]][:5]
    fd = _fday(bars)
    t = 1000
    alt = bars.copy()
    alt.iloc[t + 1:] = random_bars(len(bars) - t - 1, seed=3).to_numpy()
    for s in specs:
        np.testing.assert_array_equal(generator.target(bars, cfg, fd, s)[: t + 1],
                                      generator.target(alt, cfg, fd, s)[: t + 1])


def test_evaluate_parallel_equals_sequential(cfg, bars, gcfg):
    specs = generator.generate(gcfg)
    a = generator.evaluate_all(bars, cfg, _fday(bars), specs, jobs=1, chunk=7)
    b = generator.evaluate_all(bars, cfg, _fday(bars), specs, jobs=3, chunk=20)
    for x, y in zip(a, b):
        np.testing.assert_array_equal(x, y)


def test_screen_rejects_losers(cfg, gcfg):
    rng = np.random.default_rng(0)
    net = rng.normal(0, 0.01, (1000, 3))
    net[:, 0] += 0.002
    net[:, 1] -= 0.002
    trades = np.zeros_like(net, dtype=bool)
    trades[::50] = True
    ok = generator.screen_mask(generator.screen_metrics(net, trades), gcfg, cfg)
    assert ok[0] and not ok[1]


def test_search_journals_once_then_replays(cfg, conn, gcfg):
    bars = random_bars(1800, seed=2)
    specs = generator.generate(gcfg)
    *_, replayed = generator.search(bars, cfg, conn, _fday(bars), specs, "sha", "g", jobs=1)
    assert not replayed and len(journal.trials(conn)) == 60
    *_, replayed = generator.search(bars, cfg, conn, _fday(bars), specs, "sha", "g", jobs=1)
    assert replayed and len(journal.trials(conn)) == 60
    with pytest.raises(AssertionError):
        generator.search(random_bars(1800, seed=3), cfg, conn, _fday(bars), specs, "sha", "g", jobs=1)


def test_check_end_to_end(cfg, conn, gcfg):
    cfg = copy.deepcopy(cfg)
    cfg["synth"]["n_null_paths"] = 4
    cfg["stress"]["n_boot_paths"] = 30
    bars = random_bars(1800, seed=4)
    fd = _fday(bars)
    specs = generator.generate(gcfg)
    pos, net, trades, _ = generator.search(bars, cfg, conn, fd, specs, "sha", "g", jobs=1)
    wf = generator.walk_forward(bars, cfg, fd, specs, pos, net, trades, gcfg)
    if wf["final"] < 0:
        wf["final"] = 0
    r = generator.check(bars, cfg, conn, fd, specs, net, wf, "sha", 0.0, {"X/USDT": random_bars(1200, seed=8)}, {})
    assert r["n_trials_total"] == 60 and r["passed"] == all(r["checks"].values())
    assert "X/USDT" in r["candidate"]["transfer"]


def test_calibrate_small(cfg, bars, gcfg):
    gcfg["n_strategies"] = 20
    res = generator.calibrate(bars, cfg, _fday(bars), generator.generate(gcfg), gcfg, n_paths=1, jobs=1)
    assert len(res) == 2 and set(res.phi) == {0.0, 0.2}
