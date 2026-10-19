"""
stock_spotlight.py - Flask blueprint: daily rotating Nifty 50 fundamentals snapshot.

Endpoint: GET /api/stock/spotlight            (stock picked by date rotation)
          GET /api/stock/spotlight?symbol=TCS (specific stock, for testing)
Register in your main app:
    from stock_spotlight import spotlight_bp
    app.register_blueprint(spotlight_bp)

The stock is chosen by a fixed rotation (not by performance), so the series is
not a set of "picks" or recommendations.
"""
import time
from datetime import datetime, timezone, timedelta

import yfinance as yf
from flask import Blueprint, jsonify, request

spotlight_bp = Blueprint("stock_spotlight", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_TTL = 6 * 3600
_cache = {}  # symbol -> (t, data)

# Verify this list occasionally - Nifty 50 constituents change twice a year.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY", "ITC", "JIOFIN",
    "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUMER", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO",
]


def _r(v, n=2):
    try:
        return None if v is None else round(float(v), n)
    except Exception:
        return None


def _pct(v):
    x = _r(v, 4)
    return None if x is None else round(x * 100, 2)


def _fetch(symbol):
    info = yf.Ticker(f"{symbol}.NS").info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price or not info.get("longName"):
        return None
    mcap = info.get("marketCap")
    summary = (info.get("longBusinessSummary") or "")[:600]
    return {
        "symbol": symbol,
        "name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "business_summary": summary,
        "price": _r(price),
        "market_cap_cr": _r(mcap / 1e7, 0) if mcap else None,
        "pe_trailing": _r(info.get("trailingPE")),
        "pe_forward": _r(info.get("forwardPE")),
        "price_to_book": _r(info.get("priceToBook")),
        "roe_pct": _pct(info.get("returnOnEquity")),
        "net_margin_pct": _pct(info.get("profitMargins")),
        "operating_margin_pct": _pct(info.get("operatingMargins")),
        "revenue_growth_pct": _pct(info.get("revenueGrowth")),
        "earnings_growth_pct": _pct(info.get("earningsGrowth")),
        "debt_to_equity": _r(info.get("debtToEquity")),
        "week52_high": _r(info.get("fiftyTwoWeekHigh")),
        "week52_low": _r(info.get("fiftyTwoWeekLow")),
    }


def _pick_and_build(forced=None):
    if forced:
        d = _fetch(forced.upper())
        if not d:
            raise RuntimeError(f"No data for {forced}")
        return d
    base = datetime.now(IST).date().toordinal()
    # Try up to 3 consecutive stocks in the rotation in case one has no data
    for k in range(3):
        sym = NIFTY50[(base + k) % len(NIFTY50)]
        d = _fetch(sym)
        if d:
            return d
    raise RuntimeError("No usable data for today's rotation")


@spotlight_bp.route("/api/stock/spotlight")
def spotlight():
    forced = request.args.get("symbol")
    key = forced.upper() if forced else f"rot-{datetime.now(IST).date().isoformat()}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return jsonify(hit[1])
    try:
        data = _pick_and_build(forced)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    data["as_of"] = datetime.now(IST).date().isoformat()
    data["generated_at"] = datetime.now(IST).isoformat()
    _cache[key] = (time.time(), data)
    return jsonify(data)
