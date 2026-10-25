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

To scan more stocks, add symbols to EXTRA below (NSE symbols without .NS).
Keep the list under about 150 symbols so Render's free plan does not time out.
"""
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

scanner_bp = Blueprint("scanner", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_SECONDS = 10 * 60

# BSE Sensex 30 (NSE symbols), as listed on 24 Oct 2026. BSE reviews the index twice a year (June and December);
# edit this list when it changes.
SENSEX = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "TCS", "SBIN", "BAJFINANCE", "LT", "INFY", "KOTAKBANK",
    "HINDUNILVR", "SUNPHARMA", "ADANIPORTS", "HCLTECH", "TITAN", "AXISBANK", "M&M", "MARUTI", "ULTRACEMCO", "ITC",
    "ETERNAL", "NTPC", "BAJAJFINSV", "BEL", "POWERGRID", "ASIANPAINT", "TATASTEEL", "INDIGO", "TECHM", "TRENT",
]

# Add more NSE symbols here, for example ["ZOMATO", "IRCTC", "DMART"]
EXTRA = []

_lock = threading.Lock()
_stale = {"data": None}


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


def build():
    syms = universe()
    tickers = [s + ".NS" for s in syms]
    raw = yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                      progress=False, threads=True, auto_adjust=False, timeout=25)
    rows = []
    for s, t in zip(syms, tickers):
        try:
            row = scan_symbol(s, raw[t][["Close", "High", "Low", "Volume"]])
            if row:
                row["sensex"] = s in SENSEX
                row["nifty50"] = s in NIFTY50
                rows.append(row)
        except Exception:
            continue
    return {
        "generated_at": datetime.now(IST).isoformat(),
        "universe": "Nifty 50 + Sensex" + (" + extra" if EXTRA else ""),
        "count": len(rows),
        "rows": rows,
    }


@scanner_bp.route("/api/scanner")
def scanner():
    saved = cache_get("scanner")
    if saved is not None:
        return jsonify(saved)
    with _lock:                       # one rebuild at a time
        saved = cache_get("scanner")
        if saved is not None:
            return jsonify(saved)
        try:
            data = build()
        except Exception as e:
            if _stale["data"]:
                return jsonify(_stale["data"])
            return jsonify({"error": str(e)}), 502
        if not data["rows"]:
            if _stale["data"]:
                return jsonify(_stale["data"])
            return jsonify({"error": "no data from the market data source"}), 502
        _stale["data"] = data
        cache_set("scanner", data, CACHE_SECONDS)
        return jsonify(data)
