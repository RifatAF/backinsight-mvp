"""Entry point.
  python run_pipeline.py data        download, check, cut holdout, write sha256 to journal
  python run_pipeline.py benchmarks  benchmark report on pre-holdout ETH -> out/benchmarks.md
  python run_pipeline.py calibrate   selection procedure on synthetic paths A and C -> out/calibration.*
  python run_pipeline.py select      real-data search (once; later runs replay it from the journal
                                     without new trials), final candidate and checks -> out/selection.*
  python run_pipeline.py report      full report -> out/report.md and png figures
  python run_pipeline.py final       step 6: holdout only if checks passed, verdict -> out/verdict.md

Stage 2, strategy generator (settings in generator.yaml):
  python run_pipeline.py gen-calibrate  generator procedure on bootstrap paths: FDR and power -> out/gen_calibration.md
  python run_pipeline.py gen-search     all generated specs on real data (journaled once), walk-forward, checks
                                        -> out/generator.md, out/generator_leaderboard.csv, out/gen_equity.png
  python run_pipeline.py gen-holdout    one-time holdout run of the generator candidate, only if its checks passed

Stage 3, experiments with derivatives / sentiment features, shorts, 6h bars and basket portfolios:
  python run_pipeline.py features              download OI, long/short ratios, taker ratio, basis, funding,
                                               Coinbase close, Fear & Greed -> data/raw/*_features.parquet
  python run_pipeline.py bars 4h,1h basket     extra timeframes (12h, 6h, 4h, 1h) for ETH/USDT,BTC/USDT or "basket"
  python run_pipeline.py exp-calibrate NAME    experiment procedure on bootstrap paths (FDR, power)
  python run_pipeline.py exp-search NAME       journaled search + checks -> out/exp_NAME.md
  python run_pipeline.py exp-summary           table of all experiments -> out/experiments.md
  python run_pipeline.py exp-holdout NAME      one-time holdout run, only if that experiment passed its checks
NAME is a file in experiments/ without .yaml.

Stage 4, macro / calendar / statistics:
  python run_pipeline.py macro         S&P 500, Nasdaq, Nikkei, gold, dollar index, USD/JPY, US 3m and 10y
                                       yields, VIX, MOVE (Yahoo Finance) -> data/raw/macro.parquet
  python run_pipeline.py seasonality   weekday / month / hour effects with multiple-testing correction
                                       -> out/seasonality.md (descriptive, no trials)
"""
import hashlib
import json
import sys
import time

import pandas as pd

from tradelab import (config, costs, data, experiment, features, generator, holdout, journal, macro, parallel, report,
                      seasonality, selection, synth)


def main(cmd="benchmarks", name=None, *_):
    cfg = config.load()
    conn = journal.connect()
    print(f"config sha256 {config.lock(conn)}; workers {parallel.n_jobs()}", flush=True)
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
    if cmd == "bars":
        info = data.dataset_info(conn)
        tfs = name.split(",")
        syms = (list(dict.fromkeys(cfg["data"]["symbols_main"] + info["basket"])) if len(sys.argv) > 3 and sys.argv[3] == "basket"
                else sys.argv[3].split(",") if len(sys.argv) > 3 else cfg["data"]["symbols_main"])
        out = data.download_bars(cfg, conn, tfs, syms)
        return print(json.dumps(out["reports"], indent=1))
    if cmd == "features":
        info = features.download(cfg, conn)
        return print(json.dumps(info["first_valid"], indent=1))
    if cmd == "macro":
        info = macro.download(cfg, conn)
        return print(json.dumps(info["first_valid"], indent=1))
    if cmd == "seasonality":
        return print(season(cfg, conn))
    if cmd.startswith("exp-"):
        return exp(cmd, name, cfg, conn)
    if cmd.startswith("gen-"):
        return gen(cmd, cfg, conn)
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
        f"Holdout прочитан: {'да' if out['holdout_read'] else 'нет'}."
        + (f" Причина: {out['reason']}." if "reason" in out else ""), "",
        "## Допуск по разделу 13", "", report.md_table(pd.DataFrame(rows)),
        "## Отклонения от CLAUDE.md", "", *[f"{i}. {d}" for i, d in enumerate(DEVIATIONS, 1)], "",
        f"Подробности: report.md, selection.md, calibration.md, DECISIONS.md. Пороги: {th}.", ""])
    (data.ROOT / "out" / "verdict.md").write_text(md)
    print(md)


def _eth(cfg):
    sym = cfg["data"]["symbols_main"][0]
    bars = data.load_raw(sym)
    return sym, bars, costs.funding_daily(bars.index, cfg["costs"], data.load_funding(sym)).to_numpy()


def gen(cmd, cfg, conn):
    gcfg, gsha = generator.load_config()
    generator.lock(conn, gsha)
    info = data.dataset_info(conn)
    sym, bars, fday = _eth(cfg)
    specs = generator.generate(gcfg)
    out = data.ROOT / "out"
    th = cfg["thresholds"]
    if cmd == "gen-calibrate":
        t0 = time.time()
        res = generator.calibrate(bars, cfg, fday, specs, gcfg)
        res.to_parquet(out / "gen_calibration.parquet")
        fdr = float(res.loc[res.phi == 0, "finding"].mean())
        power = float(res.loc[res.phi > 0, "finding"].mean())
        status = "stop_fdr" if fdr > th["fdr_max"] else "ok"
        payload = {"fdr": fdr, "power_phi_0.20": power, "paths": int((res.phi == 0).sum()), "status": status,
                   "n_specs": len(specs), "seconds": round(time.time() - t0, 1), "generator_sha256": gsha}
        journal.log_event(conn, "generator_calibration", payload)
        md = (f"# Калибровка генератора\n\nСтатус: **{status}**. На {payload['paths']} нулевых путях A генератор "
              f"из {len(specs)} стратегий «находит» стратегию в {fdr:.1%} случаев (порог {th['fdr_max']:.0%}). "
              f"Мощность на путях C (AR phi=0.20): {power:.1%}. Время {payload['seconds']:.0f} с на {parallel.n_jobs()} воркерах.\n")
        (out / "gen_calibration.md").write_text(md)
        return print(md)
    cal = journal.events(conn, "generator_calibration")
    if cmd == "gen-search":
        if not cal or cal[-1]["payload"]["status"] != "ok":
            raise SystemExit("сначала gen-calibrate; поиск запрещён, пока FDR генератора выше порога")
        t0 = time.time()
        pos, net, trades, replayed = generator.search(bars, cfg, conn, fday, specs, info["dataset_sha256"], gsha)
        wf = generator.walk_forward(bars, cfg, fday, specs, pos, net, trades, gcfg)
        basket = {s: data.load_raw(s) for s in info["basket"]}
        fund = {s: f for s in info["basket"] if (f := data.load_funding(s)) is not None}
        r = generator.check(bars, cfg, conn, fday, specs, net, wf, info["dataset_sha256"],
                            cal[-1]["payload"]["fdr"], basket, fund)
        r["seconds"] = round(time.time() - t0, 1)
        r["replay"] = replayed
        journal.log_event(conn, "generator_selection_replay" if replayed else "generator_selection", r)
        lb = generator.leaderboard(specs, net, trades, gcfg, cfg, bars.index)
        lb.to_csv(out / "generator_leaderboard.csv", index=False)
        oos = wf["oos"]
        nets = {"procedure": wf["net_oos"]}
        for name, fn in (("bh", strategies_bh), ("vt", strategies_vt)):
            nets[name] = backtest_net(bars, cfg, fday, fn)[oos]
        report.plot_equity(nets, bars.index[oos], out / "gen_equity.png")
        md = gen_md(r, lb, gcfg, cfg)
        (out / "generator.md").write_text(md)
        return print(md)
    if cmd == "gen-holdout":
        ev = journal.events(conn, "generator_selection_replay") or journal.events(conn, "generator_selection")
        if not ev:
            raise SystemExit("сначала gen-search")
        r = ev[-1]["payload"]
        if journal.events(conn, "generator_final_verdict"):
            raise SystemExit("вердикт генератора уже записан")
        if not r["passed"]:
            res = {"verdict": holdout.NOT_FOUND, "holdout_read": False,
                   "reason": ", ".join(k for k, v in r["checks"].items() if not v)}
        else:
            spec = r["candidate"]["spec"]
            res = holdout.run_once(cfg, conn, generator.spec_fn(spec), r["candidate"]["after_ablation"],
                                   bars, data.load_funding(sym), sym)
        journal.log_event(conn, "generator_final_verdict", res)
        md = f"# Генератор: итог\n\n**Вердикт: {res['verdict']}.** Holdout прочитан: {'да' if res['holdout_read'] else 'нет'}.\n"
        md += (f"\nНе пройдены проверки: {res['reason']}.\n" if not res["holdout_read"] else
               "\n" + report.md_table(pd.DataFrame([{"curve": k, **{m: f'{v:.3f}' for m, v in x.items()}}
                                                     for k, x in res["holdout"].items()])))
        (out / "gen_verdict.md").write_text(md)
        return print(md)
    raise SystemExit(f"неизвестная команда {cmd}")


def strategies_bh(bars, cfg):
    from tradelab import strategies
    return strategies.bh(bars, cfg)


def strategies_vt(bars, cfg):
    from tradelab import strategies
    return strategies.vt(bars, cfg)


def backtest_net(bars, cfg, fday, fn):
    from tradelab import backtest
    return backtest.pnl(bars, backtest.positions(fn(bars, cfg).to_numpy(), cfg), cfg, fday)["net"][:-1]


def gen_md(r, lb, gcfg, cfg):
    f = lambda x: f"{x:.3f}"
    th = cfg["thresholds"]
    rows = [{"curve": k, **{m: f(v) for m, v in r[k].items()}} for k in ("procedure", "bh", "vt")]
    checks = pd.DataFrame([{"check": k, "ok": "да" if v else "нет"} for k, v in r["checks"].items()])
    c = r["candidate"]
    top = lb.head(15).copy()
    for col in ("sharpe_is", "cagr_is", "max_dd_is", "trades_per_month"):
        top[col] = top[col].map(f)
    cand = "нет" if c is None else (
        f"`{c['after_ablation']}`; полная история: Шарп {f(c['full_sample']['sharpe'])}, MaxDD "
        f"{c['full_sample']['max_dd']:.1%}; плато {f(c['plateau'])}; переносимость {f(c['transfer_share'])}; "
        f"стресс {'пройден' if c['stress']['passed'] else 'не пройден'}")
    return (f"# Генератор стратегий: отбор\n\nВердикт до holdout: **{r['verdict']}**\n\n"
            f"Сгенерировано {r['n_generated']} стратегий из {lb['family'].nunique()} семейств; всего проб в журнале "
            f"на этих данных {r['n_trials_total']} (DSR учитывает все). «Не сливают» на всей истории: "
            f"{int(lb['does_not_lose'].sum())}. На фолдах фильтр проходили {min(r['fold_survivors'])}..{max(r['fold_survivors'])}.\n\n"
            f"Склеенный OOS {r['oos_days']} дней с {r['oos_start']}: DSR {f(r['dsr'])} (порог {th['dsr_min']}), "
            f"PBO {f(r['pbo'])}, FDR генератора {f(r['fdr'])}. ДИ разницы Шарпов: против bh "
            f"[{f(r['ci_vs_bh']['lo'])}, {f(r['ci_vs_bh']['hi'])}], против vt [{f(r['ci_vs_vt']['lo'])}, {f(r['ci_vs_vt']['hi'])}].\n\n"
            f"Кандидат: {cand}.\n\n" + report.md_table(pd.DataFrame(rows)) + "\n![капитал](gen_equity.png)\n\n## Проверки\n\n"
            + report.md_table(checks)
            + "\n## Топ-15 по Шарпу на всей истории (in-sample, только для обзора: этот список переобучен)\n\n"
            + report.md_table(top) + "\nПолная таблица: generator_leaderboard.csv.\n")


def exp_events(conn, kind, name):
    return [ev for ev in journal.events(conn, kind) if ev["payload"].get("experiment") == name]


def exp(cmd, name, cfg, conn):
    out = data.ROOT / "out"
    if cmd == "exp-summary":
        return print(exp_summary(conn, out))
    e, esha = experiment.load(name)
    lock = exp_events(conn, "experiment_sha256", name)
    if not lock:
        journal.log_event(conn, "experiment_sha256", {"experiment": name, "sha256": esha})
    elif lock[0]["payload"]["sha256"] != esha:
        raise SystemExit(f"experiments/{name}.yaml изменён после первого запуска: создайте новый эксперимент с другим именем")
    info = data.dataset_info(conn)
    fe = journal.events(conn, "dataset_features")
    if not fe:
        raise SystemExit("сначала: python run_pipeline.py features")
    sha = hashlib.sha256((info["dataset_sha256"] + fe[-1]["payload"]["features_sha256"]).encode()).hexdigest()
    gcfg = experiment.gen_config(e)
    specs = experiment.specs(e, gcfg)
    if experiment.uses_macro(e, specs):
        me = journal.events(conn, "dataset_macro")
        if not me:
            raise SystemExit("сначала: python run_pipeline.py macro")
        sha = hashlib.sha256((sha + me[-1]["payload"]["macro_sha256"]).encode()).hexdigest()
    coins = experiment.load_coins(e, cfg, info)
    days = experiment.days_index(coins)
    th = cfg["thresholds"]
    if cmd == "exp-calibrate":
        t0 = time.time()
        res = experiment.calibrate(e, coins, cfg, days, specs, gcfg)
        res.to_parquet(out / f"exp_{name}_calibration.parquet")
        fdr = float(res.loc[res.phi == 0, "finding"].mean())
        power = float(res.loc[res.phi > 0, "finding"].mean())
        status = "stop_fdr" if fdr > th["fdr_max"] else "ok"
        payload = {"experiment": name, "fdr": fdr, "power_phi_0.20": power, "paths": int((res.phi == 0).sum()),
                   "status": status, "seconds": round(time.time() - t0, 1)}
        journal.log_event(conn, "experiment_calibration", payload)
        return print(json.dumps(payload, ensure_ascii=False))
    if cmd == "exp-search":
        cal = exp_events(conn, "experiment_calibration", name)
        if not cal or cal[-1]["payload"]["status"] != "ok":
            raise SystemExit(f"сначала exp-calibrate {name}; поиск запрещён, пока FDR выше порога")
        t0 = time.time()
        net, trades, replayed = experiment.search(e, esha, coins, cfg, conn, specs, days, sha)
        wf = experiment.walk_forward(coins, cfg, days, specs, net, trades, gcfg)
        r = experiment.check(e, coins, cfg, conn, days, specs, net, wf, cal[-1]["payload"]["fdr"],
                             experiment.transfer_coins(e, cfg, info, coins))
        r["seconds"] = round(time.time() - t0, 1)
        r["replay"] = replayed
        r["power_phi_0.20"] = cal[-1]["payload"]["power_phi_0.20"]
        journal.log_event(conn, "experiment_selection", r)
        lb = generator.leaderboard(specs, net, trades, gcfg, cfg, days)
        lb.to_csv(out / f"exp_{name}_leaderboard.csv", index=False)
        bench = experiment.benchmarks(coins, cfg, days)
        report.plot_equity({"procedure": wf["net_oos"], "bh": bench["bh"][wf["oos"]], "vt": bench["vt"][wf["oos"]]},
                           days[wf["oos"]], out / f"exp_{name}_equity.png")
        md = gen_md(r, lb, gcfg, cfg).replace("# Генератор стратегий: отбор", f"# Эксперимент {name}") \
            .replace("gen_equity.png", f"exp_{name}_equity.png").replace("generator_leaderboard.csv", f"exp_{name}_leaderboard.csv")
        md = md.replace("Вердикт до holdout", f"Монеты: {', '.join(r['coins'])}; свечи {r['timeframe']}; "
                        f"направление {r['direction']}. Эталоны bh и vt считаются по тому же портфелю.\n\nВердикт до holdout", 1)
        (out / f"exp_{name}.md").write_text(md)
        print(md)
        return print(exp_summary(conn, out))
    if cmd == "exp-holdout":
        sel = exp_events(conn, "experiment_selection", name)
        if not sel:
            raise SystemExit(f"сначала exp-search {name}")
        r = sel[-1]["payload"]
        if not r["passed"]:
            raise SystemExit(f"{name}: проверки до holdout не пройдены ({', '.join(k for k, v in r['checks'].items() if not v)}); "
                             "holdout не читается")
        res = experiment.holdout_run(e, cfg, conn, info, r["candidate"]["spec"])
        journal.log_event(conn, "experiment_final_verdict", res)
        md = (f"# {name}: holdout\n\n**Вердикт: {res['verdict']}.**\n\n"
              + report.md_table(pd.DataFrame([{"curve": k, **{m: f'{v:.3f}' for m, v in x.items()}}
                                               for k, x in res["holdout"].items()])))
        (out / f"exp_{name}_holdout.md").write_text(md)
        return print(md)
    raise SystemExit(f"неизвестная команда {cmd}")


def season(cfg, conn):
    info = data.dataset_info(conn)
    series = {}
    for sym in cfg["data"]["symbols_main"]:
        series[f"{sym} 1d"] = (data.load_raw(sym, "1d"), ("weekday", "month"))
        if (data.RAW_DIR / data.fname(sym, "1h")).exists():
            series[f"{sym} 1h"] = (data.load_raw(sym, "1h"), ("hour",))
    df = seasonality.run(series)
    c = cfg["costs"]
    md = seasonality.report(df, c["fee_per_side"] + c["slippage_base"])
    journal.log_event(conn, "research_seasonality", {"groups": len(df), "significant_q05": int((df["q"] < 0.05).sum()),
                                                     "holdout_start": info["holdout_start"]})
    (data.ROOT / "out" / "seasonality.md").write_text(md)
    return md


def exp_summary(conn, out):
    rows = []
    for ev in journal.events(conn, "experiment_selection"):
        r = ev["payload"]
        c = r["candidate"]
        rows.append({"эксперимент": r["experiment"], "OOS Шарп": f"{r['procedure']['sharpe']:.2f}",
                     "OOS CAGR": f"{r['procedure']['cagr']:.1%}", "OOS MaxDD": f"{r['procedure']['max_dd']:.1%}",
                     "bh Шарп": f"{r['bh']['sharpe']:.2f}", "vt Шарп": f"{r['vt']['sharpe']:.2f}",
                     "ДИ низ vs bh": f"{r['ci_vs_bh']['lo']:.2f}", "DSR": f"{r['dsr']:.3f}", "PBO": f"{r['pbo']:.2f}",
                     "мощность": f"{r.get('power_phi_0.20', float('nan')):.0%}",
                     "кандидат": c["after_ablation"][:90] if c else "нет", "вердикт": r["verdict"]})
    df = pd.DataFrame(rows).drop_duplicates("эксперимент", keep="last") if rows else pd.DataFrame()
    md = "# Эксперименты этапа 3\n\n" + (report.md_table(df) if len(df) else "пока нет\n")
    md += f"\nВсего проб в журнале: {len(journal.trials(conn))}. DSR каждого эксперимента учитывает их все.\n"
    (out / "experiments.md").write_text(md)
    return md


if __name__ == "__main__":
    main(*sys.argv[1:])
