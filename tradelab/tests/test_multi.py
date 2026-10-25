import numpy as np
import pandas as pd
import pytest

from tradelab import multi


def _comps(n_days=900, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n_days, freq="D", tz="UTC")
    good = pd.Series(rng.normal(0.002, 0.01, n_days), index=idx)
    bad = pd.Series(rng.normal(-0.002, 0.02, n_days), index=idx)
    late = pd.Series(rng.normal(0.001, 0.01, n_days - 300), index=idx[300:])
    return {"good": good, "bad": bad, "late": late}


def test_weights_use_only_the_past():
    x = multi.matrix(_comps())
    w1 = multi.weights(x, "A")
    y = x.copy()
    cut = pd.Timestamp("2022-03-10", tz="UTC")
    y.loc[y.index >= cut] = 5.0  # the future goes wild
    w2 = multi.weights(y, "A")
    assert w1.loc[: cut].equals(w2.loc[: cut])


def test_weights_monthly_sum_to_one_and_wait_for_history():
    x = multi.matrix(_comps())
    w = multi.weights(x, "A")
    assert (w.sum(axis=1).round(12).isin([0.0, 1.0])).all()
    assert (w.diff().abs().sum(axis=1)[w.index.day != 1] < 1e-12).all()  # changes only on the 1st
    assert w["late"][: x.index[300 + multi.MIN_DAYS]].eq(0).all()
    assert w.loc[w.index[60], :].sum() == 0  # under 90 days of history: cash


def test_rule_b_drops_losing_components():
    x = multi.matrix(_comps())
    w = multi.weights(x, "B")
    assert w["bad"].iloc[500:].mean() < 0.05 < w["good"].iloc[500:].mean()


def test_reallocation_costs_and_rule_c():
    x = multi.matrix(_comps())
    vt = pd.Series(0.001, index=x.index)
    net_b, w = multi.combine(x, "B")
    gross = (w * x.fillna(0.0)).sum(axis=1)
    cost = w.diff().abs().sum(axis=1).fillna(0.0) * multi.REALLOC_COST
    assert np.allclose(net_b, gross - cost)
    net_c, _ = multi.combine(x, "C", vt)
    assert np.allclose(net_c, 0.5 * vt + 0.5 * net_b)


def test_run_journals_three_trials_once(conn, cfg):
    comps = _comps()
    x = multi.matrix(comps)
    bench = {"bh": pd.Series(0.001, index=x.index) + np.random.default_rng(1).normal(0, 0.03, len(x)),
             "vt": pd.Series(0.0005, index=x.index) + np.random.default_rng(2).normal(0, 0.01, len(x))}
    from tradelab import journal

    r, curves = multi.run(comps, cfg, conn, bench)
    assert set(r["rules"]) == {"A", "B", "C"} and len(journal.trials(conn)) == 3
    multi.run(comps, cfg, conn, bench)
    assert len(journal.trials(conn)) == 3  # same components: no new trials
    assert r["verdict"] in ("не найдена",) or r["passed"]
    assert list(curves.columns) == ["rule_A", "rule_B", "rule_C", "bh", "vt"]
