#!/usr/bin/env python3
"""A/B-тест четырёх моделей на живых данных Bybit с виртуальными деньгами + запись всех данных для анализа.

  python abtest.py init --capital 10000 --coins BTC,ETH      # 4 виртуальных счёта с одинаковым капиталом
  python abtest.py cycle                                     # дневной цикл (после 00:00 UTC): данные -> решения -> сделки
  python abtest.py snapshot                                  # снимок рынка без сделок (стакан, funding, OI, long/short)
  python abtest.py loop                                      # снимок каждый час + дневной цикл в 00:05 UTC
  python abtest.py report                                    # сравнение моделей, статистика различий, точность M4
  python abtest.py replay --days 1095 --exchange coinbase    # те же 4 модели по истории (проверка до запуска)
  python abtest.py dataset                                   # обучающая таблица: признаки + фактические исходы
  python abtest.py doctor                                    # доступны ли биржа и источники с этого компьютера
  python abtest.py health                                    # полнота данных: пропуски, снимки, ошибки
  python abtest.py export                                    # один zip со всеми данными, логами и манифестом

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
for _s in (sys.stdout, sys.stderr):              # Windows: cp1252 в перенаправленном выводе не умеет кириллицу
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FEE = 0.001            # тейкер спот Bybit (VIP0)
PERP_FEE = 0.00055     # тейкер перпетуалов Bybit (VIP0): по ним торгуют клоны с плечом и шортом
VOL_TARGET = 0.6       # _x3: плечо до 3 по волатильности, целевая годовая волатильность портфеля 60%
MAX_LEV = 3.0
CARRY_SHARE = 0.9      # carry: доля «устойчиво свободных» денег в связке спот BTC + шорт перпа BTC
CARRY_DAYS = 7         # «устойчиво свободные» = минимум свободной доли за 7 дней
CARRY_MIN_CHANGE = 0.10
FALLBACK_SLIP = 0.0005 # если стакан недоступен (replay)
THRESHOLD = 0.02       # ребаланс, если вес ушёл больше чем на 2%
COMPOUND_STEP = 0.0    # 0 = ежедневный реинвест (позиции от текущего капитала); >0 = ступенями по столько USDT
MIN_ORDER = 5.0        # минимальная сумма ордера на Bybit (спот и перпетуалы), USDT
BASE_MODELS = ["M1", "M2", "M3", "M4"]
# _x3: та же логика, позиции ×3 (плечо, только лонг); _ls: плечо 1, но может и в шорт
MODELS = (BASE_MODELS + [f"{m}_x3" for m in BASE_MODELS] + [f"{m}_ls" for m in BASE_MODELS]
          + ["M1_x5", "ROT3", "EXP", "ZONE", "FNEG", "FMIX", "COMBO", "MEGA"])
# ROT3: каждую неделю 3 самые сильные монеты из списка по росту за 90 дней (сигнал M1 на каждую), без плеча
ROT_UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "AVAX", "LINK", "DOT", "BCH", "TRX", "BNB", "NEAR",
                "ATOM", "UNI", "SUI", "PEPE", "SHIB"]
ROT_K, ROT_LOOKBACK, ROT_MIN_HISTORY = 3, 90, 250
# EXP: покупка паники внутри бычьего рынка (против толпы и ортогонально тренду), плечо 2
EXP_Z, EXP_HOLD, EXP_TAKE, EXP_LEV = -1.0, 14, 0.05, 2.0
# ZONE: пробой зоны проторговки (профиль объёма 30 дней) + оценочная карта ликвидаций (60 дней) + перцентиль цены
ZONE_BIN, ZONE_PROFILE_DAYS, ZONE_LIQ_DAYS, ZONE_LIQ_RANGE = 0.0025, 30, 60, 0.10
ZONE_LEVS = {10: 0.40, 25: 0.35, 50: 0.15, 100: 0.10}   # предполагаемое распределение плеч новых позиций
# FNEG: BTC только когда средний funding за 7 дней < 0 (толпа в шортах). orth_lab.py: 2020–26 +18%/год, Sharpe 0.98,
# просадка −19%, в рынке 10% дней; плацебо (те же отрезки в случайном порядке) Sharpe 95% = 0.73.
FNEG_DAYS, FNEG_THR = 7, 0.0
# FMIX: сигнал FNEG, но только если предложение стейблкоинов за 30 дней растёт; плечо x2; свободные деньги в carry.
# mix_lab.py: 2020–26 +49%/год, Sharpe 1.32, просадка −19% (2020–23 / 2024–26: +58% / +37%); 22 входа, плацебо 95% = 0.70.
FMIX_LEV = 2.0
# COMBO: портфель из двух некоррелированных частей (корреляция 0.09): M1 как есть + сверху ещё 1 BTC при сигнале FMIX.
# mix_lab.py (с 2021-06): +42%/год, Sharpe 1.23, просадка −30% против M1 +26%, 0.93, −28%. Плечо до x2.
COMBO_LEV = 2.0
# MEGA: три разных источника дохода поровну — тренд (M1), покупка паники (EXP, x2), funding против толпы (FMIX, x2) —
# и общий контроль риска: плечо = 20% / фактическая волатильность портфеля за 30 дней (без плеча), не больше x2;
# свободные деньги в carry. Веса заданы заранее (не подбирались). Корреляции частей: M1–EXP 0.15, M1–FMIX 0.12.
# mega_lab.py по кривым replay 2022-03..2026-11: 1/3 без плеча +33%/год, Sharpe 1.54, просадка −21%;
# с целью 20%: +45%/год, Sharpe 1.84 (1.82 / 1.87 по половинам), просадка −24%.
# Плечо по волатильности позиций (как у _x3) здесь хуже: когда EXP и FMIX вне рынка, удваивает M1 (Sharpe 1.36).
MEGA_PARTS, MEGA_VOL, MEGA_MAX_LEV, MEGA_VOL_DAYS = ("M1", "EXP", "FMIX"), 0.20, 2.0, 30
MAINT_MARGIN = 0.005   # ликвидация, если капитал < 0.5% от суммы позиций (как у перпетуалов)
DEFAULT_FUNDING = 0.0001  # funding за 8 ч, если биржа не отдала (средний исторический уровень ≈ 11% годовых)


def model_spec(m):
    """(базовая модель, максимальное плечо, разрешён шорт)."""
    base, _, var = m.partition("_")
    lev = {"x3": 3.0, "x5": 5.0}.get(var, EXP_LEV if base == "EXP" else FMIX_LEV if base == "FMIX" else COMBO_LEV if base == "COMBO"
                                       else MEGA_MAX_LEV if base == "MEGA" else 1.0)
    return base, lev, var == "ls"


def lev_mode(m):
    """Плечо: vol = по волатильности до максимума (цель, максимум), fixed = постоянное, none = без плеча."""
    var = m.partition("_")[2]
    if var == "x3":
        return "vol", 0.6
    if var == "x5":
        return "vol", 1.0
    if m == "MEGA":
        return "realized", MEGA_VOL
    if m in ("EXP", "FMIX", "COMBO"):
        return "fixed", None
    return "none", None
DAY = 86400000
M4_HORIZON = 7
M4_HALF_LIFE = None    # без забывания: в проверке 2018-2026 веса «свежее важнее» ухудшали результат
M4_LAMBDA = 50
M4_SMOOTH = 7          # EMA вероятности, меньше лишних сделок


def now_utc():
    return datetime.now(timezone.utc)


SCHEMA_VERSION = 1
RUN_ID = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{os.getpid()}"
_logger = None


def code_version():
    try:
        if getattr(sys, "frozen", False):
            raise RuntimeError("exe: версия из файла VERSION")
        import subprocess
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent, capture_output=True,
                           text=True, timeout=5).stdout.strip()
        if h:
            return h
    except Exception:
        pass
    v = Path(__file__).parent / "VERSION"           # архив без .git: версия записана при сборке
    return v.read_text(encoding="utf-8").strip() if v.exists() else "unknown"


CODE_VERSION = code_version()
UPDATE_URL = "https://api.github.com/repos/RifatAF/backinsight-mvp/releases?per_page=50"   # релизы exe-<версия>


def fetch_update(dest, current=None, timeout=60):
    """Скачать новую версию exe из самого свежего релиза exe-<версия> (CI публикует его только после всех проверок).
    Возвращает (версия, путь) или (версия, None), если обновлять не нужно. Проверяет SHA-256 из описания релиза."""
    import hashlib
    import re
    import urllib.request
    hdr = {"User-Agent": "backinsight-updater", "Accept": "application/vnd.github+json"}
    ctx = S._ssl_ctx()
    rels = json.loads(urllib.request.urlopen(urllib.request.Request(UPDATE_URL, headers=hdr), timeout=timeout,
                                             context=ctx).read())
    rels = [r for r in rels if str(r.get("tag_name", "")).startswith("exe-") and not r.get("draft")]
    if not rels:
        raise RuntimeError("на GitHub нет релизов exe-*")
    when = lambda r: r.get("published_at") or r.get("created_at") or ""
    rel = max(rels, key=when)
    cur = current or CODE_VERSION
    mine = next((r for r in rels if r["tag_name"] == f"exe-{cur}"), None)
    if not os.environ.get("BACKINSIGHT_FAKE_VERSION"):
        # Только вперёд: если своего релиза в списке нет (список у GitHub обновляется с задержкой) или найденный
        # не новее своего — ничего не делать. Так старая версия никогда не поставится поверх новой.
        if mine is None or when(rel) <= when(mine):
            return cur, None
    body = rel.get("body") or ""
    ver = re.search(r"version:\s*(\S+)", body)
    sha = re.search(r"sha256:\s*([0-9a-fA-F]{64})", body)
    if not ver or not sha:
        raise RuntimeError("в релизе нет версии или контрольной суммы")
    ver, sha = ver.group(1), sha.group(1).lower()
    if ver == (current or CODE_VERSION):
        return ver, None
    asset = next((x for x in rel.get("assets", []) if x["name"] == "backinsight.exe"), None)
    if not asset:
        raise RuntimeError("в релизе нет backinsight.exe")
    tmp = Path(dest).with_suffix(".part")
    h = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": hdr["User-Agent"]}),
                                timeout=timeout * 5, context=ctx) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            h.update(b)
            f.write(b)
    if h.hexdigest() != sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("контрольная сумма не совпала, файл удалён")
    os.replace(tmp, dest)
    return ver, Path(dest)


def auto_update(a):
    """Раз в час (из loop): если на GitHub есть новая проверенная версия, заменить exe и перезапуститься.
    Данные в папке abtest не трогаются; перед заменой копия state.json кладётся в state_backups.
    Выключить: создать пустой файл abtest/autoupdate_off. Откат: backinsight.old.exe рядом с exe."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return
    root = Path(a.dir)
    if (root / "autoupdate_off").exists():
        return
    hist_p = root / "update.json"
    hist = json.loads(hist_p.read_text(encoding="utf-8")) if hist_p.exists() else {"failed": {}, "done": []}
    exe = Path(sys.executable)
    new = exe.with_name("backinsight.new.exe")
    cur = os.environ.get("BACKINSIGHT_FAKE_VERSION", CODE_VERSION)     # только для проверки обновления в CI
    try:
        ver, path = fetch_update(new, current=cur)
    except Exception as e:
        log(f"автообновление: проверка не удалась ({str(e)[:120]}), работаю на {CODE_VERSION}", "warning")
        return
    if path is None:
        return
    if hist["failed"].get(ver, 0) >= 3:
        new.unlink(missing_ok=True)
        return
    import shutil
    import subprocess
    try:
        r = subprocess.run([str(new), "version"], capture_output=True, text=True, timeout=180)
        if ver not in (r.stdout or ""):
            raise RuntimeError(f"новая версия не запустилась: {(r.stdout or r.stderr)[-200:]}")
        sp = root / "state.json"
        if sp.exists():
            (root / "state_backups").mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, root / "state_backups" / f"state_before_update_{cur}_to_{ver}.json")
        old = exe.with_name("backinsight.old.exe")
        old.unlink(missing_ok=True)
        os.replace(exe, old)                            # Windows разрешает переименовать запущенный exe
        try:
            os.replace(new, exe)
        except Exception:
            os.replace(old, exe)                        # не вышло: вернуть как было
            raise
    except Exception as e:
        hist["failed"][ver] = hist["failed"].get(ver, 0) + 1
        atomic_write(hist_p, json.dumps(hist, indent=2, ensure_ascii=False))
        log(f"автообновление до {ver} не удалось: {str(e)[:200]}", "error")
        return
    hist["done"].append({"ts": now_utc().isoformat(), "from": cur, "to": ver})
    atomic_write(hist_p, json.dumps(hist, indent=2, ensure_ascii=False))
    log(f"автообновление: {cur} -> {ver}, перезапуск (планировщик поднимет новую версию за 1 минуту)")
    import logging
    logging.shutdown()
    os._exit(3)                                         # код ≠ 0: задача планировщика перезапускается сама


def setup_logging(base_dir):
    """Консоль + logs/abtest.log (ротация в полночь, 200 дней) + logs/errors.log (только ошибки)."""
    import logging
    from logging.handlers import TimedRotatingFileHandler
    global _logger
    d = Path(base_dir) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("abtest")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    fmt = logging.Formatter("%(asctime)s UTC %(levelname)s [" + RUN_ID + "] %(message)s", "%Y-%m-%d %H:%M:%S")
    fmt.converter = time.gmtime
    handlers = [(logging.StreamHandler(sys.stdout), logging.INFO)] if sys.stdout else []   # pythonw: консоли нет
    for h, lvl in handlers + [
                   (TimedRotatingFileHandler(d / "abtest.log", when="midnight", backupCount=200, utc=True, encoding="utf-8"), logging.INFO),
                   (logging.FileHandler(d / "errors.log", encoding="utf-8"), logging.WARNING)]:
        h.setLevel(lvl)
        h.setFormatter(fmt)
        lg.addHandler(h)
    _logger = lg


def log(msg, level="info"):
    if _logger is None:
        print(f"[{now_utc():%Y-%m-%d %H:%M:%S} UTC] {msg}", flush=True)
    else:
        getattr(_logger, level)(msg)


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
        if coin in ("PEPE", "SHIB") and self.name == "bybit":   # на Bybit перпы этих монет в тысячах: 1000PEPEUSDT
            return f"1000{coin}/USDT:USDT"
        return f"{coin}/USDT:USDT"

    def derivs(self, coin):
        """Компактный снимок деривативов: funding, mark/index, открытый интерес, лонг/шорт. Ошибки по полям."""
        snap = {"ts": now_utc().isoformat(), "exchange": self.name, "coin": coin}
        for key, fn in (("funding", lambda: {k: self.perp.fetch_funding_rate(self.psym(coin)).get(k) for k in
                                             ("fundingRate", "fundingTimestamp", "markPrice", "indexPrice")}),
                        ("open_interest", lambda: {k: self.perp.fetch_open_interest(self.psym(coin)).get(k) for k in
                                                   ("openInterestAmount", "openInterestValue")}),
                        ("long_short", lambda: [r.get("longShortRatio") for r in
                                                self.perp.fetch_long_short_ratio_history(self.psym(coin), "1h", limit=1)])):
            try:
                snap[key] = fn()
            except Exception as e:
                snap[key] = None
                snap.setdefault("errors", {})[key] = str(e)[:200]
        return snap

    def funding_history(self, coin, since_ms):
        """Все выплаты funding с since_ms (каждые 8 ч у BTC/ETH, у части монет чаще)."""
        rows = []
        while True:
            batch = self._retry(self.perp.fetch_funding_rate_history, self.psym(coin), since=since_ms, limit=200)
            batch = [b for b in batch if b["timestamp"] >= since_ms]
            if not batch:
                break
            rows += [{"t": b["timestamp"], "rate": b["fundingRate"]} for b in batch]
            since_ms = batch[-1]["timestamp"] + 1
            if len(batch) < 100:
                break
        df = pd.DataFrame(rows).drop_duplicates("t") if rows else pd.DataFrame(columns=["t", "rate"])
        df.index = pd.to_datetime(df.t, unit="ms")
        return df.drop(columns="t")

    def _retry(self, fn, *a, **kw):
        for attempt in range(4):
            try:
                return fn(*a, **kw)
            except Exception as e:
                if attempt == 3:
                    raise
                log(f"повтор {getattr(fn, '__name__', fn)}: {str(e)[:120]}", "warning")
                time.sleep(2 ** attempt)

    def daily(self, coin, days=1800, tf="1d"):
        step = {"1d": DAY, "4h": DAY // 6}[tf]
        since = self.spot.milliseconds() - days * DAY
        rows = []
        while True:
            batch = self._retry(self.spot.fetch_ohlcv, self.sym(coin), tf, since=since, limit=300)
            if not batch:
                if not rows and since + 300 * step < self.spot.milliseconds():
                    since += 300 * step                  # запрошено раньше начала истории монеты: двигаемся вперёд
                    continue
                break
            rows += batch
            since = batch[-1][0] + step
            if since > self.spot.milliseconds() or (len(batch) < 300 and since > self.spot.milliseconds() - 300 * step):
                break                                    # короткий ответ в начале истории монеты не значит конец
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).drop_duplicates("t")
        df.index = pd.to_datetime(df.t, unit="ms")
        df = df.drop(columns="t").sort_index()
        return df[df.index < today_utc()]     # только закрытые свечи (для 4h: до начала текущих суток UTC)

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
    try:
        oc = S.fetch_onchain()
    except Exception as e:                             # Coin Metrics недоступен: старый кэш или копия из exe
        seed = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "data" / "onchain_seed.csv"
        src = path if path.exists() else seed
        if not src.exists():
            raise
        oc = pd.read_csv(src, index_col=0, parse_dates=True)
        log(f"on-chain (Coin Metrics) недоступен: {str(e)[:120]}. Беру сохранённые данные до {oc.index[-1].date()}: "
            f"MVRV меняется медленно, торговле это почти не мешает", "warning")
        return oc
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


def short_signal(d):
    """Зеркало тренда R3: BTC за 30 дней падает и RSI монеты < 50."""
    return float(pd.notna(d.btc_mom) and pd.notna(d.rsi) and d.btc_mom < 0 and d.rsi < 50)


def volume_profile(h, l, v, bin_=ZONE_BIN):
    """Профиль объёма: объём свечи поровну по ценовым корзинам между её low и high. -> (POC, VAL, VAH), 70% объёма."""
    lo, hi = np.log(l.min()), np.log(h.max())
    n = max(int((hi - lo) / bin_) + 1, 1)
    hist = np.zeros(n)
    a = ((np.log(l) - lo) / bin_).astype(int)
    b = ((np.log(h) - lo) / bin_).astype(int)
    for i in range(len(v)):
        hist[a[i]:b[i] + 1] += v[i] / (b[i] - a[i] + 1)
    poc = int(hist.argmax())
    lo_i = hi_i = poc
    tot, acc = hist.sum(), hist[poc]
    while acc < 0.7 * tot and (lo_i > 0 or hi_i < n - 1):
        up = hist[hi_i + 1] if hi_i < n - 1 else -1
        dn = hist[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    px = lambda i: math.exp(lo + (i + 0.5) * bin_)
    return px(poc), px(lo_i), px(hi_i)


def liq_magnet(h, l, c, v, price, rng=ZONE_LIQ_RANGE):
    """Оценочная карта ликвидаций без данных OI: объём свечи = новые позиции, половина лонги, половина шорты, плечи
    ZONE_LEVS. Уровень снят, если цена после открытия до него дошла. -> (доля ликвидаций в ±rng выше цены,
    сумма выше, сумма ниже). Выше цены лежат ликвидации шортов, ниже лонгов."""
    nxt_min = np.append(np.minimum.accumulate(l[::-1])[::-1][1:], np.inf)
    nxt_max = np.append(np.maximum.accumulate(h[::-1])[::-1][1:], -np.inf)
    above = below = 0.0
    for L, w in ZONE_LEVS.items():
        lq_long, lq_short = c * (1 - 1 / L + 0.005), c * (1 + 1 / L - 0.005)
        below += (v * 0.5 * w)[(lq_long < nxt_min) & (lq_long >= price * (1 - rng))].sum()
        above += (v * 0.5 * w)[(lq_short > nxt_max) & (lq_short <= price * (1 + rng))].sum()
    return (above / (above + below) if above + below > 0 else 0.5), above, below


def zone_features(h4, day):
    """Зоны и карта ликвидаций по 4-часовым свечам, закрытым до конца дня day."""
    x = h4[h4.index < day + pd.Timedelta(days=1)]
    if len(x) < ZONE_LIQ_DAYS * 6 * 0.8:
        return None
    H, L, C = x.high.values, x.low.values, x.close.values
    V = (x.volume * x.close).values                    # объём в USDT
    p = C[-1]
    poc, val, vah = volume_profile(H[-ZONE_PROFILE_DAYS * 6:], L[-ZONE_PROFILE_DAYS * 6:], V[-ZONE_PROFILE_DAYS * 6:])
    n = ZONE_LIQ_DAYS * 6
    mag, up, dn = liq_magnet(H[-n:], L[-n:], C[-n:], V[-n:], p)
    return dict(price=p, poc=poc, val=val, vah=vah, magnet=mag, liq_above=up, liq_below=dn)


def btc_bear(btc, day):
    """Подтверждённый медвежий рынок: BTC ниже SMA200 и SMA200 ниже, чем 30 дней назад."""
    b = btc.loc[:day]
    sma = b.rolling(200).mean()
    return bool(len(b) > 231 and b.iloc[-1] < sma.iloc[-1] and sma.iloc[-1] < sma.iloc[-31])


def decide(model, ctx, mstate, equity_hist, short=False, bear_only=False):
    """Целевые веса (доля всего капитала модели, минус = шорт) и объяснение решения. None = не трогать позицию.
    short=True: те же модели, но вместо «вне рынка» могут открывать шорт по зеркальному сигналу."""
    coins, closes, layers, feats, day = ctx["coins"], ctx["closes"], ctx["layers"], ctx["feats"], ctx["day"]
    n, btc = len(coins), ctx["btc"]
    targets, diag = {}, {}
    if model == "COMBO":                                 # веса делятся на плечо: execute умножит их обратно на COMBO_LEV
        t1, d1 = decide("M1", ctx, mstate.setdefault("m1", {}), equity_hist)
        t2, d2 = decide("FMIX", ctx, mstate.setdefault("fmix", {}), equity_hist)
        for k in set(t1) | set(t2):
            if t1.get(k) is None and k in t1:
                targets[k] = None
            else:
                targets[k] = ((t1.get(k) or 0.0) + (t2.get(k) or 0.0)) / COMBO_LEV
        return targets, {"M1": d1, "FMIX": d2}
    if model == "MEGA":                                  # доли частей с их собственным плечом, затем общий риск
        parts, last = {}, mstate.setdefault("last", {})
        for m in MEGA_PARTS:
            t, d = decide(m, ctx, mstate.setdefault(m, {}), equity_hist)
            mult = model_spec(m)[1]
            for k, v in t.items():
                if v is None:                            # «не трогать»: берём прошлую цель этой части
                    v = last.get(m, {}).get(k, 0.0)
                else:
                    v = v * mult
                last.setdefault(m, {})[k] = v
                targets[k] = targets.get(k, 0.0) + v / len(MEGA_PARTS)
            parts[m] = d
        return targets, parts
    can_short = short and (not bear_only or btc_bear(btc, day))   # bear_only: шорт только в медвежьем рынке
    if model in ("M1", "M2"):
        p = S.Params(mode="A" if model == "M1" else "B")
        for k in coins:
            d = S.coin_position(closes[k].loc[:day], btc.loc[:day], layers, p, detail=True).iloc[-1]
            pos = float(np.nan_to_num(d.pos))
            info = {kk: (float(v) if pd.notna(v) else None) for kk, v in d.items()}
            ssig = short_signal(d) if short else 0.0
            if can_short and pos == 0 and ssig:
                pos = -float(np.nan_to_num(d.vol_size))    # шорт с размером по волатильности
                info["short"] = True
            if model == "M2":
                st = mstate.setdefault(k, {"peak": None, "stopped": False})
                st.setdefault("trough", None)
                st.setdefault("stop_side", 1)
                px = float(closes[k].loc[day])
                if st["stopped"]:
                    if (d.trend if st["stop_side"] > 0 else ssig) == 0:
                        st["stopped"] = False          # сигнал сбросился, можно снова входить
                    pos = 0.0
                    info["stop"] = "ждём сброса сигнала после стопа"
                elif pos > 0:
                    st["peak"] = max(st["peak"] or px, px)
                    if px < st["peak"] * 0.75:
                        st.update(stopped=True, peak=None, stop_side=1)
                        pos = 0.0
                        info["stop"] = "трейлинг-стоп 25% от максимума"
                elif pos < 0:
                    st["trough"] = min(st["trough"] or px, px)
                    if px > st["trough"] * 1.25:
                        st.update(stopped=True, trough=None, stop_side=-1)
                        pos = 0.0
                        info["stop"] = "трейлинг-стоп шорта 25% от минимума"
                if pos <= 0:
                    st["peak"] = None
                if pos >= 0:
                    st["trough"] = None
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
            if short:
                # держать лонг после входа, но в зоне перегрева (MVRV монеты > 3 или MVRV BTC в верхних 10%
                # истории) при падающем тренде переворачиваться в шорт
                dd = S.coin_position(closes[k].loc[:day], btc.loc[:day], layers, S.Params(mode="A"), detail=True).iloc[-1]
                mvrv = ctx["coin_mvrv"].get(k)
                mv = float(mvrv.loc[:day].iloc[-1]) if mvrv is not None and len(mvrv.loc[:day]) else np.nan
                lay = layers.loc[:day]
                mp = float(lay.mvrv_pct.iloc[-1]) if len(lay) else np.nan
                p200w = ctx["coin_sma1400_ratio"].get(k)
                r200 = float(p200w.loc[:day].iloc[-1]) if p200w is not None and len(p200w.loc[:day]) else np.nan
                hot = (mv > 3) or (mp >= 0.9)
                if not st["entered"]:
                    reason = ("MVRV < 1" if mv < 1 else "цена ниже 200-недельной средней" if r200 < 1
                              else "трендовый сигнал" if dd.trend == 1 else None)
                    if reason:
                        st.update(entered=True, entry_day=str(day.date()), reason=reason)
                if can_short and hot and short_signal(dd):
                    targets[k] = -1.0 / n
                else:
                    targets[k] = (1.0 / n) if st["entered"] else 0.0
                diag[k] = {"mvrv": mv, "btc_mvrv_pct": mp, "hot": bool(hot), "short_sig": short_signal(dd),
                           "entered": st["entered"], "target": targets[k]}
                continue
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
        cache = ctx.setdefault("_m4", {})            # одна подгонка в день на все клоны M4
        if day not in cache:
            cache.clear()
            cache[day] = M4Online().fit_predict({k: feats[k] for k in coins}, closes, day)
        res = cache[day]
        for k in coins:
            prob = None if res is None else res["prob"][k]
            ema = mstate.get(f"ema_{k}")
            if prob is not None:
                ema = prob if ema is None else ema + 2 / (M4_SMOOTH + 1) * (prob - ema)
                mstate[f"ema_{k}"] = ema
            vol = float(feats[k].loc[day, "vol30"])
            size = 0.0 if ema is None else float(np.clip((ema - 0.5) / 0.1, -1 if can_short else 0, 1)
                                                 * min(1, 0.5 / max(vol, 1e-6)))
            targets[k] = size / n
            diag[k] = {"prob_up_7d": prob, "prob_smoothed": ema, "vol30": vol, "size": size}
        if res:
            diag["_model"] = {"coef": res["coef"], "n_train": res["n_train"], "base_rate": res["base_rate"]}
    elif model == "ROT3":
        uni = ctx.get("uni") or {}
        st = mstate
        if st.get("last") is None or (day - pd.Timestamp(st["last"])).days >= 7:
            score = {}
            for k, c in uni.items():
                c = c.loc[:day]
                if len(c) < ROT_MIN_HISTORY or c.index[-1] != day:
                    continue
                p = S.coin_position(c, btc.loc[:day], layers, S.Params(mode="A")).iloc[-1]
                if pd.notna(p) and p > 0:
                    score[k] = float(np.log(c.iloc[-1] / c.iloc[-1 - ROT_LOOKBACK]))
            st["pick"] = sorted(score, key=lambda k: -score[k])[:ROT_K]
            st["last"] = str(day.date())
            st["scores"] = {k: round(v, 4) for k, v in sorted(score.items(), key=lambda x: -x[1])}
        targets = {k: 0.0 for k in uni}
        for k in st.get("pick", []):
            c = uni[k].loc[:day]
            p = S.coin_position(c, btc.loc[:day], layers, S.Params(mode="A")).iloc[-1]
            targets[k] = float(np.nan_to_num(p)) / ROT_K      # сигнал пропал внутри недели: выходим
        diag["_rotation"] = {"pick": st.get("pick", []), "since": st.get("last"), "scores": st.get("scores", {})}
    elif model == "EXP":
        # Покупка паники в бычьем рынке: BTC выше растущей SMA200, а монета за 7 дней упала сильнее обычного
        # (z-оценка 7-дневной доходности за год < -1). Держим до 14 дней или до +5% за 7 дней. Толпа в этот
        # момент продаёт по трендовым сигналам, а исторически такие просадки в быке выкупались.
        b = btc.loc[:day]
        sma = b.rolling(200).mean()
        bull = bool(len(b) > 231 and b.iloc[-1] > sma.iloc[-1] and sma.iloc[-1] > sma.iloc[-31])
        for k in [c for c in coins if c in ("BTC", "ETH")]:
            c = closes[k].loc[:day]
            r7 = np.log(c).diff(7)
            z = float((r7.iloc[-1] - r7.iloc[-365:].mean()) / (r7.iloc[-365:].std() + 1e-12)) if len(c) > 380 else np.nan
            st = mstate.setdefault(k, {"left": 0})
            if bull and z < EXP_Z:
                st["left"] = EXP_HOLD
            if st["left"] > 0 and r7.iloc[-1] > EXP_TAKE:
                st["left"] = 0
            on = st["left"] > 0
            st["left"] = max(0, st["left"] - 1)
            targets[k] = (1.0 / 2) if on else 0.0
            diag[k] = {"bull": bull, "z7": z, "ret7": float(r7.iloc[-1]), "days_left": st["left"], "on": on}
    elif model == "ZONE":
        # Вход: цена закрылась выше верхней границы зоны проторговки 30 дней (VAH), цена выше медианы года
        # (перцентиль > 50%) и над ценой больше неснятых ликвидаций шортов, чем под ней лонгов (magnet > 0.5).
        # Выход: закрытие ниже POC (уровня с наибольшим объёмом). Только BTC и ETH, по половине капитала.
        h4 = ctx.get("h4") or {}
        for k in [c for c in coins if c in ("BTC", "ETH")]:
            on = bool(mstate.get(k, False))
            zf = zone_features(h4[k], day) if k in h4 else None
            c = closes[k].loc[:day].iloc[-366:]
            pct = float((c.iloc[:-1] < c.iloc[-1]).mean()) if len(c) > 200 else np.nan
            if zf is None:
                diag[k] = {"error": "нет 4ч свечей", "on": on}
            else:
                p = float(closes[k].loc[:day].iloc[-1])
                if not on and p > zf["vah"] and pct > 0.5 and zf["magnet"] > 0.5:
                    on = True
                elif on and p < zf["poc"]:
                    on = False
                diag[k] = {**{q: float(v) for q, v in zf.items()}, "pct365": pct, "on": on}
            mstate[k] = on
            targets[k] = 0.5 if on else 0.0
    elif model in ("FNEG", "FMIX"):
        fd = ctx.get("fund_btc")
        f = fd.loc[:day].iloc[-FNEG_DAYS:] if fd is not None and len(fd) else None
        if f is None or len(f) < FNEG_DAYS:
            diag["BTC"] = {"error": "нет истории funding", "on": False}
            targets["BTC"] = 0.0
        else:
            on = float(f.mean()) < FNEG_THR
            diag["BTC"] = {"funding_7d_per_day": float(f.mean()), "on": on}
            if model == "FMIX":
                sg = ctx.get("stables")
                g = sg.loc[:day].dropna() if sg is not None else None
                growth = float(g.iloc[-1] / g.iloc[-31] - 1) if g is not None and len(g) > 31 else None
                diag["BTC"]["stables_growth30"] = growth
                if growth is None:
                    diag["BTC"]["error"] = "нет данных о стейблкоинах"
                on = on and growth is not None and growth > 0
                diag["BTC"]["on"] = on
            targets["BTC"] = 1.0 if on else 0.0
    return targets, diag


def funding_daily(df):
    """Сумма ставок funding за каждый день UTC (BTC: 3 выплаты по 8 ч)."""
    if df is None or not len(df):
        return None
    r = df["rate"] if "rate" in df else df.iloc[:, 0]
    r.index = pd.to_datetime(r.index)
    d = r.groupby(r.index.floor("D")).sum()
    return d.iloc[:-1] if len(d) > 1 and r.index[-1].hour < 16 else d    # неполный текущий день не берём


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


def execute(acct, targets, prices, books, model, day, rec, lev=1.0, allow_short=False, fee=None):
    """Ребаланс к целевым весам. lev > 1 или шорт: маржинальный счёт (cash может быть < 0, units < 0),
    сумма позиций не больше lev × капитал."""
    equity = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in prices)
    if equity <= 0:
        return []
    margin = lev > 1 or allow_short
    fee = FEE if fee is None else fee
    targets = {k: v for k, v in targets.items() if k in prices}   # монета ещё не торгуется или нет цены за день
    fills = []
    order = sorted(targets, key=lambda k: -1 if targets[k] is None else targets[k] - acct["units"].get(k, 0) * prices[k] / equity)
    for k in order:
        if targets[k] is None:
            continue
        cur_val = acct["units"].get(k, 0) * prices[k]
        cur_w = cur_val / equity
        diff = targets[k] - cur_w
        if abs(diff) <= THRESHOLD and not (targets[k] == 0 and abs(cur_w) > 0.0005):
            continue
        side = "buy" if diff > 0 else "sell"
        notional = abs(diff) * equity if targets[k] != 0 else abs(cur_val)
        if not margin and side == "buy":
            notional = min(notional, acct["cash"] / (1 + fee))
        if margin:
            others = sum(abs(acct["units"].get(j, 0) * prices[j]) for j in prices if j != k)
            room = max(0.0, lev * equity - others)          # сколько может занять эта монета
            new_val = cur_val + (notional if side == "buy" else -notional)
            if abs(new_val) > room and abs(new_val) > abs(cur_val):
                lim = max(room, abs(cur_val)) * np.sign(new_val)
                notional = abs(lim - cur_val)
        if notional < MIN_ORDER and not (margin and targets[k] == 0):
            # меньше минимума Bybit ордер не пройдёт (закрыть позицию целиком с плечом/шортом можно: reduce-only)
            if rec and notional >= 0.01:
                rec.append_csv("orders/skipped.csv", dict(ts=now_utc().isoformat(), day=str(day.date()), model=model,
                               coin=k, side=side, notional=notional, target_w=targets[k], prev_w=cur_w,
                               reason=f"меньше минимума {MIN_ORDER:g} USDT"))
            continue
        book = (books or {}).get(k)
        px, used = fill_price(side, notional, book, prices[k])
        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2 if book and book.get("bids") and book.get("asks") else prices[k]
        qty = notional / px
        if side == "buy":
            acct["cash"] -= notional * (1 + fee)
            acct["units"][k] = acct["units"].get(k, 0) + qty
        else:
            if not allow_short:
                qty = min(qty, max(acct["units"].get(k, 0), 0))
            acct["cash"] += qty * px * (1 - fee)
            acct["units"][k] = acct["units"].get(k, 0) - qty
        f = dict(ts=now_utc().isoformat(), day=str(day.date()), model=model, coin=k, side=side, qty=qty, vwap=px,
                 mid=mid, slippage_bps=(px / mid - 1) * 1e4 * (1 if side == "buy" else -1), notional=qty * px,
                 fee=qty * px * fee, market="perp" if margin else "spot", book_levels_used=used, target_w=targets[k], prev_w=cur_w,
                 book_source="live" if book else "fallback (catchup или нет стакана)")
        fills.append(f)
        if rec:
            rec.append_csv("orders/fills.csv", f)
    return fills


def apply_funding(acct, prices, funding):
    """Плата за удержание позиций с плечом/шортом за сутки (3 периода funding по 8 ч): лонг платит при
    положительной ставке, шорт получает. Возвращает уплаченную сумму (минус = получено)."""
    paid = 0.0
    for k, u in acct["units"].items():
        r = funding.get(k)
        r = DEFAULT_FUNDING if r is None else r
        paid += u * prices[k] * r * 3
    acct["cash"] -= paid
    return paid


def check_liquidation(acct, lows, highs):
    """Ликвидация по худшей цене дня (минимум для лонга, максимум для шорта)."""
    worst = {k: (lows[k] if u > 0 else highs[k]) for k, u in acct["units"].items() if u and k in lows}
    if not worst:
        return None
    eq = acct["cash"] + sum(acct["units"][k] * p for k, p in worst.items())
    gross = sum(abs(acct["units"][k]) * p for k, p in worst.items())
    if gross > 0 and eq < MAINT_MARGIN * gross:
        info = {"equity_at_worst": eq, "gross": gross, "prices": worst}
        acct["cash"] = max(0.0, eq - FEE * gross)
        acct["units"] = {}
        return info
    return None


def port_vol(targets, closes, day, n=30):
    """Годовая волатильность портфеля с весами targets по дневным доходностям за n дней."""
    ks = [k for k, w in targets.items() if w]
    if not ks:
        return 0.0
    r = pd.DataFrame({k: closes[k].loc[:day].pct_change().iloc[-n:] for k in ks}).fillna(0)
    w = np.array([targets[k] for k in ks])
    return float(np.sqrt(max(w @ r.cov().values @ w, 0) * 365))


def carry_step(acct, funding, eq):
    """Свободные деньги счёта без плеча стоят в связке «спот BTC + шорт перпа BTC» (без риска цены) и получают
    funding. Включено, если средний funding BTC за прошлые дни > 0. Размер = 90% минимальной свободной доли
    за 7 дней; смена размера больше 10% капитала оплачивается комиссией обеих ног."""
    fr = funding.get("BTC")
    fr = DEFAULT_FUNDING if fr is None else fr
    pnl = acct.get("carry", 0.0) * fr * 3                 # funding за прошедшие сутки на открытую связку
    acct["cash"] += pnl
    hist = acct.setdefault("fund_hist", [])
    sig = (np.mean(hist[-7:]) if hist else fr) > 0
    hist.append(fr)
    del hist[:-30]
    return pnl, sig


def carry_rebalance(acct, sig, idle_frac, eq):
    ih = acct.setdefault("idle_hist", [])
    ih.append(idle_frac)
    del ih[:-CARRY_DAYS]
    cur = acct.get("carry", 0.0)
    tgt = CARRY_SHARE * min(ih) * eq if sig and eq > 0 else 0.0
    tgt = min(tgt, CARRY_SHARE * idle_frac * eq)          # стратегия зашла в рынок: связку уменьшить сразу
    cost = 0.0
    must_cut = cur > CARRY_SHARE * idle_frac * eq + 1e-9   # свободных денег стало меньше, чем в связке
    if abs(tgt - cur) > CARRY_MIN_CHANGE * eq or (tgt == 0 and cur > 0) or must_cut:
        cost = abs(tgt - cur) * (FEE + PERP_FEE)
        acct["cash"] -= cost
        acct["carry"] = tgt
    return cost


FLOW_KEYS = ("fees", "funding", "carry_income", "carry_cost", "liquidations")


def flows_split(acct, eq):
    """Итог счёта по источникам: рынок (движение цен), комиссии сделок, funding по плечу/шорту, carry."""
    fl = acct.get("flows", {})
    other = -fl.get("fees", 0.0) - fl.get("funding", 0.0) + fl.get("carry_income", 0.0) - fl.get("carry_cost", 0.0)
    return dict(total=eq - acct.get("start", eq), market=eq - acct.get("start", eq) - other, fees=-fl.get("fees", 0.0),
                funding=-fl.get("funding", 0.0), carry=fl.get("carry_income", 0.0) - fl.get("carry_cost", 0.0),
                carry_income=fl.get("carry_income", 0.0), carry_cost=-fl.get("carry_cost", 0.0),
                liquidations=int(fl.get("liquidations", 0)))


def step_model(m, acct, ms, eq_hist, ctx, day, prices, books, rec, funding, lows, highs):
    """Один день одной модели: ликвидация по диапазону дня, funding, carry, решение, сделки."""
    base, lev, short = model_spec(m)
    margin = lev > 1 or short
    liq = check_liquidation(acct, lows, highs) if margin and lows else None
    paid = apply_funding(acct, prices, funding) if margin else 0.0
    carry_on = lev == 1 or base in ("EXP", "FMIX", "COMBO", "MEGA")                # EXP большую часть времени вне рынка
    carry_pnl, carry_sig = carry_step(acct, funding, None) if carry_on else (0.0, False)
    targets, diag = decide(base, ctx, ms, eq_hist, short=short, bear_only=short)
    eq0 = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in prices)
    start = acct.setdefault("start", acct.get("base", eq0))
    old = acct.get("base", start)
    if COMPOUND_STEP > 0:
        sizing = max(0.0, start + COMPOUND_STEP * math.floor((eq0 - start) / COMPOUND_STEP))   # всегда <= капитала
    else:
        sizing = max(0.0, eq0)
    acct["base"] = sizing
    if COMPOUND_STEP > 0 and sizing != old:
        diag["_compound"] = {"from": old, "to": sizing, "equity": eq0}
    mode, tv = lev_mode(m)
    if mode == "vol":                                    # плечо по волатильности: спокойный рынок больше, бурный меньше
        pv = port_vol({k: (v or 0.0) for k, v in targets.items()}, ctx["closes"], day)
        lev = min(model_spec(m)[1], tv / pv) if pv > 0 else 1.0
        diag["_leverage"] = {"port_vol_unlevered": pv, "leverage": lev}
    elif mode == "realized":                             # плечо по фактической волатильности самого портфеля
        lh = ms.setdefault("_lev_hist", [])
        eq = pd.Series(list(eq_hist[-MEGA_VOL_DAYS - 1:]), dtype=float)
        r = eq.pct_change().dropna()
        past_lev = np.array(lh[-len(r):]) if lh and len(r) else np.array([])
        if len(r) >= 10 and len(past_lev) == len(r) and past_lev.min() > 0:
            rv = float((r.values / past_lev).std() * math.sqrt(365))   # волатильность без плеча
            lev = min(model_spec(m)[1], tv / rv) if rv > 0 else 1.0
        else:
            lev = 1.0                                    # первые 10 дней без плеча
            rv = None
        lh.append(lev)
        del lh[:-MEGA_VOL_DAYS - 5]
        diag["_leverage"] = {"realized_vol_unlevered": rv, "leverage": lev}
    scale = (sizing / eq0 if eq0 > 0 else 0.0) * lev    # позиции считаются от ступенчатой базы, остаток лежит в USDT
    if scale != 1:
        targets = {k: (None if v is None else v * scale) for k, v in targets.items()}
    if liq:
        diag["_liquidation"] = liq
    fills = execute(acct, targets, prices, books, m, day, rec, lev=model_spec(m)[1] if margin and not short else 1.0,
                    allow_short=short, fee=PERP_FEE if margin else FEE)
    eq = acct["cash"] + sum(acct["units"].get(k, 0) * prices[k] for k in prices)
    cost = 0.0
    if carry_on:
        gross = sum(abs(acct["units"].get(k, 0)) * prices[k] for k in prices)
        was = acct.get("carry", 0.0)
        cost = carry_rebalance(acct, carry_sig, max(0.0, (eq - gross) / eq) if eq > 0 else 0.0, eq)
        eq -= cost
        diag["_carry"] = {"notional": acct.get("carry", 0.0), "pnl": carry_pnl, "cost": cost, "on": bool(carry_sig)}
        if cost and rec:
            rec.append_csv("orders/carry.csv", dict(ts=now_utc().isoformat(), day=str(day.date()), model=m,
                                                    from_usdt=was, to_usdt=acct.get("carry", 0.0), cost=cost,
                                                    funding_btc=funding.get("BTC")))
    fl = acct.setdefault("flows", {k: 0.0 for k in FLOW_KEYS})   # накопленные потоки денег счёта
    fl["fees"] = fl.get("fees", 0.0) + sum(f["fee"] for f in fills)
    fl["funding"] = fl.get("funding", 0.0) + paid
    fl["carry_income"] = fl.get("carry_income", 0.0) + carry_pnl
    fl["carry_cost"] = fl.get("carry_cost", 0.0) + cost
    fl["liquidations"] = fl.get("liquidations", 0.0) + (1 if liq else 0)
    diag["_flows_today"] = {"fees": sum(f["fee"] for f in fills), "funding": paid, "carry_income": carry_pnl,
                            "carry_cost": cost}
    return targets, diag, fills, eq, paid, liq


# ====================================================================================== запись данных

class Recorder:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _clean(x):
        if isinstance(x, dict):
            return {k: Recorder._clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [Recorder._clean(v) for v in x]
        if isinstance(x, (np.floating, float)):
            return None if math.isnan(float(x)) or math.isinf(float(x)) else float(x)
        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, (pd.Timestamp, datetime)):
            return x.isoformat()
        return x

    def append_jsonl(self, rel, obj):
        rec = {"_schema": SCHEMA_VERSION, "_run": RUN_ID, "_code": CODE_VERSION, "_written": now_utc().isoformat(),
               **self._clean(obj)}
        with open(self.path(rel), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def append_csv(self, rel, row):
        p = self.path(rel)
        row = {**row, "_run": RUN_ID, "_code": CODE_VERSION}
        if p.exists():   # одинаковый порядок и набор колонок, новые поля не ломают файл
            cols = list(pd.read_csv(p, nrows=0).columns)
            extra = [c for c in row if c not in cols]
            if extra:
                old = pd.read_csv(p)
                for c in extra:
                    old[c] = np.nan
                atomic_write(p, old.to_csv(index=False))
                cols += extra
            pd.DataFrame([row]).reindex(columns=cols).to_csv(p, mode="a", header=False, index=False)
        else:
            pd.DataFrame([row]).to_csv(p, index=False)

    def upsert_csv(self, rel, df):
        p = self.path(rel)
        if p.exists():
            old = pd.read_csv(p, index_col=0, parse_dates=True)
            df = pd.concat([old[~old.index.isin(df.index)], df]).sort_index()
        atomic_write(p, df.to_csv())


def atomic_write(path, text):
    """Запись через временный файл и переименование: при сбое питания файл не окажется наполовину записан."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


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
                coin_mvrv=coin_mvrv, coin_sma1400_ratio=coin_200w,
                stables=oc["stables"].shift(1) if oc is not None and "stables" in oc else None)


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
    atomic_write(p, d.to_csv(index=False))


# ====================================================================================== команды

def state_path(a):
    return Path(a.dir) / "state.json"


def load_state(a):
    p = state_path(a)
    if not p.exists():
        raise SystemExit(f"нет {p}: сначала python abtest.py init --capital 100 --coins BTC,ETH")
    st = json.loads(p.read_text(encoding="utf-8"))
    for m in MODELS:                                   # счета, добавленные в новой версии
        if m not in st["accounts"]:
            st["accounts"][m] = {"cash": st["capital"], "units": {}, "start": st["capital"], "base": st["capital"]}
            st["model_state"][m], st["equity_hist"][m] = {}, {}
            st.setdefault("_added_models", []).append(m)
        st["accounts"][m].setdefault("start", st["capital"])
    return st


def save_state(a, st):
    st["code_version"] = CODE_VERSION
    st.pop("_added_models", None)
    atomic_write(state_path(a), json.dumps(st, indent=2, ensure_ascii=False, default=str))
    b = Path(a.dir) / "state_backups" / f"state_{now_utc():%Y-%m-%d}.json"
    b.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(b, json.dumps(st, indent=2, ensure_ascii=False, default=str))


def cmd_init(a):
    Path(a.dir).mkdir(parents=True, exist_ok=True)
    coins = a.coins.split(",")
    if "BTC" not in coins:
        coins = ["BTC"] + coins
    st = dict(created=now_utc().isoformat(), capital=a.capital, coins=coins, exchange=a.exchange, last_cycle=None,
              accounts={m: {"cash": a.capital, "units": {}, "start": a.capital, "base": a.capital} for m in MODELS},
              model_state={m: {} for m in MODELS}, equity_hist={m: {} for m in MODELS})
    save_state(a, st)
    log(f"создано {len(MODELS)} виртуальных счетов по {a.capital:,.0f} {a.exchange} {coins}, папка {a.dir}")


def cmd_snapshot(a, feed=None, st=None):
    st = st or load_state(a)
    feed = feed or Feed(st["exchange"])
    rec = Recorder(Path(a.dir) / "data_lake")
    books = {}
    for k in st["coins"]:
        s = feed.snapshot(k)
        rec.append_jsonl(f"market/snapshots/{s['ts'][:10]}.jsonl", s)
        books[k] = s.get("orderbook")
        fr = (s.get("funding") or {}).get("fundingRate")
        if fr is not None:
            st.setdefault("funding", {})[k] = float(fr)
        log(f"снимок {k}: last {((s.get('ticker') or {}).get('last'))}, спред {s.get('spread_bps', float('nan')):.1f} bps, "
            f"funding {((s.get('funding') or {}).get('fundingRate'))}")
    if feed.name != "coinbase":                        # остальные монеты списка: funding, OI, лонг/шорт каждый час
        ok = 0
        for k in [c for c in ROT_UNIVERSE if c not in st["coins"]]:
            try:
                d = feed.derivs(k)
                rec.append_jsonl(f"market/derivs/{d['ts'][:10]}.jsonl", d)
                ok += d.get("funding") is not None
            except Exception as e:
                log(f"деривативы {k}: {str(e)[:100]}", "warning")
        log(f"деривативы ещё {ok} монет записаны")
    for r in options_snapshot():
        rec.append_jsonl(f"market/options/{r['ts'][:10]}.jsonl", r)
    return books


# ====================================================================================== редкие данные (копятся)

def options_snapshot():
    """Deribit, BTC и ETH: индекс DVOL, открытый интерес и объём путов/коллов, IV около денег и перекос (skew)
    на ближайшей экспирации к 30 дням. Истории перекоса и put/call в бесплатном доступе нет, поэтому пишем сами."""
    out = []
    for cur in ("BTC", "ETH"):
        r = {"ts": now_utc().isoformat(), "currency": cur}
        try:
            r["dvol"] = S._get_json(f"https://www.deribit.com/api/v2/public/get_index_price?index_name={cur.lower()}dvol_usdc")["result"]["index_price"]
            book = S._get_json(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={cur}&kind=option")["result"]
            rows = []
            for b in book:
                _, exp, strike, typ = b["instrument_name"].split("-")
                rows.append(dict(exp=pd.Timestamp(datetime.strptime(exp, "%d%b%y")), k=float(strike), t=typ,
                                 oi=b.get("open_interest") or 0, vol=b.get("volume") or 0, iv=b.get("mark_iv"),
                                 u=b.get("underlying_price")))
            o = pd.DataFrame(rows)
            r["put_oi"], r["call_oi"] = float(o[o.t == "P"].oi.sum()), float(o[o.t == "C"].oi.sum())
            r["put_vol_24h"], r["call_vol_24h"] = float(o[o.t == "P"].vol.sum()), float(o[o.t == "C"].vol.sum())
            r["put_call_oi"] = r["put_oi"] / r["call_oi"] if r["call_oi"] else None
            now = pd.Timestamp.now().normalize()
            exps = sorted(o.exp.unique(), key=lambda e: abs((e - now).days - 30))
            e = o[o.exp == exps[0]]
            u = float(e.u.median())
            pick = lambda typ, k: e[e.t == typ].iloc[(e[e.t == typ].k - k).abs().argsort()[:1]]
            atm_c, atm_p = pick("C", u), pick("P", u)
            p90, c110 = pick("P", 0.9 * u), pick("C", 1.1 * u)
            r.update(expiry=str(exps[0].date()), underlying=u,
                     atm_iv=float((atm_c.iv.iloc[0] + atm_p.iv.iloc[0]) / 2),
                     put90_iv=float(p90.iv.iloc[0]), call110_iv=float(c110.iv.iloc[0]),
                     skew_90_110=float(p90.iv.iloc[0] - c110.iv.iloc[0]))
        except Exception as ex:
            r["error"] = str(ex)[:200]
        out.append(r)
    return out


def record_daily_extras(feed, rec):
    """Раз в день: ставки Bybit Earn (гибкие, USDT/BTC/ETH) и индекс страха и жадности."""
    if feed.name == "bybit":
        for c in ("USDT", "BTC", "ETH"):
            try:
                r = feed.spot.publicGetV5EarnProduct({"category": "FlexibleSaving", "coin": c})
                rec.append_jsonl("market/earn.jsonl", {"ts": now_utc().isoformat(), "coin": c,
                                                       "products": (r.get("result") or {}).get("list", [])})
            except Exception as e:
                rec.append_jsonl("market/earn.jsonl", {"ts": now_utc().isoformat(), "coin": c, "error": str(e)[:200]})
    try:
        d = S._get_json("https://api.alternative.me/fng/?limit=0")["data"]
        f = pd.DataFrame({"fng": [int(x["value"]) for x in d]},
                         index=pd.to_datetime([int(x["timestamp"]) for x in d], unit="s"))
        rec.upsert_csv("market/fng.csv", f.sort_index())
    except Exception as e:
        log(f"индекс страха и жадности: {str(e)[:100]}", "warning")


LIQ_STATUS = {"connected": False, "n": 0, "error": None}


def start_liquidation_stream(exchange, coins, root):
    """Фоновый поток: все ликвидации по перпам монет списка через websocket биржи -> market/liquidations/<день>.csv.
    История ликвидаций по REST не отдаётся, поэтому копится только пока бот работает."""
    import asyncio
    import threading

    def run():
        try:
            run_inner()
        except BaseException as e:
            import traceback
            LIQ_STATUS["error"] = f"{type(e).__name__}: {e}"
            log(f"поток ликвидаций остановлен: {e}\n{traceback.format_exc()}", "error")

    def run_inner():
        import ccxt.pro as ccxtpro
        rec = Recorder(Path(root) / "data_lake")
        feed_sym = Feed.__new__(Feed)
        feed_sym.name = exchange

        def save(ex, q, coin):
            info = q.get("info") or {}
            det = (info.get("details") or [{}])[0] if isinstance(info.get("details"), list) else {}
            cs = q.get("contractSize") or (ex.markets.get(q.get("symbol"), {}) or {}).get("contractSize") or 1
            px, n = q.get("price"), q.get("contracts")
            rec.append_csv(f"market/liquidations/{now_utc():%Y-%m-%d}.csv", dict(
                ts=q.get("datetime"), exchange=exchange, coin=coin, price=px, contracts=n,
                usd=(px * n * cs) if px and n else q.get("quoteValue"),
                side=q.get("side") or info.get("S") or info.get("side"),
                pos_side=det.get("posSide") or ("long" if info.get("S") == "Buy" else "short" if info.get("S") == "Sell" else None)))

        async def one(ex, syms):
            while True:
                try:
                    if len(syms) > 1:
                        got = await ex.watch_liquidations_for_symbols(list(syms))
                    else:
                        got = await ex.watch_liquidations(next(iter(syms)))
                    for q in got:
                        save(ex, q, syms.get(q.get("symbol"), q.get("symbol")))
                        LIQ_STATUS["n"] += 1
                except Exception as e:
                    log(f"поток ликвидаций: {str(e)[:120]}, переподключение через 30 с", "warning")
                    await asyncio.sleep(30)

        async def main():
            ex = getattr(ccxtpro, exchange)({"options": {"defaultType": "swap"}})
            ex.aiohttp_trust_env = True                 # системный прокси, если он есть
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            if proxy:
                ex.wss_proxy = proxy
            await ex.load_markets()
            LIQ_STATUS["connected"] = True
            syms = {Feed.psym(feed_sym, k): k for k in coins if Feed.psym(feed_sym, k) in ex.markets}
            if ex.has.get("watchLiquidationsForSymbols") is True:
                await one(ex, syms)                     # одна подписка на все монеты (OKX)
            else:
                await asyncio.gather(*(one(ex, {s_: k}) for s_, k in syms.items()))   # по монете (Bybit)
        asyncio.run(main())
    t = threading.Thread(target=run, name="liquidations", daemon=True)
    t.start()
    return t


def record_funding_history(feed, rec, coins):
    """market/funding/<COIN>.csv: полная история ставок funding, дописывается раз в день. При первом запуске
    берёт всё, что отдаёт биржа (до ~2 лет), дальше только новое. История копится, даже если биржа потом её урежет."""
    for k in coins:
        p = rec.path(f"market/funding/{k}.csv")
        try:
            since = (pd.read_csv(p, index_col=0, parse_dates=True).index[-1].value // 10**6 + 1) if p.exists() \
                else feed.perp.milliseconds() - 730 * DAY
            df = feed.funding_history(k, since)
            if len(df):
                rec.upsert_csv(f"market/funding/{k}.csv", df)
        except Exception as e:
            log(f"история funding {k}: {str(e)[:100]}", "warning")


def cmd_cycle(a):
    st = load_state(a)
    feed = Feed(st["exchange"])
    rec = Recorder(Path(a.dir) / "data_lake")
    coins = st["coins"]
    closes_df = {k: feed.daily(k) for k in coins}
    closes = {k: v.close for k, v in closes_df.items()}
    latest = max(c.index[-1] for c in closes.values())
    common = sorted(set.intersection(*[set(c.index) for c in closes.values()]))
    last = pd.Timestamp(st["last_cycle"]) if st["last_cycle"] else None
    todo = [d for d in common if last is None or d > last]
    if last is None:
        todo = todo[-1:]                               # первый запуск: только последняя свеча
    if a.force and not todo:
        todo = [latest]
    if not todo:
        log(f"цикл за свечу {latest.date()} уже выполнен")
        added = st.get("_added_models")
        if added:                                      # новые стратегии после обновления: сразу видны на панели
            save_state(a, st)
            log(f"добавлены стратегии: {', '.join(added)}, начнут торговать со следующего дневного цикла")
        return
    if len(todo) > 1:
        log(f"ноутбук был выключен или спал: догоняю {len(todo)} дней {todo[0].date()} .. {todo[-1].date()} "
            f"(пропущенные дни исполняются по цене закрытия и помечаются catchup)", "warning")
        rec.append_jsonl("meta/gaps.jsonl", {"from": str(todo[0].date()), "to": str(todo[-1].date()), "days": len(todo)})
    uni_df = {}
    for k in ROT_UNIVERSE:                             # для ROT3: свечи всех монет списка
        if k in closes_df:
            uni_df[k] = closes_df[k]
            continue
        try:
            uni_df[k] = feed.daily(k, days=600)
        except Exception as e:
            log(f"ROT3: нет данных по {k}: {str(e)[:100]}", "warning")
    for k, v in uni_df.items():
        rec.upsert_csv(f"market/ohlcv_1d/{k}.csv", v)
    oc = onchain(a.dir)
    ocf = onchain_features(oc)
    layers = S.market_layers(oc, S.Params())
    rec.upsert_csv("market/onchain_daily.csv", oc)
    ctx = build_context(closes, ocf, oc, layers, coins, todo[-1])
    ctx["uni"] = {k: v.close for k, v in uni_df.items()}
    ctx["lows"] = {k: v.low for k, v in uni_df.items()}
    ctx["highs"] = {k: v.high for k, v in uni_df.items()}
    ctx["h4"] = {}
    for k in ("BTC", "ETH"):                           # для ZONE: 4-часовые свечи (зоны и карта ликвидаций)
        if k in coins:
            try:
                ctx["h4"][k] = feed.daily(k, days=ZONE_LIQ_DAYS + 5 + (pd.Timestamp(latest) - todo[0]).days, tf="4h")
                rec.upsert_csv(f"market/ohlcv_4h/{k}.csv", ctx["h4"][k])
            except Exception as e:
                log(f"ZONE: нет 4ч свечей {k}: {str(e)[:100]}", "warning")
    record_funding_history(feed, rec, list(dict.fromkeys(coins + ROT_UNIVERSE)))
    try:
        ctx["fund_btc"] = funding_daily(pd.read_csv(rec.path("market/funding/BTC.csv"), index_col=0, parse_dates=True))
    except Exception as e:
        ctx["fund_btc"] = None
        log(f"FNEG: нет истории funding BTC: {str(e)[:100]}", "warning")
    record_daily_extras(feed, rec)
    for day in todo:
        live = day == latest
        books = cmd_snapshot(a, feed, st) if live else None      # живой стакан только для текущего дня
        if live:
            for k in uni_df:                           # стаканы остальных монет списка (для сделок ROT3)
                if k not in books:
                    try:
                        ob = feed.spot.fetch_order_book(feed.sym(k), 50)
                        books[k] = {"bids": [[float(x[0]), float(x[1])] for x in ob["bids"]],
                                    "asks": [[float(x[0]), float(x[1])] for x in ob["asks"]]}
                    except Exception:
                        books[k] = None
        prices = {}
        for k, v in uni_df.items():
            if day not in v.index:
                continue
            bid = ((books or {}).get(k) or {}).get("bids") if live else None
            prices[k] = float(bid[0][0]) if bid else float(v.close.loc[day])
        process_day(st, rec, ctx, day, prices, books, catchup=not live)
        st["last_cycle"] = str(day.date())
        save_state(a, st)
    label_m4_predictions(rec, closes)
    try:
        cmd_dashboard(a, st)
    except Exception as e:
        log(f"панель не обновлена: {e}", "warning")


def process_day(st, rec, ctx, day, prices, books, catchup):
    coins = st["coins"]
    ctx["day"] = day
    feat_rows = {k: ctx["feats"][k].loc[day].to_dict() for k in coins}
    lay = ctx["layers"].reindex([day]).iloc[0].to_dict()
    rec.append_jsonl(f"features/{day.date()}.jsonl", {"day": str(day.date()), "catchup": catchup, "prices": prices,
                                                      "features": feat_rows, "layers": lay})
    lows = {k: float(ctx["lows"][k].get(day, prices[k])) for k in prices if k in ctx["lows"]} if ctx.get("lows") else None
    highs = {k: float(ctx["highs"][k].get(day, prices[k])) for k in prices if k in ctx["highs"]} if ctx.get("highs") else None
    funding = st.get("funding", {})
    for m in MODELS:
        acct, ms = st["accounts"][m], st["model_state"][m]
        targets, diag, fills, eq, paid, liq = step_model(m, acct, ms, list(st["equity_hist"][m].values()), ctx, day,
                                                         prices, books, rec, funding, lows, highs)
        if "_compound" in diag:
            c = diag["_compound"]
            log(f"{day.date()} {m}: база для расчёта позиций {c['from']:,.0f} -> {c['to']:,.0f} (капитал {c['equity']:,.2f})")
        if liq:
            log(f"{day.date()} {m}: ЛИКВИДАЦИЯ, капитал при худшей цене дня {liq['equity_at_worst']:,.2f}", "warning")
        st["equity_hist"][m][str(day.date())] = eq
        rec.append_jsonl(f"decisions/{day.date()}.jsonl", {"day": str(day.date()), "model": m, "catchup": catchup,
                                                           "targets": targets, "diag": diag, "n_fills": len(fills)})
        gross = sum(abs(acct["units"].get(k, 0)) * prices[k] for k in prices)
        rec.append_csv("equity/equity.csv", dict(day=str(day.date()), ts=now_utc().isoformat(), model=m, equity=eq,
                                                 cash=acct["cash"], catchup=catchup, leverage=gross / eq if eq > 0 else 0,
                                                 funding_paid=paid, liquidated=bool(liq), sizing_base=acct.get("base"),
                                                 carry_notional=acct.get("carry", 0.0),
                                                 carry_pnl=(diag.get("_carry") or {}).get("pnl", 0.0),
                                                 **{f"cum_{k}": v for k, v in flows_split(acct, eq).items()},
                                                 **{f"w_{k}": acct["units"].get(k, 0) * prices[k] / eq if eq > 0 else 0
                                                    for k in coins},
                                                 positions=json.dumps({k: round(u * prices[k] / eq, 4) for k, u in
                                                                       acct["units"].items() if u and k in prices
                                                                       and eq > 0})))
        if m == "M4":
            for k in coins:
                rec.append_csv("models/m4_predictions.csv", dict(day=str(day.date()), coin=k,
                               prob_up_7d=diag[k]["prob_up_7d"], prob_smoothed=diag[k]["prob_smoothed"],
                               size=diag[k]["size"], realized_ret=np.nan, realized_up=np.nan, correct=np.nan))
            if "_model" in diag:
                rec.append_jsonl("models/m4_versions.jsonl", {"day": str(day.date()), **diag["_model"]})
        ft = diag.get("_flows_today", {})
        extra = "".join(t for t in (
            f", комиссии -{ft.get('fees', 0):.2f}" if ft.get("fees") else "",
            f", funding {-ft.get('funding', 0):+.2f}" if abs(ft.get("funding", 0)) >= 0.005 else "",
            f", carry {ft.get('carry_income', 0) - ft.get('carry_cost', 0):+.2f}"
            if abs(ft.get("carry_income", 0) - ft.get("carry_cost", 0)) >= 0.005 else ""))
        log(f"{day.date()} {m}: сделок {len(fills)}{extra}, капитал {eq:,.2f} ({eq / st['capital'] - 1:+.2%})"
            f"{' [catchup]' if catchup else ''}")



MODEL_INFO = {
    "M1": ("Базовые", "Тренд + 4 слоя (система как построена)"),
    "M2": ("Базовые", "Размер по волатильности + стоп 25% + тормоз по просадке"),
    "M3": ("Базовые", "Один вход в лучший момент, дальше держать"),
    "M4": ("Базовые", "Онлайн-ML, дообучается каждый день"),
    "M1_x3": ("Плечо до x3", "M1, плечо по волатильности"),
    "M2_x3": ("Плечо до x3", "M2, плечо по волатильности"),
    "M3_x3": ("Плечо до x3", "M3, плечо по волатильности"),
    "M4_x3": ("Плечо до x3", "M4, плечо по волатильности"),
    "M1_ls": ("Лонг + шорт", "M1, шорт в медвежьем рынке"),
    "M2_ls": ("Лонг + шорт", "M2, шорт в медвежьем рынке"),
    "M3_ls": ("Лонг + шорт", "M3, шорт на перегреве"),
    "M4_ls": ("Лонг + шорт", "M4, шорт в медвежьем рынке"),
    "M1_x5": ("Эксперименты", "M1, плечо по волатильности до x5"),
    "ROT3": ("Эксперименты", "Ротация: 3 сильнейшие монеты из 19"),
    "EXP": ("Эксперименты", "Эксперт: покупка паники в бычьем рынке, x2"),
    "ZONE": ("Эксперименты", "Пробой зоны проторговки + карта ликвидаций + перцентиль"),
    "FNEG": ("Эксперименты", "BTC только при отрицательном funding за 7 дней (против толпы)"),
    "FMIX": ("Эксперименты", "FNEG + фильтр роста стейблкоинов, x2, свободные деньги в carry"),
    "COMBO": ("Эксперименты", "M1 + сверху 1 BTC при сигнале FMIX (две некоррелированные части), до x2"),
    "MEGA": ("Эксперименты", "Портфель: тренд M1 + паника EXP + funding FMIX поровну, риск 20% годовых, до x2"),
}
PALETTE = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#db2777",
           "#4f46e5", "#059669", "#b91c1c", "#7c3aed", "#0d9488", "#c2410c", "#64748b", "#a16207", "#be185d", "#15803d", "#1e3a8a", "#b45309"]


def cmd_dashboard(a, st=None):
    """abtest/dashboard.html: одна страница для наблюдения, обновляется сама (данные пересобирает loop)."""
    import html
    st = st or load_state(a)
    root = Path(a.dir)
    E = pd.DataFrame({m: pd.Series(st["equity_hist"].get(m, {})) for m in MODELS})
    E.index = pd.to_datetime(E.index)
    E = E.sort_index()
    pos = {}
    f = root / "data_lake/equity/equity.csv"
    if f.exists():
        try:
            last = pd.read_csv(f).groupby("model").tail(1).set_index("model")
            for m, r in last.iterrows():
                p = json.loads(r["positions"]) if isinstance(r.get("positions"), str) else {}
                pos[m] = (p, r.get("leverage", 0.0), r.get("carry_notional", 0.0), r.get("equity", np.nan))
        except Exception as e:
            log(f"панель: не прочитал equity.csv: {e}", "warning")
    cap = st["capital"]
    rows, legend, lines = [], [], []
    n = len(E)
    W, H, PAD = 900, 300, 40
    if n >= 2:
        norm = E / cap * 100
        lo, hi = float(np.nanmin(norm.values)), float(np.nanmax(norm.values))
        lo, hi = min(lo, 95), max(hi, 105)
        X = lambda i: PAD + i * (W - 2 * PAD) / (n - 1)
        Y = lambda v: H - PAD - (v - lo) * (H - 2 * PAD) / (hi - lo)
        lines.append(f'<line x1="{PAD}" x2="{W - PAD}" y1="{Y(100):.1f}" y2="{Y(100):.1f}" class="base"/>')
        for t in np.linspace(lo, hi, 5):
            lines.append(f'<text x="4" y="{Y(t) + 4:.1f}" class="ax">{t:.0f}</text>')
        for j, m in enumerate(MODELS):
            s_ = norm[m].dropna()
            if len(s_) < 2:
                continue
            pts = " ".join(f"{X(norm.index.get_loc(d)):.1f},{Y(v):.1f}" for d, v in s_.items())
            lines.append(f'<polyline points="{pts}" fill="none" stroke="{PALETTE[j]}" stroke-width="1.6" data-m="{m}"/>')
        lines.append(f'<text x="{PAD}" y="{H - 8}" class="ax">{E.index[0].date()}</text>')
        lines.append(f'<text x="{W - PAD - 70}" y="{H - 8}" class="ax">{E.index[-1].date()}</text>')
    group = None
    ranked = sorted(MODELS, key=lambda m: -(E[m].dropna().iloc[-1] if E[m].notna().any() else cap))
    best = ranked[0] if n else None
    for j, m in enumerate(MODELS):
        g, desc = MODEL_INFO.get(m, ("", ""))
        if g != group:
            rows.append(f'<tr class="grp"><td colspan="7">{html.escape(g)}</td></tr>')
            group = g
        e = E[m].dropna()
        eq = float(e.iloc[-1]) if len(e) else cap
        ret = eq / cap - 1
        dd = float((e / e.cummax() - 1).min()) if len(e) else 0.0
        day = float(e.iloc[-1] / e.iloc[-2] - 1) if len(e) > 1 else 0.0
        p, levv, carry, _ = pos.get(m, ({}, 0.0, 0.0, eq))
        ptxt = ", ".join(f"{k} {'шорт ' if v < 0 else ''}{abs(v):.0%}" for k, v in sorted(p.items(), key=lambda x: -abs(x[1]))) or "вне рынка"
        if carry and carry > 0.01:
            ptxt += f" · carry {carry:,.0f}"
        cls = "pos" if ret > 0 else "neg" if ret < 0 else ""
        star = " ★" if m == best and n else ""
        rows.append(f'<tr><td><span class="dot" style="background:{PALETTE[j]}"></span><b>{m}</b>{star}'
                    f'<div class="d">{html.escape(desc)}</div></td><td class="r">{eq:,.2f}</td>'
                    f'<td class="r {cls}">{ret:+.1%}</td><td class="r {"pos" if day > 0 else "neg" if day < 0 else ""}">{day:+.2%}</td>'
                    f'<td class="r">{dd:.1%}</td><td class="r">{(levv or 0):.1f}x</td><td>{html.escape(ptxt)}</td></tr>')
    logf = root / "logs/abtest.log"
    tail = []
    if logf.exists():
        try:
            tail = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-14:]
        except Exception:
            tail = []
    runs = root / "data_lake/meta/runs.jsonl"
    last_run = ""
    if runs.exists():
        try:
            last_run = json.loads(runs.read_text(encoding="utf-8").splitlines()[-1]).get("_written", "")
        except Exception:
            pass
    money = []
    tot = dict(market=0.0, fees=0.0, funding=0.0, carry=0.0, total=0.0)
    for j, m in enumerate(MODELS):
        e = E[m].dropna()
        f_ = flows_split(st["accounts"][m], float(e.iloc[-1]) if len(e) else cap)
        f_ = {k: (0.0 if isinstance(v, float) and abs(v) < 0.005 else v) for k, v in f_.items()}   # без «-0.00»
        for k in tot:
            tot[k] += f_[k]
        c = lambda v: f'class="r {"pos" if v > 0.005 else "neg" if v < -0.005 else ""}"'
        carry_t = (f'{f_["carry"]:+.2f}<div class="d">+{f_["carry_income"]:.2f} получено, {f_["carry_cost"]:.2f} комиссии</div>'
                   if f_["carry_income"] or f_["carry_cost"] else "0.00")
        money.append(f'<tr><td><span class="dot" style="background:{PALETTE[j]}"></span><b>{m}</b>'
                     f'{" · ликвидаций " + str(f_["liquidations"]) if f_["liquidations"] else ""}</td>'
                     f'<td {c(f_["market"])}>{f_["market"]:+.2f}</td><td {c(f_["fees"])}>{f_["fees"]:+.2f}</td>'
                     f'<td {c(f_["funding"])}>{f_["funding"]:+.2f}</td><td {c(f_["carry"])}>{carry_t}</td>'
                     f'<td {c(f_["total"])}><b>{f_["total"]:+.2f}</b></td></tr>')
    money.append(f'<tr><td><b>Все {len(MODELS)}</b></td>' + "".join(f'<td class="r"><b>{tot[k]:+.2f}</b></td>' for k in
                                                         ("market", "fees", "funding", "carry", "total")) + "</tr>")
    ops = []
    for rel, kind in (("orders/fills.csv", "fill"), ("orders/carry.csv", "carry")):
        fp = root / "data_lake" / rel
        if fp.exists():
            try:
                d = pd.read_csv(fp).tail(15)
                for _, r in d.iterrows():
                    if kind == "fill":
                        txt = (f'{r["model"]}: {"покупка" if r["side"] == "buy" else "продажа"} {r["coin"]} на '
                               f'{r["notional"]:,.2f} USDT по {r["vwap"]:,.2f} ({r.get("market", "spot")}), комиссия {r["fee"]:.3f}')
                    else:
                        txt = (f'{r["model"]}: carry (BTC спот + шорт перпа) {r["from_usdt"]:,.2f} → '
                               f'{r["to_usdt"]:,.2f} USDT, комиссии {r["cost"]:.3f}')
                    ops.append((str(r["day"]) + " " + str(r["ts"])[11:16], txt))
            except Exception as e:
                log(f"панель: не прочитал {rel}: {e}", "warning")
    ops = sorted(ops, reverse=True)[:15]
    ops_html = "".join(f'<tr><td class="d" style="white-space:nowrap">{html.escape(t)}</td>'
                       f'<td>{html.escape(x)}</td></tr>' for t, x in ops) or '<tr><td class="mut">Операций ещё не было: первые будут в дневном цикле 03:05 МСК.</td></tr>'
    def cnt(sub, pat="*"):
        d_ = root / "data_lake" / sub
        return sorted(d_.glob(pat)) if d_.exists() else []
    liq_files = cnt("market/liquidations", "*.csv")
    n_liq = sum(max(0, sum(1 for _ in open(f_, encoding="utf-8")) - 1) for f_ in liq_files[-31:])
    acc = (f"Снимки рынка: {len(cnt('market/snapshots', '*.jsonl'))} дн · деривативы 19 монет: "
           f"{len(cnt('market/derivs', '*.jsonl'))} дн · опционы Deribit: {len(cnt('market/options', '*.jsonl'))} дн · "
           f"ликвидации: {len(liq_files)} дн, {n_liq:,} за последний месяц · история funding: "
           f"{len(cnt('market/funding', '*.csv'))} монет · Earn: {'есть' if (root / 'data_lake/market/earn.jsonl').exists() else 'нет'}")
    status = ""
    stf = root / "selftest.json"
    if stf.exists():
        try:
            t = json.loads(stf.read_text(encoding="utf-8"))
            if t.get("ok"):
                status = (f'<div class="card ok">✓ <b>Тестовая сделка {t["ts"][:16].replace("T", " ")} UTC прошла.</b> '
                          f'Купил {t["qty"]:.8f} BTC за {t["notional"]:g} USDT по {t["buy_px"]:,.2f}, продал по '
                          f'{t["sell_px"]:,.2f}, издержки {t["cost"]:.4f} USDT. Виртуально, на живом стакане {html.escape(t["exchange"])}.</div>')
            else:
                status = f'<div class="card bad">✗ <b>Тестовая сделка не прошла:</b> {html.escape(str(t.get("error")))}</div>'
        except Exception:
            pass
    gen = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>backinsight</title><style>
:root{{--bg:#f8fafc;--card:#fff;--fg:#0f172a;--mut:#64748b;--line:#e2e8f0;--pos:#15803d;--neg:#b91c1c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b1220;--card:#111a2e;--fg:#e2e8f0;--mut:#94a3b8;--line:#1e293b;--pos:#4ade80;--neg:#f87171}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.w{{max-width:1000px;margin:0 auto;padding:16px}} h1{{font-size:20px;margin:4px 0}} .mut{{color:var(--mut)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:12px 0;overflow-x:auto}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{text-align:left;color:var(--mut);font-weight:500}} .r{{text-align:right;white-space:nowrap}} .d{{color:var(--mut);font-size:12px}}
.grp td{{background:transparent;color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em;padding-top:14px}}
.pos{{color:var(--pos)}} .neg{{color:var(--neg)}} .dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}
svg{{width:100%;height:auto}} .ax{{fill:var(--mut);font-size:11px}} .base{{stroke:var(--line);stroke-dasharray:4 4}}
.ok{{border-color:var(--pos)}} .bad{{border-color:var(--neg);color:var(--neg)}} #alive{{font-weight:600}}
pre{{white-space:pre-wrap;font:12px/1.4 ui-monospace,Consolas,monospace;margin:0;color:var(--mut)}}
</style></head><body><div class="w">
<h1>backinsight · {len(MODELS)} стратегий</h1>
<div id="alive" class="pos">● бот работает</div>
<script>var g=Date.parse("{gen}"),m=(Date.now()-g)/60000;var e=document.getElementById("alive");
if(m>130){{e.className="neg";e.textContent="● бот не отвечает "+Math.round(m/60)+" ч: проверьте, включён ли ноутбук и интернет";}}
else e.textContent="● бот работает, последняя проверка "+Math.max(0,Math.round(m))+" мин назад";</script>
{status}
<div class="mut">Деньги виртуальные, данные живые ({html.escape(st["exchange"])}). Старт {st["created"][:10]}, по {cap:,.0f} USDT на стратегию.
Дней в тесте: {n}. Последний дневной цикл: {st.get("last_cycle") or "ещё не было (первый в 03:05 МСК)"}.
Страница обновлена: {now_utc():%Y-%m-%d %H:%M} UTC{f", последний запуск {last_run[:16]}" if last_run else ""}. Версия {CODE_VERSION}{" (автообновление выключено)" if (Path(a.dir) / "autoupdate_off").exists() else ", обновляется сама с GitHub"}.</div>
<div class="card">{f'<svg viewBox="0 0 {W} {H}">{"".join(lines)}</svg>' if lines else '<div class="mut">График появится после второго дневного цикла.</div>'}
<div class="mut">Капитал каждой стратегии, старт = 100</div></div>
<div class="card"><table><tr><th>Стратегия</th><th class="r">Капитал</th><th class="r">Всего</th><th class="r">За день</th>
<th class="r">Макс. просадка</th><th class="r">Плечо</th><th>Сейчас</th></tr>{"".join(rows)}</table>
<div class="mut" style="margin-top:8px">★ лучшая сейчас. Меньше 90 дней: разница между стратегиями ещё в основном случайна.</div></div>
<div class="card"><b>Куда шли деньги</b> <span class="mut">(USDT с начала теста)</span><table>
<tr><th>Стратегия</th><th class="r">Рынок (цены)</th><th class="r">Комиссии сделок</th><th class="r">Funding плечо/шорт</th>
<th class="r">Carry (funding на свободные деньги)</th><th class="r">Итог</th></tr>{"".join(money)}</table>
<div class="mut" style="margin-top:8px">Рынок: прибыль и убыток от движения цен, включая проскальзывание и ликвидации. Funding: плата
за позиции с плечом и шорты, раз в 8 часов (плюс = получено). Carry: свободные деньги стоят в связке «купить BTC + шорт BTC-перпа»,
цена не влияет, доход даёт funding. Earn не используется.</div></div>
<div class="card"><b>Последние операции</b><table>{ops_html}</table></div>
<div class="card"><b>Накоплено данных</b><div class="mut">{html.escape(acc)}</div></div>
<div class="card"><b>Лог</b><pre>{html.escape(chr(10).join(tail)) or "пусто"}</pre></div>
</div></body></html>"""
    atomic_write(root / "dashboard.html", page)
    return root / "dashboard.html"


def cmd_selftest(a, st=None):
    """Тестовая сделка при запуске: виртуальная покупка на MIN_ORDER USDT по живому стакану и сразу продажа.
    Счета стратегий не трогает. Проверяет биржу, стакан, исполнение, запись на диск. Итог в selftest.json."""
    st = st or load_state(a)
    root = Path(a.dir)
    res = {"ts": now_utc().isoformat(), "exchange": st["exchange"], "ok": False, "steps": {}}
    try:
        t0 = time.time()
        feed = Feed(st["exchange"])
        ob = feed.snapshot("BTC", depth=20).get("orderbook")
        res["steps"]["биржа"] = f"{(time.time() - t0) * 1000:.0f} мс"
        if not ob or not ob.get("bids") or not ob.get("asks"):
            raise RuntimeError("биржа не отдала стакан BTC")
        mid = (ob["bids"][0][0] + ob["asks"][0][0]) / 2
        bpx, _ = fill_price("buy", MIN_ORDER, ob, mid)
        qty = MIN_ORDER * (1 - FEE) / bpx
        spx, _ = fill_price("sell", qty * mid, ob, mid)
        back = qty * spx * (1 - FEE)
        res.update(coin="BTC", notional=MIN_ORDER, buy_px=bpx, qty=qty, sell_px=spx, returned=back,
                   cost=MIN_ORDER - back)
        res["steps"]["покупка"] = f"{qty:.8f} BTC по {bpx:,.2f}"
        res["steps"]["продажа"] = f"по {spx:,.2f}, вернулось {back:.4f} USDT"
        Recorder(root / "data_lake").append_jsonl("meta/selftest.jsonl", res)
        res["steps"]["запись на диск"] = "ок"
        res["ok"] = 0 < res["cost"] < MIN_ORDER * 0.02          # потеря только комиссии и спред, меньше 2%
        if not res["ok"]:
            res["error"] = f"странная потеря {res['cost']:.4f} USDT"
    except Exception as e:
        res["error"] = str(e)[:300]
    atomic_write(root / "selftest.json", json.dumps(res, ensure_ascii=False, indent=1))
    if res["ok"]:
        log(f"тестовая сделка ОК: купил {res['qty']:.8f} BTC за {MIN_ORDER:g} USDT по {res['buy_px']:,.2f}, "
            f"продал по {res['sell_px']:,.2f}, издержки {res['cost']:.4f} USDT (виртуально)")
    else:
        log(f"тестовая сделка НЕ ПРОШЛА: {res.get('error')}", "error")
    return res["ok"]

def cmd_streamtest(a):
    """Проверка потока ликвидаций: 90 секунд слушает websocket и печатает, сколько записано."""
    st = load_state(a)
    start_liquidation_stream(st["exchange"], list(dict.fromkeys(st["coins"] + ROT_UNIVERSE)), a.dir)
    time.sleep(90)
    log(f"поток ликвидаций: подключён {LIQ_STATUS['connected']}, за 90 с записано {LIQ_STATUS['n']}, "
        f"ошибка {LIQ_STATUS['error']}")
    return LIQ_STATUS["connected"] and not LIQ_STATUS["error"]

_LOCK = None


def single_instance(base_dir):
    """Не даёт запустить второй loop на тех же данных (автозапуск + ручной запуск)."""
    global _LOCK
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    _LOCK = open(Path(base_dir) / "loop.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            _LOCK.seek(0)
            msvcrt.locking(_LOCK.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("loop уже запущен другим процессом, выхожу", "warning")
        sys.exit(0)


def cmd_loop(a):
    single_instance(a.dir)
    log("loop: снимок рынка каждый час, дневной цикл в 00:05 UTC. Ctrl+C для остановки")
    try:
        st0 = load_state(a)
        if st0["exchange"] in ("bybit", "okx"):
            start_liquidation_stream(st0["exchange"], list(dict.fromkeys(st0["coins"] + ROT_UNIVERSE)), a.dir)
            log("поток ликвидаций запущен (websocket биржи, пишет market/liquidations/)")
    except Exception as e:
        log(f"поток ликвидаций не запущен: {e}", "warning")
    try:
        cmd_selftest(a)
    except Exception as e:
        log(f"тестовая сделка: {e}", "error")
    while True:
        try:
            st = load_state(a)
            n = now_utc()
            if st["last_cycle"] != str((n - timedelta(days=1)).date()) and (n.hour > 0 or n.minute >= 5):
                cmd_cycle(a)                       # вчерашняя свеча закрыта и ещё не обработана (догоняет пропуски)
                month = f"{n:%Y%m}"
                if not list((Path(a.dir) / "exports").glob(f"abtest_export_{month}*.zip")):
                    cmd_export(a)                  # резервный архив раз в месяц
            else:
                cmd_snapshot(a)
            cmd_dashboard(a)
            auto_update(a)
        except SystemExit:
            raise
        except Exception as e:
            import traceback
            log(f"ошибка: {e}\n{traceback.format_exc()}", "error")
        nxt = (now_utc() + timedelta(hours=1)).replace(minute=5, second=0, microsecond=0)
        while now_utc() < nxt:                     # короткие паузы: после сна/гибернации проснётся сразу
            time.sleep(30)


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
    E = pd.DataFrame({m: pd.Series(st["equity_hist"][m]) for m in MODELS if st["equity_hist"].get(m)})
    if E.empty:
        print("пока нет данных: запустите cycle")
        return
    E.index = pd.to_datetime(E.index)
    E = E.sort_index().dropna()
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
    dfs = {k: feed.daily(k, days=a.days + 1500) for k in coins}
    for k in ROT_UNIVERSE:
        if k not in dfs:
            try:
                dfs[k] = feed.daily(k, days=a.days + 400)
            except Exception:
                pass
    closes = {k: dfs[k].close for k in coins}
    oc = onchain(a.dir)
    ocf = onchain_features(oc)
    layers = S.market_layers(oc, S.Params())
    common = sorted(set.intersection(*[set(c.index) for c in closes.values()]))
    days = common[-a.days:]
    accts = {m: {"cash": a.capital, "units": {}, "start": a.capital, "base": a.capital} for m in MODELS}
    mstate = {m: {} for m in MODELS}
    eqh = {m: {} for m in MODELS}
    ctx = build_context(closes, ocf, oc, layers, coins, days[0])
    ctx["uni"] = {k: v.close for k, v in dfs.items()}
    ctx["h4"] = {}
    for k in ("BTC", "ETH"):                             # для ZONE: без 4ч свечей она в replay не торговала бы
        try:
            ctx["h4"][k] = feed.daily(k, days=a.days + ZONE_LIQ_DAYS + 5, tf="4h")
        except Exception as e:
            log(f"ZONE: нет 4ч свечей {k}: {str(e)[:100]}", "warning")
    try:
        ctx["fund_btc"] = funding_daily(feed.funding_history("BTC", int(pd.Timestamp(days[0]).value // 10**6) - 10 * DAY))
    except Exception as e:
        ctx["fund_btc"] = None
        log(f"FNEG: нет истории funding: {str(e)[:100]}", "warning")
    local = Path(__file__).parent / "data/binance/BTC_funding.csv"   # биржи отдают историю funding коротко
    if local.exists():
        try:
            f = pd.read_csv(local)
            f.index = pd.to_datetime(f.t, unit="ms")
            hist = funding_daily(f[["rate"]])
            cur = ctx.get("fund_btc")
            ctx["fund_btc"] = hist if cur is None else pd.concat([hist[hist.index < cur.index[0]], cur])
        except Exception as e:
            log(f"локальная история funding: {str(e)[:100]}", "warning")
    liqs = {m: 0 for m in MODELS}
    m4_rows, nfills = [], {m: 0 for m in MODELS}
    for i, day in enumerate(days):
        ctx["day"] = day
        prices = {k: float(v.close.loc[day]) for k, v in dfs.items() if day in v.index}
        lows = {k: float(dfs[k].low.loc[day]) for k in prices}
        highs = {k: float(dfs[k].high.loc[day]) for k in prices}
        for m in MODELS:
            targets, diag, fills, eq, paid, liq = step_model(m, accts[m], mstate[m], list(eqh[m].values()), ctx, day,
                                                             prices, None, None, {}, lows, highs)
            nfills[m] += len(fills)
            liqs[m] += bool(liq)
            eqh[m][day] = eq
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
    t["ликвидаций"] = pd.Series(liqs)
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


def cmd_doctor(a):
    """Проверка: доступны ли биржа и источники данных с этого компьютера, и точны ли часы."""
    ok = True
    print(f"Python {sys.version.split()[0]}, pandas {pd.__version__}, numpy {np.__version__}, код {CODE_VERSION}")
    for name in ([a.exchange] + (["okx"] if a.exchange != "okx" else [])):
        try:
            f = Feed(name)
            t0 = time.time()
            srv = f.spot.fetch_time()
            lag = abs(srv / 1000 - time.time())
            snap = f.snapshot("BTC")
            got = [k for k in ("ticker", "orderbook", "funding", "open_interest", "long_short") if snap.get(k)]
            print(f"[{'OK' if snap.get('orderbook') else 'ПЛОХО'}] {name}: {', '.join(got)}; "
                  f"ответ {time.time() - t0:.1f} с; расхождение часов {lag:.1f} с")
            if lag > 5:
                print("   часы компьютера расходятся с биржей больше 5 с: включите автосинхронизацию времени")
            if snap.get("errors"):
                print(f"   не отдано: {list(snap['errors'])}")
        except Exception as e:
            ok = ok and name != a.exchange
            print(f"[НЕДОСТУПНО] {name}: {str(e)[:160]}")
    for name, url in (("Coin Metrics", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=PriceUSD&page_size=1"),
                      ("DefiLlama", "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1")):
        try:
            S._get_json(url)
            print(f"[OK] {name}")
        except Exception as e:
            print(f"[НЕДОСТУПНО] {name}: {str(e)[:160]}")
    free = __import__("shutil").disk_usage(Path(a.dir).resolve().parent if not Path(a.dir).exists() else a.dir).free / 1e9
    print(f"Свободно на диске: {free:.1f} ГБ (за 3 месяца данных набирается около 0.3–1 ГБ)")
    if not ok:
        print(f"\nБиржа {a.exchange} недоступна. Если okx выше OK, создайте счета с --exchange okx")
    return ok


def cmd_health(a):
    """Полнота данных: сколько циклов и снимков записано, где пропуски, сколько ошибок."""
    st = load_state(a)
    root = Path(a.dir) / "data_lake"
    start = pd.Timestamp(st["created"][:10])
    days = pd.date_range(start, today_utc() - pd.Timedelta(days=1))
    eq = pd.read_csv(root / "equity/equity.csv") if (root / "equity/equity.csv").exists() else pd.DataFrame(columns=["day", "catchup"])
    cyc = set(eq.day) if len(eq) else set()
    catch = set(eq[eq.catchup == True].day) if "catchup" in eq and len(eq) else set()
    snaps = {}
    for f in sorted((root / "market/snapshots").glob("*.jsonl")):
        n = sum(1 for _ in open(f, encoding="utf-8"))
        snaps[f.stem] = n / max(len(st["coins"]), 1)
    print(f"Тест с {start.date()}, монеты {st['coins']}, биржа {st['exchange']}, код {st.get('code_version')}")
    print(f"Дней с начала: {len(days)}; обработано циклом: {len(cyc)} (из них догнано после сна/выключения: {len(catch)})")
    miss = [str(d.date()) for d in days if str(d.date()) not in cyc]
    print(f"Дни без цикла: {len(miss)} {miss[-10:] if miss else ''}")
    if snaps:
        sv = pd.Series(snaps)
        print(f"Часовые снимки: в среднем {sv.mean():.1f} в день из 24, полных дней (≥20) {(sv >= 20).sum()} из {len(sv)}")
        print("Последние 7 дней (снимков в день): " + ", ".join(f"{k[5:]}={v:.0f}" for k, v in sv.tail(7).items()))
    err = Path(a.dir) / "logs/errors.log"
    if err.exists():
        lines = err.read_text(encoding="utf-8", errors="ignore").splitlines()
        print(f"Предупреждений и ошибок в логе: {sum(1 for l in lines if ' WARNING ' in l)} / {sum(1 for l in lines if ' ERROR ' in l)}")
        last_err = [l for l in lines if " ERROR " in l][-3:]
        for l in last_err:
            print("   " + l[:200])
    runs = root / "meta/runs.jsonl"
    if runs.exists():
        r = pd.read_json(runs, lines=True)
        print(f"Запусков: {len(r)}, с ошибкой: {(r.status != 'ok').sum()}, последний: {r.iloc[-1]._written} ({r.iloc[-1].cmd}, {r.iloc[-1].status})")


def cmd_export(a):
    """Один архив со всем для анализа: данные, состояние, бэкапы, логи, манифест с контрольными суммами."""
    import hashlib
    import zipfile
    base = Path(a.dir)
    st = load_state(a)
    files = [p for p in base.rglob("*") if p.is_file() and p.suffix not in (".tmp", ".zip") and "exports" not in p.parts]
    manifest = {"exported": now_utc().isoformat(), "code_version": CODE_VERSION, "schema_version": SCHEMA_VERSION,
                "test_started": st["created"], "coins": st["coins"], "exchange": st["exchange"], "capital": st["capital"],
                "last_cycle": st["last_cycle"], "files": []}
    for p in files:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        rows = None
        if p.suffix in (".csv", ".jsonl", ".log"):
            rows = sum(1 for _ in open(p, encoding="utf-8", errors="ignore"))
        manifest["files"].append({"path": str(p.relative_to(base)), "bytes": p.stat().st_size, "sha256": h, "lines": rows})
    out = base / "exports" / f"abtest_export_{now_utc():%Y%m%d_%H%M}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(base))
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for doc in ("DATA_SCHEMA.md", "ABTEST.md"):
            src = Path(__file__).parent / doc
            if src.exists():
                z.write(src, doc)
    print(f"Архив {out} ({out.stat().st_size / 1e6:.1f} МБ, файлов {len(files)}). Его и нужно передать для анализа.")



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "cycle", "snapshot", "loop", "report", "replay", "dataset", "doctor",
                                    "health", "export", "dashboard", "selftest", "streamtest", "version", "update"])
    ap.add_argument("--exchange", default="bybit")
    ap.add_argument("--coins", default="BTC,ETH")
    ap.add_argument("--capital", type=float, default=100)
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--dir", default="abtest")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    setup_logging(a.dir)
    fn = {"init": cmd_init, "cycle": cmd_cycle, "snapshot": cmd_snapshot, "loop": cmd_loop, "report": cmd_report,
          "replay": cmd_replay, "dataset": cmd_dataset, "doctor": cmd_doctor, "health": cmd_health,
          "export": cmd_export, "dashboard": cmd_dashboard, "selftest": cmd_selftest, "streamtest": cmd_streamtest,
          "version": lambda a: print(f"backinsight {CODE_VERSION}"), "update": auto_update}[a.cmd]
    if a.cmd == "version":
        return fn(a) or 0
    if a.cmd in ("report", "doctor", "health", "export", "replay", "dashboard", "selftest", "streamtest", "update"):
        return 1 if fn(a) is False else 0
    t0, status, err = time.time(), "ok", None
    try:
        fn(a)
    except SystemExit as e:
        status, err = ("ok" if not e.code else "stopped"), str(e.code) if e.code else None
        if e.code:
            log(str(e.code), "warning")
    except Exception as e:
        import traceback
        status, err = "error", traceback.format_exc()
        log(f"ошибка {a.cmd}: {e}\n{err}", "error")
        raise
    finally:
        if Path(a.dir).exists():
            Recorder(Path(a.dir) / "data_lake").append_jsonl("meta/runs.jsonl", {
                "cmd": a.cmd, "status": status, "seconds": round(time.time() - t0, 1), "error": err,
                "python": sys.version.split()[0], "platform": sys.platform})


if __name__ == "__main__":
    sys.exit(main())
