#!/usr/bin/env python3
"""Поиск быстрых источников дохода (дни, а не месяцы) на перпетуалах Binance, 19 монет, 2020-01 .. 2026-10.

  python fast_lab.py

Данные: data.binance.vision (дневные и часовые свечи UM-перпов, история funding). Все правила заданы заранее,
без подбора под историю. Проверка: 2020–2023 и отдельно 2024–2026 (вторая половина как «будущее»).
Издержки: тейкер 0.055% за сторону, funding начисляется на позиции (лонг платит, шорт получает при ставке > 0).
Рыночно-нейтральные портфели: половина капитала в лонгах, половина в шортах, равные доли.
Оговорка: список монет сегодняшний (нет LUNA, FTT и других, что обнулились), это завышает лонги по альтам.
"""
import math
import sys

import numpy as np
import pandas as pd

FEE = 0.00055
COINS = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "AVAX", "LINK", "DOT", "BCH", "TRX", "BNB", "NEAR",
         "ATOM", "UNI", "SUI", "PEPE", "SHIB"]
SPLIT = "2024-01-01"


def load():
    C, F, V = {}, {}, {}
    for k in COINS:
        d = pd.read_csv(f"data/binance/{k}_1d.csv")
        d.index = pd.to_datetime(d.t, unit="ms")
        C[k], V[k] = d.close, d.quote_vol
        f = pd.read_csv(f"data/binance/{k}_funding.csv")
        f.index = pd.to_datetime(f.t, unit="ms").dt.floor("D")
        F[k] = f.rate.groupby(level=0).sum()
    C, F, V = pd.DataFrame(C), pd.DataFrame(F), pd.DataFrame(V)
    F = F.reindex(C.index)
    return C, F, V


def run_weights(W, C, F, rebalance_every=1):
    """W: DataFrame весов (решение на закрытии дня t, держим день t+1). Возвращает дневную доходность портфеля."""
    R = C.pct_change(fill_method=None).fillna(0)
    W = W.reindex(C.index).fillna(0)
    if rebalance_every > 1:
        keep = np.arange(len(W)) % rebalance_every == 0
        W = W.where(pd.Series(keep, W.index), np.nan).ffill().fillna(0)
    Wh = W.shift(1).fillna(0)                          # позиция, которую держим в день t
    pnl = (Wh * R).sum(axis=1) - (Wh * F.fillna(0)).sum(axis=1)
    turn = (W - W.shift(1).fillna(0) * (1 + R)).abs().sum(axis=1).shift(1).fillna(0)
    return pnl - turn * FEE


def ls_weights(score, C, n=4, min_hist=60):
    """Лонг n монет с наименьшим score, шорт n с наибольшим (по 0.5 капитала на сторону)."""
    alive = C.notna() & (C.notna().cumsum() >= min_hist)
    W = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    for t in C.index:
        s = score.loc[t][alive.loc[t]].dropna()
        if len(s) < 2 * n:
            continue
        s = s.sort_values()
        W.loc[t, s.index[:n]] = 0.5 / n
        W.loc[t, s.index[-n:]] = -0.5 / n
    return W


def stats(r):
    r = r.dropna()
    if len(r) < 30:
        return dict(cagr=np.nan, sharpe=np.nan, dd=np.nan)
    e = (1 + r).cumprod()
    yrs = len(r) / 365
    return dict(cagr=e.iloc[-1] ** (1 / yrs) - 1, sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365),
                dd=(e / e.cummax() - 1).min())


def report(name, r):
    a, b, c = stats(r.loc["2020-03-01":]), stats(r.loc[:"2023-12-31"].loc["2020-03-01":]), stats(r.loc[SPLIT:])
    print(f"{name:52s} {a['cagr']:+7.0%} {a['sharpe']:5.2f} {a['dd']:+6.0%} | 2020–23 {b['cagr']:+6.0%} "
          f"Sh {b['sharpe']:5.2f} | 2024–26 {c['cagr']:+6.0%} Sh {c['sharpe']:5.2f} {c['dd']:+5.0%}")
    return dict(name=name, **{f"all_{k}": v for k, v in a.items()}, **{f"is_{k}": v for k, v in b.items()},
                **{f"oos_{k}": v for k, v in c.items()})


def main():
    C, F, V = load()
    R = C.pct_change(fill_method=None)
    rows = []
    print(f"{'стратегия':52s} {'в год':>7s} {'Sh':>5s} {'просад':>6s}")
    btc = R.BTC.fillna(0)
    rows.append(report("Держать BTC (для сравнения)", btc))

    # 1. Funding кросс-секция: лонг монеты с самым низким funding, шорт с самым высоким
    f3 = F.rolling(3, min_periods=2).mean()
    for n, reb in ((4, 1), (4, 7), (3, 3)):
        rows.append(report(f"Funding: лонг низкий / шорт высокий, {n}+{n}, ребаланс {reb} дн",
                           run_weights(ls_weights(f3, C, n), C, F, reb)))
    # 2. Моментум кросс-секция (лонг сильные, шорт слабые)
    for look, reb in ((7, 7), (30, 7)):
        rows.append(report(f"Моментум {look} дн: лонг сильные / шорт слабые, 4+4, ребаланс {reb}",
                           run_weights(ls_weights(-np.log(C).diff(look), C, 4), C, F, reb)))
    # 3. Краткосрочный разворот (лонг вчерашние проигравшие, шорт победители)
    rows.append(report("Разворот 1 день: лонг проигравшие / шорт победители, 4+4",
                       run_weights(ls_weights(np.log(C).diff(1), C, 4), C, F, 1)))
    rows.append(report("Разворот 3 дня, ребаланс 3",
                       run_weights(ls_weights(np.log(C).diff(3), C, 4), C, F, 3)))
    # 4. Funding-тайминг BTC: держать BTC, но выходить, когда funding за 7 дней в верхних 10% года
    f7 = F.BTC.rolling(7).mean()
    hot = f7 > f7.rolling(365, min_periods=180).quantile(0.9)
    W = pd.DataFrame({"BTC": (~hot).astype(float)}).reindex(columns=C.columns).fillna(0)
    rows.append(report("BTC, но вне рынка при перегретом funding (топ-10% года)", run_weights(W, C, F)))
    # 5. Выкуп резкого падения альта (монета −10% за день, BTC не хуже −3%), держать 3 дня
    ev = (R.le(-0.10)) & (R.BTC.ge(-0.03).values[:, None])
    hold = ev.astype(float).rolling(3, min_periods=1).max()
    W = hold.div(hold.sum(axis=1).clip(lower=1), axis=0).fillna(0)
    rows.append(report("Выкуп падения альта −10% за день, держать 3 дня", run_weights(W, C, F)))
    # 6. Выходные: BTC только с пятницы на понедельник / только в будни
    dow = C.index.dayofweek
    wk = pd.DataFrame({"BTC": pd.Series(np.isin(dow, [4, 5]), C.index).astype(float)}).reindex(columns=C.columns).fillna(0)
    rows.append(report("BTC только на выходные (держать сб, вс)", run_weights(wk, C, F)))
    # 7. Часы суток BTC: лучшие 6 часов по 2020–2023, проверка на 2024–2026
    h = pd.read_csv("data/binance/BTC_1h.csv")
    h.index = pd.to_datetime(h.t, unit="ms")
    hr = h.close.pct_change()
    is_mean = hr.loc[:"2023-12-31"].groupby(hr.loc[:"2023-12-31"].index.hour).mean()
    best = is_mean.sort_values().index[-6:]
    on = pd.Series(np.isin(hr.index.hour, best), hr.index).astype(float)
    pos = on.shift(0)                                   # позиция в час, выбранный заранее
    hp = pos * hr - pos.diff().abs().fillna(0) * FEE
    rows.append(report(f"BTC только в часы {sorted(best.tolist())} UTC", hp.groupby(hp.index.floor("D")).sum()))
    pd.DataFrame(rows).to_csv("improve_results/fast_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
