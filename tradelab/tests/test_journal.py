import sqlite3

import pandas as pd
import pytest

from tradelab import config, holdout, journal


def test_journal_append_only(conn):
    assert not [n for n in dir(journal) if "update" in n.lower() or "delete" in n.lower()]
    tid = journal.start_trial(conn, {"id": "x"}, ["2018", "2024"], "abc")
    assert journal.trials(conn)[0]["metrics"] == {"status": "started"}
    assert not journal.events(conn, "trial_metrics")  # trial exists before metrics
    journal.finish_trial(conn, tid, {"sharpe": 1.0})
    assert journal.events(conn, "trial_metrics")[0]["payload"]["trial_id"] == tid
    for sql in ("UPDATE trials SET metrics_json='{}'", "DELETE FROM trials",
                "UPDATE events SET kind='x'", "DELETE FROM events"):
        with pytest.raises(sqlite3.DatabaseError):
            with conn:
                conn.execute(sql)


def test_holdout_guard(conn, tmp_path):
    p = tmp_path / "ETH_USDT_1d.parquet"
    pd.DataFrame({"close": [1.0, 2.0]}).to_parquet(p)
    with pytest.raises(holdout.HoldoutViolation):
        holdout.load(conn=conn, path=tmp_path)
    assert len(journal.events(conn, "holdout_violation")) == 1
    data = holdout.load(allow=True, conn=conn, path=tmp_path)
    assert list(data) == ["ETH_USDT_1d"]
    with pytest.raises(holdout.HoldoutViolation):
        holdout.load(allow=True, conn=conn, path=tmp_path)
    assert len(journal.events(conn, "holdout_violation")) == 2


def test_config_lock(conn, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(config.DEFAULT_PATH.read_bytes())
    h = config.lock(conn, p)
    assert config.lock(conn, p) == h
    p.write_text(p.read_text().replace("seed: 42", "seed: 43"))
    with pytest.raises(config.ConfigChanged):
        config.lock(conn, p)


def test_connect_reads_default_path_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DEFAULT_PATH", tmp_path / "j.sqlite")
    journal.connect().close()
    assert (tmp_path / "j.sqlite").exists()


def _sel(passed):
    return {"passed": passed, "checks": {"dsr": passed, "pbo": True},
            "candidate": {"after_ablation": "tsmom_60|v=None|p=None|q=None"}}


def test_final_does_not_touch_holdout_when_checks_fail(cfg, conn, tmp_path):
    out = holdout.final(cfg, conn, _sel(False), None, None, path=tmp_path)
    assert out["verdict"] == holdout.NOT_FOUND and out["holdout_read"] is False and "dsr" in out["reason"]
    assert not journal.events(conn, "holdout_read") and not journal.events(conn, "holdout_violation")
    with pytest.raises(holdout.HoldoutViolation):
        holdout.final(cfg, conn, _sel(False), None, None, path=tmp_path)


def test_final_reads_holdout_once_when_checks_pass(cfg, conn, tmp_path):
    from conftest import random_bars

    bars = random_bars(1200, seed=11)
    raw, hold = bars.iloc[:900], bars.iloc[900:]
    hold.to_parquet(tmp_path / "ETH_USDT_1d.parquet")
    out = holdout.final(cfg, conn, _sel(True), raw, None, path=tmp_path)
    assert out["holdout_read"] and out["verdict"] in (holdout.FOUND, holdout.NOT_FOUND)
    assert out["holdout"]["candidate"]["days"] == 299 and len(journal.events(conn, "holdout_read")) == 1
    with pytest.raises(holdout.HoldoutViolation):
        holdout.load(allow=True, conn=conn, path=tmp_path)
