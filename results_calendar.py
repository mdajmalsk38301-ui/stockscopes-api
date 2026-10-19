"""
results_calendar.py - Flask blueprint for the quarterly results calendar (NSE board meetings).

Endpoint: GET /api/results/calendar
Register in your main app:
    from results_calendar import results_bp
    app.register_blueprint(results_bp)
"""
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Blueprint, jsonify

results_bp = Blueprint("results_calendar", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_TTL = 1800  # 30 min
_cache = {"t": 0, "data": None}

HOME = "https://www.nseindia.com/"
API = "https://www.nseindia.com/api/corporate-board-meetings"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-board-meetings",
}


def _fetch_raw(from_dt, to_dt):
    params = {
        "index": "equities",
        "from_date": from_dt.strftime("%d-%m-%Y"),
        "to_date": to_dt.strftime("%d-%m-%Y"),
    }
    last_err = None
    for _ in range(2):
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get(HOME, timeout=15)  # cookies required by NSE
            r = s.get(API, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"NSE fetch failed: {last_err}")


def _parse_date(s):
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except Exception:
            continue
    return None


def _build():
    today = datetime.now(IST).date()
    raw = _fetch_raw(today - timedelta(days=3), today + timedelta(days=7))
    if isinstance(raw, dict):
        raw = raw.get("data", [])
    if not isinstance(raw, list):
        raise RuntimeError("Unexpected NSE response")

    seen, items = set(), []
    for row in raw:
        purpose = str(row.get("bm_purpose") or row.get("purpose") or "")
        desc = str(row.get("bm_desc") or "")
        if "result" not in (purpose + " " + desc).lower():
            continue
        d = _parse_date(row.get("bm_date") or row.get("date"))
        if not d:
            continue
        symbol = row.get("bm_symbol") or row.get("symbol") or ""
        key = (symbol, d)
        if not symbol or key in seen:
            continue
        seen.add(key)
        items.append({
            "symbol": symbol,
            "company": row.get("sm_name") or row.get("companyName") or symbol,
            "date": d.isoformat(),
            "purpose": purpose[:120],
        })

    items.sort(key=lambda x: (x["date"], x["symbol"]))
    t = today.isoformat()
    return {
        "as_of": t,
        "today": [i for i in items if i["date"] == t][:60],
        "upcoming": [i for i in items if i["date"] > t][:80],
        "recently_held": [i for i in items if i["date"] < t][:60],
        "generated_at": datetime.now(IST).isoformat(),
    }


@results_bp.route("/api/results/calendar")
def results_calendar():
    now = time.time()
    if _cache["data"] and now - _cache["t"] < CACHE_TTL:
        return jsonify(_cache["data"])
    try:
        data = _build()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    _cache.update(t=now, data=data)
    return jsonify(data)
