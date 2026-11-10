#!/usr/bin/env python3
"""Нестандартные и контринтуитивные идеи из исследований (научные статьи, Glassnode/CryptoQuant, отчёты фондов и маркетмейкеров).

  python orth_lab.py

Все правила заданы до запуска, без подбора. Проверка: 2020–2023 и отдельно 2024–2026, издержки как в fast_lab.
Источники идей:
  MAX-эффект: Özdamar, Akdeniz, Sensoy (Financial Innovation, 2021) — монеты с самым большим дневным ростом за месяц
    дальше растут лучше (в акциях наоборот).
  Низкая волатильность / низкая бета (betting-against-beta): классика акций, проверяем на крипте.
  Идиосинкратический моментум: моментум после вычета движения BTC (Fieberg и др., Quantitative Finance, 2023 — моментум).
  Всплеск внимания: аномальный объём.
  Funding-контрариан: покупать, когда толпа шортит (funding < 0).
  Hash ribbons (Charles Edwards): покупка после капитуляции майнеров.
  Ликвидность стейблкоинов (SSR, Glassnode/CryptoQuant): BTC только когда предложение стейблкоинов растёт.
  Покупка страха: индекс страха и жадности < 25, держать 30 дней.
  Премия за волатильность (Alexander & Imeraj; Almeida и др., 2024): продажа недельного ATM-стрэддла BTC по DVOL.
"""
import sys

import numpy as np
import pandas as pd

import fast_lab as F


def straddle_weekly(cur="BTC", only_rich=False):
    """Каждый понедельник продаём 7-дневный ATM-стрэддл на номинал = капитал. Премия ≈ 0.8·IV·√(7/365),
    выплата = |S_T/S_0 − 1|. Издержки: 0.1% номинала (комиссии Deribit на 2 опциона + экспирация) и 5% премии (спред)."""
    d = pd.read_csv(f"data/binance/{cur}_1d.csv")
    d.index = pd.to_datetime(d.t, unit="ms")
    v = pd.read_csv(f"data/{cur}_dvol_daily.csv")
    v.index = pd.to_datetime(v.t, unit="ms")
    iv = v.close.reindex(d.index).ffill() / 100
    rv = np.log(d.close).diff().rolling(30).std() * np.sqrt(365)
    out = pd.Series(0.0, d.index)
    mondays = [t for t in d.index if t.dayofweek == 0 and pd.notna(iv.get(t))]
    for t0 in mondays:
        t1 = t0 + pd.Timedelta(days=7)
        if t1 not in d.index:
            continue
        if only_rich and not iv[t0] > rv[t0] * 1.1:
            continue
        prem = 0.8 * iv[t0] * np.sqrt(7 / 365)
        pay = abs(d.close[t1] / d.close[t0] - 1)
        out[t1] = prem * 0.95 - pay - 0.001
    return out


def main():
    C, Fd, V = F.load()
    R = C.pct_change(fill_method=None)
    btcR = R.BTC.fillna(0)
    rows = [F.report("Держать BTC (для сравнения)", btcR)]
    wk = 7

    # --- Кросс-секция (лонг 4 / шорт 4, ребаланс раз в неделю) ---
    mx = R.rolling(30, min_periods=20).max()
    rows.append(F.report("MAX-эффект: лонг с max дневным ростом за 30д", F.run_weights(F.ls_weights(-mx, C, 4), C, Fd, wk)))
    vol = R.rolling(30, min_periods=20).std()
    rows.append(F.report("Низкая волатильность: лонг спокойные / шорт буйные", F.run_weights(F.ls_weights(vol, C, 4), C, Fd, wk)))
    cov = R.rolling(60, min_periods=40).cov(R.BTC)
    beta = cov.div(R.BTC.rolling(60, min_periods=40).var(), axis=0)
    rows.append(F.report("Против беты: лонг низкая бета / шорт высокая", F.run_weights(F.ls_weights(beta, C, 4), C, Fd, wk)))
    resid = R.sub(beta.shift(1).mul(R.BTC, axis=0))
    idio = resid.rolling(30, min_periods=20).sum()
    rows.append(F.report("Идиосинкратический моментум 30д (без BTC)", F.run_weights(F.ls_weights(-idio, C, 4), C, Fd, wk)))
    att = V.rolling(7).mean() / V.rolling(90, min_periods=60).mean()
    rows.append(F.report("Всплеск объёма: лонг всплеск / шорт тишина", F.run_weights(F.ls_weights(-att, C, 4), C, Fd, wk)))
    rows.append(F.report("Всплеск объёма наоборот: лонг тишина", F.run_weights(F.ls_weights(att, C, 4), C, Fd, wk)))

    # --- Тайминг BTC ---
    def timing(name, on):
        W = pd.DataFrame({"BTC": on.reindex(C.index).fillna(0).astype(float)}).reindex(columns=C.columns).fillna(0)
        rows.append(F.report(name, F.run_weights(W, C, Fd)))
    f7 = Fd.BTC.rolling(7).mean()
    timing("Funding-контрариан: BTC только при funding 7д < 0", f7 < 0)
    timing("BTC, но x0 при funding 7д в топ-25% года, иначе x1", ~(f7 > f7.rolling(365, min_periods=180).quantile(0.75)))
    oc = pd.read_csv("data/onchain_seed.csv", index_col=0, parse_dates=True)
    hr = oc.HashRate
    s30, s60 = hr.rolling(30).mean(), hr.rolling(60).mean()
    cap = s30 < s60
    timing("Hash ribbons: вне рынка во время капитуляции майнеров", ~cap)
    rec = cap.shift(1, fill_value=False) & ~cap
    timing("Hash ribbons: покупка после капитуляции, держать 120д", rec.astype(float).rolling(120, min_periods=1).max() > 0)
    st = oc.stables
    timing("Стейблкоины: BTC только если их предложение за 30д растёт", st.pct_change(30) > 0)
    timing("Стейблкоины: BTC только если рост за 30д > 2%", st.pct_change(30) > 0.02)
    fg = pd.read_csv("data/fng_history.csv", index_col=0, parse_dates=True).value
    timing("Покупка страха: индекс < 25, держать 30д", (fg < 25).astype(float).rolling(30, min_periods=1).max() > 0)
    timing("Выход на жадности: BTC, но вне рынка при индексе > 75", ~(fg > 75))

    # --- Продажа волатильности ---
    rows.append(F.report("Продажа недельного стрэддла BTC каждую неделю", straddle_weekly("BTC")))
    rows.append(F.report("То же, только когда IV > реализованной ×1.1", straddle_weekly("BTC", True)))
    rows.append(F.report("Продажа недельного стрэддла ETH", straddle_weekly("ETH")))
    pd.DataFrame(rows).to_csv("improve_results/orth_lab.csv", index=False)


if __name__ == "__main__":
    sys.exit(main())
