"""Entry point.
  python run_pipeline.py data        download, check, cut holdout, write sha256 to journal
  python run_pipeline.py benchmarks  benchmark report on pre-holdout ETH -> out/benchmarks.md
  python run_pipeline.py calibrate   selection procedure on synthetic paths A and C -> out/calibration.*
"""
import json
import sys
import time

import pandas as pd

from tradelab import config, costs, data, journal, parallel, report, synth


def main(cmd="benchmarks"):
    cfg = config.load()
    conn = journal.connect()
    print(f"config sha256 {config.lock(conn)}; workers {parallel.n_jobs()}")
    if cmd == "data":
        info = data.download(cfg, conn)
        print(json.dumps({k: info[k] for k in ("holdout_start", "basket", "dataset_sha256")}, indent=1))
        for name, rep in info["reports"].items():
            extra = {k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()}
            print(name, extra)
    if cmd == "calibrate":
        return calibrate(cfg, conn)
    info = data.dataset_info(conn)
    start = pd.Timestamp(info["holdout_start"])
    sym = cfg["data"]["symbols_main"][0]
    bars = data.load_raw(sym)
    table = report.benchmark_table(bars, cfg, start, data.load_funding(sym))
    md = report.benchmarks_md(table, sym, bars.index[0], bars.index[-1])
    (data.ROOT / "out" / "benchmarks.md").write_text(md)
    print(md)


FUNDING_HYPOTHESIS = ("Фильтр фундинга q=0.90: если перцентиль 7-дневной суммы фундинга в расширяющемся окне "
                      "больше 0.90, позиция 0. Гипотеза: перегретый лонг-фандинг предшествует слабой доходности.")


def calibrate(cfg, conn):
    if not journal.events(conn, "hypothesis"):
        journal.log_event(conn, "hypothesis", {"layer": "funding_filter", "text": FUNDING_HYPOTHESIS})
    info = data.dataset_info(conn)
    sym = cfg["data"]["symbols_main"][0]
    bars = data.load_raw(sym)
    fday = costs.funding_daily(bars.index, cfg["costs"], data.load_funding(sym)).to_numpy()
    t0 = time.time()
    res = synth.calibrate(bars, cfg, fday)
    secs = time.time() - t0
    v = synth.verdict(res, cfg)
    out = data.ROOT / "out"
    res.to_parquet(out / "calibration.parquet")
    journal.log_event(conn, "calibration", {**v, "paths": len(res), "seconds": round(secs, 1),
                                            "workers": parallel.n_jobs(),
                                            "dataset_sha256": info["dataset_sha256"]})
    g = res.groupby("phi")
    table = g.agg(paths=("finding", "size"), findings=("finding", "mean"), sharpe_oos=("sharpe_oos", "mean"),
                  sharpe_bh=("sharpe_bh", "mean"), ci_lo_pos=("ci_lo", lambda x: (x > 0).mean()),
                  dsr_pass=("dsr", lambda x: (x > cfg["thresholds"]["dsr_min"]).mean()),
                  cash_folds=("cash_folds", "mean"), best_is_sharpe=("best_is_sharpe", "mean"))
    md = (f"# Калибровка\n\nСтатус: **{v['status']}**. FDR = {v['fdr']:.3f} (порог {cfg['thresholds']['fdr_max']}), "
          f"мощность: {', '.join(f'phi={k}: {p:.3f}' for k, p in v['power'].items())}.\n"
          f"Путей: {len(res)}, время {secs:.0f} с на {parallel.n_jobs()} воркерах.\n\n"
          + report.md_table(table.round(3).reset_index()) + "\n")
    (out / "calibration.md").write_text(md)
    print(md)
    if v["status"] == "stop_fdr":
        print("СТОП: FDR выше порога. Нужно решение пользователя об ужесточении (например dsr_min или plateau).")
    elif v["status"] == "stop_engine":
        print("СТОП: мощность при phi=0.20 ниже 3 x FDR, движок не различает сигнал.")


if __name__ == "__main__":
    main(*sys.argv[1:])
