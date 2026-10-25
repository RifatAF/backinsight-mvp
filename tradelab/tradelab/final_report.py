"""out/FINAL_REPORT.md: one file with the state of the whole project, built from the journal."""
import json

import pandas as pd

from tradelab import config, data, journal, report


def _last(conn, *kinds):
    for k in kinds:
        ev = journal.events(conn, k)
        if ev:
            return ev[-1]["payload"]
    return None


def _row(name, r, power=None):
    c = r.get("candidate")
    cid = None if not c else (c.get("after_ablation") or c.get("id") or json.dumps(c.get("config", c.get("spec"))))
    return {"процедура": name, "OOS с": r.get("oos_start", "-"), "дней": r.get("oos_days", "-"),
            "Шарп": f"{r['procedure']['sharpe']:.2f}", "CAGR": f"{r['procedure']['cagr']:.1%}",
            "MaxDD": f"{r['procedure']['max_dd']:.1%}", "bh": f"{r['bh']['sharpe']:.2f}", "vt": f"{r['vt']['sharpe']:.2f}",
            "ДИ низ vs bh": f"{r['ci_vs_bh']['lo']:.2f}", "DSR": f"{r['dsr']:.3f}", "PBO": f"{r['pbo']:.2f}",
            "FDR": f"{r['fdr']:.2f}", "мощность": "-" if power is None else f"{power:.0%}",
            "вердикт": r["verdict"], "кандидат": (cid or "нет")[:70]}


def build(conn, steps, out):
    cfg = config.load()
    rows, passed = [], []
    r = _last(conn, "selection_replay", "selection")
    if r:
        rows.append(_row("этап 1: сетка 300", r, (_last(conn, "calibration") or {}).get("power", {}).get("0.2")))
    r = _last(conn, "generator_selection_replay", "generator_selection")
    if r:
        rows.append(_row("этап 2: генератор", r, (_last(conn, "generator_calibration") or {}).get("power_phi_0.20")))
    seen = {}
    for ev in journal.events(conn, "experiment_selection"):
        seen[ev["payload"]["experiment"]] = ev["payload"]
    for name, r in seen.items():
        rows.append(_row(f"эксп. {name}", r, r.get("power_phi_0.20")))
        if r["passed"]:
            passed.append(name)
    r = _last(conn, "perps_selection")
    if r:
        rows.append(_row("этап 5: фандинг и толпа", r))
        if r["passed"]:
            passed.append("perps")
    r = _last(conn, "mtf_selection")
    if r:
        rows.append(_row("этап 6: три слоя", r))
        if r["passed"]:
            passed.append("mtf")
    m = _last(conn, "multi_selection")
    trials = len(journal.trials(conn))
    hold = journal.events(conn, "holdout_read")
    md = [f"# tradelab: итоговый отчёт\n",
          f"Собран {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC. Проб в журнале: {trials}. "
          f"Holdout прочитан: {'да' if hold else 'нет'}. Граница holdout: {data.dataset_info(conn)['holdout_start'][:10]}.\n",
          "## Все процедуры (честный склеенный OOS)\n",
          "Шарп, CAGR, MaxDD это результат процедуры отбора вне выборки; bh и vt это Шарп удержания и таргета "
          "волатильности на тех же днях; DSR должен быть больше 0.95, ДИ низ больше 0.\n",
          report.md_table(pd.DataFrame(rows)) if rows else "пока нет\n"]
    if m:
        f = lambda x: f"{x:.2f}"
        mr = [{"правило": k, "Шарп": f(v["sharpe"]), "CAGR": f"{v['cagr']:.1%}", "MaxDD": f"{v['max_dd']:.1%}",
               "ДИ низ vs bh": f(v["ci_vs_bh"]["lo"]), "ДИ низ vs vt": f(v["ci_vs_vt"]["lo"]), "DSR": f"{v['dsr']:.3f}"}
              for k, v in m["rules"].items()]
        mr += [{"правило": k, "Шарп": f(m[k]["sharpe"]), "CAGR": f"{m[k]['cagr']:.1%}", "MaxDD": f"{m[k]['max_dd']:.1%}"}
               for k in ("bh", "vt")]
        md += [f"\n## Мульти-стратегия\n\nВердикт: **{m['verdict']}**. {len(m['components'])} компонентов, с {m['start']} "
               f"({m['days']} дней). Основное правило B.\n", report.md_table(pd.DataFrame(mr))]
    ev = _last(conn, "research_seasonality")
    if ev:
        md.append(f"\n## Сезонность\n\nПроверено групп: {ev['groups']}, значимы после поправки: {ev['significant_q05']} "
                  "(подробно out/seasonality.md).\n")
    md.append("\n## Что дальше\n\n" + (
        "Проверки до holdout прошли: " + ", ".join(passed) + ". Holdout читается ОДИН раз на весь журнал: выберите одну "
        "процедуру и запустите для неё exp-holdout ИМЯ (или perps-holdout, mtf-holdout), затем пришлите этот отчёт снова.\n"
        if passed else "Ни одна процедура не прошла проверки до holdout, holdout не тронут.\n"))
    st = pd.DataFrame([{"шаг": s["step"], "статус": s["status"], "минут": f"{s['seconds'] / 60:.1f}",
                        "примечание": s.get("note", "")[:120]} for s in steps])
    md += ["\n## Шаги этого запуска\n\n", report.md_table(st) if len(st) else "все шаги уже были сделаны\n"]
    text = "\n".join(md)
    (out / "FINAL_REPORT.md").write_text(text, encoding="utf-8")
    return text
