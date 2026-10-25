"""One command for everything:  python run_all.py

Runs every step that is not done yet, in order, each as `python run_pipeline.py ...`, logs to
out/run_all.log and finishes with out/FINAL_REPORT.md. Safe to stop (Ctrl+C) and start again: done
steps are recognised from the journal and out/oos, so nothing is computed twice and no trial is
added twice. A failed step is recorded and the run moves on; failing tests stop everything.
Paper trading is not started here (it runs until you stop it): see README.
"""
import os
import subprocess
import sys
import time

from tradelab import data, journal

ROOT = data.ROOT
OUT = ROOT / "out"
OOS = OUT / "oos"
LOG = OUT / "run_all.log"
EXPERIMENTS = [  # cheap first, the hourly ones last
    "eth_1d_long", "eth_1d_both", "eth_6h_both", "eth_1w_macd_long", "eth_1d_calendar_both", "eth_12h_both",
    "basket_1d_long", "basket_1d_both", "basket_1d_stat_both", "basket_1d_external_both", "eth_4h_both_htf1d",
    "eth_1h_calendar_both", "eth_1h_both_htf1d", "basket_4h_both_htf1d"]
steps = []


def say(text):
    print(text, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def run(step, *args, stop_on_fail=False):
    t0 = time.time()
    say(f"\n=== {time.strftime('%Y-%m-%d %H:%M')} {step}: python {' '.join(args)}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    tail = []
    for line in p.stdout:
        text = line.decode("utf-8", errors="replace").rstrip()
        say(text)
        tail = (tail + [text])[-3:]
    code = p.wait()
    status = "ok" if code == 0 else "ошибка"
    steps.append({"step": step, "status": status, "seconds": time.time() - t0, "note": "" if code == 0 else " | ".join(tail)})
    if code != 0 and stop_on_fail:
        finish()
        sys.exit(f"{step} не прошёл, дальше не идём")
    return code == 0


def has(conn, kind, **match):
    return any(all(e["payload"].get(k) == v for k, v in match.items()) for e in journal.events(conn, kind))


def finish():
    from tradelab import final_report

    conn = journal.connect()
    text = final_report.build(conn, steps, OUT)
    say("\n" + text)
    say(f"\nГотово. Пришлите файл {OUT / 'FINAL_REPORT.md'}")


def main():
    OUT.mkdir(exist_ok=True)
    run("тесты", "-m", "pytest", "-q", stop_on_fail=True)
    conn = journal.connect()
    if not journal.events(conn, "dataset"):
        run("данные", "run_pipeline.py", "data")
    if not journal.events(conn, "dataset_features"):
        run("признаки", "run_pipeline.py", "features")
    info = data.dataset_info(conn)
    need = [s for s in ("ETH/USDT", "BTC/USDT") for tf in ("12h", "4h", "1h") if not (data.RAW_DIR / data.fname(s, tf)).exists()]
    if need:
        run("свечи 12h/4h/1h", "run_pipeline.py", "bars", "12h,4h,1h", "ETH/USDT,BTC/USDT")
    if any(not (data.RAW_DIR / data.fname(s, "4h")).exists() for s in info["basket"]):
        run("свечи 4h корзины", "run_pipeline.py", "bars", "4h", "basket")
    if not journal.events(conn, "dataset_macro"):
        run("макро", "run_pipeline.py", "macro")
    if not journal.events(conn, "research_seasonality"):
        run("сезонность", "run_pipeline.py", "seasonality")
    if not (OOS / "grid.parquet").exists():
        run("этап 1 (повтор, без новых проб)", "run_pipeline.py", "select")
    if not (OOS / "generator.parquet").exists():
        if not journal.events(conn, "generator_calibration"):
            run("генератор: калибровка", "run_pipeline.py", "gen-calibrate")
        run("генератор: поиск", "run_pipeline.py", "gen-search")
    for name in EXPERIMENTS:
        if (OOS / f"exp_{name}.parquet").exists():
            continue
        if not has(conn, "experiment_calibration", experiment=name):
            run(f"{name}: калибровка", "run_pipeline.py", "exp-calibrate", name)
        cal = [e["payload"] for e in journal.events(conn, "experiment_calibration") if e["payload"]["experiment"] == name]
        if cal and cal[-1]["status"] == "ok":
            run(f"{name}: поиск", "run_pipeline.py", "exp-search", name)
        elif cal:
            steps.append({"step": f"{name}: поиск", "status": "пропущен", "seconds": 0,
                          "note": f"FDR {cal[-1]['fdr']:.2f} выше порога"})
    if not journal.events(conn, "dataset_perps"):
        run("этап 5: данные", "run_pipeline.py", "perps-data")
    if not journal.events(conn, "perps_calibration"):
        run("этап 5: калибровка", "run_pipeline.py", "perps-calibrate")
    if not (OOS / "perps.parquet").exists():
        run("этап 5: поиск", "run_pipeline.py", "perps-search")
    run("мульти-стратегия", "run_pipeline.py", "multi")
    finish()


if __name__ == "__main__":
    main()
