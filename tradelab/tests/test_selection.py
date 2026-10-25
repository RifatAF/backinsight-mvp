import copy

import numpy as np
import pandas as pd
import pytest
from conftest import random_bars

from tradelab import grid, journal, selection, stress, synth


def _fday(bars):
    return np.full(len(bars), 3e-4)


def test_stylized_facts_on_gaussian():
    f = synth.stylized_facts(np.random.default_rng(0).normal(0, 0.02, 20000))
    assert abs(f["kurtosis"] - 3) < 0.2 and abs(f["acf_r_1"]) < 0.03 and abs(f["acf_abs_10"]) < 0.03


def test_path_b_fat_tails_and_clustering(bars):
    prm = synth.fit_b(bars)
    assert 2 < prm["nu"] < 200 and prm["alpha"] + prm["beta"] < 1
    p = synth.path_b(bars, prm, seed=1)
    pd.testing.assert_frame_equal(p, synth.path_b(bars, prm, seed=1))
    assert (p["open"].iloc[1:].to_numpy() == p["close"].iloc[:-1].to_numpy()).all()
    assert (p["high"] >= p[["open", "close"]].max(axis=1) - 1e-9).all()
    f = synth.facts_table(bars, [synth.path_b(bars, prm, s) for s in range(5)])
    assert list(f.index)[:2] == ["kurtosis", "hill_top5"] and "real" in f.columns


def test_delay_shifts_signal(cfg, bars):
    conf = {"base": "tsmom_20", "v": None, "p": None, "q": None}
    a = stress.candidate_net(bars, cfg, _fday(bars), conf)
    b = stress.candidate_net(bars, cfg, _fday(bars), conf, delay=2)
    assert not np.allclose(a, b)


def test_double_costs_hurt(cfg, bars):
    conf = {"base": "tsmom_20", "v": None, "p": None, "q": None}
    a = stress.candidate_net(bars, cfg, _fday(bars), conf)
    b = stress.candidate_net(bars, stress.double_costs(cfg), _fday(bars), conf)
    assert b.sum() < a.sum()


def test_bootstrap_share_seeded(cfg):
    net = np.random.default_rng(0).normal(0.001, 0.01, 1500)
    assert stress.bootstrap_share(net, cfg, n=100)[0] == stress.bootstrap_share(net, cfg, n=100)[0] > 0.5


def test_ablation_drops_useless_layer():
    confs = grid.configs()
    idx = {grid.config_id(c): i for i, c in enumerate(confs)}
    rng = np.random.default_rng(0)
    net = rng.normal(0, 0.01, (500, len(confs)))
    cand = {"base": "ma_10_50", "v": 0.15, "p": 0.80, "q": None}
    net[:, idx[grid.config_id(cand)]] += 0.003
    net[:, idx[grid.config_id(dict(cand, v=None))]] += 0.001       # v helps
    net[:, idx[grid.config_id(dict(cand, p=None))]] += 0.006       # p hurts
    out, log = selection.ablate(cand, confs, net, np.arange(500))
    assert out == dict(cand, p=None) and [x["kept"] for x in log] == [True, False]


def test_search_journals_then_refuses_rerun(cfg, conn):
    bars = random_bars(1800, seed=2)
    found = selection.search(bars, cfg, conn, _fday(bars), "sha")
    trials = journal.trials(conn)
    assert len(trials) == 300 and all(t["metrics"] == {"status": "started"} for t in trials)
    ev = journal.events(conn, "trial_metrics")
    assert len(ev) == 300 and {e["payload"]["trial_id"] for e in ev} == {t["id"] for t in trials}
    assert min(e["id"] for e in ev) > 0 and found["final"] >= -1
    with pytest.raises(selection.SearchAlreadyRun):
        selection.search(bars, cfg, conn, _fday(bars), "sha")


def test_evaluate_end_to_end(cfg, conn):
    cfg = copy.deepcopy(cfg)
    cfg["synth"]["n_null_paths"] = 5
    cfg["stress"]["n_boot_paths"] = 50
    bars = random_bars(1800, seed=4)
    fday = _fday(bars)
    found = selection.search(bars, cfg, conn, fday, "sha")
    found["final"] = 0  # force a candidate to exercise the candidate branch
    basket = {"X/USDT": random_bars(1500, seed=7), "Y/USDT": random_bars(1500, seed=8)}
    r = selection.evaluate(bars, cfg, conn, fday, found, "sha", 0.0, basket, {})
    assert r["n_trials"] == 300 and 0 <= r["pbo"] <= 1 and set(r["candidate"]["transfer"]) == set(basket)
    assert r["verdict"] in ("не найдена", "кандидат допущен к holdout")
    assert r["passed"] == all(r["checks"].values())


def test_replay_matches_journal_and_adds_no_trials(cfg, conn):
    bars = random_bars(1800, seed=2)
    a = selection.search(bars, cfg, conn, _fday(bars), "sha")
    b = selection.replay(bars, cfg, conn, _fday(bars), "sha")
    assert a["final"] == b["final"] and len(journal.trials(conn)) == 300
    with pytest.raises(AssertionError):
        selection.replay(random_bars(1800, seed=3), cfg, conn, _fday(bars), "sha")


def test_path_b_stays_finite_at_igarch_boundary(bars):
    prm = dict(synth.fit_b(bars), alpha=0.114, beta=0.886, omega=0.6, nu=3.2)
    for s in range(20):
        assert np.isfinite(synth.path_b(bars, prm, s)["close"]).all()


def test_replay_tolerates_rounding_but_not_real_changes(conn):
    a = {"sharpe": 1.0, "daily_sr": 0.05, "cagr": 0.1, "max_dd": -0.2, "trades_per_month": 2.0}
    near = {k: v * (1 + 1e-12) for k, v in a.items()}
    far = dict(a, sharpe=1.01)
    assert selection.metrics_close(a, near) and not selection.metrics_close(a, far)
    pairs = [(i, a, near) for i in range(1000)]
    assert selection.verify_replay(conn, pairs, "t") == []
    assert selection.verify_replay(conn, pairs[:-3] + [(997, a, far)] * 3, "t") == [997, 997, 997]
    assert journal.events(conn, "replay_mismatch")
    with pytest.raises(AssertionError):
        selection.verify_replay(conn, pairs[:-10] + [(990, a, far)] * 10, "t")
