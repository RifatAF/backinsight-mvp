import sys, math; sys.path.insert(0, ".")
import pandas as pd, paper_bot as B

class FakeEx:
    id = "bybit"
    def __init__(s): s.bal = {"USDT": 1000.0, "BTC": 0.0, "ETH": 0.0}; s.px = {"BTC": 77000.0, "ETH": 2250.0}; s.orders = []
    def load_markets(s): pass
    def fetch_balance(s): return {k: {"free": v, "total": v} for k, v in s.bal.items()}
    def fetch_ticker(s, sym): return {"last": s.px[sym.split("/")[0]]}
    def market(s, sym): return {"limits": {"cost": {"min": 5}, "amount": {"min": 1e-6}}}
    def amount_to_precision(s, sym, q): return f"{math.floor(q * 1e6) / 1e6:.6f}"
    def price_to_precision(s, sym, p): return f"{p:.2f}"
    def create_order(s, sym, typ, side, amt, price, params):
        assert typ == "limit" and params.get("timeInForce") == "IOC"
        base = sym.split("/")[0]; fill = s.px[base] * (1.0005 if side == "buy" else 0.9995)
        assert (fill <= price) if side == "buy" else (fill >= price), "лимит нарушен"
        cost = amt * fill
        if side == "buy":
            fee = amt * 0.001; s.bal["USDT"] -= cost; s.bal[base] += amt - fee; fc = base   # Bybit: комиссия покупки в базовой монете
        else:
            fee = cost * 0.001; s.bal["USDT"] += cost - fee; s.bal[base] -= amt; fc = "USDT"
        o = {"id": str(len(s.orders)), "status": "closed", "filled": amt, "cost": cost, "fee": {"cost": fee, "currency": fc}}
        s.orders.append(o); return o
    def fetch_order(s, i, sym): return s.orders[int(i)]

class Acct:
    def __init__(s): s.trades = []
    def log_trade(s, t): s.trades.append(t)
    def save(s, st): pass

ex, acct = FakeEx(), Acct()
st = {"cash": 600.0, "units": {}, "capital": 600.0}
closes = {"BTC": pd.Series([76500.0]), "ETH": pd.Series([2240.0])}
print("--- пробный прогон"); r = B.rebalance_live(ex, st, ["BTC", "ETH"], {"BTC": 0.5, "ETH": 0.5}, closes, 0.02, False, acct)
assert r == [] and not ex.orders and st["cash"] == 600.0
print("--- покупка"); B.rebalance_live(ex, st, ["BTC", "ETH"], {"BTC": 0.5, "ETH": 0.5}, closes, 0.02, True, acct)
print("state", st, "биржа", ex.bal)
assert abs(st["units"]["BTC"] - ex.bal["BTC"]) < 1e-12 and abs(st["units"]["ETH"] - ex.bal["ETH"]) < 1e-12
assert ex.bal["USDT"] >= 400 - 1 and st["cash"] >= -1e-9, "вышли за бюджет"
print("--- полный выход из ETH, BTC остаётся")
B.rebalance_live(ex, st, ["BTC", "ETH"], {"BTC": 0.5, "ETH": 0.0}, closes, 0.02, True, acct)
print("state", st, "биржа", ex.bal)
assert st["units"]["ETH"] < 1e-6 and ex.bal["ETH"] < 1e-6
print("--- проверка разрыва цены > 15%")
ex.px["BTC"] = 60000.0
try: B.rebalance_live(ex, st, ["BTC", "ETH"], {"BTC": 0.5, "ETH": 0}, closes, 0.02, True, acct); print("ОШИБКА: не остановился")
except SystemExit as e: print("остановлено:", e)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ, сделок", len(acct.trades))
