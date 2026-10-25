import numpy as np
import pandas as pd

from tradelab import report


def _nets(n=800):
    rng = np.random.default_rng(0)
    idx = pd.date_range("2021-06-01", periods=n, freq="D", tz="UTC")
    return {k: rng.normal(0.0005, 0.02, n) for k in ("procedure", "bh", "vt")}, idx


def test_regime_table_marks_regimes_without_oos(cfg):
    nets, idx = _nets()
    t = report.regime_oos_table(nets, idx, cfg, pd.Timestamp("2025-10-25", tz="UTC"))
    assert set(t.loc[t.regime == "bear_2018", "sharpe"]) == {"нет OOS"}
    b = t[(t.regime == "bear_2022") & (t.curve == "bh")]
    assert int(b["days"].iloc[0]) > 30 and b["sharpe"].iloc[0] != "нет OOS"


def test_figures_written(tmp_path):
    nets, idx = _nets()
    report.plot_equity(nets, idx, tmp_path / "e.png")
    report.plot_drawdowns(nets, idx, tmp_path / "d.png")
    share = report.plot_null_hist(np.linspace(0, 2, 50), {"a": 1.0, "b": 1.0}, tmp_path / "h.png")
    report.plot_plateau(np.arange(6.0).reshape(3, 2) - 2, ["x", "y", "z"], ["u", "v"], (1, 1), tmp_path / "p.png", "t")
    assert all((tmp_path / f).stat().st_size > 5000 for f in ("e.png", "d.png", "h.png", "p.png"))
    assert abs(share - 0.5) < 0.03


def test_md_table():
    assert report.md_table(pd.DataFrame({"a": [1], "b": ["x"]})) == "| a | b |\n|---|---|\n| 1 | x |\n"
