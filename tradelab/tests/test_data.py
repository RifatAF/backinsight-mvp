import numpy as np
import pandas as pd
import pytest

from tradelab import costs, data

DAY = data.TF_MS["1d"]


class FakeExchange:
    """Serves kline pages like Binance /klines: startTime filter, limit, closeTime column."""

    def __init__(self, n, limit=data.KLINE_LIMIT):
        self.rows = [[i * DAY, "1", "2", "0.5", str(1 + i), "10", i * DAY + DAY - 1, "100", 1, "0", "0", "0"]
                     for i in range(n)]
        self.calls = 0

    def publicGetKlines(self, p):
        self.calls += 1
        out = [r for r in self.rows if r[0] >= p["startTime"]]
        return out[: p["limit"]]


def test_pagination_reads_all_closed_bars():
    ex = FakeExchange(2500)
    df = data.fetch_klines(ex, "ETHUSDT", "1d", now_ms=2500 * DAY)
    assert len(df) == 2500 and ex.calls == 3
    assert df["timestamp"].is_monotonic_increasing and str(df["timestamp"].dt.tz) == "UTC"
    assert df["quote_volume"].iloc[0] == 100.0


def test_incomplete_last_bar_dropped():
    ex = FakeExchange(10)
    df = data.fetch_klines(ex, "ETHUSDT", "1d", now_ms=9 * DAY + 5)
    assert len(df) == 9


def _frame(days, close):
    return pd.DataFrame({"timestamp": pd.to_datetime(days, utc=True), "open": close, "high": close,
                         "low": close, "close": close, "volume": 1.0, "quote_volume": 1.0})


def test_check_flags_gaps_dups_big_moves():
    days = ["2020-01-01", "2020-01-02", "2020-01-02", "2020-01-04", "2020-01-05"]
    df, rep = data.check(_frame(days, [1.0, 1.1, 1.1, 2.0, 2.1]), "1d")
    assert rep["duplicates"] == 1
    assert rep["missing"] == ["2020-01-03 00:00:00+00:00"]
    assert rep["big_moves"] == ["2020-01-04 00:00:00+00:00"]
    assert df.index.is_unique and str(df.index.tz) == "UTC"


def test_check_rejects_naive_index():
    f = _frame(["2020-01-01", "2020-01-02"], [1.0, 1.0])
    f["timestamp"] = f["timestamp"].dt.tz_localize(None)
    with pytest.raises(ValueError):
        data.check(f, "1d")


def test_holdout_split_is_exact():
    idx = pd.date_range("2020-01-01", "2022-12-31", freq="D", tz="UTC")
    df = pd.DataFrame({"close": np.arange(len(idx), dtype=float)}, index=idx)
    start = data.holdout_start(idx[-1], 365)
    pre, hold = data.split(df, start)
    assert len(hold) == 365 and len(pre) + len(hold) == len(df)
    assert pre.index.max() < hold.index.min()


def test_basket_needs_history_and_ranks_pre_holdout_volume():
    start = pd.Timestamp("2025-01-01", tz="UTC")

    def hist(first, vol_pre, vol_hold):
        idx = pd.date_range(first, "2025-12-31", freq="D", tz="UTC")
        v = np.where(idx < start, vol_pre, vol_hold)
        return pd.DataFrame({"quote_volume": v}, index=idx)

    h = {"OLD/USDT": hist("2018-01-01", 5.0, 1.0), "BIG/USDT": hist("2019-01-01", 9.0, 0.0),
         "HOT/USDT": hist("2018-01-01", 1.0, 1e9), "NEW/USDT": hist("2023-01-01", 1e9, 1e9)}
    assert data.select_basket(h, start, 2) == ["BIG/USDT", "OLD/USDT"]


def test_funding_daily_sum_and_fallback(cfg):
    idx = pd.date_range("2019-12-30", periods=4, freq="D", tz="UTC")
    ev = pd.Series([0.001, 0.002, 0.003],
                   index=pd.to_datetime(["2020-01-01 00:00", "2020-01-01 08:00", "2020-01-01 16:00"], utc=True))
    f = costs.funding_daily(idx, cfg["costs"], ev)
    fb = 3 * cfg["costs"]["funding_fallback_per_8h"]
    np.testing.assert_allclose(f.to_numpy(), [fb, fb, 0.006, fb])
