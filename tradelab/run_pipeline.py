"""Entry point.
  python run_pipeline.py data        download, check, cut holdout, write sha256 to journal
  python run_pipeline.py benchmarks  benchmark report on pre-holdout ETH -> out/benchmarks.md
"""
import json
import sys

import pandas as pd

from tradelab import config, data, journal, parallel, report


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
    info = data.dataset_info(conn)
    start = pd.Timestamp(info["holdout_start"])
    sym = cfg["data"]["symbols_main"][0]
    bars = data.load_raw(sym)
    table = report.benchmark_table(bars, cfg, start, data.load_funding(sym))
    md = report.benchmarks_md(table, sym, bars.index[0], bars.index[-1])
    (data.ROOT / "out" / "benchmarks.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main(*sys.argv[1:])
