"""
fii_dii.py - Flask blueprint for daily FII/DII cash market flows (from NSE).

Endpoint: GET /api/market/fii-dii
Register in your main app:
    from fii_dii import fii_dii_bp
    app.register_blueprint(fii_dii_bp)

Needs: requests (already installed with yfinance).
"""
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Blueprint, jsonify

fii_dii_bp = Blueprint("fii_dii", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_TTL = 900  # 15 min
_cache = {"t": 0, "data": None}

HOME = "https://www.nseindia.com/"
API = "https://www.nseindia.com/api/fiidiiTradeReact"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _fetch_raw():
    last_err = None
    for _ in range(2):
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get(HOME, timeout=15)  # sets the cookies NSE requires
            r = s.get(API, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"NSE fetch failed: {last_err}")


def _build():
    raw = _fetch_raw()
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("Unexpected NSE response")

    out = {"fii": None, "dii": None}
    data_date = None
    for row in raw:
        cat = str(row.get("category", "")).upper()
        item = {
            "buy": _num(row.get("buyValue")),
            "sell": _num(row.get("sellValue")),
            "net": _num(row.get("netValue")),
        }
        if "FII" in cat or "FPI" in cat:
            out["fii"] = item
        elif "DII" in cat:
            out["dii"] = item
        data_date = data_date or row.get("date")

    if not out["fii"] or not out["dii"]:
        raise RuntimeError("Missing FII or DII row")

    iso = datetime.strptime(data_date, "%d-%b-%Y").strftime("%Y-%m-%d")
    today = datetime.now(IST).strftime("%Y-%m-%d")

    return {
        "data_date": iso,
        "is_trading_day": iso == today,
        "unit": "INR crore",
        "fii": out["fii"],
        "dii": out["dii"],
        "combined_net": round((out["fii"]["net"] or 0) + (out["dii"]["net"] or 0), 2),
        "generated_at": datetime.now(IST).isoformat(),
    }


@fii_dii_bp.route("/api/market/fii-dii")
def fii_dii():
    now = time.time()
    if _cache["data"] and now - _cache["t"] < CACHE_TTL:
        return jsonify(_cache["data"])
    try:
        data = _build()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    _cache.update(t=now, data=data)
    return jsonify(data)
    
