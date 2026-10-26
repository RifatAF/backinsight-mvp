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

Stage 5, funding / crowd strategies on low-liquidity perpetuals (settings in perps.yaml):
  python run_pipeline.py perps-data        Binance USDT-M archive, all symbols incl. delisted, 1h -> data/perps/
  python run_pipeline.py perps-calibrate   signal-shift null: FDR of the whole procedure
  python run_pipeline.py perps-search      all configs journaled, walk-forward, checks -> out/perps.md
  python run_pipeline.py perps-holdout     one-time holdout run, only if the checks passed
  python run_pipeline.py paper [CONFIG]    paper trading on live public data, fills on Hyperliquid's book
  python run_pipeline.py paper-report      -> out/paper.md

Stage 6, three layers (regime 1M/1W, setup 1D/4H, trigger 1H/15m/5m) on 12 liquid perps (mtf.yaml):
  python run_pipeline.py mtf-data          5m candles, 5m open interest / long-short / taker, funding, Coinbase 1h
  python run_pipeline.py mtf-calibrate     signal-shift null: FDR
  python run_pipeline.py mtf-search        45 configs journaled, walk-forward, checks, layer effects -> out/mtf.md
  python run_pipeline.py mtf-holdout       one-time holdout, only if the checks passed

Everything at once:
  python run_all.py                        every step that is not done yet, then out/FINAL_REPORT.md
  python run_pipeline.py multi             multi-strategy from all saved OOS curves -> out/multi.md
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
    if cmd.startswith("perps-"):
        return perps_cmd(cmd, cfg, conn)
    if cmd.startswith("mtf-"):
        return mtf_cmd(cmd, cfg, conn)
    if cmd == "multi":
        return multi_cmd(cfg, conn)
    if cmd == "paper":
        return paper(cfg, conn, name)
    if cmd == "paper-report":
        from tradelab import paperbot
        return print(paperbot.report(out=data.ROOT / "out" / "paper.md"))
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
    (data.ROOT / "out" / "benchmarks.md").write_text(md, encoding="utf-8")
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
    (out / "calibration.md").write_text(md, encoding="utf-8")
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
    replayed = any(t["dataset_sha256"] == sha and "stage" not in t["config"] for t in journal.trials(conn))
    found = (selection.replay if replayed else selection.search)(bars, cfg, conn, fday, sha)
    basket = {s: data.load_raw(s) for s in info["basket"]}
    funding = {s: data.load_funding(s) for s in info["basket"]}
    r = selection.evaluate(bars, cfg, conn, fday, found, sha, cal[-1]["payload"]["fdr"], basket,
                           {k: v for k, v in funding.items() if v is not None})
    facts = r.pop("facts")
    r["seconds"] = round(time.time() - t0, 1)
    r["replay"] = replayed
    journal.log_event(conn, "selection_replay" if replayed else "selection", r)
    save_oos("grid", bars.index[found["wf"]["oos"]], found["wf"]["net_oos"])
    out = data.ROOT / "out"
    (out / "selection.json").write_text(json.dumps(r, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    md = selection_md(r, facts, cfg)
    (out / "selection.md").write_text(md, encoding="utf-8")
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
    (data.ROOT / "out" / "verdict.md").write_text(md, encoding="utf-8")
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
        (out / "gen_calibration.md").write_text(md, encoding="utf-8")
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
        save_oos("generator", bars.index[wf["oos"]], wf["net_oos"])
        lb = generator.leaderboard(specs, net, trades, gcfg, cfg, bars.index)
        lb.to_csv(out / "generator_leaderboard.csv", index=False)
        oos = wf["oos"]
        nets = {"procedure": wf["net_oos"]}
        for name, fn in (("bh", strategies_bh), ("vt", strategies_vt)):
            nets[name] = backtest_net(bars, cfg, fday, fn)[oos]
        report.plot_equity(nets, bars.index[oos], out / "gen_equity.png")
        md = gen_md(r, lb, gcfg, cfg)
        (out / "generator.md").write_text(md, encoding="utf-8")
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
        (out / "gen_verdict.md").write_text(md, encoding="utf-8")
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


OOS_DIR = data.ROOT / "out" / "oos"


def save_oos(name, dates, net):
    """Stitched out-of-sample daily net of a procedure: the honest building block of the multi-strategy."""
    OOS_DIR.mkdir(parents=True, exist_ok=True)
    idx = pd.DatetimeIndex(dates).floor("D")
    pd.DataFrame({"net": list(map(float, net))}, index=idx).to_parquet(OOS_DIR / f"{name}.parquet")


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
        save_oos(f"exp_{name}", days[wf["oos"]], wf["net_oos"])
        lb = generator.leaderboard(specs, net, trades, gcfg, cfg, days)
        lb.to_csv(out / f"exp_{name}_leaderboard.csv", index=False)
        bench = experiment.benchmarks(coins, cfg, days)
        report.plot_equity({"procedure": wf["net_oos"], "bh": bench["bh"][wf["oos"]], "vt": bench["vt"][wf["oos"]]},
                           days[wf["oos"]], out / f"exp_{name}_equity.png")
        md = gen_md(r, lb, gcfg, cfg).replace("# Генератор стратегий: отбор", f"# Эксперимент {name}") \
            .replace("gen_equity.png", f"exp_{name}_equity.png").replace("generator_leaderboard.csv", f"exp_{name}_leaderboard.csv")
        md = md.replace("Вердикт до holdout", f"Монеты: {', '.join(r['coins'])}; свечи {r['timeframe']}; "
                        f"направление {r['direction']}. Эталоны bh и vt считаются по тому же портфелю.\n\nВердикт до holdout", 1)
        (out / f"exp_{name}.md").write_text(md, encoding="utf-8")
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
        (out / f"exp_{name}_holdout.md").write_text(md, encoding="utf-8")
        return print(md)
    raise SystemExit(f"неизвестная команда {cmd}")


def perps_cmd(cmd, cfg, conn):
    import os

    import yaml

    from tradelab import perpdata, perps, perpsearch

    out = data.ROOT / "out"
    raw = (data.ROOT / "perps.yaml").read_bytes()
    pcfg, psha = yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()
    if cmd == "perps-data":
        res = perpdata.download(cfg, pcfg, conn)
        return print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}, indent=1))
    lock = journal.events(conn, "perps_config_sha256")
    if not lock:
        journal.log_event(conn, "perps_config_sha256", {"sha256": psha})
    elif lock[0]["payload"]["sha256"] != psha:
        raise SystemExit("perps.yaml изменён после первого запуска: верните файл или ведите новый журнал")
    ds = journal.events(conn, "dataset_perps")
    if not ds:
        raise SystemExit("сначала: python run_pipeline.py perps-data")
    t0 = time.time()
    a = perps.prepare(perpdata.load_panels(), pcfg)
    print(f"монет в выборке: {len(a['symbols'])}, часов: {len(a['index'])}, подготовка {time.time() - t0:.0f} с", flush=True)
    jobs = int(os.environ.get("TRADELAB_PERPS_JOBS", min(parallel.n_jobs(), 6)))
    if cmd == "perps-calibrate":
        res = perpsearch.calibrate(a, cfg, pcfg, jobs)
        res.to_parquet(out / "perps_calibration.parquet")
        fdr = float(res["finding"].mean())
        payload = {"fdr": fdr, "paths": len(res), "status": "stop_fdr" if fdr > cfg["thresholds"]["fdr_max"] else "ok",
                   "best_null_sharpe_oos": float(res["sharpe_oos"].max()), "seconds": round(time.time() - t0, 1)}
        journal.log_event(conn, "perps_calibration", payload)
        return print(json.dumps(payload, ensure_ascii=False))
    if cmd == "perps-search":
        cal = journal.events(conn, "perps_calibration")
        if not cal or cal[-1]["payload"]["status"] != "ok":
            raise SystemExit("сначала perps-calibrate; поиск запрещён, пока FDR выше порога")
        confs, net, trades = perpsearch.search(a, cfg, pcfg, conn, ds[-1]["payload"]["perps_sha256"], jobs)
        r = perpsearch.check(a, cfg, pcfg, conn, confs, net, cal[-1]["payload"]["fdr"])
        wf = r.pop("wf")
        r["seconds"] = round(time.time() - t0, 1)
        journal.log_event(conn, "perps_selection", r)
        save_oos("perps", wf["days"], wf["proc"])
        lb = perpsearch.leaderboard(confs, net, trades, cfg)
        lb.to_csv(out / "perps_leaderboard.csv", index=False)
        report.plot_equity({"procedure": wf["proc"], "bh": wf["bh"], "vt": wf["vt"]}, wf["days"], out / "perps_equity.png")
        md = perps_md(r, lb, cfg)
        (out / "perps.md").write_text(md, encoding="utf-8")
        return print(md)
    if cmd == "perps-holdout":
        sel = journal.events(conn, "perps_selection")
        if not sel:
            raise SystemExit("сначала perps-search")
        r = sel[-1]["payload"]
        if not r["passed"]:
            raise SystemExit("проверки до holdout не пройдены (" + ", ".join(k for k, v in r["checks"].items() if not v)
                             + "); holdout не читается")
        res = perps_holdout(cfg, pcfg, conn, r["candidate"]["config"])
        journal.log_event(conn, "perps_final_verdict", res)
        md = (f"# Этап 5: holdout\n\n**Вердикт: {res['verdict']}.**\n\n"
              + report.md_table(pd.DataFrame([{"curve": k, **{m: f'{v:.3f}' for m, v in x.items()}}
                                               for k, x in res["holdout"].items()])))
        (out / "perps_holdout.md").write_text(md, encoding="utf-8")
        return print(md)
    raise SystemExit(f"неизвестная команда {cmd}")


def perps_holdout(cfg, pcfg, conn, conf):
    from tradelab import perpdata, perps, perpsearch, selection

    info = data.dataset_info(conn)
    start = pd.Timestamp(info["holdout_start"])
    hold = holdout.load(allow=True, conn=conn, path=[perpdata.HOLD, holdout.DEFAULT_DIR])
    frames = {}
    for p in sorted(perpdata.RAW.glob("*.parquet")):
        h = hold.get(p.stem)
        frames[p.stem] = pd.concat([pd.read_parquet(p), h]) if h is not None else pd.read_parquet(p)
    for stem, h in hold.items():
        if stem.endswith("USDT") and stem not in frames:
            frames[stem] = h
    a = perps.prepare(perpdata.load_panels(frames=frames), pcfg)
    di = perps.days(a)
    net = perps.run_config(conf, a, pcfg)[0]
    eth = pd.concat([data.load_raw("ETH/USDT"), hold["ETH_USDT_1d"]])
    fund = data.load_funding("ETH/USDT")
    if fund is not None and "ETH_USDT_funding" in hold:
        fund = pd.concat([fund, hold["ETH_USDT_funding"]["funding_rate"]])
    bench = perpsearch.eth_benchmarks(cfg, di, eth, fund)
    m = di >= start
    cur = {"candidate": selection._curve(net[m]), "bh": selection._curve(bench["bh"][m]),
           "vt": selection._curve(bench["vt"][m])}
    ok = all(cur["candidate"][k] >= cur[b][k] for b in ("bh", "vt") for k in ("sharpe", "calmar"))
    return {"verdict": holdout.FOUND if ok else holdout.NOT_FOUND, "holdout_read": True, "candidate": conf,
            "days": int(m.sum()), "holdout": cur}


def perps_md(r, lb, cfg):
    f = lambda x: f"{x:.3f}"
    th = cfg["thresholds"]
    rows = [{"curve": k, **{m: f(v) for m, v in r[k].items()}} for k in ("procedure", "bh", "vt")]
    checks = pd.DataFrame([{"check": k, "ok": "да" if v else "нет"} for k, v in r["checks"].items()])
    c = r["candidate"]
    cand = "нет" if c is None else (
        f"`{c['id']}`; полная история: Шарп {f(c['full_sample']['sharpe'])}, CAGR {c['full_sample']['cagr']:.1%}, "
        f"MaxDD {c['full_sample']['max_dd']:.1%}; плато {f(c['plateau'])}; стресс "
        f"{'пройден' if c['stress']['passed'] else 'не пройден'} (двойные издержки {f(c['stress']['double_costs_sharpe'])}, "
        f"задержка 1ч {f(c['stress']['delay1h_sharpe'])}, 8ч {f(c['stress']['delay8h_sharpe'])}, "
        f"соседи {c['stress']['neighbor_positive_share']:.0%})")
    top = lb.head(15).copy()
    for col in ("sharpe_is", "cagr_is", "max_dd_is", "active_days_per_month"):
        top[col] = top[col].map(f)
    best = lb.groupby("family")["sharpe_is"].max().map(f).reset_index()
    return (f"# Этап 5: фандинг и толпа на низколиквидных фьючерсах\n\nВердикт до holdout: **{r['verdict']}**\n\n"
            f"Монет в выборке (хоть раз прошли фильтр оборота): {r['coins']}. Конфигураций {r['n_configs']}, всего проб "
            f"в журнале {r['n_trials_total']} (DSR учитывает все). Склеенный OOS {r['oos_days']} дней с {r['oos_start']}: "
            f"DSR {f(r['dsr'])} (порог {th['dsr_min']}), PBO {f(r['pbo'])}, FDR {f(r['fdr'])}. ДИ разницы Шарпов: против bh "
            f"[{f(r['ci_vs_bh']['lo'])}, {f(r['ci_vs_bh']['hi'])}], против vt [{f(r['ci_vs_vt']['lo'])}, "
            f"{f(r['ci_vs_vt']['hi'])}]. Эталоны: удержание ETH и ETH с таргетом волатильности.\n\n"
            f"Кандидат: {cand}.\n\n" + report.md_table(pd.DataFrame(rows))
            + "\n![капитал](perps_equity.png)\n\n## Проверки\n\n" + report.md_table(checks)
            + "\n## Лучший Шарп по семействам (вся история, in-sample)\n\n" + report.md_table(best)
            + "\n## Топ-15 (in-sample, только для обзора)\n\n" + report.md_table(top)
            + "\nПолная таблица: perps_leaderboard.csv.\n")


def perps_grids():
    from tradelab import perps
    return perps.GRIDS


def paper(cfg, conn, conf_json):
    import yaml

    from tradelab import paperbot

    pcfg = yaml.safe_load((data.ROOT / "perps.yaml").read_text(encoding="utf-8"))
    if conf_json:  # JSON, or the short form: carry n=9 thr=0.0005 k=10
        conf = json.loads(conf_json) if conf_json.lstrip().startswith("{") else {
            "family": sys.argv[2], **{k: yaml.safe_load(v) for k, v in (x.split("=", 1) for x in sys.argv[3:])}}
        if conf["family"] not in perps_grids() or set(conf) - {"family"} != set(perps_grids()[conf["family"]]):
            raise SystemExit(f"неизвестная стратегия {conf}; параметры семейств: {perps_grids()}")
    else:
        sel = journal.events(conn, "perps_selection")
        if not sel or not sel[-1]["payload"]["candidate"]:
            raise SystemExit("нет кандидата из perps-search; укажите стратегию JSON-строкой (пример в README)")
        conf = sel[-1]["payload"]["candidate"]["config"]
        if not sel[-1]["payload"]["passed"]:
            print("внимание: кандидат не прошёл проверки до holdout; бумажная торговля идёт как наблюдение", flush=True)
    bot = paperbot.Bot(pcfg, conf, pcfg["venue"]["capital_usd"])
    print(f"бумажная торговля: {json.dumps(conf)}; лог paper.sqlite; остановка Ctrl+C", flush=True)
    print(f"первый шаг (загрузка истории ~10 мин): капитал {bot.step():,.2f}", flush=True)
    bot.run()

def multi_cmd(cfg, conn):
    from tradelab import multi, perpsearch

    comps = multi.load(OOS_DIR)
    if len(comps) < 2:
        raise SystemExit("нужно хотя бы два прогона с сохранённым OOS (select, gen-search, exp-search, perps-search)")
    x = multi.matrix(comps)
    eb = perpsearch.eth_benchmarks(cfg, x.index)
    bench = {k: pd.Series(v, index=x.index) for k, v in eb.items()}
    r, curves = multi.run(comps, cfg, conn, bench)
    journal.log_event(conn, "multi_selection", r)
    out = data.ROOT / "out"
    report.plot_equity({"procedure": curves["rule_B"].to_numpy(), "bh": curves["bh"].to_numpy(), "vt": curves["vt"].to_numpy()},
                       curves.index, out / "multi_equity.png")
    md = multi_md(r)
    (out / "multi.md").write_text(md, encoding="utf-8")
    return print(md)


def multi_md(r):
    f = lambda x: f"{x:.2f}"
    rows = [{"кривая": f"правило {k}" + (" (основное)" if k == "B" else ""), "Шарп": f(v["sharpe"]),
             "CAGR": f"{v['cagr']:.1%}", "MaxDD": f"{v['max_dd']:.1%}", "Кальмар": f(v["calmar"]),
             "ДИ низ vs bh": f(v["ci_vs_bh"]["lo"]), "ДИ низ vs vt": f(v["ci_vs_vt"]["lo"]), "DSR": f"{v['dsr']:.3f}"}
            for k, v in r["rules"].items()]
    rows += [{"кривая": k, "Шарп": f(r[k]["sharpe"]), "CAGR": f"{r[k]['cagr']:.1%}", "MaxDD": f"{r[k]['max_dd']:.1%}",
              "Кальмар": f(r[k]["calmar"])} for k in ("bh", "vt")]
    comp = [{"компонент": k, "OOS с": v["start"], "дней": v["days"], "Шарп": f(v["sharpe"]), "CAGR": f"{v['cagr']:.1%}",
             "MaxDD": f"{v['max_dd']:.1%}", "средний вес B": f"{v['avg_weight_B']:.0%}"}
            for k, v in r["component_stats"].items()]
    checks = pd.DataFrame([{"check": k, "ok": "да" if v else "нет"} for k, v in r["checks"].items()])
    return (f"# Мульти-стратегия\n\nВердикт: **{r['verdict']}**\n\nСобрана из честных OOS-кривых {len(r['components'])} "
            f"процедур, с {r['start']} ({r['days']} дней). Правила: A обратная волатильность по всем; B то же, но только "
            "компоненты с положительным Шарпом за прошлый год (основное); C половина ETH с таргетом волатильности и "
            f"половина B. Веса раз в месяц только по прошлым данным. Проб в журнале {r['n_trials_total']}.\n\n"
            + report.md_table(pd.DataFrame(rows)) + "\n![капитал](multi_equity.png)\n\n## Проверки (правило B)\n\n"
            + report.md_table(checks) + "\n## Компоненты\n\n" + report.md_table(pd.DataFrame(comp)))

def mtf_cmd(cmd, cfg, conn):
    import os

    import yaml

    from tradelab import mtf, mtfdata, mtfsearch

    out = data.ROOT / "out"
    raw = (data.ROOT / "mtf.yaml").read_bytes()
    mcfg, msha = yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()
    if cmd == "mtf-data":
        return print(json.dumps(mtfdata.download(cfg, mcfg, conn), indent=1, ensure_ascii=False))
    lock = journal.events(conn, "mtf_config_sha256")
    if not lock:
        journal.log_event(conn, "mtf_config_sha256", {"sha256": msha})
    elif lock[0]["payload"]["sha256"] != msha:
        raise SystemExit("mtf.yaml изменён после первого запуска: верните файл или ведите новый журнал")
    ds = journal.events(conn, "dataset_mtf")
    if not ds:
        raise SystemExit("сначала: python run_pipeline.py mtf-data")
    t0 = time.time()
    frames, cb = mtfdata.load()
    a = mtf.prepare(frames, cb, mcfg)
    print(f"монет: {len(a['symbols'])}, 5-минутных баров: {len(a['index'])}, подготовка слоёв {time.time() - t0:.0f} с", flush=True)
    jobs = int(os.environ.get("TRADELAB_MTF_JOBS", min(parallel.n_jobs(), 6)))
    if cmd == "mtf-calibrate":
        res = mtfsearch.calibrate(a, cb, cfg, mcfg, jobs)
        res.to_parquet(out / "mtf_calibration.parquet")
        fdr = float(res["finding"].mean())
        payload = {"fdr": fdr, "paths": len(res), "status": "stop_fdr" if fdr > cfg["thresholds"]["fdr_max"] else "ok",
                   "best_null_sharpe_oos": float(res["sharpe_oos"].max()), "seconds": round(time.time() - t0, 1)}
        journal.log_event(conn, "mtf_calibration", payload)
        return print(json.dumps(payload, ensure_ascii=False))
    if cmd == "mtf-search":
        cal = journal.events(conn, "mtf_calibration")
        if not cal or cal[-1]["payload"]["status"] != "ok":
            raise SystemExit("сначала mtf-calibrate; поиск запрещён, пока FDR выше порога")
        confs, net, trades = mtfsearch.search(a, cfg, mcfg, conn, ds[-1]["payload"]["mtf_sha256"], jobs)
        r = mtfsearch.check(a, cfg, mcfg, conn, confs, net, cal[-1]["payload"]["fdr"])
        wf = r.pop("wf")
        r["seconds"] = round(time.time() - t0, 1)
        journal.log_event(conn, "mtf_selection", r)
        save_oos("mtf", wf["days"], wf["proc"])
        lb = mtfsearch.leaderboard(confs, net, trades, cfg)
        lb.to_csv(out / "mtf_leaderboard.csv", index=False)
        report.plot_equity({"procedure": wf["proc"], "bh": wf["bh"], "vt": wf["vt"]}, wf["days"], out / "mtf_equity.png")
        md = mtf_md(r, lb, cfg)
        (out / "mtf.md").write_text(md, encoding="utf-8")
        return print(md)
    if cmd == "mtf-holdout":
        sel = journal.events(conn, "mtf_selection")
        if not sel:
            raise SystemExit("сначала mtf-search")
        r = sel[-1]["payload"]
        if not r["passed"]:
            raise SystemExit("проверки до holdout не пройдены (" + ", ".join(k for k, v in r["checks"].items() if not v)
                             + "); holdout не читается")
        res = mtf_holdout(cfg, mcfg, conn, r["candidate"]["config"])
        journal.log_event(conn, "mtf_final_verdict", res)
        md = (f"# Этап 6: holdout\n\n**Вердикт: {res['verdict']}.**\n\n"
              + report.md_table(pd.DataFrame([{"curve": k, **{m: f'{v:.3f}' for m, v in x.items()}}
                                               for k, x in res["holdout"].items()])))
        (out / "mtf_holdout.md").write_text(md, encoding="utf-8")
        return print(md)
    raise SystemExit(f"неизвестная команда {cmd}")


def mtf_holdout(cfg, mcfg, conn, conf):
    from tradelab import mtf, mtfdata, perpsearch, selection

    start = pd.Timestamp(data.dataset_info(conn)["holdout_start"])
    hold = holdout.load(allow=True, conn=conn, path=[mtfdata.HOLD, holdout.DEFAULT_DIR])
    frames, cb = mtfdata.load()
    frames = {s: pd.concat([f, hold[s]]) if s in hold else f for s, f in frames.items()}
    if cb is not None and "coinbase_1h" in hold:
        cb = pd.concat([cb, hold["coinbase_1h"]])
    a = mtf.prepare(frames, cb, mcfg)
    di = mtf.days(a, mcfg)
    net = mtf.run_config(conf, a, mcfg)[0]
    eth = pd.concat([data.load_raw("ETH/USDT"), hold["ETH_USDT_1d"]])
    fund = data.load_funding("ETH/USDT")
    if fund is not None and "ETH_USDT_funding" in hold:
        fund = pd.concat([fund, hold["ETH_USDT_funding"]["funding_rate"]])
    bench = perpsearch.eth_benchmarks(cfg, di, eth, fund)
    m = di >= start
    cur = {"candidate": selection._curve(net[m]), "bh": selection._curve(bench["bh"][m]),
           "vt": selection._curve(bench["vt"][m])}
    ok = all(cur["candidate"][k] >= cur[b][k] for b in ("bh", "vt") for k in ("sharpe", "calmar"))
    return {"verdict": holdout.FOUND if ok else holdout.NOT_FOUND, "holdout_read": True, "candidate": conf,
            "days": int(m.sum()), "holdout": cur}


def mtf_md(r, lb, cfg):
    f = lambda x: f"{x:.3f}"
    th = cfg["thresholds"]
    rows = [{"curve": k, **{m: f(v) for m, v in r[k].items()}} for k in ("procedure", "bh", "vt")]
    checks = pd.DataFrame([{"check": k, "ok": "да" if v else "нет"} for k, v in r["checks"].items()])
    c = r["candidate"]
    cand = "нет" if c is None else (
        f"`{c['id']}`; полная история: Шарп {f(c['full_sample']['sharpe'])}, CAGR {c['full_sample']['cagr']:.1%}, "
        f"MaxDD {c['full_sample']['max_dd']:.1%}; плато {f(c['plateau'])}; стресс "
        f"{'пройден' if c['stress']['passed'] else 'не пройден'}")
    lay = lambda key: pd.DataFrame([{**x, "медиана ΔШарп": f"{x['медиана ΔШарп']:+.2f}",
                                     "доля пар, где лучше": f"{x['доля пар, где лучше']:.0%}"} for x in r[key]])
    top = lb.head(10).copy()
    for col in ("sharpe_is", "cagr_is", "max_dd_is", "active_days_per_month"):
        top[col] = top[col].map(f)
    return (f"# Этап 6: три слоя (режим 1M/1W, сетап 1D/4H, вход 1H/15m/5m)\n\nВердикт до holdout: **{r['verdict']}**\n\n"
            f"Монеты: {', '.join(r['coins'])}. Конфигураций {r['n_configs']}, всего проб в журнале {r['n_trials_total']}. "
            f"Склеенный OOS {r['oos_days']} дней с {r['oos_start']}: DSR {f(r['dsr'])} (порог {th['dsr_min']}), PBO {f(r['pbo'])}, "
            f"FDR {f(r['fdr'])}. ДИ разницы Шарпов: против bh [{f(r['ci_vs_bh']['lo'])}, {f(r['ci_vs_bh']['hi'])}], "
            f"против vt [{f(r['ci_vs_vt']['lo'])}, {f(r['ci_vs_vt']['hi'])}].\n\nКандидат: {cand}.\n\n"
            + report.md_table(pd.DataFrame(rows)) + "\n![капитал](mtf_equity.png)\n\n## Проверки\n\n" + report.md_table(checks)
            + "\n## Что даёт каждый слой\n\nРазница Шарпа варианта слоя и того же набора без этого слоя (вариант none), "
            "остальные два слоя те же. Дни склеенного OOS:\n\n" + report.md_table(lay("layers_oos"))
            + "\nВся история (in-sample, для обзора):\n\n" + report.md_table(lay("layers_all"))
            + "\n## Топ-10 (in-sample, только для обзора)\n\n" + report.md_table(top) + "\nПолная таблица: mtf_leaderboard.csv.\n")

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
    (data.ROOT / "out" / "seasonality.md").write_text(md, encoding="utf-8")
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
    (out / "experiments.md").write_text(md, encoding="utf-8")
    return md


if __name__ == "__main__":
    main(*sys.argv[1:])
