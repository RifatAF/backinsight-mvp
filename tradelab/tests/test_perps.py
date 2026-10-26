import copy

import numpy as np
import pandas as pd
import pytest
import yaml

from tradelab import data, journal, paperbot, perpdata, perps

PCFG = yaml.safe_load((data.ROOT / "perps.yaml").read_text(encoding="utf-8"))


def _frames(n_coins=12, days=150, seed=0, start="2022-01-01"):
    """Test fixture only: random hourly perp frames with 8h funding, never a stand-in for market data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=24 * days, freq="1h", tz="UTC")
    out = {}
    for i in range(n_coins):
        lo = 0 if i % 3 else 24 * 10 * (i % 4)  # some coins list later
        close = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx))))
        op = np.concatenate([[10.0], close[:-1]])
        qv = rng.uniform(1e5, 4e5, len(idx)) * (1 + i)  # 2.4M..29M per day
        settled = (idx.hour % 8) == 7
        f = pd.DataFrame({"open": op, "high": np.maximum(op, close), "low": np.minimum(op, close), "close": close,
                          "quote_volume": qv, "taker_buy_quote": qv * rng.uniform(0.3, 0.7, len(idx)),
                          "missing": False, "premium_open": rng.normal(0, 5e-4, len(idx)),
                          "premium": rng.normal(0, 5e-4, len(idx)),
                          "funding": np.where(settled, rng.normal(3e-4, 6e-4, len(idx)), 0.0),
                          "funding_hours": 8.0, "settled": settled}, index=idx)
        out[f"C{i:02d}USDT"] = f.iloc[lo:]
    return out


def _pcfg(**venue):
    p = copy.deepcopy(PCFG)
    p["universe"].update({"min_daily_volume_usd": 1e6, "max_daily_volume_usd": 1e9, "min_history_days": 20})
    p["venue"].update(venue)
    return p


@pytest.fixture
def a():
    return perps.prepare(perpdata.load_panels(frames=_frames()), _pcfg())


def test_grid_and_neighbours():
    confs = perps.configs()
    assert len(confs) == len({perps.config_id(c) for c in confs}) == 126
    nb = perps.neighbors(confs)
    assert all(confs[j]["family"] == c["family"] for c, js in zip(confs, nb) for j in js)


def test_returns_use_next_open_and_close_at_delisting():
    op = np.array([[10.0], [11.0], [12.0]])
    cl = np.array([[11.0], [12.0], [9.0]])
    r, spot = perps.returns(op, cl, np.full((3, 1), np.nan))
    assert np.allclose(r[:, 0], [0.1, 12 / 11 - 1, 9 / 12 - 1]) and np.allclose(spot, r)


def test_costs_and_funding_exact(a):
    """Flat price, weight 0/1 alternating on one coin: net = -turnover * (fee + slip) - pos * funding."""
    b = dict(a)
    b["r"] = np.zeros_like(a["r"])
    b["spot_r"] = np.zeros_like(a["r"])
    b["quote_volume"] = np.full_like(a["quote_volume"], np.inf)
    b["listed"] = np.ones_like(a["listed"])
    fund = np.zeros_like(a["funding"])
    fund[5, 0] = 0.001
    b["funding"] = fund
    pc = _pcfg(taker_fee=0.0005, slippage_base=0.0002, slippage_size_coef=0.0)
    wp = np.zeros_like(a["r"])
    wp[2:6, 0] = 1.0  # held over bars 3..6
    bar, tr = perps.pnl(b, wp, np.zeros_like(wp), pc)
    assert np.isclose(bar[3], -0.0007) and np.isclose(bar[7], -0.0007)
    assert np.isclose(bar[5], -0.001)  # a long pays a positive rate
    assert np.isclose(bar.sum(), -0.0014 - 0.001) and tr.sum() == 2


def test_loss_limited_to_capital(a):
    b = dict(a, r=np.full_like(a["r"], 3.0), listed=np.ones_like(a["listed"]))
    wp = np.zeros_like(a["r"])
    wp[:, 0] = -0.5
    bar, _ = perps.pnl(b, wp, np.zeros_like(wp), _pcfg(taker_fee=0.0, slippage_base=0.0, slippage_size_coef=0.0))
    assert bar[5] == pytest.approx(-0.5 - 0.5 * a["funding"][5, 0])


@pytest.mark.parametrize("conf", [c for c in perps.configs() if c.get("k", 5) == 5 and c.get("n", 3) == 3
                                  and c.get("h", 12) == 12 and c.get("m", 3.0) == 3.0])
def test_weights_causal(conf):
    """Weights up to bar t do not change when every input after t is replaced."""
    fr = _frames()
    alt = {k: v.copy() for k, v in fr.items()}
    cut = pd.Timestamp("2022-03-15", tz="UTC")
    other = _frames(seed=7)
    for k in alt:
        m = alt[k].index > cut
        alt[k].loc[m] = other[k].loc[other[k].index > cut].to_numpy()
    pc = _pcfg()
    a1 = perps.prepare(perpdata.load_panels(frames=fr), pc)
    a2 = perps.prepare(perpdata.load_panels(frames=alt), pc)
    w1, s1 = perps.weights(a1, conf)
    w2, s2 = perps.weights(a2, conf)
    t = a1["index"].get_loc(cut)
    assert np.array_equal(w1[: t + 1], w2[: t + 1]) and np.array_equal(s1[: t + 1], s2[: t + 1])
    assert np.abs(w1).sum(1).max() <= 1.0 + 1e-9 and (np.abs(w1) + np.abs(s1)).sum(1).max() <= 1.0 + 1e-9


def test_eligibility_point_in_time():
    fr = _frames()
    pc = _pcfg()
    a1 = perps.prepare(perpdata.load_panels(frames=fr), pc)
    big = {k: v.copy() for k, v in fr.items()}
    day = pd.Timestamp("2022-03-01", tz="UTC")
    for v in big.values():
        v.loc[v.index >= day, "quote_volume"] *= 1000  # volumes from that day on move far above the band
    a2 = perps.prepare(perpdata.load_panels(frames=big), pc)
    t = a1["index"].get_loc(day + pd.Timedelta(hours=23))
    assert np.array_equal(a1["elig"][: t + 1], a2["elig"][: t + 1])
    assert a2["elig"][t + 24 * 20:].sum() == 0


def test_carry_is_delta_neutral(a):
    wp, ws = perps.weights(a, {"family": "carry", "n": 1, "thr": 0.0003, "k": 3})
    assert (ws >= 0).all() and np.allclose(wp, -ws) and ws.sum(1).max() <= 0.5 + 1e-9 and ws.any()


def test_shifted_signals_keep_prices(a):
    s = perps.shifted_signals(a, 3, 24 * 5)
    assert not np.allclose(np.nan_to_num(s["f8"]), np.nan_to_num(a["f8"]))
    assert np.array_equal(np.isnan(s["f8"]).sum(0) > 0, np.isnan(a["f8"]).sum(0) > 0)
    assert np.nansum(s["f8"]) == pytest.approx(np.nansum(a["f8"]))


def test_walk_book():
    asks = [(100.0, 1.0), (101.0, 2.0)]
    assert paperbot.walk_book(asks, 50.0) == 100.0
    assert paperbot.walk_book(asks, 302.0) == pytest.approx(302.0 / (1.0 + 202.0 / 101.0))
    assert paperbot.walk_book(asks, 1000.0) is None


def test_hl_coin_names():
    assert paperbot.hl_coin("SOLUSDT") == "SOL" and paperbot.hl_coin("1000PEPEUSDT") == "kPEPE"


def test_paper_settlement_maps_to_bar_before():
    fr = _frames(n_coins=1)
    sym = next(iter(fr))
    bars = fr[sym][["open", "close", "quote_volume", "taker_buy_quote"]]
    ms = int(pd.Timestamp("2022-02-01 08:00", tz="UTC").timestamp() * 1000)
    p = paperbot.panels_from({sym: bars}, {sym: [(ms - 8 * 3_600_000, 0.0001), (ms, 0.0004)]})
    assert p["settled"].loc[pd.Timestamp("2022-02-01 07:00", tz="UTC"), sym]
    assert p["funding"].loc[pd.Timestamp("2022-02-01 07:00", tz="UTC"), sym] == 0.0004


class FakeHL:
    def __init__(self, px):
        self.px = px

    def ctxs(self):
        return {c: {"markPx": p, "oraclePx": p, "funding": 1e-5} for c, p in self.px.items()}

    def book(self, coin):
        p = self.px[coin]
        return [(p * 0.999, 1e9)], [(p * 1.001, 1e9)]

    def funding(self, coin, start_ms):
        return [(start_ms + 3_600_000, 0.0001)]


def test_paper_rebalance_and_funding(tmp_path):
    hl = FakeHL({"AAA": 10.0, "BBB": 20.0})
    acct = paperbot.Account(10_000.0)
    logs = []
    pc = _pcfg(taker_fee=0.0005)
    paperbot.rebalance(acct, {"AAA": (-0.5, 0.0), "BBB": (0.25, 0.25)}, hl.ctxs(), hl, pc, lambda k, p: logs.append(k))
    assert acct.perp["AAA"] == pytest.approx(-500.0) and acct.perp["BBB"] == pytest.approx(125.0)
    assert acct.spot["BBB"] == pytest.approx(125.0)
    eq = acct.equity({"AAA": 10.0, "BBB": 20.0}, {"AAA": 10.0, "BBB": 20.0})
    assert 9_980 < eq < 10_000  # paid spread and fees only
    paid = paperbot.accrue_funding(acct, hl, hl.ctxs(), 0, lambda k, p: logs.append(k))
    assert paid == pytest.approx(-500 * 10 * 1e-4 + 125 * 20 * 1e-4)  # the short receives, the long pays
    # a small drift does not trade
    n = logs.count("fill")
    paperbot.rebalance(acct, {"AAA": (-0.51, 0.0), "BBB": (0.25, 0.25)}, hl.ctxs(), hl, pc, lambda k, p: logs.append(k))
    assert logs.count("fill") == n


def test_paper_state_survives_restart(tmp_path, monkeypatch):
    class FakeBN:
        def symbols(self):
            return []

        def due(self, now_ms):
            return set()

    db = tmp_path / "paper.sqlite"
    conf = {"family": "spread", "n": 3, "k": 5}
    bot = paperbot.Bot(_pcfg(), conf, 1000.0, db=db, hl=FakeHL({"ETH": 3000.0}), bn=FakeBN())
    assert bot.step(pd.Timestamp("2026-01-01 05:00", tz="UTC")) == pytest.approx(1000.0)
    bot2 = paperbot.Bot(_pcfg(), conf, 5.0, db=db, hl=FakeHL({"ETH": 3000.0}), bn=FakeBN())
    assert bot2.acct.cash == pytest.approx(1000.0)
    bot2.step(pd.Timestamp("2026-01-02 06:00", tz="UTC"))
    with pytest.raises(SystemExit):
        paperbot.Bot(_pcfg(), {"family": "carry", "n": 1, "thr": 0.001, "k": 3}, 5.0, db=db, hl=FakeHL({}), bn=FakeBN())
    assert "Бумажная торговля" in paperbot.report(db=db)
