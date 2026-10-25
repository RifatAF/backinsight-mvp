import copy

import numpy as np
import pandas as pd
import pytest
from conftest import random_bars

from tradelab import experiment, features, generator, indicators, journal


def _daily_feats(index, seed=0):
    rng = np.random.default_rng(seed)
    n = len(index)
    f = pd.DataFrame({"funding_day": rng.normal(3e-4, 3e-4, n), "oi_value": 1e9 * np.exp(np.cumsum(rng.normal(0, 0.03, n))),
                      "top_ls_acc": rng.uniform(0.5, 3, n), "top_ls_pos": rng.uniform(0.5, 3, n),
                      "global_ls": rng.uniform(0.5, 3, n), "taker_ratio": rng.uniform(0.8, 1.2, n),
                      "basis": rng.normal(0, 3e-4, n), "cb_close": np.nan, "fng": rng.uniform(5, 95, n)}, index=index)
    f.loc[f.index[:300], ["oi_value", "top_ls_acc", "top_ls_pos", "global_ls", "taker_ratio"]] = np.nan
    return f[features.COLUMNS]


def _coin(cfg, n=1500, seed=0, sym="AAA/USDT"):
    bars = random_bars(n, seed=seed)
    f = _daily_feats(bars.index, seed)
    f["cb_close"] = bars["close"] * (1 + np.random.default_rng(seed + 1).normal(0, 1e-3, n))
    return experiment.coin_context(sym, "1d", cfg, bars, bars["close"], f, None)


def _ecfg(direction="both", n=40):
    return {"name": "t", "universe": "AAA/USDT", "timeframe": "1d", "direction": direction, "families": "all",
            "seed": 3, "n_strategies": n, "combo_share": 0.3, "trend_filter_share": 0.3,
            "screen": {"min_sharpe": 0.0, "min_cagr": 0.0, "max_drawdown": 0.6, "min_trades": 6},
            "plateau_top_k": 5, "n_null_paths": 1, "n_synth_b": 3}


def test_invert_mirrors_trend(bars):
    inv = indicators.invert(bars)
    up = indicators.roc(bars, 20).iloc[20:]
    down = indicators.roc(inv, 20).iloc[20:]
    assert ((up + down) <= 1).all() and (inv["high"] >= inv["low"]).all()


@pytest.mark.parametrize("family", list(indicators.FEATURE_FAMILIES))
def test_feature_family_causal(family, cfg):
    coin = _coin(cfg)
    p = {k: v[0] for k, v in indicators.grid(family).items()}
    lo, sh = indicators.long_short(coin["bars"], coin["feats"], family, p, coin["inv"])
    assert set(np.unique(lo)) <= {0.0, 1.0} and set(np.unique(sh)) <= {0.0, 1.0}
    t = 1000
    alt_bars = coin["bars"].copy()
    alt_bars.iloc[t + 1:] = random_bars(len(alt_bars) - t - 1, seed=9).to_numpy()
    fd = _daily_feats(alt_bars.index, 0)
    fd.iloc[: t + 1] = _daily_feats(alt_bars.index, 0).iloc[: t + 1]
    fd.iloc[t + 1:] = _daily_feats(alt_bars.index, 5).iloc[t + 1:]
    fd["cb_close"] = alt_bars["close"]
    base_fd = _daily_feats(coin["bars"].index, 0)
    base_fd["cb_close"] = coin["bars"]["close"]
    a = experiment.coin_context("A", "1d", cfg, coin["bars"], coin["bars"]["close"], base_fd, None)
    b = experiment.coin_context("A", "1d", cfg, alt_bars, alt_bars["close"], fd, None)
    la, sa = indicators.long_short(a["bars"], a["feats"], family, p, a["inv"])
    lb, sb = indicators.long_short(b["bars"], b["feats"], family, p, b["inv"])
    pd.testing.assert_series_equal(la.iloc[: t + 1], lb.iloc[: t + 1])
    pd.testing.assert_series_equal(sa.iloc[: t + 1], sb.iloc[: t + 1])


def test_align_uses_only_closed_days():
    days = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    d = pd.DataFrame({c: [1.0, 2.0, 3.0] for c in features.COLUMNS}, index=days)
    b1 = pd.DataFrame(index=days)
    assert features.align(d, b1, "1d")["fng"].tolist() == [1.0, 2.0, 3.0]
    b6 = pd.DataFrame(index=pd.date_range("2024-01-02", periods=4, freq="6h", tz="UTC"))
    assert features.align(d, b6, "6h")["fng"].tolist() == [1.0, 1.0, 1.0, 2.0]


def test_bar_funding_6h(cfg):
    idx = pd.date_range("2024-01-01", periods=12, freq="6h", tz="UTC")
    ev = pd.Series([0.001, 0.002, 0.003], index=pd.to_datetime(["2024-01-01 08:00", "2024-01-01 16:00", "2024-01-02 00:00"], utc=True))
    f = experiment.bar_funding(pd.DataFrame(index=idx), "6h", ev, None, cfg["costs"])
    assert f[1] == 0.001 and f[2] == 0.002 and f[0] == 0 and f[4] == 0.003
    assert f[-1] == pytest.approx(3 * cfg["costs"]["funding_fallback_per_8h"] / 4)


def test_long_only_never_short_and_both_can_short(cfg):
    coin = _coin(cfg)
    spec = {"family": "roc", "params": {"n": 20}, "confirm": None, "trend": None, "v": None, "p": None}
    assert experiment.target(coin, cfg, dict(spec, direction="long")).min() >= 0
    s = experiment.target(coin, cfg, dict(spec, direction="both"))
    assert s.min() < 0 < s.max()


def test_portfolio_of_identical_coins_equals_single(cfg):
    a = _coin(cfg, sym="A")
    b = dict(a, sym="B")
    days = experiment.days_index([a])
    fns = [experiment.bh_target, experiment.vt_target]
    one, _ = experiment.evaluate([a], cfg, fns, days)
    two, _ = experiment.evaluate([a, b], cfg, fns, days)
    np.testing.assert_allclose(one, two)
    assert (one[: experiment.WARMUP_DAYS] == 0).all()


def test_stitched_matches_single_spec_after_entry(cfg):
    coin = _coin(cfg, n=1600)
    days = experiment.days_index([coin])
    spec = dict(generator.generate(dict(_ecfg(), families=["roc"], n_strategies=1))[0])
    net, _ = experiment.evaluate([coin], cfg, [experiment.spec_target(spec)], days)
    folds = experiment.walkforward.folds(len(days), cfg)
    st = experiment.stitched([coin], cfg, days, [spec], [0] * len(folds), folds)
    first = folds[0][1]
    np.testing.assert_allclose(st[first + 1: folds[-1][2]], net[first + 1: folds[-1][2], 0])


def test_search_replay_and_calibrate(cfg, conn):
    cfg = copy.deepcopy(cfg)
    cfg["stress"]["n_boot_paths"] = 20
    e = _ecfg()
    gcfg = experiment.gen_config(e)
    coins = [_coin(cfg, n=1700, seed=1), _coin(cfg, n=1500, seed=2, sym="BBB/USDT")]
    days = experiment.days_index(coins)
    specs = generator.generate(gcfg)
    assert all(s["direction"] == "both" for s in specs)
    net, trades, replayed = experiment.search(e, "esha", coins, cfg, conn, specs, days, "sha", jobs=1)
    assert not replayed and len(journal.trials(conn)) == 40
    *_, replayed = experiment.search(e, "esha", coins, cfg, conn, specs, days, "sha", jobs=2)
    assert replayed and len(journal.trials(conn)) == 40
    wf = experiment.walk_forward(coins, cfg, days, specs, net, trades, gcfg)
    if wf["final"] < 0:
        wf["final"] = 0
    r = experiment.check(e, coins, cfg, conn, days, specs, net, wf, 0.0, coins)
    assert r["passed"] == all(r["checks"].values()) and r["n_trials_total"] == 40
    cal = experiment.calibrate(e, coins, cfg, days, specs[:10], gcfg, jobs=1, n_paths=1)
    assert len(cal) == 2


def test_resample_and_htf_causal(cfg):
    idx = pd.date_range("2022-01-01", periods=24 * 400, freq="1h", tz="UTC")
    rng = np.random.default_rng(0)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.005, len(idx)))), index=idx)
    bars = pd.DataFrame({"open": c.shift(1).fillna(100), "high": c * 1.002, "low": c * 0.998, "close": c,
                         "quote_volume": 1e6}, index=idx)
    d = experiment.resample(bars, "1d")
    assert len(d) == 400 and d["close"].iloc[0] == c.iloc[23] and d["high"].iloc[0] == bars["high"].iloc[:24].max()
    sig = experiment.align_htf(pd.Series(np.arange(400.0), index=d.index), "1d", bars, "1h")
    assert sig.iloc[:23].eq(0).all() and sig.iloc[23] == 0 and sig.iloc[47] == 1  # day 0 visible from its last hour's close
    f = _daily_feats(d.index)
    coin = experiment.coin_context("A", "1h", cfg, bars, d["close"], f, None, "1d")
    spec = {"family": "roc", "params": {"n": 24}, "confirm": None, "trend": None, "v": None, "p": None,
            "direction": "both", "htf": {"family": "ema_cross", "params": {"fast": 5, "slow": 50}}}
    base = experiment.target(coin, cfg, spec)
    t = 24 * 200 + 5
    alt = bars.copy()
    alt.iloc[t + 1:, :4] *= 1.5
    coin2 = experiment.coin_context("A", "1h", cfg, alt, experiment.resample(alt, "1d")["close"], f, None, "1d")
    np.testing.assert_array_equal(base[: t + 1], experiment.target(coin2, cfg, spec)[: t + 1])


def test_htf_generation_only_when_configured():
    e = _ecfg()
    a = generator.generate(experiment.gen_config(e))
    e2 = dict(e, htf="1d", htf_share=0.6)
    b = generator.generate(experiment.gen_config(e2))
    assert not any("htf" in s for s in a) and any("htf" in s for s in b)
