from pathlib import Path

import pandas as pd

from tradelab import journal

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "holdout"


class HoldoutViolation(Exception):
    pass


def load(allow=False, *, conn, path=DEFAULT_DIR):
    """The only reader of holdout data. allow=True works exactly once per journal."""
    if not allow:
        journal.log_event(conn, "holdout_violation", {"reason": "allow=False", "path": str(path)})
        raise HoldoutViolation("holdout read without allow=True")
    if journal.events(conn, "holdout_read"):
        journal.log_event(conn, "holdout_violation", {"reason": "second read", "path": str(path)})
        raise HoldoutViolation("holdout already read once")
    journal.log_event(conn, "holdout_read", {"path": str(path)})
    path = Path(path)
    files = [path] if path.is_file() else sorted(path.glob("*.parquet"))
    return {f.stem: pd.read_parquet(f) for f in files}


FOUND = "стратегия, бьющая удержание, найдена"
NOT_FOUND = "не найдена"


def _curves(bars, cfg, conf, funding, start):
    """Candidate, bh and vt on the holdout rows; signals warm up on the history before `start`."""
    import numpy as np

    from tradelab import backtest, costs, metrics, strategies, stress

    fday = costs.funding_daily(bars.index, cfg["costs"], funding).to_numpy()
    rows = np.where(bars.index[:-1] >= start)[0]
    nets = {"candidate": stress.candidate_net(bars, cfg, fday, conf)[rows]}
    for name, fn in (("bh", strategies.bh), ("vt", strategies.vt)):
        nets[name] = backtest.pnl(bars, backtest.positions(fn(bars, cfg).to_numpy(), cfg), cfg, fday)["net"][:-1][rows]
    return {k: {"sharpe": float(metrics.sharpe(v)), "calmar": float(metrics.calmar(v)),
                "max_dd": float(metrics.max_drawdown(v)), "days": len(v)} for k, v in nets.items()}


def final(cfg, conn, sel, raw_bars, raw_funding, symbol="ETH/USDT", path=DEFAULT_DIR):
    """Pipeline step 6: the holdout is read once, and only if the pre-holdout checks passed."""
    from tradelab import grid

    if journal.events(conn, "final_verdict"):
        raise HoldoutViolation("final verdict already recorded")
    if not sel["passed"]:
        out = {"verdict": NOT_FOUND, "holdout_read": False,
               "reason": "проверки до holdout не пройдены: " + ", ".join(k for k, v in sel["checks"].items() if not v)}
        journal.log_event(conn, "final_verdict", out)
        return out
    conf = {grid.config_id(c): c for c in grid.configs()}[sel["candidate"]["after_ablation"]]
    hold = load(allow=True, conn=conn, path=path)
    stem = symbol.replace("/", "_")
    bars = pd.concat([raw_bars, hold[f"{stem}_1d"]])
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    fh = hold.get(f"{stem}_funding")
    funding = pd.concat([raw_funding, fh["funding_rate"]]) if fh is not None and raw_funding is not None else raw_funding
    start = hold[f"{stem}_1d"].index[0]
    cur = _curves(bars, cfg, conf, funding, start)
    c = cur["candidate"]
    ok = all(c[m] >= cur[b][m] for b in ("bh", "vt") for m in ("sharpe", "calmar"))
    out = {"verdict": FOUND if ok else NOT_FOUND, "holdout_read": True, "candidate": sel["candidate"]["after_ablation"],
           "holdout": cur, "holdout_start": str(start)}
    journal.log_event(conn, "final_verdict", out)
    return out
