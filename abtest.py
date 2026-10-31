#!/usr/bin/env python3
"""A/B-тест четырёх моделей на живых данных Bybit с виртуальными деньгами + запись всех данных для анализа.

  python abtest.py init --capital 10000 --coins BTC,ETH      # 4 виртуальных счёта с одинаковым капиталом
  python abtest.py cycle                                     # дневной цикл (после 00:00 UTC): данные -> решения -> сделки
  python abtest.py snapshot                                  # снимок рынка без сделок (стакан, funding, OI, long/short)
  python abtest.py loop                                      # снимок каждый час + дневной цикл в 00:05 UTC
  python abtest.py report                                    # сравнение моделей, статистика различий, точность M4
  python abtest.py replay --days 1095 --exchange coinbase    # те же 4 модели по истории (проверка до запуска)
  python abtest.py dataset                                   # обучающая таблица: признаки + фактические исходы

Модели:
  M1  система как построена: переключатель режима A (в быке чистый тренд, иначе 4 слоя)          system.py
  M2  риск-менеджмент: режим B (размер по волатильности) + трейлинг-стоп 25% + тормоз при просадке портфеля 20%
  M3  вход в лучший момент по данным (зона дна: MVRV < 1 или цена ниже 200-недельной средней, иначе первый
      трендовый сигнал), затем просто держать без продаж
  M4  онлайн-модель: логистическая регрессия на трендовых признаках, каждый день дообучается на всей истории,
      прогнозирует рост за 7 дней; позиция = сглаженная уверенность × размер по волатильности

Исполнение виртуальных сделок: по живому стакану Bybit (средняя цена прохода по уровням) + комиссия тейкера 0.1%.
Все данные пишутся в data_lake/ (описание полей в DATA_SCHEMA.md). Реальные ордера не отправляются никогда.
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import system as S

FEE = 0.001            # тейкер спот Bybit
FALLBACK_SLIP = 0.0005 # если стакан недоступен (replay)
THRESHOLD = 0.02       # ребаланс, если вес ушёл больше чем на 2%
MODELS = ["M1", "M2", "M3", "M4"]
DAY = 86400000
M4_HORIZON = 7
M4_HALF_LIFE = None    # без забывания: в проверке 2018-2026 веса «свежее важнее» ухудшали результат
M4_LAMBDA = 50
M4_SMOOTH = 7          # EMA вероятности, меньше лишних сделок


def now_utc():
    return datetime.now(timezone.utc)


def log(msg):
    print(f"[{now_utc():%Y-%m-%d %H:%M:%S} UTC] {msg}", flush=True)


def today_utc():
    return pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()


# ====================================================================================== данные

class Feed:
    """Живые данные: свечи, тикер, стакан спота и деривативы с той же биржи (ccxt)."""

    def __init__(self, name="bybit"):
        import ccxt
        self.spot = getattr(ccxt, name)({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        self.perp = getattr(ccxt, name)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        for ex in (self.spot, self.perp):
            if ca:
                ex.validateServerSsl = ca
        self.name = name
        self.quote = "USD" if name in ("coinbase", "kraken") else "USDT"

    def sym(self, coin):
        return f"{coin}/{self.quote}"

    def psym(self, coin):
        return f"{coin}/USDT:USDT"

    def _retry(self, fn, *a, **kw):
        for attempt in range(4):
            try:
                return fn(*a, **kw)
            except Exception as e:
                if attempt == 3:
                    raise
                log(f"повтор {getattr(fn, '__name__', fn)}: {str(e)[:120]}")
                time.sleep(2 ** attempt)

    def daily(self, coin, days=1800):
        since = self.spot.milliseconds() - days * DAY
        rows = []
        while True:
            batch = self._retry(self.spot.fetch_ohlcv, self.sym(coin), "1d", since=since, limit=300)
            if not batch:
                break
            rows += batch
            since = batch[-1][0] + DAY
            if len(batch) < 300 or since > self.spot.milliseconds():
                break
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).drop_duplicates("t")
        df.index = pd.to_datetime(df.t, unit="ms")
        df = df.drop(columns="t").sort_index()
        return df[df.index < today_utc()]     # только закрытые свечи

    def snapshot(self, coin, depth=50):
        """Всё, что известно о рынке монеты прямо сейчас. Каждое поле опционально (None, если биржа не отдала)."""
        snap = {"ts": now_utc().isoformat(), "exchange": self.name, "coin": coin}

        def safe(key, fn):
            try:
                snap[key] = fn()
            except Exception as e:
                snap[key] = None
                snap.setdefault("errors", {})[key] = str(e)[:200]

        safe("ticker", lambda: {k: self.spot.fetch_ticker(self.sym(coin)).get(k) for k in
                                ("last", "bid", "ask", "baseVolume", "quoteVolume", "percentage", "vwap")})
        def book():
            ob = self.spot.fetch_order_book(self.sym(coin), depth)
            lv = lambda side: [[float(x[0]), float(x[1])] for x in ob.get(side, [])]   # у части бирж 3 поля на уровень
            return {"bids": lv("bids"), "asks": lv("asks"), "timestamp": ob.get("timestamp")}
        safe("orderbook", book)
        if snap.get("orderbook"):
            b, a = snap["orderbook"]["bids"], snap["orderbook"]["asks"]
            if b and a:
                mid = (b[0][0] + a[0][0]) / 2
                snap["mid"] = mid
                snap["spread_bps"] = (a[0][0] - b[0][0]) / mid * 1e4
                snap["depth_1pct_bid_usd"] = sum(p * q for p, q in b if p >= mid * 0.99)
                snap["depth_1pct_ask_usd"] = sum(p * q for p, q in a if p <= mid * 1.01)
        if self.name != "coinbase":
            safe("funding", lambda: {k: self.perp.fetch_funding_rate(self.psym(coin)).get(k) for k in
                                     ("fundingRate", "fundingTimestamp", "markPrice", "indexPrice", "interestRate")})
            safe("open_interest", lambda: {k: self.perp.fetch_open_interest(self.psym(coin)).get(k) for k in
                                           ("openInterestAmount", "openInterestValue", "timestamp")})
            safe("long_short", lambda: [{k: r.get(k) for k in ("timestamp", "longShortRatio")} for r in
                                        self.perp.fetch_long_short_ratio_history(self.psym(coin), "1h", limit=1)])
        return snap


def onchain(cache_dir):
    """Coin Metrics + DefiLlama, кэш на 12 часов. ETH-MVRV и цены CM нужны для зоны дна M3."""
    path = Path(cache_dir) / "onchain_cache.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 12 * 3600:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    oc = S.fetch_onchain()
    try:
        url = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=eth,btc"
               "&metrics=CapMVRVCur,PriceUSD&frequency=1d&start_time=2014-01-01&page_size=10000")
        rows = []
        while url:
            d = S._get_json(url)
            rows += d["data"]
            url = d.get("next_page_url")
        x = pd.DataFrame(rows)
        x["time"] = pd.to_datetime(x.time).dt.tz_localize(None).dt.floor("D")
        for asset in ("btc", "eth"):
            y = x[x.asset == asset].set_index("time")
            oc[f"{asset}_mvrv"] = pd.to_numeric(y.CapMVRVCur, errors="coerce").reindex(oc.index)
            oc[f"{asset}_price"] = pd.to_numeric(y.PriceUSD, errors="coerce").reindex(oc.index)
    except Exception as e:
        log(f"ETH on-chain недоступен: {e}")
    path.parent.mkdir(parents=True, exist_ok=True)
    oc.to_csv(path)
    return oc


def onchain_features(oc):
    lg = lambda c: np.log(oc[c].where(oc[c] > 0))
    f = pd.DataFrame(index=oc.index)
    f["oc_addr_growth30"] = lg("AdrBalCnt").diff(30)
    f["oc_hashprice_z"] = S.zz(np.log((oc.IssTotUSD / oc.HashRate).rolling(7).mean()))
    f["oc_stables_growth30"] = lg("stables").diff(30)
    f["oc_hash_ribbons"] = np.log(oc.HashRate.rolling(30).mean() / oc.HashRate.rolling(60).mean())
    mv = oc.CapMVRVCur
    f["oc_btc_mvrv"] = mv
    f["oc_btc_mvrv_pct"] = mv.expanding(365).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    return f.shift(1)   # публикуется после закрытия дня


# ====================================================================================== признаки

def coin_features(c, btc, ocf):
    """Признаки монеты на закрытие каждого дня (без заглядывания вперёд). Их же видит M4 и пишет recorder."""
    lc = np.log(c)
    r = lc.diff()
    b = btc.reindex(c.index).ffill()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f = pd.DataFrame(index=c.index)
    f["ret_1d"] = r
    f["mom_7d"] = lc.diff(7)
    f["mom_30d"] = lc.diff(30)
    f["mom_90d"] = lc.diff(90)
    f["rsi14"] = S.rsi(c) / 100
    f["macd_hist_norm"] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    f["dist_sma50"] = c / c.rolling(50).mean() - 1
    f["dist_sma200"] = c / c.rolling(200).mean() - 1
    f["vol30"] = r.rolling(30).std() * math.sqrt(365)
    f["vol_ratio_7_30"] = r.rolling(7).std() / r.rolling(30).std()
    f["btc_mom_30d"] = np.log(b).diff(30)
    sma = b.rolling(200).mean()
    f["btc_bull"] = ((b > sma) & (sma > sma.shift(30))).astype(float)
    f = f.join(ocf.reindex(c.index).ffill())
    return f


# Записываются все признаки, модель учится на 6 трендовых: в отборе 2018-2023 и проверке 2023-2026 полный набор
# из 16 признаков (с ончейн) переобучался и уходил в минус, трендовые 6 дали Sharpe 0.74 / 0.70.
ALL_FEATURES = ["mom_7d", "mom_30d", "mom_90d", "rsi14", "macd_hist_norm", "dist_sma50", "dist_sma200", "vol30",
                "vol_ratio_7_30", "btc_mom_30d", "btc_bull", "oc_addr_growth30", "oc_hashprice_z",
                "oc_stables_growth30", "oc_hash_ribbons", "oc_btc_mvrv_pct"]
M4_FEATURES = ["mom_30d", "mom_90d", "dist_sma50", "dist_sma200", "btc_mom_30d", "btc_bull"]


# ====================================================================================== модели

def weighted_logit(X, y, w, lam=2.0, it=30):
    beta = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
        W = w * p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + lam * np.eye(len(beta))
        g = X.T @ (w * (p - y)) + lam * beta
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-6:
            break
    return beta


class M4Online:
    """Пулированная (все монеты) логистическая регрессия, переобучение каждый день, экспоненциальные веса."""

    def fit_predict(self, feats: dict, closes: dict, day):
        Xs, ys, ws, ages = [], [], [], []
        for k, f in feats.items():
            c = closes[k]
            fwd = np.log(c.shift(-M4_HORIZON) / c)
            d = f[M4_FEATURES].join(fwd.rename("fwd")).loc[:day - pd.Timedelta(days=M4_HORIZON)].dropna()
            Xs.append(d[M4_FEATURES].values)
            ys.append((d.fwd > 0).astype(float).values)
            ages.append((day - d.index).days.values)
        X, y, age = np.vstack(Xs), np.concatenate(ys), np.concatenate(ages)
        if len(y) < 300:
            return None
        w = 0.5 ** (age / M4_HALF_LIFE) if M4_HALF_LIFE else np.ones(len(y))
        mu, sd = X.mean(0), X.std(0) + 1e-9
        Xz = np.c_[np.ones(len(X)), (X - mu) / sd]
        beta = weighted_logit(Xz, y, w, lam=M4_LAMBDA)
        out = {}
        for k, f in feats.items():
            x = f.loc[:day, M4_FEATURES].iloc[-1].values
            if np.isnan(x).any():
                out[k] = None
                continue
            out[k] = float(1 / (1 + np.exp(-np.r_[1, (x - mu) / sd] @ beta)))
        return dict(prob=out, coef=dict(zip(["const"] + M4_FEATURES, np.round(beta, 4).tolist())), n_train=int(len(y)),
                    base_rate=float(np.average(y, weights=w)))


def decide(model, ctx, mstate, equity_hist):
    """Целевые веса (доля всего капитала модели) по монетам и объяснение решения. None = не трогать позицию."""
    coins, closes, layers, feats, day = ctx["coins"], ctx["closes"], ctx["layers"], ctx["feats"], ctx["day"]
    n, btc = len(coins), ctx["btc"]
    targets, diag = {}, {}
    if model in ("M1", "M2"):
        p = S.Params(mode="A" if model == "M1" else "B")
        for k in coins:
            d = S.coin_position(closes[k].loc[:day], btc.loc[:day], layers, p, detail=True).iloc[-1]
            pos = float(np.nan_to_num(d.pos))
            info = {kk: (float(v) if pd.notna(v) else None) for kk, v in d.items()}
            if model == "M2":
                st = mstate.setdefault(k, {"peak": None, "stopped": False})
                px = float(closes[k].loc[day])
                if st["stopped"]:
                    if d.trend == 0:
                        st["stopped"] = False          # сигнал сбросился, можно снова входить
                    pos = 0.0
                    info["stop"] = "ждём сброса сигнала после стопа"
                elif pos > 0:
                    st["peak"] = max(st["peak"] or px, px)
                    if px < st["peak"] * 0.75:
                        st.update(stopped=True, peak=None)
                        pos = 0.0
                        info["stop"] = "трейлинг-стоп 25% от максимума"
                else:
                    st["peak"] = None
            targets[k] = pos / n
            diag[k] = info
        if model == "M2" and equity_hist:
            eq = pd.Series(equity_hist)
            dd = eq.iloc[-1] / eq.max() - 1
            if dd < -0.20:
                targets = {k: v * 0.5 for k, v in targets.items()}
                diag["_portfolio"] = {"brake": f"просадка {dd:.0%} > 20%, экспозиция ×0.5"}
    elif model == "M3":
        for k in coins:
            st = mstate.setdefault(k, {"entered": False, "entry_day": None, "reason": None})
            if st["entered"]:
                targets[k] = None
                diag[k] = {"hold": True, "entry_day": st["entry_day"], "reason": st["reason"]}
                continue
            f = feats[k].loc[:day].iloc[-1]
            mvrv = ctx["coin_mvrv"].get(k)
            mv = float(mvrv.loc[:day].iloc[-1]) if mvrv is not None and len(mvrv.loc[:day]) else np.nan
            p200w = ctx["coin_sma1400_ratio"].get(k)
            r200 = float(p200w.loc[:day].iloc[-1]) if p200w is not None and len(p200w.loc[:day]) else np.nan
            trend = S.coin_position(closes[k].loc[:day], btc.loc[:day], layers, S.Params(mode="A"), detail=True).trend.iloc[-1]
            reason = ("MVRV < 1" if mv < 1 else "цена ниже 200-недельной средней" if r200 < 1
                      else "трендовый сигнал" if trend == 1 else None)
            if reason:
                st.update(entered=True, entry_day=str(day.date()), reason=reason)
                targets[k] = 1.0 / n
            else:
                targets[k] = 0.0
            diag[k] = {"mvrv": mv, "price_to_200w": r200, "trend": float(trend), "entry": reason}
    elif model == "M4":
        res = M4Online().fit_predict({k: feats[k] for k in coins}, closes, day)
        for k in coins:
            prob = None if res is None else res["prob"][k]
            ema = mstate.get(f"ema_{k}")
            if prob is not None:
                ema = prob if ema is None else ema + 2 / (M4_SMOOTH + 1) * (prob - ema)
                mstate[f"ema_{k}"] = ema
            vol = float(feats[k].loc[day, "vol30"])
            size = 0.0 if ema is None else float(np.clip((ema - 0.5) / 0.1, 0, 1) * min(1, 0.5 / max(vol, 1e-6)))
            targets[k] = size / n
            diag[k] = {"prob_up_7d": prob, "prob_smoothed": ema, "vol30": vol, "size": size}
        if res:
            diag["_model"] = {"coef": res["coef"], "n_train": res["n_train"], "base_rate": res["base_rate"]}
    return targets, diag


# ====================================================================================== виртуальный брокер

def fill_price(side, notional, book, ref):
    """Средняя цена исполнения рыночной заявки на notional по уровням стакана."""
    if not book or not book.get("asks") or not book.get("bids"):
        return ref * (1 + FALLBACK_SLIP if side == "buy" else 1 - FALLBACK_SLIP), 0
    levels = book["asks"] if side == "buy" else book["bids"]
    left, qty, used = notional, 0.0, 0
    for px, q in levels:
        take = min(left, px * q)
        qty += take / px
        left -= take
        used += 1
        if left <= 1e-9:
            break
    if left > 1e-9:                                # стакан кончился: остаток по последнему уровню с запасом 1%
        last = levels[-1][0] * (1.01 if side == "buy" else 0.99)
        qty += left / last
    return notional / qty, used


def execute(acct, targets, prices, books, model, day, rec):
    equity = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in prices)
    fills = []
    order = sorted(targets, key=lambda k: -1 if targets[k] is None else targets[k] - acct["units"].get(k, 0) * prices[k] / equity)
    for k in order:
        if targets[k] is None:
            continue
        cur_w = acct["units"].get(k, 0) * prices[k] / equity
        diff = targets[k] - cur_w
        if abs(diff) <= THRESHOLD and not (targets[k] == 0 and cur_w > 0.0005):
            continue
        side = "buy" if diff > 0 else "sell"
        notional = abs(diff) * equity if not (targets[k] == 0) else acct["units"].get(k, 0) * prices[k]
        if side == "buy":
            notional = min(notional, acct["cash"] / (1 + FEE))
        if notional < 5:
            continue
        book = (books or {}).get(k)
        px, used = fill_price(side, notional, book, prices[k])
        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2 if book and book.get("bids") and book.get("asks") else prices[k]
        qty = notional / px
        if side == "buy":
            acct["cash"] -= notional * (1 + FEE)
            acct["units"][k] = acct["units"].get(k, 0) + qty
        else:
            qty = min(qty, acct["units"].get(k, 0))
            acct["cash"] += qty * px * (1 - FEE)
            acct["units"][k] = acct["units"].get(k, 0) - qty
        f = dict(ts=now_utc().isoformat(), day=str(day.date()), model=model, coin=k, side=side, qty=qty, vwap=px,
                 mid=mid, slippage_bps=(px / mid - 1) * 1e4 * (1 if side == "buy" else -1), notional=qty * px,
                 fee=qty * px * FEE, book_levels_used=used, target_w=targets[k], prev_w=cur_w,
                 book_source="live" if book else "fallback")
        fills.append(f)
        if rec:
            rec.append_csv("orders/fills.csv", f)
    return fills


# ====================================================================================== запись данных

class Recorder:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def append_jsonl(self, rel, obj):
        with open(self.path(rel), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False, default=lambda x: None if x is None or (isinstance(x, float) and math.isnan(x)) else str(x)) + "\n")

    def append_csv(self, rel, row):
        p = self.path(rel)
        pd.DataFrame([row]).to_csv(p, mode="a", header=not p.exists(), index=False)

    def upsert_csv(self, rel, df):
        p = self.path(rel)
        if p.exists():
            old = pd.read_csv(p, index_col=0, parse_dates=True)
            df = pd.concat([old[~old.index.isin(df.index)], df]).sort_index()
        df.to_csv(p)


# ====================================================================================== контекст дня

def build_context(closes_full, ocf, oc, layers, coins, day):
    btc = closes_full["BTC"]
    feats = {k: coin_features(closes_full[k], btc, ocf) for k in coins}
    coin_mvrv, coin_200w = {}, {}
    for k in coins:
        if oc is not None and f"{k.lower()}_mvrv" in oc:
            coin_mvrv[k] = oc[f"{k.lower()}_mvrv"].shift(1)
            cm_px = oc[f"{k.lower()}_price"]
            coin_200w[k] = (cm_px / cm_px.rolling(1400).mean()).shift(1)
        else:
            coin_mvrv[k] = None
            c = closes_full[k]
            coin_200w[k] = c / c.rolling(1400, min_periods=1400).mean()
    return dict(coins=coins, closes=closes_full, btc=btc, layers=layers, feats=feats, day=day,
                coin_mvrv=coin_mvrv, coin_sma1400_ratio=coin_200w)


def label_m4_predictions(rec, closes):
    """Дописать фактический исход к прогнозам M4, у которых горизонт уже прошёл (для анализа и дообучения)."""
    p = rec.root / "models/m4_predictions.csv"
    if not p.exists():
        return
    d = pd.read_csv(p)
    for i, r in d[d.realized_ret.isna()].iterrows():
        c = closes.get(r.coin)
        t0, t1 = pd.Timestamp(r.day), pd.Timestamp(r.day) + pd.Timedelta(days=M4_HORIZON)
        if c is not None and t1 in c.index and t0 in c.index:
            ret = float(np.log(c[t1] / c[t0]))
            d.loc[i, "realized_ret"] = ret
            d.loc[i, "realized_up"] = float(ret > 0)
            d.loc[i, "correct"] = float((r.prob_up_7d > 0.5) == (ret > 0)) if pd.notna(r.prob_up_7d) else np.nan
    d.to_csv(p, index=False)


# ====================================================================================== команды

def state_path(a):
    return Path(a.dir) / "state.json"


def load_state(a):
    p = state_path(a)
    if not p.exists():
        raise SystemExit(f"нет {p}: сначала python abtest.py init --capital 10000 --coins BTC,ETH")
    return json.loads(p.read_text())


def save_state(a, st):
    state_path(a).write_text(json.dumps(st, indent=2, ensure_ascii=False))


def cmd_init(a):
    Path(a.dir).mkdir(parents=True, exist_ok=True)
    coins = a.coins.split(",")
    if "BTC" not in coins:
        coins = ["BTC"] + coins
    st = dict(created=now_utc().isoformat(), capital=a.capital, coins=coins, exchange=a.exchange, last_cycle=None,
              accounts={m: {"cash": a.capital, "units": {}} for m in MODELS},
              model_state={m: {} for m in MODELS}, equity_hist={m: {} for m in MODELS})
    save_state(a, st)
    log(f"создано 4 виртуальных счёта по {a.capital:,.0f} {a.exchange} {coins}, папка {a.dir}")


def cmd_snapshot(a, feed=None, st=None):
    st = st or load_state(a)
    feed = feed or Feed(st["exchange"])
    rec = Recorder(Path(a.dir) / "data_lake")
    books = {}
    for k in st["coins"]:
        s = feed.snapshot(k)
        rec.append_jsonl(f"market/snapshots/{s['ts'][:10]}.jsonl", s)
        books[k] = s.get("orderbook")
        log(f"снимок {k}: last {((s.get('ticker') or {}).get('last'))}, спред {s.get('spread_bps', float('nan')):.1f} bps, "
            f"funding {((s.get('funding') or {}).get('fundingRate'))}")
    return books


def cmd_cycle(a):
    st = load_state(a)
    feed = Feed(st["exchange"])
    rec = Recorder(Path(a.dir) / "data_lake")
    coins = st["coins"]
    closes_df = {k: feed.daily(k) for k in coins}
    closes = {k: v.close for k, v in closes_df.items()}
    day = max(c.index[-1] for c in closes.values())
    if st["last_cycle"] == str(day.date()) and not a.force:
        log(f"цикл за свечу {day.date()} уже выполнен")
        return
    for k, v in closes_df.items():
        rec.upsert_csv(f"market/ohlcv_1d/{k}.csv", v)
    oc = onchain(a.dir)
    ocf = onchain_features(oc)
    layers = S.market_layers(oc, S.Params())
    rec.upsert_csv("market/onchain_daily.csv", oc)
    books = cmd_snapshot(a, feed, st)                 # живой стакан для исполнения
    ctx = build_context(closes, ocf, oc, layers, coins, day)
    prices = {k: float(((books.get(k) or {}).get("bids") or [[closes[k].iloc[-1]]])[0][0]) for k in coins}
    feat_rows = {k: ctx["feats"][k].loc[day].to_dict() for k in coins}
    rec.append_jsonl(f"features/{day.date()}.jsonl", {"day": str(day.date()), "features": feat_rows,
                                                      "layers": layers.reindex([day]).iloc[0].to_dict()})
    label_m4_predictions(rec, closes)
    for m in MODELS:
        acct, ms = st["accounts"][m], st["model_state"][m]
        targets, diag = decide(m, ctx, ms, list(st["equity_hist"][m].values()))
        fills = execute(acct, targets, prices, books, m, day, rec)
        eq = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in coins)
        st["equity_hist"][m][str(day.date())] = eq
        rec.append_jsonl(f"decisions/{day.date()}.jsonl", {"day": str(day.date()), "model": m, "targets": targets,
                                                           "diag": diag, "n_fills": len(fills)})
        rec.append_csv("equity/equity.csv", dict(day=str(day.date()), ts=now_utc().isoformat(), model=m, equity=eq,
                                                 cash=acct["cash"], **{f"w_{k}": acct["units"].get(k, 0) * prices[k] / eq for k in coins}))
        if m == "M4":
            for k in coins:
                rec.append_csv("models/m4_predictions.csv", dict(day=str(day.date()), coin=k,
                               prob_up_7d=diag[k]["prob_up_7d"], prob_smoothed=diag[k]["prob_smoothed"],
                               size=diag[k]["size"], realized_ret=np.nan,
                               realized_up=np.nan, correct=np.nan))
            if "_model" in diag:
                rec.append_jsonl("models/m4_versions.jsonl", {"day": str(day.date()), **diag["_model"]})
        log(f"{m}: сделок {len(fills)}, капитал {eq:,.2f} ({eq / st['capital'] - 1:+.2%})")
    st["last_cycle"] = str(day.date())
    save_state(a, st)


def cmd_loop(a):
    log("loop: снимок рынка каждый час, дневной цикл в 00:05 UTC. Ctrl+C для остановки")
    while True:
        try:
            st = load_state(a)
            n = now_utc()
            if st["last_cycle"] != str((n - timedelta(days=1)).date()) and (n.hour > 0 or n.minute >= 5):
                cmd_cycle(a)                       # вчерашняя свеча закрыта и ещё не обработана (догоняет пропуски)
            else:
                cmd_snapshot(a)
        except SystemExit:
            raise
        except Exception as e:
            log(f"ошибка: {e}")
        n = now_utc()
        nxt = (n + timedelta(hours=1)).replace(minute=5, second=0, microsecond=0)
        time.sleep(max(30, (nxt - n).total_seconds()))


def stats(eq):
    r = eq.pct_change().dropna()
    if len(r) < 2:
        return dict(ret=eq.iloc[-1] / eq.iloc[0] - 1, cagr=np.nan, sharpe=np.nan, dd=(eq / eq.cummax() - 1).min())
    yrs = len(r) / 365
    return dict(ret=eq.iloc[-1] / eq.iloc[0] - 1, cagr=(eq.iloc[-1] / eq.iloc[0]) ** (1 / max(yrs, 1e-9)) - 1,
                sharpe=r.mean() / (r.std() + 1e-12) * math.sqrt(365), dd=(eq / eq.cummax() - 1).min())


def compare(E, capital):
    """Таблица по моделям и бутстрэп разницы дневных доходностей каждой модели против M1."""
    rows = {m: stats(E[m]) for m in E}
    t = pd.DataFrame(rows).T
    r = E.pct_change().dropna()
    rng = np.random.default_rng(0)
    for m in E:
        if m == "M1" or len(r) < 30:
            continue
        d = (r[m] - r["M1"]).values
        boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
        t.loc[m, "P(лучше M1)"] = float(np.mean(np.array(boots) > 0))
    return t


def cmd_report(a):
    st = load_state(a)
    rec = Recorder(Path(a.dir) / "data_lake")
    E = pd.DataFrame({m: pd.Series(st["equity_hist"][m]) for m in MODELS})
    if E.empty:
        print("пока нет данных: запустите cycle")
        return
    E.index = pd.to_datetime(E.index)
    print(f"A/B-тест с {E.index[0].date()} по {E.index[-1].date()} ({len(E)} дней), старт {st['capital']:,.0f}")
    print(compare(E, st["capital"]).to_string(formatters={"ret": "{:+.1%}".format, "cagr": "{:+.0%}".format,
                                                         "sharpe": "{:.2f}".format, "dd": "{:+.1%}".format}))
    f = rec.root / "orders/fills.csv"
    if f.exists():
        fl = pd.read_csv(f)
        print("\nСделки:")
        print(fl.groupby("model").agg(сделок=("qty", "size"), оборот=("notional", "sum"), комиссии=("fee", "sum"),
                                      проскальзывание_bps=("slippage_bps", "mean")).to_string(float_format=lambda v: f"{v:,.2f}"))
    p = rec.root / "models/m4_predictions.csv"
    if p.exists():
        d = pd.read_csv(p).dropna(subset=["correct"])
        if len(d):
            print(f"\nM4: прогнозов с известным исходом {len(d)}, точность направления {d.correct.mean():.1%}, "
                  f"доля роста {d.realized_up.mean():.1%}")
    if len(E) < 90:
        print("\nМеньше 90 дней: различия между моделями пока статистически ничего не значат.")


def cmd_replay(a):
    """Те же модели и тот же код по истории. Стакана в прошлом нет: исполнение по закрытию + проскальзывание 5 bps."""
    feed = Feed(a.exchange)
    coins = a.coins.split(",")
    if "BTC" not in coins:
        coins = ["BTC"] + coins
    Path(a.dir).mkdir(parents=True, exist_ok=True)
    closes = {k: feed.daily(k, days=a.days + 1500).close for k in coins}
    oc = onchain(a.dir)
    ocf = onchain_features(oc)
    layers = S.market_layers(oc, S.Params())
    common = sorted(set.intersection(*[set(c.index) for c in closes.values()]))
    days = common[-a.days:]
    accts = {m: {"cash": a.capital, "units": {}} for m in MODELS}
    mstate = {m: {} for m in MODELS}
    eqh = {m: {} for m in MODELS}
    ctx = build_context(closes, ocf, oc, layers, coins, days[0])
    m4_rows, nfills = [], {m: 0 for m in MODELS}
    for i, day in enumerate(days):
        ctx["day"] = day
        prices = {k: float(closes[k].loc[day]) for k in coins}
        for m in MODELS:
            targets, diag = decide(m, ctx, mstate[m], list(eqh[m].values()))
            nfills[m] += len(execute(accts[m], targets, prices, None, m, day, None))
            eqh[m][day] = accts[m]["cash"] + sum(accts[m]["units"].get(k, 0) * prices[k] for k in coins)
            if m == "M4":
                for k in coins:
                    m4_rows.append(dict(day=day, coin=k, prob=diag[k]["prob_up_7d"]))
        if i % 100 == 0:
            log(f"replay {day.date()} ({i}/{len(days)})")
    E = pd.DataFrame(eqh)
    hold = pd.DataFrame({k: closes[k].loc[days] / closes[k].loc[days[0]] for k in coins}).mean(axis=1) * a.capital
    E["держать поровну"] = hold
    print(f"\nReplay {days[0].date()} .. {days[-1].date()} ({len(days)} дней), монеты {coins}, данные {feed.name}")
    t = compare(E[MODELS + ["держать поровну"]], a.capital)
    t["сделок"] = pd.Series(nfills)
    print(t.to_string(formatters={"ret": "{:+.0%}".format, "cagr": "{:+.0%}".format, "sharpe": "{:.2f}".format,
                                  "dd": "{:+.0%}".format}))
    yearly = E.groupby(E.index.year).apply(lambda s: s.iloc[-1] / s.iloc[0] - 1)
    print("\nпо годам:")
    print(yearly.to_string(float_format=lambda v: f"{v:+.0%}"))
    pr = pd.DataFrame(m4_rows).dropna()
    if len(pr):
        pr["fwd"] = [np.log(closes[r.coin].shift(-M4_HORIZON).get(r.day, np.nan) / closes[r.coin].get(r.day)) for r in pr.itertuples()]
        pr = pr.dropna()
        acc = ((pr.prob > 0.5) == (pr.fwd > 0)).mean()
        base = max((pr.fwd > 0).mean(), 1 - (pr.fwd > 0).mean())
        conf = pr[pr.prob > 0.6]
        print(f"\nM4 вне выборки: точность направления {acc:.1%} при базовой {base:.1%}; "
              f"когда уверенность > 60% ({len(conf)} дней): рост в {(conf.fwd > 0).mean():.1%} случаев")
    E.to_csv(Path(a.dir) / "replay_equity.csv")
    print(f"кривые -> {Path(a.dir) / 'replay_equity.csv'}")


def cmd_dataset(a):
    """Обучающая таблица из записанных данных: признаки дня + будущие доходности (метки) на 1/7/30 дней."""
    root = Path(a.dir) / "data_lake"
    rows = []
    for f in sorted((root / "features").glob("*.jsonl")):
        for line in open(f, encoding="utf-8"):
            x = json.loads(line)
            for coin, feats in x["features"].items():
                rows.append({"day": x["day"], "coin": coin, **feats, **{f"layer_{k}": v for k, v in (x.get("layers") or {}).items()}})
    if not rows:
        raise SystemExit("нет записанных признаков: нужны дневные циклы")
    d = pd.DataFrame(rows)
    d["day"] = pd.to_datetime(d.day)
    for coin in d.coin.unique():
        c = pd.read_csv(root / f"market/ohlcv_1d/{coin}.csv", index_col=0, parse_dates=True).close
        m = d.coin == coin
        for h in (1, 7, 30):
            fwd = np.log(c.shift(-h) / c)
            d.loc[m, f"fwd_ret_{h}d"] = d.loc[m, "day"].map(fwd)
    out = root / "ml/training_set.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False)
    print(f"{len(d)} строк, {d.day.min().date()} .. {d.day.max().date()}, с метками 7д: {d.fwd_ret_7d.notna().sum()} -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "cycle", "snapshot", "loop", "report", "replay", "dataset"])
    ap.add_argument("--exchange", default="bybit")
    ap.add_argument("--coins", default="BTC,ETH")
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--dir", default="abtest")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"init": cmd_init, "cycle": cmd_cycle, "snapshot": cmd_snapshot, "loop": cmd_loop, "report": cmd_report,
     "replay": cmd_replay, "dataset": cmd_dataset}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
