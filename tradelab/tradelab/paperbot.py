"""Stage 5 paper trading: the research strategy on live public data, fills simulated on Hyperliquid.

Signals come from Binance USDT-M public endpoints, the same source as the backtest (klines with taker
volume, settled funding). Orders are simulated as taker orders that walk Hyperliquid's live order book
(l2Book), pay the Hyperliquid taker fee and receive or pay Hyperliquid's real hourly funding. No keys,
no orders: everything is public data. The delta-neutral carry's spot leg is simulated at the
Hyperliquid oracle (spot index) price with the spot fee from perps.yaml.

State and every event go to paper.sqlite (append-only, same schema as journal.sqlite events), so a
restart continues where it stopped and `report` rebuilds the equity curve from the log.
"""
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab import data, journal, perps

HL = "https://api.hyperliquid.xyz/info"
FAPI = "https://fapi.binance.com"
PAPER_DB = data.ROOT / "paper.sqlite"
HISTORY_HOURS = 24 * 65  # 30-day volume median, 60-day listing age, weekly shock window
DRIFT = 0.02             # trade a coin only if its weight is off target by more than 2% of capital


def _http(url, body=None, tries=4):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "tradelab-paper"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 451:
                raise SystemExit("Binance futures API недоступен из вашей страны (код 451); бумажный бот берёт сигналы с Binance")
            if i == tries - 1:
                raise
        except Exception:
            if i == tries - 1:
                raise
        time.sleep(2 ** i)


# ---------- exchanges (public endpoints) ----------

class Hyperliquid:
    def ctxs(self):
        meta, ctx = _http(HL, {"type": "metaAndAssetCtxs"})
        return {u["name"]: {k: (float(v) if isinstance(v, str) else v) for k, v in c.items() if k != "impactPxs"}
                for u, c in zip(meta["universe"], ctx) if not u.get("isDelisted")}

    def book(self, coin):
        b = _http(HL, {"type": "l2Book", "coin": coin})["levels"]
        return ([(float(x["px"]), float(x["sz"])) for x in b[0]], [(float(x["px"]), float(x["sz"])) for x in b[1]])

    def funding(self, coin, start_ms):
        return [(int(x["time"]), float(x["fundingRate"])) for x in _http(HL, {"type": "fundingHistory", "coin": coin, "startTime": start_ms})]


class Binance:
    """Public endpoints, throttled under Binance's limits (klines weight 5 per 1000 bars, fundingRate
    500 calls per 5 minutes)."""
    PAUSE = {"klines": 0.15, "funding": 0.65}

    def __init__(self):
        self.next_funding = {}

    def _wait(self, kind):
        time.sleep(self.PAUSE[kind])

    def due(self, now_ms):
        """Symbols whose funding settlement has passed since the last check (one call for all)."""
        out = []
        for x in _http(f"{FAPI}/fapi/v1/premiumIndex"):
            nxt = int(x["nextFundingTime"])
            if self.next_funding.get(x["symbol"], 0) <= now_ms:
                out.append(x["symbol"])
            self.next_funding[x["symbol"]] = nxt
        return set(out)

    def symbols(self):
        info = _http(f"{FAPI}/fapi/v1/exchangeInfo")
        return [s["symbol"] for s in info["symbols"] if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"]

    def klines(self, sym, start_ms, limit=1000):
        k = []
        while True:
            self._wait("klines")
            page = _http(f"{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&startTime={start_ms}&limit={limit}")
            k += page
            if len(page) < limit:
                break
            start_ms = int(page[-1][0]) + 3_600_000
        return pd.DataFrame({"open": [float(x[1]) for x in k], "close": [float(x[4]) for x in k],
                             "quote_volume": [float(x[7]) for x in k], "taker_buy_quote": [float(x[10]) for x in k]},
                            index=pd.to_datetime([x[0] for x in k], unit="ms", utc=True))

    def funding(self, sym, start_ms):
        self._wait("funding")
        f = _http(f"{FAPI}/fapi/v1/fundingRate?symbol={sym}&startTime={start_ms}&limit=1000")
        return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in f]


def hl_coin(binance_symbol):
    """BTCUSDT -> BTC, 1000PEPEUSDT -> kPEPE (Hyperliquid's name for 1000-unit contracts)."""
    base = binance_symbol[:-4]
    return "k" + base[4:] if base.startswith("1000") and not base.startswith("1000000") else base


# ---------- signals: the research code on a live window ----------

def panels_from(bars, funding):
    """Research panels from live data: bars {sym: hourly frame}, funding {sym: [(ms, rate)]} (8h scale
    taken from the spacing of settlements)."""
    frames = {}
    for sym, b in bars.items():
        f = b.copy()
        f["premium"] = f["premium_open"] = np.nan
        f["funding"], f["funding_hours"], f["settled"] = 0.0, np.nan, False
        ev = sorted(funding.get(sym, []))
        for i, (ms, rate) in enumerate(ev):
            bar = pd.Timestamp(ms, unit="ms", tz="UTC").floor("1h") - pd.Timedelta(hours=1)
            if bar in f.index:
                f.loc[bar, ["funding", "settled"]] = [rate, True]
                hours = (ms - ev[i - 1][0]) / 3.6e6 if i else 8.0
                f.loc[bar, "funding_hours"] = float(min(8.0, max(1.0, round(hours))))
        f["funding_hours"] = f["funding_hours"].ffill()
        frames[sym] = f
    from tradelab import perpdata

    return perpdata.load_panels(frames=frames)


def target_weights(panels, pcfg, conf):
    """Weights the strategy holds after the last closed bar, by Binance symbol."""
    a = perps.prepare(panels, pcfg)
    if not a["symbols"]:
        return {}
    wp, ws = perps.weights(a, conf)
    last = len(a["index"]) - 1
    return {s: (float(wp[last, j]), float(ws[last, j])) for j, s in enumerate(a["symbols"])
            if wp[last, j] != 0 or ws[last, j] != 0}


# ---------- simulated execution ----------

def walk_book(levels, notional):
    """Average fill price of a taker order of `notional` USD through price levels [(px, size)];
    None if the book is too thin."""
    left, cost, qty = notional, 0.0, 0.0
    for px, sz in levels:
        take = min(left, px * sz)
        cost += take
        qty += take / px
        left -= take
        if left <= 1e-9:
            return cost / qty
    return None


class Account:
    def __init__(self, cash, perp=None, spot=None):
        self.cash = cash
        self.perp = dict(perp or {})  # coin -> size in coins (negative = short)
        self.spot = dict(spot or {})

    def equity(self, marks, oracle):
        return (self.cash + sum(sz * marks[c] for c, sz in self.perp.items() if c in marks)
                + sum(sz * oracle[c] for c, sz in self.spot.items() if c in oracle))

    def state(self):
        return {"cash": self.cash, "perp": self.perp, "spot": self.spot}


def rebalance(acct, targets, ctx, ex, pcfg, log):
    """Move every position to its target notional (taker orders on the live book)."""
    v = pcfg["venue"]
    marks = {c: x["markPx"] for c, x in ctx.items()}
    oracle = {c: x["oraclePx"] for c, x in ctx.items()}
    eq = acct.equity(marks, oracle)
    coins = set(acct.perp) | set(acct.spot) | set(targets)
    for coin in sorted(coins):
        if coin not in ctx:
            continue
        tp, ts = targets.get(coin, (0.0, 0.0))
        cur = acct.perp.get(coin, 0.0)
        dq = tp * eq / marks[coin] - cur
        if abs(dq * marks[coin]) > 1.0 and (abs(dq * marks[coin]) > DRIFT * eq or tp == 0):
            bids, asks = ex.book(coin)
            px = walk_book(asks if dq > 0 else bids, abs(dq) * marks[coin])
            if px is None:
                log("skip_thin_book", {"coin": coin, "notional": abs(dq) * marks[coin]})
            else:
                fee = abs(dq) * px * v["taker_fee"]
                acct.cash -= dq * px + fee
                acct.perp[coin] = acct.perp.get(coin, 0.0) + dq
                log("fill", {"coin": coin, "leg": "perp", "qty": dq, "px": px, "fee": fee, "mark": marks[coin]})
        ds = ts * eq / oracle[coin] - acct.spot.get(coin, 0.0)
        if abs(ds * oracle[coin]) > 1.0 and (abs(ds * oracle[coin]) > DRIFT * eq or ts == 0):
            px = oracle[coin] * (1 + np.sign(ds) * v["slippage_base"])
            fee = abs(ds) * px * v["spot_taker_fee"]
            acct.cash -= ds * px + fee
            acct.spot[coin] = acct.spot.get(coin, 0.0) + ds
            log("fill", {"coin": coin, "leg": "spot", "qty": ds, "px": px, "fee": fee, "oracle": oracle[coin]})
    acct.perp = {c: q for c, q in acct.perp.items() if abs(q) > 1e-12}
    acct.spot = {c: q for c, q in acct.spot.items() if abs(q) > 1e-12}


def accrue_funding(acct, ex, ctx, since_ms, log):
    """Hyperliquid hourly funding on open perp positions: a long pays size * oracle price * rate."""
    total = 0.0
    for coin, sz in acct.perp.items():
        if coin not in ctx:
            continue
        for ms, rate in ex.funding(coin, since_ms):
            pay = sz * ctx[coin]["oraclePx"] * rate
            acct.cash -= pay
            total += pay
            log("funding", {"coin": coin, "time": ms, "rate": rate, "paid": pay})
    return total


# ---------- loop ----------

class Bot:
    def __init__(self, pcfg, conf, capital, db=PAPER_DB, hl=None, bn=None):
        self.pcfg, self.conf = pcfg, conf
        self.hl, self.bn = hl or Hyperliquid(), bn or Binance()
        self.conn = journal.connect(db)
        last = journal.events(self.conn, "state")
        if last:
            s = last[-1]["payload"]
            self.acct = Account(s["cash"], s["perp"], s["spot"])
            self.last_ms = s["time_ms"]
            if s.get("config") != conf:
                raise SystemExit("в paper.sqlite другая стратегия; для новой укажите другой файл --db")
        else:
            self.acct = Account(capital)
            self.last_ms = int(time.time() * 1000)
            self.log("start", {"capital": capital, "config": conf, "venue": pcfg["venue"]})
        self.bars, self.fund = {}, {}

    def log(self, kind, payload):
        journal.log_event(self.conn, kind, payload)

    def refresh(self, now):
        """Binance hourly bars and funding for every perpetual that is also on Hyperliquid."""
        hl = set(self.hl.ctxs())
        start = int((now - pd.Timedelta(hours=HISTORY_HOURS)).timestamp() * 1000)
        due = self.bn.due(int(now.timestamp() * 1000))
        for sym in self.bn.symbols():
            if hl_coin(sym) not in hl or sym in self.pcfg["universe"]["exclude"]:
                continue
            old = self.bars.get(sym)
            since = start if old is None or not len(old) else int(old.index[-1].timestamp() * 1000)
            new = self.bn.klines(sym, since)
            b = new if old is None else pd.concat([old, new])
            b = b[~b.index.duplicated(keep="last")]
            self.bars[sym] = b[(b.index >= now - pd.Timedelta(hours=HISTORY_HOURS)) & (b.index < now)]  # closed bars only
            f = self.fund.get(sym)
            if f is None or sym in due:
                f = f or []
                f = sorted(set(f + self.bn.funding(sym, f[-1][0] + 1 if f else start)))
            self.fund[sym] = [x for x in f if x[0] >= start]

    def step(self, now=None):
        now = (now or pd.Timestamp.now(tz="UTC")).floor("1h")
        self.refresh(now)
        ctx = self.hl.ctxs()
        paid = accrue_funding(self.acct, self.hl, ctx, self.last_ms, self.log)
        tw = target_weights(panels_from(self.bars, self.fund), self.pcfg, self.conf) if self.bars else {}
        targets = {hl_coin(s): w for s, w in tw.items()}
        rebalance(self.acct, targets, ctx, self.hl, self.pcfg, self.log)
        marks = {c: x["markPx"] for c, x in ctx.items()}
        oracle = {c: x["oraclePx"] for c, x in ctx.items()}
        eq = self.acct.equity(marks, oracle)
        self.last_ms = int(now.timestamp() * 1000)
        self.log("hl_ctx", {"time_ms": self.last_ms, "ctx": ctx})  # live Hyperliquid data kept for research
        self.log("state", {"time_ms": self.last_ms, "equity": eq, "funding_paid": paid, "targets": targets,
                           "eth_mark": marks.get("ETH"), "config": self.conf, **self.acct.state()})
        return eq

    def run(self):
        while True:
            now = pd.Timestamp.now(tz="UTC")
            nxt = now.floor("1h") + pd.Timedelta(hours=1, minutes=1)  # one minute after each hourly close
            time.sleep(max(1.0, (nxt - now).total_seconds()))
            try:
                eq = self.step()
                print(f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} капитал {eq:,.2f}", flush=True)
            except SystemExit:
                raise
            except Exception as e:  # network hiccup: log and wait for the next hour
                self.log("error", {"error": repr(e)})
                print(f"ошибка: {e!r}; следующая попытка через час", flush=True)


def report(db=PAPER_DB, out=None):
    from tradelab import metrics, report as rep

    conn = journal.connect(db)
    st = [e["payload"] for e in journal.events(conn, "state")]
    if len(st) < 2:
        return "пока мало данных: бот должен проработать хотя бы несколько часов\n"
    s = pd.DataFrame({"equity": [x["equity"] for x in st], "eth": [x["eth_mark"] for x in st]},
                     index=pd.to_datetime([x["time_ms"] for x in st], unit="ms", utc=True))
    d = s.resample("1D").last().dropna()
    start = journal.events(conn, "start")[0]["payload"]
    rows = []
    for name, col in (("бот", "equity"), ("ETH удержание", "eth")):
        r = d[col].pct_change().dropna().to_numpy()
        tot = d[col].iloc[-1] / s[col].iloc[0] - 1
        rows.append({"кривая": name, "доходность": f"{tot:.2%}",
                     "Шарп": f"{metrics.sharpe(r):.2f}" if len(r) > 2 and r.std() > 0 else "мало дней",
                     "MaxDD": f"{metrics.max_drawdown(r):.1%}" if len(r) else "-"})
    fills = journal.events(conn, "fill")
    fees = sum(e["payload"]["fee"] for e in fills)
    fund = sum(e["payload"]["paid"] for e in journal.events(conn, "funding"))
    md = (f"# Бумажная торговля\n\nСтратегия: `{json.dumps(start['config'])}`; старт {s.index[0]:%Y-%m-%d %H:%M} UTC, "
          f"капитал {start['capital']:,.0f}; часов в логе {len(s)}, дней {len(d)}.\n\n"
          + rep.md_table(pd.DataFrame(rows))
          + f"\nСделок {len(fills)}, комиссии {fees:,.2f}, фандинг уплачен {fund:,.2f} (минус значит получен). "
          f"Пропущено из-за тонкого стакана: {len(journal.events(conn, 'skip_thin_book'))}. Ошибок сети: "
          f"{len(journal.events(conn, 'error'))}.\n\nМеньше 3 месяцев бумажной торговли статистически почти ничего не "
          "доказывают; сравнивайте с бэктестом этой же стратегии в out/perps.md.\n")
    if out:
        Path(out).write_text(md, encoding="utf-8")
    return md
