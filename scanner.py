"""
scanner.py - stock scanner endpoint for the StockScopes API.

Endpoint: GET /api/scanner
Returns one row per stock with the numbers a scanner needs (price, % change,
volume spike, RSI, moving averages, 52-week range, 1m/3m returns, trend).
The website (scanner page) does the filtering, so this file only has to
calculate and cache.

Add to your repo:
  1. Put this file next to app.py.
  2. Replace register_all.py with the one in this folder (or add scanner_bp to it).
  3. Commit. Render redeploys by itself.

To scan more stocks, add lines to extra_universe.py (NSE symbols without .NS).
The download runs in batches and refreshes in the background, so a few hundred stocks are fine.
"""
import os
import time
import threading
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf
from flask import Blueprint, jsonify

from cache_util import cache_get, cache_set

try:
    from market_wrap import NIFTY50
except Exception:  # keeps this file working on its own
    NIFTY50 = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC", "LT", "AXISBANK", "BHARTIARTL"]

class _RegisterOnce(Blueprint):
    """Registering this twice (for example once in app.py and once in register_all.py) is harmless."""
    def register(self, app, options):
        if self.name in app.blueprints:
            return
        super().register(app, options)


scanner_bp = _RegisterOnce("scanner", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_SECONDS = 10 * 60

# BSE Sensex 30 (NSE symbols), as listed on 24 Oct 2026. BSE reviews the index twice a year (June and December);
# edit this list when it changes.
SENSEX = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "TCS", "SBIN", "BAJFINANCE", "LT", "INFY", "KOTAKBANK",
    "HINDUNILVR", "SUNPHARMA", "ADANIPORTS", "HCLTECH", "TITAN", "AXISBANK", "M&M", "MARUTI", "ULTRACEMCO", "ITC",
    "ETERNAL", "NTPC", "BAJAJFINSV", "BEL", "POWERGRID", "ASIANPAINT", "TATASTEEL", "INDIGO", "TECHM", "TRENT",
]

# Company names shown on the website. A symbol that is not listed here is shown as its symbol.
NAMES = {
    "RELIANCE": "Reliance Industries",
    "TCS": "Tata Consultancy Services",
    "HDFCBANK": "HDFC Bank",
    "INFY": "Infosys",
    "ICICIBANK": "ICICI Bank",
    "BHARTIARTL": "Bharti Airtel",
    "SBIN": "State Bank of India",
    "ITC": "ITC",
    "LT": "Larsen & Toubro",
    "HINDUNILVR": "Hindustan Unilever",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "AXISBANK": "Axis Bank",
    "BAJFINANCE": "Bajaj Finance",
    "BAJAJFINSV": "Bajaj Finserv",
    "MARUTI": "Maruti Suzuki",
    "M&M": "Mahindra & Mahindra",
    "SUNPHARMA": "Sun Pharma",
    "TITAN": "Titan Company",
    "ULTRACEMCO": "UltraTech Cement",
    "NTPC": "NTPC",
    "POWERGRID": "Power Grid Corporation",
    "ASIANPAINT": "Asian Paints",
    "HCLTECH": "HCL Technologies",
    "TECHM": "Tech Mahindra",
    "WIPRO": "Wipro",
    "TATASTEEL": "Tata Steel",
    "ADANIENT": "Adani Enterprises",
    "ADANIPORTS": "Adani Ports & SEZ",
    "APOLLOHOSP": "Apollo Hospitals",
    "BAJAJ-AUTO": "Bajaj Auto",
    "BEL": "Bharat Electronics",
    "CIPLA": "Cipla",
    "COALINDIA": "Coal India",
    "DRREDDY": "Dr Reddy's Laboratories",
    "EICHERMOT": "Eicher Motors",
    "ETERNAL": "Eternal (Zomato)",
    "GRASIM": "Grasim Industries",
    "HDFCLIFE": "HDFC Life Insurance",
    "HEROMOTOCO": "Hero MotoCorp",
    "HINDALCO": "Hindalco Industries",
    "INDUSINDBK": "IndusInd Bank",
    "JIOFIN": "Jio Financial Services",
    "JSWSTEEL": "JSW Steel",
    "NESTLEIND": "Nestle India",
    "ONGC": "ONGC",
    "SBILIFE": "SBI Life Insurance",
    "SHRIRAMFIN": "Shriram Finance",
    "TATACONSUMER": "Tata Consumer Products",
    "TRENT": "Trent",
    "INDIGO": "InterGlobe Aviation (IndiGo)"
}

# More stocks live in extra_universe.py (about 140 beyond Nifty 50 and Sensex).
try:
    from extra_universe import EXTRA_STOCKS
except Exception:
    EXTRA_STOCKS = {}
EXTRA = list(EXTRA_STOCKS)
for _s, (_n, _sec) in EXTRA_STOCKS.items():
    NAMES.setdefault(_s, _n)

_lock = threading.Lock()
_stale = {"data": None}
_log = []                                             # last few progress lines, shown at /api/scanner-status


def _note(msg):
    print("scanner:", msg, flush=True)
    _log.append(datetime.now(timezone.utc).strftime("%H:%M:%S ") + msg)
    del _log[:-25]


# Yahoo uses a shorter code for a few companies. The log showed "TATACONSUMER.NS: No data found".
YAHOO_CODE = {"TATACONSUMER": "TATACONSUM", "INFOEDGE": "NAUKRI"}


def universe():
    seen, out = set(), []
    for s in list(NIFTY50) + list(SENSEX) + list(EXTRA):
        s = str(s).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def rsi_wilder(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    last_gain, last_loss = gain.iloc[-1], loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - 100 / (1 + rs))


def scan_symbol(symbol, df):
    """df has Close, High, Low, Volume columns (daily). Returns a dict or None."""
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return None
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    if prev == 0:
        return None
    pct = (last - prev) / prev * 100

    v_today = float(vol.iloc[-1]) if pd.notna(vol.iloc[-1]) else 0.0
    v_base = vol.iloc[-21:-1].mean() if len(vol) >= 21 else vol.iloc[:-1].mean()
    vol_ratio = (v_today / float(v_base)) if pd.notna(v_base) and v_base > 0 else None

    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    y_high, y_low = float(high.tail(252).max()), float(low.tail(252).min())

    def ret(n):
        return round((last / float(close.iloc[-1 - n]) - 1) * 100, 2) if len(close) > n else None

    if sma50 and sma200 and last > sma50 > sma200:
        trend = "Bullish"
    elif sma50 and sma200 and last < sma50 < sma200:
        trend = "Bearish"
    elif sma50 and sma200:
        trend = "Neutral"
    else:
        trend = "N/A"

    rsi = rsi_wilder(close)
    return {
        "symbol": symbol,
        "price": round(last, 2),
        "pct": round(pct, 2),
        "volume": int(v_today),
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "rsi": round(rsi, 1) if rsi is not None else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "high52": round(y_high, 2),
        "low52": round(y_low, 2),
        "from_high": round((last / y_high - 1) * 100, 2) if y_high else None,   # 0 or negative
        "from_low": round((last / y_low - 1) * 100, 2) if y_low else None,      # 0 or positive
        "ret_1m": ret(21),
        "ret_3m": ret(63),
        "trend": trend,
        "date": df.index[-1].strftime("%Y-%m-%d"),
    }


def _scan_batch(syms):
    tickers = [YAHOO_CODE.get(s, s) + ".NS" for s in syms]
    rows = []
    raw = None
    for attempt in (1, 2):                            # one quiet retry: Yahoo sometimes throttles for a few seconds
        try:
            raw = yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                              progress=False, threads=True, auto_adjust=False, timeout=30)
            if raw is not None and len(raw) > 0:
                break
            _note("batch empty on try %d" % attempt)
        except Exception as e:
            _note("batch failed on try %d: %s" % (attempt, str(e)[:120]))
            raw = None
        time.sleep(6)
    if raw is None or len(raw) == 0:
        return rows
    for s, t in zip(syms, tickers):
        try:
            row = scan_symbol(s, raw[t][["Close", "High", "Low", "Volume"]])
            if row:
                row["name"] = NAMES.get(s, s)
                row["sensex"] = s in SENSEX
                row["nifty50"] = s in NIFTY50
                rows.append(row)
        except Exception:
            continue
    return rows


def _publish(rows, partial):
    """Make what we have so far available straight away, so the website never waits for the whole list."""
    data = {
        "generated_at": datetime.now(IST).isoformat(),
        "universe": "Nifty 50 + Sensex" + (" + extra" if EXTRA else ""),
        "count": len(rows),
        "partial": partial,
        "rows": list(rows),
    }
    _stale["data"] = data
    cache_set("scanner", data, 90 if partial else CACHE_SECONDS)
    return data


def build():
    syms = universe()
    rows = []
    t0 = time.time()
    for i in range(0, len(syms), 25):                # small batches: results show up early and one slow batch cannot hold up the rest
        got = _scan_batch(syms[i:i + 25])
        rows.extend(got)
        _note("batch %d-%d gave %d rows (%d so far, %ds)" % (i, i + 25, len(got), len(rows), time.time() - t0))
        if rows and i + 25 < len(syms):
            _publish(rows, True)
        time.sleep(1)
    return _publish(rows, False) if rows else {"rows": []}


_busy = {"on": False}


def _refresh():
    try:
        build()
    except Exception as e:
        print("scanner: build failed:", e, flush=True)
    finally:
        _busy["on"] = False


def _refresh_in_background():
    with _lock:
        if _busy["on"]:
            return
        _busy["on"] = True
    threading.Thread(target=_refresh, daemon=True).start()


if not os.environ.get("NO_WARM"):
    _refresh_in_background()                          # warm up when the app starts, so the first visitor is not kept waiting


@scanner_bp.route("/api/scanner-status")
def scanner_status():
    d = _stale["data"]
    return jsonify({"busy": _busy["on"], "rows_now": (d or {}).get("count", 0), "partial": (d or {}).get("partial"), "log": list(_log)})


@scanner_bp.route("/api/scanner")
def scanner():
    saved = cache_get("scanner")
    if saved is not None:
        return jsonify(saved)
    if _stale["data"]:                                # serve the last good copy now, rebuild quietly
        _refresh_in_background()
        return jsonify(_stale["data"])
    if _busy["on"]:
        for _ in range(40):                           # wait up to 20 seconds for the first batch
            if _stale["data"]:
                return jsonify(_stale["data"])
            time.sleep(0.5)
    if _busy["on"]:                                   # first start: wait a little for the first batch instead of failing
        for _ in range(40):
            if _stale["data"]:
                return jsonify(_stale["data"])
            time.sleep(0.5)
        return jsonify({"building": True, "message": "Collecting prices. Try again in a minute."}), 503
    with _lock:
        _busy["on"] = True
    _refresh()
    if _stale["data"]:
        return jsonify(_stale["data"])
    return jsonify({"error": "no data from the market data source"}), 502
