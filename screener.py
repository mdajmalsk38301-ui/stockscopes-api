"""
screener.py - fundamentals screener endpoint for the StockScopes API.

Endpoint: GET /api/screener
One row per stock with valuation and quality numbers (market cap, P/E, P/B,
ROE, debt to equity, dividend yield, margins, growth). The website page does
the filtering; this file collects the numbers and remembers them.

Collecting about 50 stocks from Yahoo takes 1 to 2 minutes, so it runs in the
background. While it runs, /api/screener answers 503 {"building": true}. Once
ready, the answer is kept for 12 hours (and saved in a file, so a restart of the
app does not start from zero).

Add to your repo: this file next to app.py, plus the updated register_all.py.
To scan more stocks, add NSE symbols to EXTRA (keep it under about 100).
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import yfinance as yf
from flask import Blueprint, jsonify

try:
    from market_wrap import NIFTY50
except Exception:
    NIFTY50 = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC", "LT", "AXISBANK", "BHARTIARTL"]

screener_bp = Blueprint("screener", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
FRESH_SECONDS = 12 * 3600
CACHE_FILE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "stockscopes_screener.json")
EXTRA = []

# BSE Sensex 30 (NSE symbols), as listed on 24 Oct 2026. BSE reviews the index twice a year (June and December);
# edit this list when it changes.
SENSEX = [
    "RELIANCE", "BHARTIARTL", "HDFCBANK", "ICICIBANK", "TCS", "SBIN", "BAJFINANCE", "LT", "INFY", "KOTAKBANK",
    "HINDUNILVR", "SUNPHARMA", "ADANIPORTS", "HCLTECH", "TITAN", "AXISBANK", "M&M", "MARUTI", "ULTRACEMCO", "ITC",
    "ETERNAL", "NTPC", "BAJAJFINSV", "BEL", "POWERGRID", "ASIANPAINT", "TATASTEEL", "INDIGO", "TECHM", "TRENT",
]

_state = {"data": None, "t": 0, "building": False}
_lock = threading.Lock()


def universe():
    seen, out = set(), []
    for s in list(NIFTY50) + list(SENSEX) + list(EXTRA):
        s = str(s).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _pct(v):
    """Fraction (0.153) to percent (15.3)."""
    f = _num(v)
    return None if f is None else round(f * 100, 2)


def _div_yield(v):
    """yfinance has returned this as a fraction and as a percent in different versions.
    Real yields are well under 100%, so anything under 1 is treated as a fraction."""
    f = _num(v)
    if f is None:
        return None
    return round(f * 100, 2) if f < 1 else round(f, 2)


def parse_info(symbol, info):
    """Turn a yfinance .info dict into one screener row, or None if there is no usable price."""
    if not info:
        return None
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    if not price:
        return None
    mcap = _num(info.get("marketCap"))
    de = _num(info.get("debtToEquity"))                  # Yahoo gives this as a percent (45 = 0.45)
    pe = _num(info.get("trailingPE"))
    return {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "sector": info.get("sector") or "Other",
        "price": round(price, 2),
        "market_cap_cr": round(mcap / 1e7) if mcap else None,       # rupees crore
        "pe": round(pe, 1) if pe is not None and pe > 0 else None,  # negative P/E means losses: left blank
        "pb": round(_num(info.get("priceToBook")), 2) if _num(info.get("priceToBook")) is not None else None,
        "roe": _pct(info.get("returnOnEquity")),
        "debt_equity": round(de / 100, 2) if de is not None else None,
        "div_yield": _div_yield(info.get("dividendYield")),
        "profit_margin": _pct(info.get("profitMargins")),
        "rev_growth": _pct(info.get("revenueGrowth")),
        "eps": round(_num(info.get("trailingEps")), 2) if _num(info.get("trailingEps")) is not None else None,
        "beta": round(_num(info.get("beta")), 2) if _num(info.get("beta")) is not None else None,
    }


def _one(symbol):
    for attempt in range(2):
        try:
            row = parse_info(symbol, yf.Ticker(symbol + ".NS").info)
            if row:
                row["sensex"] = symbol in SENSEX
                row["nifty50"] = symbol in NIFTY50
                return row
        except Exception:
            time.sleep(1.5)
    return None


def build():
    syms = universe()
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = [r for r in ex.map(_one, syms) if r]
    return {
        "generated_at": datetime.now(IST).isoformat(),
        "universe": "Nifty 50 + Sensex" + (" + extra" if EXTRA else ""),
        "count": len(rows),
        "rows": rows,
    }


def _save(data):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"t": time.time(), "data": data}, f)
    except Exception:
        pass


def _load():
    try:
        with open(CACHE_FILE) as f:
            j = json.load(f)
        return j["data"], float(j["t"])
    except Exception:
        return None, 0


def _worker():
    try:
        data = build()
        if data["rows"]:
            with _lock:
                _state.update(data=data, t=time.time())
            _save(data)
    except Exception:
        pass
    finally:
        with _lock:
            _state["building"] = False


def _start_if_needed():
    with _lock:
        if _state["building"]:
            return
        _state["building"] = True
    threading.Thread(target=_worker, daemon=True).start()


@screener_bp.route("/api/screener")
def screener():
    with _lock:
        data, t = _state["data"], _state["t"]
    if data is None:
        data, t = _load()
        if data is not None:
            with _lock:
                _state.update(data=data, t=t)
    if data is None:
        _start_if_needed()
        return jsonify({"building": True, "message": "Collecting company numbers. Try again in 2 minutes."}), 503
    if time.time() - t > FRESH_SECONDS:
        _start_if_needed()            # refresh in the background, serve the saved copy meanwhile
    return jsonify(data)
