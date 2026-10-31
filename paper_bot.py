#!/usr/bin/env python3
"""Бумажная торговля по итоговой системе (system.py) на Bybit.

  python paper_bot.py reset --capital 10000          # начать бумажный счёт
  python paper_bot.py signals                         # текущие сигналы по монетам, без сделок
  python paper_bot.py run                             # один цикл: посчитать цели и исполнить (раз в день после 00:00 UTC)
  python paper_bot.py loop                            # работать постоянно: цикл каждый день в 00:05 UTC
  python paper_bot.py status                          # портфель, доходность, сравнение с держанием
  python paper_bot.py replay --days 365               # прогнать бота по прошлым дням тем же кодом (проверка)

Режимы:
  --mode paper    (по умолчанию) сделки только в локальном файле, цены с биржи, комиссия и проскальзывание учитываются
  --mode testnet  рыночные ордера на тестнете Bybit (testnet.bybit.com); ключи тестнета в BYBIT_TESTNET_KEY и
                  BYBIT_TESTNET_SECRET. Основной счёт программа не использует ни в каком режиме.

Опции: --coins BTC,ETH  --system A|B  --exchange bybit  --threshold 0.02  --dir paper
"""
import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import system as S

FEE = 0.001       # комиссия тейкера спота Bybit (уточните по своему уровню)
SLIP = 0.0005     # проскальзывание рыночного ордера
DAY = 86400000


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC] {msg}", flush=True)


# ---------- данные ----------

def make_exchange(name, testnet=False):
    import ccxt
    ex = getattr(ccxt, name)({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca:
        ex.validateServerSsl = ca
    if testnet:
        if name != "bybit":
            raise SystemExit("testnet поддерживается только для bybit")
        ex.set_sandbox_mode(True)
        ex.apiKey = os.environ.get("BYBIT_TESTNET_KEY")
        ex.secret = os.environ.get("BYBIT_TESTNET_SECRET")
        if not ex.apiKey or not ex.secret:
            raise SystemExit("Задайте BYBIT_TESTNET_KEY и BYBIT_TESTNET_SECRET (ключи с testnet.bybit.com)")
    return ex


def quote_for(ex_name):
    return "USD" if ex_name in ("coinbase", "kraken") else "USDT"


def fetch_daily(ex, symbol, days=600):
    since = ex.milliseconds() - days * DAY
    rows = []
    while True:
        for attempt in range(5):
            try:
                batch = ex.fetch_ohlcv(symbol, "1d", since=since, limit=300)
                break
            except Exception as e:
                if attempt == 4:
                    raise
                log(f"повтор {symbol}: {e}")
                time.sleep(2 ** attempt)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + DAY
        if len(batch) < 300 or since > ex.milliseconds():
            break
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"]).drop_duplicates("t")
    s = pd.Series(df.c.values, index=pd.to_datetime(df.t, unit="ms")).sort_index()
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    return s[s.index < today]   # только закрытые дневные свечи


def load_layers(cache_dir, p):
    path = Path(cache_dir) / "onchain_cache.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < 12 * 3600
    try:
        if fresh:
            oc = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            log("загрузка ончейн-данных (Coin Metrics, DefiLlama)...")
            oc = S.fetch_onchain()
            oc.to_csv(path)
        return S.market_layers(oc, p), True
    except Exception as e:
        log(f"ВНИМАНИЕ: ончейн-данные недоступны ({e}); слои цикла и подтверждений отключены (=1)")
        return None, False


def compute_targets(ex, coins, p, cache_dir, days=600):
    q = quote_for(ex.id)
    closes = {c: fetch_daily(ex, f"{c}/{q}", days) for c in coins}
    btc = closes["BTC"] if "BTC" in closes else fetch_daily(ex, f"BTC/{q}", days)
    layers, ok = load_layers(cache_dir, p)
    if layers is None:
        idx = btc.index
        layers = pd.DataFrame({"regime": 1.0, "conf": 1.0}, index=idx)
    detail = {c: S.coin_position(closes[c], btc, layers, p, detail=True) for c in coins}
    return closes, btc, layers, detail


# ---------- счёт ----------

class PaperAccount:
    def __init__(self, d):
        self.dir = Path(d)
        self.state_path = self.dir / "state.json"

    def load(self):
        if not self.state_path.exists():
            raise SystemExit(f"Нет счёта в {self.dir}. Запустите: python paper_bot.py reset --capital 10000")
        return json.loads(self.state_path.read_text())

    def save(self, st):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st, indent=2, ensure_ascii=False))

    def log_trade(self, row):
        f = self.dir / "trades.csv"
        pd.DataFrame([row]).to_csv(f, mode="a", header=not f.exists(), index=False)

    def log_equity(self, row):
        f = self.dir / "equity.csv"
        pd.DataFrame([row]).to_csv(f, mode="a", header=not f.exists(), index=False)


def rebalance_paper(st, prices, targets, threshold, date, acct=None):
    """Сделки по последней цене закрытия с комиссией и проскальзыванием. Возвращает список сделок."""
    equity = st["cash"] + sum(st["units"].get(k, 0) * prices[k] for k in prices)
    trades = []
    # сначала продажи, чтобы освободить кэш
    order = sorted(targets, key=lambda k: targets[k] - st["units"].get(k, 0) * prices[k] / equity)
    for k in order:
        cur_w = st["units"].get(k, 0) * prices[k] / equity
        diff = targets[k] - cur_w
        if abs(diff) <= threshold and not (targets[k] == 0 and cur_w > 0):
            continue
        if abs(diff) * equity < 5:      # минимальный размер ордера
            continue
        notional = diff * equity
        if notional > 0:
            notional = min(notional, st["cash"] / (1 + FEE))
            px = prices[k] * (1 + SLIP)
            qty = notional / px
            st["cash"] -= notional * (1 + FEE)
            side = "buy"
        else:
            qty = min(-notional / prices[k], st["units"].get(k, 0))
            px = prices[k] * (1 - SLIP)
            st["cash"] += qty * px * (1 - FEE)
            qty = -qty
            side = "sell"
        st["units"][k] = st["units"].get(k, 0) + qty
        t = dict(date=str(date.date()), coin=k, side=side, qty=abs(qty), price=px, notional=abs(qty) * px,
                 fee=abs(qty) * px * FEE, target_w=round(targets[k], 4), prev_w=round(cur_w, 4))
        trades.append(t)
        if acct:
            acct.log_trade(t)
    return trades


def rebalance_testnet(ex, coins, targets, threshold):
    q = quote_for(ex.id)
    ex.load_markets()
    bal = ex.fetch_balance()
    tick = {k: ex.fetch_ticker(f"{k}/{q}")["last"] for k in coins}
    free = lambda a: float(bal.get(a, {}).get("total", 0) or 0)
    equity = free(q) + sum(free(k) * tick[k] for k in coins)
    log(f"testnet: капитал {equity:,.2f} {q}")
    done = []
    order = sorted(coins, key=lambda k: targets[k] - free(k) * tick[k] / equity)
    for k in order:
        sym = f"{k}/{q}"
        cur_w = free(k) * tick[k] / equity
        diff = targets[k] - cur_w
        if abs(diff) <= threshold and not (targets[k] == 0 and cur_w > 0.001):
            continue
        m = ex.market(sym)
        qty = abs(diff) * equity / tick[k]
        min_cost = (m.get("limits", {}).get("cost", {}) or {}).get("min") or 5
        if qty * tick[k] < min_cost:
            continue
        side = "buy" if diff > 0 else "sell"
        if side == "sell":
            qty = min(qty, free(k))
        amt = float(ex.amount_to_precision(sym, qty))
        if amt <= 0:
            continue
        o = ex.create_order(sym, "market", side, amt)
        log(f"testnet ордер {side} {amt} {sym}: {o.get('id')}")
        done.append(dict(coin=k, side=side, qty=amt, order_id=o.get("id")))
    return done, equity


# ---------- команды ----------

def cmd_reset(a):
    acct = PaperAccount(a.dir)
    for f in ("state.json", "trades.csv", "equity.csv"):
        (acct.dir / f).unlink(missing_ok=True)
    st = dict(created=datetime.now(timezone.utc).isoformat(), capital=a.capital, cash=a.capital, units={},
              coins=a.coins.split(","), system=a.system, last_run=None, hold_units=None)
    acct.save(st)
    log(f"бумажный счёт создан: {a.capital:,.0f} USDT, монеты {a.coins}, система {a.system}, папка {a.dir}")


def print_signals(detail, coins, date=None):
    rows = []
    for k in coins:
        d = detail[k].dropna(subset=["pos"])
        r = d.iloc[-1] if date is None else d.loc[:date].iloc[-1]
        rows.append({"монета": k, "дата свечи": d.index[-1].date() if date is None else date.date(),
                     "рынок BTC": "бык" if r.bull else "не бык",
                     "BTC 30д": f"{r.btc_mom:+.1%}", "RSI": f"{r.rsi:.0f}", "тренд": "да" if r.trend else "нет",
                     "размер по вол.": f"{r.vol_size:.0%}", "режим цикла": f"{r.regime:.0f}",
                     "ончейн «за»": f"{r.conf:.0%}", "позиция": f"{r.pos:.0%}"})
    print(pd.DataFrame(rows).to_string(index=False))


def cmd_signals(a):
    p = S.Params(mode=a.system)
    ex = make_exchange(a.exchange)
    coins = a.coins.split(",")
    _, _, _, detail = compute_targets(ex, coins, p, a.dir if Path(a.dir).exists() else ".")
    print_signals(detail, coins)
    print(f"\nВеса портфеля: каждой монете 1/{len(coins)} капитала × её позиция, остаток в {quote_for(ex.id)}")


def cmd_run(a):
    acct = PaperAccount(a.dir)
    st = acct.load()
    coins, p = st["coins"], S.Params(mode=st["system"])
    tex = make_exchange("bybit", testnet=True) if a.mode == "testnet" else None   # ключи проверяются сразу
    ex = make_exchange(a.exchange)
    closes, btc, layers, detail = compute_targets(ex, coins, p, a.dir)
    candle = max(c.index[-1] for c in closes.values())
    if st.get("last_run") == str(candle.date()) and not a.force:
        log(f"цикл для свечи {candle.date()} уже выполнен; следующая проверка после 00:00 UTC (или --force)")
        return
    targets = {k: float(np.nan_to_num(detail[k].pos.iloc[-1])) / len(coins) for k in coins}
    prices = {k: float(closes[k].iloc[-1]) for k in coins}
    print_signals(detail, coins)
    if a.mode == "testnet":
        done, equity = rebalance_testnet(tex, coins, targets, a.threshold)
        st["last_run"] = str(candle.date())
        acct.save(st)
        acct.log_equity(dict(date=str(candle.date()), equity=equity, mode="testnet"))
        log(f"testnet: ордеров {len(done)}")
        return
    if st.get("hold_units") is None:   # бенчмарк: купить и держать в тот же день равными долями
        st["hold_units"] = {k: st["capital"] / len(coins) / prices[k] for k in coins}
    trades = rebalance_paper(st, prices, targets, a.threshold, candle, acct)
    equity = st["cash"] + sum(st["units"].get(k, 0) * prices[k] for k in coins)
    hold = sum(st["hold_units"][k] * prices[k] for k in coins)
    st["last_run"] = str(candle.date())
    acct.save(st)
    acct.log_equity(dict(date=str(candle.date()), equity=round(equity, 2), hold=round(hold, 2), cash=round(st["cash"], 2),
                         **{f"w_{k}": round(st["units"].get(k, 0) * prices[k] / equity, 4) for k in coins}))
    for t in trades:
        log(f"{t['side']} {t['qty']:.6f} {t['coin']} по {t['price']:,.2f} (вес {t['prev_w']:.0%} -> {t['target_w']:.0%})")
    log(f"сделок {len(trades)}; капитал {equity:,.2f} ({equity / st['capital'] - 1:+.1%}), держание {hold / st['capital'] - 1:+.1%}")


def cmd_status(a):
    acct = PaperAccount(a.dir)
    st = acct.load()
    eqf = acct.dir / "equity.csv"
    print(f"Счёт: {st['capital']:,.0f} USDT старт {st['created'][:10]}, система {st['system']}, монеты {', '.join(st['coins'])}")
    print(f"Кэш: {st['cash']:,.2f}; позиции: " + ", ".join(f"{k} {v:.6f}" for k, v in st["units"].items() if v > 0))
    if eqf.exists():
        e = pd.read_csv(eqf)
        e = e[e.get("mode", pd.Series(["paper"] * len(e))).fillna("paper") != "testnet"] if "mode" in e else e
        if len(e):
            last = e.iloc[-1]
            dd = (e.equity / e.equity.cummax() - 1).min()
            print(f"На {last.date}: капитал {last.equity:,.2f} ({last.equity / st['capital'] - 1:+.1%}), "
                  f"держание {last.hold / st['capital'] - 1:+.1%}, макс. просадка {dd:.1%}, дней {len(e)}")
    tf = acct.dir / "trades.csv"
    if tf.exists():
        t = pd.read_csv(tf)
        print(f"Сделок всего {len(t)}, комиссий {t.fee.sum():,.2f}. Последние:")
        print(t.tail(10).to_string(index=False))


def cmd_replay(a):
    """Прогон по прошлым дням тем же кодом (rebalance_paper), чтобы проверить бота против бэктеста."""
    p = S.Params(mode=a.system)
    coins = a.coins.split(",")
    ex = make_exchange(a.exchange)
    Path(a.dir).mkdir(parents=True, exist_ok=True)
    closes, btc, layers, detail = compute_targets(ex, coins, p, a.dir, days=a.days + 450)
    idx = sorted(set.intersection(*[set(closes[k].index) for k in coins]))[-a.days:]
    st = dict(cash=a.capital, units={})
    hold_units = {k: a.capital / len(coins) / closes[k].loc[idx[0]] for k in coins}
    rows, ntr = [], 0
    for d in idx:
        prices = {k: float(closes[k].loc[d]) for k in coins}
        targets = {k: float(np.nan_to_num(detail[k].pos.loc[d])) / len(coins) for k in coins}
        ntr += len(rebalance_paper(st, prices, targets, a.threshold, d))
        eq = st["cash"] + sum(st["units"].get(k, 0) * prices[k] for k in coins)
        rows.append(dict(date=d, equity=eq, hold=sum(hold_units[k] * prices[k] for k in coins)))
    r = pd.DataFrame(rows).set_index("date")
    dd = lambda s: (s / s.cummax() - 1).min()
    yrs = len(r) / 365
    print(f"Replay {idx[0].date()} .. {idx[-1].date()} ({len(r)} дней), система {a.system}, монеты {a.coins}, данные {ex.id}")
    print(f"Бот:       {r.equity.iloc[-1] / a.capital - 1:+.1%} всего, {(r.equity.iloc[-1] / a.capital) ** (1 / yrs) - 1:+.1%} в год, "
          f"просадка {dd(r.equity):.1%}, сделок {ntr}")
    print(f"Держание:  {r.hold.iloc[-1] / a.capital - 1:+.1%} всего, {(r.hold.iloc[-1] / a.capital) ** (1 / yrs) - 1:+.1%} в год, "
          f"просадка {dd(r.hold):.1%}")
    out = Path(a.dir) / f"replay_{a.system}.csv"
    r.to_csv(out)
    print(f"Кривая -> {out}")


def cmd_loop(a):
    log("режим loop: цикл каждый день в 00:05 UTC. Остановка: Ctrl+C")
    while True:
        try:
            cmd_run(a)
        except SystemExit:
            raise
        except Exception as e:
            log(f"ошибка цикла: {e}; повтор через 15 минут")
            time.sleep(900)
            continue
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        log(f"следующий цикл {nxt:%Y-%m-%d %H:%M} UTC")
        time.sleep(max(60, (nxt - now).total_seconds()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["reset", "signals", "run", "loop", "status", "replay"])
    ap.add_argument("--mode", choices=["paper", "testnet"], default="paper")
    ap.add_argument("--exchange", default="bybit", help="откуда брать цены (bybit; для проверки можно coinbase)")
    ap.add_argument("--coins", default="BTC,ETH")
    ap.add_argument("--system", choices=["A", "B"], default="A", help="A: бык -> чистый тренд; B: бык -> тренд × волатильность")
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--threshold", type=float, default=0.02, help="сделка, если вес отклонился больше порога")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--dir", default="paper")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"reset": cmd_reset, "signals": cmd_signals, "run": cmd_run, "loop": cmd_loop, "status": cmd_status,
     "replay": cmd_replay}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
