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

class _RegisterOnce(Blueprint):
    """Registering this twice (for example once in app.py and once in register_all.py) is harmless."""
    def register(self, app, options):
        if self.name in app.blueprints:
            return
        super().register(app, options)


screener_bp = _RegisterOnce("screener", __name__)

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


# Used only when Yahoo refuses the company-details call (.info). Sectors are fixed here because that call also carries them.
SECTORS = {
    "RELIANCE": "Energy", "ONGC": "Energy", "COALINDIA": "Energy", "BPCL": "Energy", "NTPC": "Utilities", "POWERGRID": "Utilities",
    "TCS": "Technology", "INFY": "Technology", "HCLTECH": "Technology", "WIPRO": "Technology", "TECHM": "Technology", "LTIM": "Technology",
    "HDFCBANK": "Financial Services", "ICICIBANK": "Financial Services", "SBIN": "Financial Services", "AXISBANK": "Financial Services",
    "KOTAKBANK": "Financial Services", "INDUSINDBK": "Financial Services", "BAJFINANCE": "Financial Services", "BAJAJFINSV": "Financial Services",
    "SBILIFE": "Financial Services", "HDFCLIFE": "Financial Services", "SHRIRAMFIN": "Financial Services", "JIOFIN": "Financial Services",
    "HINDUNILVR": "Consumer Defensive", "ITC": "Consumer Defensive", "NESTLEIND": "Consumer Defensive", "BRITANNIA": "Consumer Defensive", "TATACONSUM": "Consumer Defensive",
    "MARUTI": "Consumer Cyclical", "M&M": "Consumer Cyclical", "TATAMOTORS": "Consumer Cyclical", "BAJAJ-AUTO": "Consumer Cyclical", "EICHERMOT": "Consumer Cyclical",
    "HEROMOTOCO": "Consumer Cyclical", "TITAN": "Consumer Cyclical", "TRENT": "Consumer Cyclical", "ETERNAL": "Consumer Cyclical", "INDIGO": "Industrials",
    "LT": "Industrials", "ADANIPORTS": "Industrials", "BEL": "Industrials", "ADANIENT": "Industrials",
    "SUNPHARMA": "Healthcare", "CIPLA": "Healthcare", "DRREDDY": "Healthcare", "APOLLOHOSP": "Healthcare", "DIVISLAB": "Healthcare",
    "ULTRACEMCO": "Basic Materials", "GRASIM": "Basic Materials", "TATASTEEL": "Basic Materials", "JSWSTEEL": "Basic Materials", "HINDALCO": "Basic Materials", "ASIANPAINT": "Basic Materials",
    "BHARTIARTL": "Communication Services",
}


try:
    from scanner import NAMES as _NAMES
except Exception:
    _NAMES = {}


def _series(df, names):
    """Values of the first matching row of a yfinance statement table, newest first."""
    if df is None or getattr(df, "empty", True):
        return []
    for n in names:
        if n in df.index:
            out = []
            for v in df.loc[n].tolist():
                f = _num(v)
                out.append(f)
            return out
    return []


def parse_fallback(symbol, price, mcap, income, balance, dividends_12m):
    """Work out the screener numbers from price plus financial statements (no Yahoo .info needed)."""
    if not price:
        return None
    rev = _series(income, ["Total Revenue", "Operating Revenue"])
    ni = _series(income, ["Net Income", "Net Income Common Stockholders"])
    eps = _series(income, ["Diluted EPS", "Basic EPS"])
    equity = _series(balance, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"])
    debt = _series(balance, ["Total Debt"])
    first = lambda a: a[0] if a and a[0] is not None else None
    e0, n0, r0, eq0, d0 = first(eps), first(ni), first(rev), first(equity), first(debt)
    ratio = lambda a, b: round(a / b, 2) if a is not None and b else None
    growth = None
    if len(rev) > 1 and rev[0] is not None and rev[1]:
        growth = round((rev[0] / rev[1] - 1) * 100, 2)
    return {
        "symbol": symbol,
        "name": _NAMES.get(symbol, symbol),
        "sector": SECTORS.get(symbol, "Other"),
        "price": round(price, 2),
        "market_cap_cr": round(mcap / 1e7, 0) if mcap else None,
        "pe": round(price / e0, 2) if e0 and e0 > 0 else None,
        "pb": ratio(mcap, eq0) if mcap and eq0 and eq0 > 0 else None,
        "roe": round(n0 / eq0 * 100, 2) if n0 is not None and eq0 and eq0 > 0 else None,
        "debt_equity": ratio(d0, eq0) if d0 is not None and eq0 and eq0 > 0 else None,
        "div_yield": round(dividends_12m / price * 100, 2) if dividends_12m else 0.0,
        "profit_margin": round(n0 / r0 * 100, 2) if n0 is not None and r0 else None,
        "rev_growth": growth,
        "eps": round(e0, 2) if e0 is not None else None,
        "beta": None,
    }


def _fallback_row(symbol):
    t = yf.Ticker(symbol + ".NS")
    fi = t.fast_info
    price = _num(fi["lastPrice"]); mcap = _num(fi["marketCap"])
    try:
        income = t.income_stmt
    except Exception:
        income = None
    try:
        balance = t.balance_sheet
    except Exception:
        balance = None
    div = 0.0
    try:
        d = t.dividends
        if d is not None and len(d):
            cutoff = d.index.max() - timedelta(days=365)
            div = float(d[d.index > cutoff].sum())
    except Exception:
        pass
    row = parse_fallback(symbol, price, mcap, income, balance, div)
    if row and not any(row[k] is not None for k in ("pe", "roe", "profit_margin", "rev_growth", "debt_equity")):
        return None                                   # a price and nothing else is not worth listing
    return row


def _fetch_row(symbol):
    try:
        row = parse_info(symbol, yf.Ticker(symbol + ".NS").info)
        if row:
            return row
    except Exception:
        pass
    try:
        return _fallback_row(symbol)
    except Exception:
        return None


def _one(symbol):
    for attempt in range(3):
        try:
            row = _fetch_row(symbol)
            if row:
                row["sensex"] = symbol in SENSEX
                row["nifty50"] = symbol in NIFTY50
                return row
        except Exception:
            pass
        time.sleep(2 + attempt * 3)          # wait longer each time, so Yahoo is not hammered
    return None


def _probe(symbols):
    """Try a few stocks once. If Yahoo refuses all of them there is no point trying the other 40."""
    for sym in symbols:
        try:
            if _fetch_row(sym):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def build():
    syms = universe()
    if not _probe(syms[:3]):
        return {"generated_at": datetime.now(IST).isoformat(), "count": 0, "rows": []}
    fails = {"n": 0}

    def work(sym):
        if fails["n"] >= 6:                       # Yahoo has started refusing: stop asking, keep what we have
            return None
        row = _one(sym)
        fails["n"] = 0 if row else fails["n"] + 1
        return row

    with ThreadPoolExecutor(max_workers=2) as ex:
        rows = [r for r in ex.map(work, syms) if r]
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
            _state["last_try"] = time.time()


def _start_if_needed():
    with _lock:
        if _state["building"]:
            return
        if _state["data"] is None and time.time() - _state.get("last_try", 0) < 21600:
            return                    # the last try found nothing; wait 6 hours before asking Yahoo again
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
