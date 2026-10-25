import numpy as np
import pytest

from tradelab import metrics, parallel


def test_dsr_expected_max(cfg):
    rng = np.random.default_rng(cfg["seed"])
    sims = rng.standard_normal((10000, 100)).max(1).mean()
    formula = metrics.expected_max_sr(100, 1.0)
    assert abs(formula / sims - 1) < 0.05


def test_pbo_noise(cfg):
    rng = np.random.default_rng(cfg["seed"])
    x = rng.normal(0, 0.01, (2000, 50))
    assert 0.35 <= metrics.pbo(x) <= 0.65


def test_pbo_drift(cfg):
    rng = np.random.default_rng(cfg["seed"])
    x = rng.normal(0, 0.01, (2000, 50))
    x[:, 7] += 0.5 * 0.01
    assert metrics.pbo(x) < 0.10


def test_basic_metrics():
    net = np.array([0.1, -0.5, 0.2])
    assert metrics.max_drawdown(net) == pytest.approx(-0.5)
    assert metrics.cagr(np.zeros(365)) == 0
    assert metrics.psr(np.random.default_rng(1).normal(0.01, 0.01, 500)) > 0.99


def test_bootstrap_ci_seeded():
    rng = np.random.default_rng(3)
    a, b = rng.normal(0.001, 0.02, 800), rng.normal(0, 0.02, 800)
    r1 = metrics.sharpe_diff_ci(a, b, reps=200, seed=42)
    r2 = metrics.sharpe_diff_ci(a, b, reps=200, seed=42)
    assert r1 == r2 and r1["lo"] < r1["diff"] < r1["hi"]


def _job(seed):
    return np.random.default_rng(seed).normal(size=1000).sum()


def test_parallel_matches_sequential():
    seeds = parallel.job_seeds(42, 24)
    assert parallel.pmap(_job, seeds, jobs=1) == parallel.pmap(_job, seeds, jobs=4)
