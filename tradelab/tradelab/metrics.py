from itertools import combinations

import numpy as np
from arch.bootstrap import StationaryBootstrap
from scipy import stats

EULER_GAMMA = 0.5772


def sharpe(net, ann=365):
    net = np.asarray(net, dtype=float)
    return net.mean() / net.std(ddof=1) * np.sqrt(ann)


def equity(net):
    return np.cumprod(1.0 + np.asarray(net, dtype=float))


def cagr(net, ann=365):
    eq = equity(net)
    return eq[-1] ** (ann / len(eq)) - 1.0


def max_drawdown(net):
    eq = equity(net)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float((eq / peak - 1.0).min())


def calmar(net, ann=365):
    return cagr(net, ann) / abs(max_drawdown(net))


def psr(net, sr0=0.0):
    """Probabilistic Sharpe ratio on daily non-annualized SR; g4 is plain (non-excess) kurtosis."""
    net = np.asarray(net, dtype=float)
    t = len(net)
    sr = net.mean() / net.std(ddof=1)
    g3 = stats.skew(net)
    g4 = stats.kurtosis(net, fisher=False)
    return float(stats.norm.cdf((sr - sr0) * np.sqrt(t - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr**2)))


def expected_max_sr(n_trials, var_sr):
    """SR0 of the deflated Sharpe ratio: expected max of N trial SRs under the null."""
    g = EULER_GAMMA
    return float(np.sqrt(var_sr) * ((1 - g) * stats.norm.ppf(1 - 1 / n_trials)
                                    + g * stats.norm.ppf(1 - 1 / (n_trials * np.e))))


def dsr(net, trial_daily_srs):
    """Deflated Sharpe ratio. trial_daily_srs: daily SRs of all real-data trials (N from the journal)."""
    srs = np.asarray(trial_daily_srs, dtype=float)
    return psr(net, expected_max_sr(len(srs), srs.var(ddof=1)))


def pbo(returns, n_blocks=16):
    """Probability of backtest overfitting by CSCV. returns: T x N daily net returns."""
    x = np.asarray(returns, dtype=float)
    t, n = x.shape
    bounds = np.linspace(0, t, n_blocks + 1).astype(int)
    blocks = [x[a:b] for a, b in zip(bounds[:-1], bounds[1:])]
    cnt = np.array([len(b) for b in blocks], dtype=float)
    s1 = np.stack([b.sum(0) for b in blocks])
    s2 = np.stack([(b**2).sum(0) for b in blocks])

    splits = np.array(list(combinations(range(n_blocks), n_blocks // 2)))
    mask = np.zeros((len(splits), n_blocks))
    mask[np.arange(len(splits))[:, None], splits] = 1.0

    def half_sr(m):
        k = (m @ cnt)[:, None]
        mu = (m @ s1) / k
        var = ((m @ s2) - k * mu**2) / (k - 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            sr = mu / np.sqrt(var)
        return np.where(np.isfinite(sr), sr, -np.inf)

    sr_is = half_sr(mask)
    sr_oos = half_sr(1.0 - mask)
    best = sr_is.argmax(1)
    rank = (sr_oos < sr_oos[np.arange(len(best)), best][:, None]).sum(1) + 1
    omega = rank / (n + 1)
    lam = np.log(omega / (1 - omega))
    return float((lam <= 0).mean())


def sharpe_diff_ci(net_a, net_b, level=0.90, block=20, reps=1000, seed=42, ann=365):
    """Stationary bootstrap CI of sharpe(a) - sharpe(b) with shared indices."""
    a = np.asarray(net_a, dtype=float)
    b = np.asarray(net_b, dtype=float)
    bs = StationaryBootstrap(block, a, b, seed=np.random.default_rng(seed))
    diffs = bs.apply(lambda x, y: np.array([sharpe(x, ann) - sharpe(y, ann)]), reps).ravel()
    lo, hi = np.quantile(diffs, [(1 - level) / 2, (1 + level) / 2])
    return {"diff": sharpe(a, ann) - sharpe(b, ann), "lo": float(lo), "hi": float(hi)}
