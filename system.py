"""Итоговая торговая система: переключатель режимов по рынку BTC поверх слоёв.

Слои для каждой монеты (позиция 0..1 от её доли портфеля):
  тренд R3:      BTC за mom_days > 0 и RSI(rsi_len) монеты > rsi_th
  волатильность: min(1, vol_target / годовая волатильность монеты за vol_days)
  режим цикла:   MVRV BTC ниже mvrv_pct-го перцентиля своей истории
  ончейн:        размер ×(0.5 + 0.5·доля «за» из hashprice, Hash Ribbons, роста адресов с балансом, роста стейблкоинов)
Переключатель по рынку BTC: бык = BTC выше SMA(sma_regime) и SMA выше, чем sma_slope дней назад.
  режим A: в быке чистый тренд R3, иначе все 4 слоя
  режим B: в быке тренд × волатильность, иначе все 4 слоя
Все сигналы считаются по закрытым дневным свечам (UTC) и исполняются по закрытию дня.
"""
import json
import math
import time
import urllib.request
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Params:
    mode: str = "A"
    mom_days: int = 30
    rsi_len: int = 14
    rsi_th: float = 50
    vol_target: float = 0.5
    vol_days: int = 30
    sma_regime: int = 200
    sma_slope: int = 30
    mvrv_pct: float = 0.9


# ---------- индикаторы ----------

def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def zz(s, n=365):
    return (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()


# ---------- рыночные слои (ончейн BTC) ----------

_SSL = None


def _ssl_ctx():
    """Системные корни + certifi. На Windows Python видит не все корни (Windows докачивает их по требованию),
    без certifi часть сайтов даёт CERTIFICATE_VERIFY_FAILED."""
    global _SSL
    if _SSL is None:
        import ssl
        _SSL = ssl.create_default_context()
        try:
            import certifi
            _SSL.load_verify_locations(certifi.where())
        except Exception:
            pass
    return _SSL


def _get_json(url):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-bot"})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def fetch_onchain(start="2014-01-01"):
    """Coin Metrics community (бесплатно, без ключа) + DefiLlama стейблкоины."""
    metrics = "CapMVRVCur,AdrBalCnt,HashRate,IssTotUSD"
    url = (f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics={metrics}"
           f"&frequency=1d&start_time={start}&page_size=10000")
    rows = []
    while url:
        d = _get_json(url)
        rows += d["data"]
        url = d.get("next_page_url")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.time).dt.tz_localize(None).dt.floor("D")
    df = df.drop(columns=["asset", "time"] + [c for c in df if "-status" in c]).apply(pd.to_numeric, errors="coerce")
    st = _get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    s = pd.Series({pd.Timestamp(int(x["date"]), unit="s").floor("D"): x["totalCirculatingUSD"].get("peggedUSD", np.nan)
                   for x in st})
    df["stables"] = s.reindex(df.index)
    return df


def market_layers(oc, p: Params):
    """Режим цикла и доля ончейн-подтверждений; сдвиг на 1 день, т.к. данные публикуются после закрытия."""
    lg = lambda c: np.log(oc[c].where(oc[c] > 0))
    feats = {
        "addr": lg("AdrBalCnt").diff(30),
        "hashprice": zz(np.log((oc.IssTotUSD / oc.HashRate).rolling(7).mean())),
        "stables": lg("stables").diff(30),
        "ribbons": np.log(oc.HashRate.rolling(30).mean() / oc.HashRate.rolling(60).mean()),
    }
    votes = pd.concat([(f > f.rolling(365, min_periods=180).median()).where(f.notna()) for f in feats.values()], axis=1)
    conf = votes.astype(float).mean(axis=1, skipna=True)
    mv = oc.CapMVRVCur
    pct = mv.expanding(365).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    regime = (pct < p.mvrv_pct).astype(float).where(pct.notna())
    out = pd.DataFrame({"regime": regime, "conf": conf, "mvrv": mv, "mvrv_pct": pct}).shift(1)
    out.index = out.index.normalize()
    return out


# ---------- позиции ----------

def coin_position(c, btc, layers, p: Params, detail=False):
    """Целевая позиция монеты 0..1 (доля от выделенного ей капитала) на каждый день."""
    btc = btc.reindex(c.index).ffill()
    trend = ((np.log(btc).diff(p.mom_days) > 0) & (rsi(c, p.rsi_len) > p.rsi_th)).astype(float)
    vol = np.log(c).diff().rolling(p.vol_days).std() * math.sqrt(365)
    vt = (p.vol_target / vol).clip(upper=1)
    L = layers.reindex(c.index).ffill()
    sma = btc.rolling(p.sma_regime).mean()
    bull = (btc > sma) & (sma > sma.shift(p.sma_slope))
    four = L.regime * trend * vt * (0.5 + 0.5 * L.conf)
    aggressive = trend if p.mode == "A" else trend * vt
    pos = aggressive.where(bull, four)
    if detail:
        return pd.DataFrame({"pos": pos, "trend": trend, "vol_size": vt, "bull": bull, "regime": L.regime,
                             "conf": L.conf, "rsi": rsi(c, p.rsi_len), "btc_mom": np.log(btc).diff(p.mom_days)})
    return pos


def targets(closes: dict, btc, layers, p: Params):
    """Целевые веса портфеля: каждой монете 1/N капитала × её позиция. Остаток в USDT."""
    n = len(closes)
    return pd.DataFrame({k: coin_position(c, btc, layers, p) / n for k, c in closes.items()})


def params_dict(p: Params):
    return asdict(p)
