"""Walk-forward selection procedure (CLAUDE.md section 11.3)."""
import numpy as np

from tradelab import backtest


def folds(n, cfg):
    """(train_end, test_start, test_end) row positions; train = [0, train_end), embargo between."""
    w = cfg["walkforward"]
    out, ts = [], w["min_train_days"] + w["embargo_days"]
    while ts < n:
        out.append((ts - w["embargo_days"], ts, min(ts + w["test_days"], n)))
        ts += w["test_days"]
    return out


def sharpe_cols(x, ann=365):
    with np.errstate(divide="ignore", invalid="ignore"):
        sr = x.mean(0) / x.std(0, ddof=1) * np.sqrt(ann)
    return np.where(np.isfinite(sr), sr, -np.inf)


def plateau(sr, nbrs):
    """Mean neighbour Sharpe / own Sharpe; -inf where own Sharpe <= 0."""
    out = np.full(len(sr), -np.inf)
    for i, nb in enumerate(nbrs):
        if sr[i] > 0 and np.isfinite(sr[i]):
            out[i] = np.mean(sr[nb]) / sr[i]
    return out


def select(net, turnover, nbrs, cfg):
    """Config index with max Sharpe among plateau-ok, trade-limit-ok configs; -1 if none."""
    th = cfg["thresholds"]
    sr = sharpe_cols(net, cfg["annualization"])
    tpm = (turnover > 0).sum(0) / (len(net) / (365 / 12))
    ok = (plateau(sr, nbrs) >= th["plateau_min_ratio"]) & (tpm <= th["max_trades_per_month"]) & np.isfinite(sr)
    if not ok.any():
        return -1
    return int(np.argmax(np.where(ok, sr, -np.inf)))


def run(bars, signals, cfg, funding_day, nbrs):
    """signals: T x N targets. Returns per-fold choices and the stitched OOS pnl.

    The stitched position is rebuilt from the chosen configs' positions and run through the
    same cost model, so switches between configs and the first entry pay costs. A fold with
    no eligible config holds cash.
    """
    pos = backtest.positions(signals, cfg)
    p = backtest.pnl(bars, pos, cfg, funding_day)
    net, turnover = p["net"][:-1], p["turnover"][:-1]
    stitched = np.zeros(len(bars))
    choices, oos = [], []
    for tr_end, ts, te in folds(len(net), cfg):
        j = select(net[:tr_end], turnover[:tr_end], nbrs, cfg)
        choices.append(j)
        if j >= 0:
            stitched[ts:te] = pos[ts:te, j]
        oos.append(np.arange(ts, te))
    oos = np.concatenate(oos)
    proc = backtest.pnl(bars, stitched, cfg, funding_day)
    return {"choices": choices, "oos": oos, "net_oos": proc["net"][oos], "pos": stitched,
            "net_all": net, "turnover_all": turnover}
