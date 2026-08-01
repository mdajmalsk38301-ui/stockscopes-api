"""
StockScopes API — Candle Data Endpoint
========================================
Serves OHLCV candle data for the StockScopes native chart component
(Lightweight Charts on the WordPress side), so the site doesn't depend
on TradingView's hosted widgets for basic price charts.

Data source: yfinance (free, unofficial Yahoo Finance wrapper). No API
key needed. Covers NSE stocks via the ".NS" suffix (RELIANCE -> RELIANCE.NS)
and BSE via ".BO" (RELIANCE -> RELIANCE.BO). Indices use "^" prefixes
(e.g. "^NSEI" for Nifty 50, "^NSEBANK" for Bank Nifty) — see SYMBOL_MAP
below for the common ones so callers can keep using "NIFTY" / "BANKNIFTY"
style names instead of memorising Yahoo's tickers.

DROP-IN INSTRUCTIONS
---------------------
1. pip install yfinance flask-cors  (add both to requirements.txt)
2. Copy this file into your existing Flask project, e.g. as `candles.py`
   next to your main app.py.
3. In your main app.py, register the blueprint:

       from candles import candles_bp
       app.register_blueprint(candles_bp)

   (If you're not using blueprints elsewhere, that's fine — this file is
   self-contained and doesn't require you to restructure anything else.)
4. Make sure CORS is enabled for your WordPress origin. If you don't
   already have flask-cors set up:

       from flask_cors import CORS
       CORS(app, resources={r"/api/candles": {"origins": "https://stockscopes.in"}})

   If you already use flask-cors elsewhere in app.py, just make sure the
   /api/candles route is covered by an allowed origin — don't add a
   second conflicting CORS() call.

ENDPOINT
--------
GET /api/candles?symbol=RELIANCE&range=6mo&interval=1d

Query params:
  symbol    required. Plain NSE ticker (e.g. "RELIANCE"), or a full
            Yahoo ticker if you want to be explicit ("RELIANCE.NS",
            "^NSEI"), or one of the SYMBOL_MAP aliases below ("NIFTY").
  range     optional, default "6mo". One of: 1mo, 3mo, 6mo, 1y, 2y, 5y, max.
  interval  optional, default "1d". One of: 1d, 1wk, 1mo.
            (Intraday intervals like 1m/5m are intentionally not exposed
            here — Yahoo only keeps a few days of intraday history and
            it adds a lot of edge cases for very little benefit on a
            site that isn't doing real-time trading.)

Response:
  200 {
        "symbol": "RELIANCE.NS",
        "interval": "1d",
        "candles": [
          {"time": "2026-01-02", "open": 1234.5, "high": 1250.0,
           "low": 1220.0, "close": 1245.0, "volume": 3456789},
          ...
        ]
      }
  400 { "error": "..." }   — bad/missing params
  502 { "error": "..." }   — upstream (Yahoo) fetch failed
"""

import time
import threading
from flask import Blueprint, request, jsonify

try:
    import yfinance as yf
except ImportError:
    yf = None  # blueprint will return a clear 500 instead of crashing on import

candles_bp = Blueprint("candles", __name__)

# Common index aliases so the frontend can pass friendly names.
# Extend this as you need more indices/ETFs.
SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "NIFTYIT": "^CNXIT",
    "NIFTYPHARMA": "^CNXPHARMA",
    "NIFTYMIDCAP50": "^NSEMDCP50",
}

ALLOWED_RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
ALLOWED_INTERVALS = {"1d", "1wk", "1mo"}

# Tiny in-memory cache so repeated page loads for the same symbol/range
# within a few minutes don't all hit Yahoo separately. Good enough for a
# single Render instance; swap for Redis/Flask-Caching if you scale out
# to multiple workers later.
_CACHE = {}
_CACHE_TTL_SECONDS = 5 * 60
_cache_lock = threading.Lock()


def _resolve_symbol(raw_symbol):
    """Turn a plain/alias symbol into the Yahoo Finance ticker to query."""
    s = raw_symbol.strip().upper()

    # Already looks like a full Yahoo ticker (has a suffix or index prefix)
    if s.startswith("^") or "." in s:
        return s

    if s in SYMBOL_MAP:
        return SYMBOL_MAP[s]

    # Default: treat as an NSE equity
    return f"{s}.NS"


def _cache_get(key):
    with _cache_lock:
        entry = _CACHE.get(key)
        if not entry:
            return None
        payload, saved_at = entry
        if time.time() - saved_at > _CACHE_TTL_SECONDS:
            del _CACHE[key]
            return None
        return payload


def _cache_set(key, payload):
    with _cache_lock:
        _CACHE[key] = (payload, time.time())


@candles_bp.route("/api/candles", methods=["GET"])
def get_candles():
    if yf is None:
        return jsonify({"error": "yfinance is not installed on the server"}), 500

    raw_symbol = request.args.get("symbol", "").strip()
    if not raw_symbol:
        return jsonify({"error": "symbol query param is required"}), 400

    rng = request.args.get("range", "6mo")
    if rng not in ALLOWED_RANGES:
        return jsonify({"error": f"range must be one of {sorted(ALLOWED_RANGES)}"}), 400

    interval = request.args.get("interval", "1d")
    if interval not in ALLOWED_INTERVALS:
        return jsonify({"error": f"interval must be one of {sorted(ALLOWED_INTERVALS)}"}), 400

    yahoo_symbol = _resolve_symbol(raw_symbol)
    cache_key = f"{yahoo_symbol}|{rng}|{interval}"

    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        ticker = yf.Ticker(yahoo_symbol)
        hist = ticker.history(period=rng, interval=interval, auto_adjust=False)
    except Exception as exc:  # yfinance/Yahoo hiccups shouldn't 500 the whole site
        return jsonify({"error": f"failed to fetch data for {yahoo_symbol}: {exc}"}), 502

    if hist is None or hist.empty:
        return jsonify({"error": f"no data returned for {yahoo_symbol}"}), 502

    candles = []
    date_fmt = "%Y-%m-%d" if interval != "1mo" else "%Y-%m-%d"
    for ts, row in hist.iterrows():
        # Skip rows with missing OHLC (occasional holidays/gaps in the feed)
        if row[["Open", "High", "Low", "Close"]].isnull().any():
            continue
        candles.append({
            "time":   ts.strftime(date_fmt),
            "open":   round(float(row["Open"]), 2),
            "high":   round(float(row["High"]), 2),
            "low":    round(float(row["Low"]), 2),
            "close":  round(float(row["Close"]), 2),
            "volume": int(row["Volume"]) if not row["Volume"] != row["Volume"] else 0,  # NaN-safe
        })

    payload = {
        "symbol": yahoo_symbol,
        "interval": interval,
        "candles": candles,
    }
    _cache_set(cache_key, payload)
    return jsonify(payload)
