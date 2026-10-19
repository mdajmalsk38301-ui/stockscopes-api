"""
market_wrap.py - Flask blueprint for the evening market wrap.

Endpoint: GET /api/market/wrap
Register in your main app:
    from market_wrap import market_wrap_bp
    app.register_blueprint(market_wrap_bp)
"""
import time
from datetime import datetime, timezone, timedelta

import yfinance as yf
from flask import Blueprint, jsonify

market_wrap_bp = Blueprint("market_wrap", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_TTL = 600  # seconds
_cache = {"t": 0, "data": None}

INDICES = {
    "Nifty 50": "^NSEI",
    "Sensex": "^BSESN",
    "Bank Nifty": "^NSEBANK",
}

SECTORS = {
    "IT": "^CNXIT",
    "Pharma": "^CNXPHARMA",
    "Auto": "^CNXAUTO",
    "FMCG": "^CNXFMCG",
    "Metal": "^CNXMETAL",
    "Energy": "^CNXENERGY",
    "Realty": "^CNXREALTY",
    "PSU Bank": "^CNXPSUBANK",
}

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


def _changes(symbols):
    """Return {symbol: {last, prev, change, pct, date}} using last two daily closes."""
    out = {}
    if not symbols:
        return out
    df = yf.download(
        symbols, period="10d", interval="1d", group_by="ticker",
        progress=False, threads=True, auto_adjust=False,
    )
    for s in symbols:
        try:
            close = df[s]["Close"].dropna()
            if len(close) < 2:
                continue
            last, prev = float(close.iloc[-1]), float(close.iloc[-2])
            out[s] = {
                "last": round(last, 2),
                "prev": round(prev, 2),
                "change": round(last - prev, 2),
                "pct": round((last - prev) / prev * 100, 2),
                "date": close.index[-1].strftime("%Y-%m-%d"),
            }
        except Exception:
            continue
    return out


def _build():
    idx = _changes(list(INDICES.values()))
    sec = _changes(list(SECTORS.values()))
    stocks = _changes([f"{s}.NS" for s in NIFTY50])

    indices = [
        {"name": n, **idx[sym]} for n, sym in INDICES.items() if sym in idx
    ]
    sectors = sorted(
        [{"name": n, "pct": sec[sym]["pct"]} for n, sym in SECTORS.items() if sym in sec],
        key=lambda x: x["pct"], reverse=True,
    )
    ranked = sorted(
        [{"symbol": s.replace(".NS", ""), "price": v["last"], "pct": v["pct"]}
         for s, v in stocks.items()],
        key=lambda x: x["pct"], reverse=True,
    )
    advances = sum(1 for r in ranked if r["pct"] > 0)
    declines = sum(1 for r in ranked if r["pct"] < 0)

    market_date = indices[0]["date"] if indices else None
    today = datetime.now(IST).strftime("%Y-%m-%d")

    return {
        "market_date": market_date,
        "is_trading_day": market_date == today,
        "indices": indices,
        "sectors": sectors,
        "top_gainers": ranked[:5],
        "top_losers": list(reversed(ranked[-5:])),
        "nifty50_breadth": {"advances": advances, "declines": declines},
        "generated_at": datetime.now(IST).isoformat(),
    }


@market_wrap_bp.route("/api/market/wrap")
def market_wrap():
    now = time.time()
    if _cache["data"] and now - _cache["t"] < CACHE_TTL:
        return jsonify(_cache["data"])
    try:
        data = _build()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    if not data["indices"]:
        return jsonify({"error": "no index data"}), 502
    _cache.update(t=now, data=data)
    return jsonify(data)
