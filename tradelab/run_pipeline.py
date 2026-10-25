"""Entry point.
  python run_pipeline.py data        download, check, cut holdout, write sha256 to journal
  python run_pipeline.py benchmarks  benchmark report on pre-holdout ETH -> out/benchmarks.md
  python run_pipeline.py calibrate   selection procedure on synthetic paths A and C -> out/calibration.*
  python run_pipeline.py select      real-data search (once; later runs replay it from the journal
                                     without new trials), final candidate and checks -> out/selection.*
  python run_pipeline.py report      full report -> out/report.md and png figures
  python run_pipeline.py final       step 6: holdout only if checks passed, verdict -> out/verdict.md
"""
import json
import sys
import time

import pandas as pd

from tradelab import config, costs, data, holdout, journal, parallel, report, selection, synth


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
    if cmd == "select":
        return select(cfg, conn)
    if cmd == "final":
        return final(cfg, conn)
    if cmd == "report":
        md = report.build(cfg, conn, data.ROOT / "out")
        return print(md)
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


def select(cfg, conn):
    cal = journal.events(conn, "calibration")
    if not cal or cal[-1]["payload"]["status"] != "ok":
        raise SystemExit("калибровка не пройдена или не запускалась: поиск запрещён")
    info = data.dataset_info(conn)
    sha = info["dataset_sha256"]
    sym = cfg["data"]["symbols_main"][0]
    bars = data.load_raw(sym)
    fday = costs.funding_daily(bars.index, cfg["costs"], data.load_funding(sym)).to_numpy()
    t0 = time.time()
    replayed = any(t["dataset_sha256"] == sha for t in journal.trials(conn))
    found = (selection.replay if replayed else selection.search)(bars, cfg, conn, fday, sha)
    basket = {s: data.load_raw(s) for s in info["basket"]}
    funding = {s: data.load_funding(s) for s in info["basket"]}
    r = selection.evaluate(bars, cfg, conn, fday, found, sha, cal[-1]["payload"]["fdr"], basket,
                           {k: v for k, v in funding.items() if v is not None})
    facts = r.pop("facts")
    r["seconds"] = round(time.time() - t0, 1)
    r["replay"] = replayed
    journal.log_event(conn, "selection_replay" if replayed else "selection", r)
    out = data.ROOT / "out"
    (out / "selection.json").write_text(json.dumps(r, indent=1, ensure_ascii=False, default=float))
    md = selection_md(r, facts, cfg)
    (out / "selection.md").write_text(md)
    print(md)


def selection_md(r, facts, cfg):
    f = lambda x: f"{x:.3f}"
    rows = [{"curve": k, **{m: f(v) for m, v in r[k].items()}} for k in ("procedure", "bh", "vt")]
    checks = pd.DataFrame([{"check": k, "ok": "да" if v else "нет"} for k, v in r["checks"].items()])
    c = r["candidate"]
    cand = "нет допустимой конфигурации" if c is None else (
        f"{c['selected']} -> после абляции {c['after_ablation']}; плато {f(c['plateau'])}; "
        f"переносимость {f(c['transfer_share'])}; стресс: бутстрэп {f(c['stress']['boot_positive_share'])}, "
        f"x2 издержки {f(c['stress']['double_costs_sharpe'])}, задержка 2 {f(c['stress']['delay2_sharpe'])}, "
        f"соседи {f(c['stress']['neighbor_positive_share'])}, синтетика B {f(c['stress']['synth_b_positive_share'])}")
    return (f"# Отбор на реальных данных\n\nВердикт: **{r['verdict']}**\n\n"
            f"N проб = {r['n_trials']}, OOS {r['oos_days']} дней с {r['oos_start']}. DSR {f(r['dsr'])}, "
            f"PBO {f(r['pbo'])}, FDR {f(r['fdr'])}. ДИ {cfg['thresholds']['ci_level']:.0%} разницы Шарпов: "
            f"против bh [{f(r['ci_vs_bh']['lo'])}, {f(r['ci_vs_bh']['hi'])}], "
            f"против vt [{f(r['ci_vs_vt']['lo'])}, {f(r['ci_vs_vt']['hi'])}].\n"
            f"Месяцы > {cfg['thresholds']['monthly_return_flag']:.0%} (подозрение на подгонку): "
            f"{', '.join(r['monthly_flags']) or 'нет'}.\n\n"
            f"Кандидат: {cand}.\n\n## Склеенный OOS\n\n" + report.md_table(pd.DataFrame(rows))
            + "\n## Проверки\n\n" + report.md_table(checks)
            + "\n## Стилизованные факты: реальные против синтетики B\n\n"
            + report.md_table(facts.round(3).reset_index().rename(columns={"index": "fact"})))


DEVIATIONS = [
    "Запрет 1 (только ccxt): фандинг взят из публичного архива Binance data.binance.vision, не через ccxt; "
    "fapi.binance.com отвечает 451. Одобрено пользователем. Спот загружен через ccxt, но с зеркала data-api.binance.vision.",
    "Структура проекта: добавлен модуль tradelab/parallel.py (пул joblib, сиды через SeedSequence); "
    "в config.yaml число воркеров не пишется.",
    "Запрет 5 (журнал только дописывается): до официального поиска пробный прогон по ошибке записал в journal.sqlite "
    "синтетические пробы; файл возвращён к закоммиченному состоянию через git, ошибка исправлена, добавлен тест.",
    "Веха 4: после официального поиска исправлена ошибка синтетики B (стартовая дисперсия GARCH); проверки повторены "
    "командой replay без новых проб, вердикт не изменился. В журнале оба события: selection и selection_replay.",
    "Калибровка останавливается и при нулевой мощности при phi = 0.20 (строже раздела 11.2).",
    "Стресс считается пройденным, только если пройдены все пять частей (раздел 13 требует только долю путей).",
    "MaxDD и сделки в месяц проверяются и на склеенном OOS, и на полной истории кандидата.",
    "Хэш config фиксируется при первом запуске run_pipeline.py, раньше старта поиска.",
    "Holdout не прочитан: пункт 5 конвейера не пройден, поэтому пункт 6 не выполнялся (так требует раздел 11.6).",
]


def final(cfg, conn):
    sel_ev = journal.events(conn, "selection_replay") or journal.events(conn, "selection")
    if not sel_ev:
        raise SystemExit("отбор не запускался")
    sel = sel_ev[-1]["payload"]
    sym = cfg["data"]["symbols_main"][0]
    out = holdout.final(cfg, conn, sel, data.load_raw(sym), data.load_funding(sym), sym)
    th = cfg["thresholds"]
    rows = [{"критерий раздела 13": k, "итог": "да" if v else "нет"} for k, v in sel["checks"].items()]
    if out["holdout_read"]:
        h = out["holdout"]
        for b in ("bh", "vt"):
            for m in ("sharpe", "calmar"):
                rows.append({"критерий раздела 13": f"holdout_{m}_not_below_{b}",
                             "итог": "да" if h["candidate"][m] >= h[b][m] else "нет"})
    else:
        rows.append({"критерий раздела 13": "holdout: Шарп и Кальмар не ниже bh и vt", "итог": "не проверялся"})
    md = "\n".join([
        "# tradelab: итог", "", f"**Вердикт: {out['verdict']}.**", "",
        f"Holdout прочитан: {'да' if out['holdout_read'] else 'нет'}. " + out.get("reason", ""), "",
        "## Допуск по разделу 13", "", report.md_table(pd.DataFrame(rows)),
        "## Отклонения от CLAUDE.md", "", *[f"{i}. {d}" for i, d in enumerate(DEVIATIONS, 1)], "",
        f"Подробности: report.md, selection.md, calibration.md, DECISIONS.md. Пороги: {th}.", ""])
    (data.ROOT / "out" / "verdict.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main(*sys.argv[1:])
