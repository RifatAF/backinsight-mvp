"""Milestone 2: benchmark-only report. Full report is milestone 5."""
import numpy as np
import pandas as pd

from tradelab import backtest, metrics, strategies


def _row(net):
    return {"sharpe": metrics.sharpe(net), "calmar": metrics.calmar(net),
            "max_dd": metrics.max_drawdown(net), "cagr": metrics.cagr(net), "days": len(net)}


def regime_bounds(cfg, holdout_start):
    out = {}
    for name, (a, b) in cfg["regimes"].items():
        b = holdout_start - pd.Timedelta(days=1) if str(b) == "holdout_start" else pd.Timestamp(b, tz="UTC")
        out[name] = (pd.Timestamp(a, tz="UTC"), pd.Timestamp(b))
    return out


def benchmark_table(bars, cfg, holdout_start, funding=None):
    rows = []
    for name, fn in (("bh", strategies.bh), ("vt", strategies.vt)):
        net = backtest.run(bars, fn(bars, cfg), cfg, funding)["net"]
        rows.append({"bench": name, "period": "all", **_row(net)})
        for reg, (a, b) in regime_bounds(cfg, holdout_start).items():
            rows.append({"bench": name, "period": reg, **_row(net[(net.index >= a) & (net.index <= b)])})
    return pd.DataFrame(rows)


def benchmarks_md(table, symbol, start, end):
    t = table.copy()
    for c in ("sharpe", "calmar"):
        t[c] = t[c].map("{:.2f}".format)
    for c in ("max_dd", "cagr"):
        t[c] = t[c].map("{:.1%}".format)
    return f"# Эталоны {symbol}, {start.date()} .. {end.date()} (до holdout)\n\n{md_table(t)}"


def md_table(t):
    head = "| " + " | ".join(map(str, t.columns)) + " |\n|" + "---|" * len(t.columns) + "\n"
    return head + "".join("| " + " | ".join(map(str, r)) + " |\n" for r in t.itertuples(index=False))


# ---------- milestone 5: full report ----------

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = {"procedure": "#2a78d6", "bh": "#eb6834", "vt": "#1baf7a"}  # validated: light, adjacent CVD dE >= 9
LABEL = {"procedure": "процедура (OOS)", "bh": "bh", "vt": "vt"}


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _fig(*a, **k):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(*a, **k)
    fig.patch.set_facecolor(SURFACE)
    return plt, fig, ax


def plot_equity(nets, index, path):
    plt, fig, ax = _fig(figsize=(9, 4.5), dpi=130)
    _style(ax)
    for k, net in nets.items():
        eq = np.cumprod(1 + net)
        ax.plot(index, eq, color=SERIES[k], linewidth=1.6, label=LABEL[k])
        ax.annotate(f"{LABEL[k]} x{eq[-1]:.1f}", (index[-1], eq[-1]), xytext=(6, 0),
                    textcoords="offset points", color=INK, fontsize=9, va="center")
    ax.set_yscale("log")
    ax.set_title("Капитал на склеенном OOS (лог шкала, старт = 1)", color=INK, fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_drawdowns(nets, index, path):
    plt, fig, axes = _fig(len(nets), 1, figsize=(9, 5.5), dpi=130, sharex=True, sharey=True)
    for ax, (k, net) in zip(axes, nets.items()):
        _style(ax)
        eq = np.cumprod(1 + net)
        dd = eq / np.maximum.accumulate(eq) - 1
        ax.fill_between(index, dd, 0, color=SERIES[k], alpha=0.35, linewidth=0)
        ax.plot(index, dd, color=SERIES[k], linewidth=1.2)
        ax.set_title(f"{LABEL[k]}: MaxDD {dd.min():.0%}", color=INK, fontsize=10, loc="left")
        ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    fig.suptitle("Просадки на склеенном OOS (общая шкала)", color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_null_hist(best_null, marks, path):
    plt, fig, ax = _fig(figsize=(8, 4), dpi=130)
    _style(ax)
    ax.hist(best_null, bins=30, color=SERIES["procedure"], edgecolor=SURFACE, linewidth=1)
    merged = {}
    for name, v in marks.items():  # marks with the same value share one line and one label
        key = round(v, 6)
        merged[key] = f"{merged[key]} = {name}" if key in merged else name
    top = ax.get_ylim()[1]
    for i, ((v, name), ls) in enumerate(zip(merged.items(), ("-", "--"))):
        ax.axvline(v, color=INK, linewidth=1.4, linestyle=ls)
        ax.annotate(f"{name}: {v:.2f}", (v, top * (0.92 - 0.08 * i)), xytext=(4, 0),
                    textcoords="offset points", color=INK, fontsize=9)
    share = float(np.mean(np.asarray(best_null) >= list(marks.values())[0]))
    ax.set_title(f"Лучший Шарп из 300 на нулевых путях A (n={len(best_null)}); "
                 f"доля путей не хуже реального: {share:.0%}", color=INK, fontsize=10, loc="left")
    ax.set_xlabel("Шарп на всей истории до holdout", color=INK2, fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return share


def plot_plateau(sr, rows, cols, mark, path, title):
    """Heatmap of full-sample Sharpe; diverging around 0 with a gray midpoint, candidate cell outlined."""
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    plt, fig, ax = _fig(figsize=(7, 6), dpi=130)
    cmap = LinearSegmentedColormap.from_list("div", ["#eb6834", "#f0efec", "#2a78d6"])
    lim = max(abs(np.nanmin(sr)), abs(np.nanmax(sr)), 1e-6)
    im = ax.imshow(sr, cmap=cmap, norm=TwoSlopeNorm(0, -lim, lim), aspect="auto")
    for i in range(sr.shape[0]):
        for j in range(sr.shape[1]):
            ax.text(j, i, f"{sr[i, j]:.2f}", ha="center", va="center", fontsize=8, color=INK)
    if mark is not None:
        ax.add_patch(plt.Rectangle((mark[1] - 0.5, mark[0] - 0.5), 1, 1, fill=False, edgecolor=INK, linewidth=2))
    ax.set_xticks(range(len(cols)), cols)
    ax.set_yticks(range(len(rows)), rows)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    fig.colorbar(im, ax=ax, shrink=0.8).ax.tick_params(colors=INK2, labelsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def regime_oos_table(nets, index, cfg, holdout_start):
    rows = []
    for reg, (a, b) in regime_bounds(cfg, holdout_start).items():
        m = (index >= a) & (index <= b)
        for k, net in nets.items():
            if m.sum() < 30:
                rows.append({"regime": reg, "curve": k, "days": int(m.sum()), "sharpe": "нет OOS",
                             "calmar": "", "max_dd": "", "cagr": ""})
                continue
            x = _row(net[m])
            rows.append({"regime": reg, "curve": k, "days": x["days"], "sharpe": f"{x['sharpe']:.2f}",
                         "calmar": f"{x['calmar']:.2f}", "max_dd": f"{x['max_dd']:.1%}", "cagr": f"{x['cagr']:.1%}"})
    return pd.DataFrame(rows)


LIMITATIONS = [
    "Смещение выживших в корзине: кандидаты отобраны среди пар, которые торгуются сейчас, по текущему 24ч объёму.",
    "Мало режимов: в истории ETH два медвежьих и один бычий цикл; склеенный OOS начинается 2020-04-23 и не видит 2018.",
    "Фандинг до начала архива (2020-01) и после его конца (2026-09) взят константой 3 x 0.0001 в сутки.",
    "Спот загружен через официальное зеркало Binance data-api.binance.vision, фандинг из архива data.binance.vision (api.binance.com недоступен, 451).",
    "Мощность процедуры низкая: при AR(1) phi = 0.20 находка на 1.5% путей C; «не найдена» не доказывает отсутствие слабого сигнала.",
    "Синтетика B даёт хвосты тяжелее реальных (эксцесс и индекс Хилла в таблице фактов), поэтому её стресс строже реальности.",
    "Издержки: фиксированная модель комиссии и проскальзывания на 10 000 USD; стакан и задержки API не моделируются.",
]


def build(cfg, conn, out_dir):
    """Full report: out/report.md plus png figures. Reads the journal; recomputes the search by replay."""
    from tradelab import costs, data, grid, journal, selection, strategies, synth, walkforward
    from tradelab import parallel, backtest

    info = data.dataset_info(conn)
    sha = info["dataset_sha256"]
    start = pd.Timestamp(info["holdout_start"])
    sel_ev = journal.events(conn, "selection_replay") or journal.events(conn, "selection")
    if not sel_ev:
        raise RuntimeError("selection has not been run")
    r = sel_ev[-1]["payload"]
    cal = journal.events(conn, "calibration")[-1]["payload"]
    sym = cfg["data"]["symbols_main"][0]
    bars = data.load_raw(sym)
    fday = costs.funding_daily(bars.index, cfg["costs"], data.load_funding(sym)).to_numpy()
    found = selection.replay(bars, cfg, conn, fday, sha)
    wf, confs = found["wf"], found["confs"]
    oos = wf["oos"]
    index = bars.index[oos]
    nets = {"procedure": wf["net_oos"]}
    for name, fn in (("bh", strategies.bh), ("vt", strategies.vt)):
        nets[name] = backtest.pnl(bars, backtest.positions(fn(bars, cfg).to_numpy(), cfg), cfg, fday)["net"][oos]

    out_dir.mkdir(parents=True, exist_ok=True)
    plot_equity(nets, index, out_dir / "equity.png")
    plot_drawdowns(nets, index, out_dir / "drawdowns.png")

    sr_full = walkforward.sharpe_cols(wf["net_all"], cfg["annualization"])
    calib = pd.read_parquet(out_dir / "calibration.parquet")
    marks = {"реальный max из 300": float(sr_full.max())}
    if found["final"] >= 0:
        marks["кандидат"] = float(sr_full[found["final"]])
    null_share = plot_null_hist(calib.loc[calib.phi == 0, "best_is_sharpe"].to_numpy(), marks, out_dir / "null_best_sharpe.png")

    ids = {grid.config_id(c): i for i, c in enumerate(confs)}
    cand = confs[found["final"]] if found["final"] >= 0 else confs[int(np.argmax(sr_full))]
    vs = grid.V_GRID
    heat = np.array([[sr_full[ids[grid.config_id(dict(cand, base=b, v=v))]] for v in vs] for b in strategies.BASES])
    mark = (strategies.BASES.index(cand["base"]), vs.index(cand["v"]))
    plot_plateau(heat, strategies.BASES, [f"v={v}" for v in vs], mark, out_dir / "plateau_heatmap.png",
                 f"Шарп на всей истории: база x таргет волатильности (p={cand['p']}, q={cand['q']})")

    prm = synth.fit_b(bars)
    facts = synth.facts_table(bars, [synth.path_b(bars, prm, s)
                                     for s in parallel.job_seeds(cfg["seed"] + 1, cfg["synth"]["n_null_paths"])])
    reg = regime_oos_table(nets, index, cfg, start)
    curves = pd.DataFrame([{"curve": k, **{m: f"{v:.3f}" for m, v in r[k].items()}} for k in ("procedure", "bh", "vt")])
    checks = pd.DataFrame([{"check": k, "ok": "да" if v else "нет"} for k, v in r["checks"].items()])
    c = r["candidate"]
    th = cfg["thresholds"]
    lines = [
        "# tradelab: отчёт", "",
        f"**Вердикт до holdout: {r['verdict']}.** Holdout ({start.date()} и далее) не тронут.", "",
        "## Числа", "",
        f"- N проб на реальных данных: {r['n_trials']}; данные ETH/USDT {bars.index[0].date()} .. {bars.index[-1].date()}, "
        f"sha256 набора `{sha[:16]}…`.",
        f"- Склеенный OOS: {r['oos_days']} дней с {r['oos_start']}, фолдов: {len(wf["choices"])}.",
        f"- DSR {r['dsr']:.3f} (порог {th['dsr_min']}), PBO {r['pbo']:.3f} (порог {th['pbo_max']}), "
        f"FDR на путях A {cal['fdr']:.3f} (порог {th['fdr_max']}), мощность при phi=0.20 {cal['power'].get('0.2', cal['power'].get(0.2, 0)):.3f}.",
        f"- ДИ {th['ci_level']:.0%} разницы Шарпов: против bh [{r['ci_vs_bh']['lo']:.2f}, {r['ci_vs_bh']['hi']:.2f}], "
        f"против vt [{r['ci_vs_vt']['lo']:.2f}, {r['ci_vs_vt']['hi']:.2f}].",
        f"- Лучший Шарп из 300 на реальных данных {marks['реальный max из 300']:.2f}; на нулевых путях A такой "
        f"или выше в {null_share:.0%} случаев.",
        f"- Месяцы с доходностью > {th['monthly_return_flag']:.0%} на склеенном OOS (подозрение на подгонку): "
        f"{', '.join(r['monthly_flags']) or 'нет'}.", "",
        "## Склеенный OOS против эталонов", "", md_table(curves), "![капитал](equity.png)", "",
        "![просадки](drawdowns.png)", "",
        "## По режимам (OOS дни)", "", md_table(reg),
        "## Нулевое распределение лучшего Шарпа", "", "![гистограмма](null_best_sharpe.png)", "",
        "## Плато", "",
        ("Кандидат: нет." if c is None else
         f"Кандидат `{c['selected']}`, после абляции `{c['after_ablation']}`; плато {c['plateau']:.3f} "
         f"(порог {th['plateau_min_ratio']}); переносимость {c['transfer_share']:.2f}; стресс "
         f"{'пройден' if c['stress']['passed'] else 'не пройден'}."), "",
        "![плато](plateau_heatmap.png)", "",
        "## Проверки (раздел 13 без holdout)", "", md_table(checks),
        "## Стилизованные факты: реальные против синтетики B", "",
        md_table(facts.round(3).reset_index().rename(columns={"index": "fact"})),
        "## Выбор по фолдам", "", ", ".join(r["fold_choices"]), "",
        "## Ограничения", "", *[f"- {x}" for x in LIMITATIONS], "",
    ]
    if c is not None:
        tr = pd.DataFrame([{"coin": k, "net_return": f"{v:.1%}"} for k, v in c["transfer"].items()])
        ab = pd.DataFrame(c["ablation"]).round(3) if c["ablation"] else pd.DataFrame([{"layer": "нет слоёв"}])
        idx = lines.index("## Стилизованные факты: реальные против синтетики B")
        lines[idx:idx] = ["## Абляция", "", md_table(ab), "## Переносимость на корзину", "", md_table(tr)]
    md = "\n".join(lines)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    return md
