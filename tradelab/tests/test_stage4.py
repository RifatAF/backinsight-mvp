import json

import numpy as np
import pandas as pd
import pytest
from conftest import random_bars
from statsmodels.stats.multitest import multipletests

from tradelab import backtest, experiment, indicators, macro, seasonality


def _macro(index, seed):
    rng = np.random.default_rng(seed)
    m = pd.DataFrame({c: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(index)))) for c in macro.COLUMNS}, index=index)
    return m[index.dayofweek < 5]  # trading days only, as downloaded


def _ctx(cfg, bars, seed=0, tf="1d"):
    day = pd.date_range(bars.index[0].floor("D"), bars.index[-1].floor("D"), freq="D", tz="UTC")
    close = bars["close"].resample("1D").last().reindex(day).ffill()
    btc = close * np.exp(np.random.default_rng(seed + 7).normal(0, 0.01, len(day)))
    return experiment.coin_context("A", tf, cfg, bars, close, None, None, None, _macro(day, seed), btc)


def _p0(family):
    return {k: v[len(v) // 2] for k, v in indicators.grid(family).items()}


@pytest.mark.parametrize("family", list(indicators.EXTRA_FAMILIES))
def test_stage4_family_causal(family, cfg):
    """Signals up to bar t do not change when bars and macro data after t are replaced."""
    bars = random_bars(1500)
    a = _ctx(cfg, bars, 0)
    t = 1000
    alt = bars.copy()
    alt.iloc[t + 1:] = random_bars(len(bars) - t - 1, seed=9).to_numpy()
    b = _ctx(cfg, alt, 0)
    m = b["feats"].index >= alt.index[t] + pd.Timedelta(days=1)
    b["feats"].loc[m] = b["feats"].loc[m] * 1.7  # future macro / BTC values replaced too
    p = _p0(family)
    la, sa = indicators.long_short(a["bars"], a["feats"], family, p)
    lb, sb = indicators.long_short(b["bars"], b["feats"], family, p)
    assert set(np.unique(la)) <= {0.0, 1.0} and set(np.unique(sa)) <= {0.0, 1.0}
    pd.testing.assert_series_equal(la.iloc[: t + 1], lb.iloc[: t + 1], check_names=False)
    pd.testing.assert_series_equal(sa.iloc[: t + 1], sb.iloc[: t + 1], check_names=False)


def test_macro_value_known_at_day_close(cfg):
    """The macro close of day D (US session ends before 00:00 UTC of D+1) is first seen by the
    daily bar of D, whose close is 00:00 UTC of D+1; earlier bars see only older closes."""
    bars = random_bars(400)
    c = _ctx(cfg, bars, 1)
    m = _macro(pd.date_range(bars.index[0], bars.index[-1], freq="D", tz="UTC"), 1)
    d = m.index[200]
    assert c["feats"].loc[d, "spx"] == m.loc[d, "spx"]
    assert c["feats"].loc[d - pd.Timedelta(days=1), "spx"] == m["spx"].iloc[199]


def test_weekday_holds_that_day(cfg):
    """weekday_up(3): the backtest holds a position exactly over Thursdays (open to open)."""
    cfg["costs"]["rebalance_band"] = 0.0
    bars = random_bars(200)
    lo, _ = indicators.long_short(bars, None, "weekday_up", {"day": 3})
    pos = backtest.positions(lo.to_numpy(), cfg)
    held = bars.index[pos > 0]
    assert len(held) > 20 and (held.dayofweek == 3).all()
    assert ((bars.index.dayofweek == 3) == (pos > 0))[1:].all()


def test_hour_family_on_hourly_bars(cfg):
    idx = pd.date_range("2022-01-01", periods=24 * 30, freq="h", tz="UTC")
    bars = random_bars(len(idx)).set_axis(idx)
    lo, _ = indicators.long_short(bars, None, "hour_up", {"hour": 14})
    assert (bars.index[lo.to_numpy() > 0] + pd.Timedelta(hours=1)).hour.unique().tolist() == [14]


def test_weekly_candles_closed_only(cfg):
    """A weekly signal reaches daily bars only after the week (Monday-Sunday UTC) has closed."""
    bars = random_bars(700, start="2018-01-03")
    w = experiment.resample(bars, "1w")
    assert (w.index.dayofweek == 0).all()
    lo, _ = indicators.long_short(w, None, "macd", {"fast": 12, "slow": 26, "signal": 9})
    t = 500
    alt = bars.copy()
    alt.iloc[t + 1:] = random_bars(len(bars) - t - 1, seed=4).to_numpy()
    lo2, _ = indicators.long_short(experiment.resample(alt, "1w"), None, "macd", {"fast": 12, "slow": 26, "signal": 9})
    a = experiment.align_htf(lo, "1w", bars, "1d")
    b = experiment.align_htf(lo2, "1w", alt, "1d")
    assert (a.iloc[: t + 1] == b.iloc[: t + 1]).all()
    sun = bars.index[bars.index.dayofweek == 6][40]
    week = sun - pd.Timedelta(days=6)
    assert a[sun] == lo[week] and a[sun - pd.Timedelta(days=1)] == lo[week - pd.Timedelta(days=7)]


def test_yahoo_dates_use_exchange_time_zone():
    ts = [int(pd.Timestamp("2024-06-02 23:00", tz="UTC").timestamp()),   # London 00:00 BST, Monday 3 June
          int(pd.Timestamp("2024-12-02 00:00", tz="UTC").timestamp())]   # London 00:00 GMT, Monday 2 December
    raw = json.dumps({"chart": {"result": [{"meta": {"gmtoffset": 0, "exchangeTimezoneName": "Europe/London"},
                                            "timestamp": ts, "indicators": {"quote": [{"close": [157.0, 150.0]}]}}]}})
    s = macro.parse(raw)
    assert [str(d.date()) for d in s.index] == ["2024-06-03", "2024-12-02"]


def test_family_set_all_is_frozen():
    assert len(experiment.FAMILY_SETS["all"]) == 21
    assert experiment.FAMILY_SETS["all"][:13] == list(indicators.FAMILIES)
    assert not set(experiment.FAMILY_SETS["all"]) & set(indicators.EXTRA_FAMILIES)


def test_fixed_specs_fill_defaults():
    e = {"direction": "long", "specs": [{"family": "hold", "params": {}, "htf": {"family": "macd", "params": {}}}]}
    s = experiment.specs(e, None)
    assert s == [{"confirm": None, "trend": None, "v": None, "p": None, "direction": "long", "family": "hold",
                  "params": {}, "htf": {"family": "macd", "params": {}}}]
    assert not experiment.uses_macro(e, s)
    assert experiment.uses_macro(e, [dict(s[0], family="macro_trend")])


def test_bh_qvalues_match_statsmodels():
    p = np.random.default_rng(0).uniform(0, 1, 50) ** 3
    assert np.allclose(seasonality.bh_qvalues(p), multipletests(p, method="fdr_bh")[1])


def test_seasonality_finds_planted_thursday():
    bars = random_bars(3000)
    r = bars["open"].shift(-1) / bars["open"] - 1
    thu = bars.index.dayofweek == 3
    o = bars["open"].to_numpy().copy()
    for i in range(1, len(o)):  # plant +1.5% on every Thursday's open-to-open return
        o[i] = o[i - 1] * (1 + r.iloc[i - 1] + (0.015 if thu[i - 1] else 0.0))
    planted = bars.assign(open=o)
    df = seasonality.run({"X": (planted, ("weekday", "month"))})
    row = df[(df["тип"] == "weekday") & (df["группа"] == "чт")].iloc[0]
    assert row["q"] < 0.01 and row["обе половины"]
    assert (df.loc[df["тип"] == "month", "q"] > 0.05).all()
