"""
StockScopes API — Fundamentals Endpoint
=========================================
Serves valuation stats (P/E, EPS, market cap, dividend yield, 52-week
range, etc.) and summarized financial statements (income statement,
balance sheet, cash flow — annual + quarterly) for the StockScopes
fundamentals shortcode. Same data source and drop-in pattern as
candles.py (yfinance, no API key, symbol resolution via SYMBOL_MAP).

DROP-IN INSTRUCTIONS
---------------------
1. pip install yfinance flask-cors   (skip if already done for candles.py)
2. Copy this file into your Flask project as `fundamentals.py`, next to
   candles.py.
3. In your main app.py, alongside the candles blueprint:

       from fundamentals import fundamentals_bp
       app.register_blueprint(fundamentals_bp)

4. Cover /api/fundamentals with the same CORS rule you set up for
   /api/candles (see candles.py's docstring), e.g.:

       CORS(app, resources={
           r"/api/candles":      {"origins": "https://stockscopes.in"},
           r"/api/fundamentals": {"origins": "https://stockscopes.in"},
       })

ENDPOINT
--------
GET /api/fundamentals?symbol=RELIANCE

Query params:
  symbol  required. Same rules as candles.py — plain NSE ticker
          ("RELIANCE" -> RELIANCE.NS), a full Yahoo ticker, or a
          SYMBOL_MAP alias.

Response:
  200 {
        "symbol": "RELIANCE.NS",
        "company": { "name": "...", "sector": "...", "industry": "..." },
        "stats": {
          "market_cap": 1234567890, "pe_ratio": 24.1, "forward_pe": 21.3,
          "pb_ratio": 2.8, "eps": 45.6, "dividend_yield": 0.42,
          "book_value": 512.3, "week52_high": 1600.0, "week52_low": 1150.0,
          "beta": 0.85
        },
        "statements": {
          "annual":    { "income_statement": [...], "balance_sheet": [...], "cash_flow": [...] },
          "quarterly": { "income_statement": [...], "balance_sheet": [...], "cash_flow": [...] }
        }
      }

  Each statement is a list of:
      { "label": "Total Revenue", "values": { "2025-03-31": 987654.0, ... } }
  with up to 4 most recent periods, newest first. Missing values are
  simply omitted from a company's numbers — not every company reports
  every line item, and Yahoo's raw label spelling varies slightly by
  filing, so this endpoint tries a few known variants per line item
  and returns whichever one Yahoo actually has for that company.

  400 { "error": "..." }   — bad/missing params
  502 { "error": "..." }   — upstream (Yahoo) fetch failed
"""

import time
import threading
from flask import Blueprint, request, jsonify

try:
    import yfinance as yf
except ImportError:
    yf = None

fundamentals_bp = Blueprint("fundamentals", __name__)

# Kept in sync with candles.py — duplicated rather than imported so this
# file stays fully self-contained/drop-in on its own.
SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "NIFTYIT": "^CNXIT",
    "NIFTYPHARMA": "^CNXPHARMA",
    "NIFTYMIDCAP50": "^NSEMDCP50",
}

# yfinance/Yahoo's raw row labels vary a bit by filing and company —
# each entry here is (friendly label, [candidate raw labels in priority
# order]). We take the first one that actually exists for this ticker.
INCOME_STATEMENT_ITEMS = [
    ("Total Revenue",     ["Total Revenue", "Operating Revenue"]),
    ("Gross Profit",      ["Gross Profit"]),
    ("Operating Income",  ["Operating Income"]),
    ("EBITDA",            ["EBITDA", "Normalized EBITDA"]),
    ("Net Income",        ["Net Income", "Net Income Common Stockholders"]),
    ("Diluted EPS",       ["Diluted EPS"]),
]
BALANCE_SHEET_ITEMS = [
    ("Total Assets",       ["Total Assets"]),
    ("Total Liabilities",  ["Total Liabilities Net Minority Interest"]),
    ("Total Equity",       ["Total Equity Gross Minority Interest", "Stockholders Equity"]),
    ("Cash & Equivalents",  ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]),
    ("Total Debt",          ["Total Debt"]),
    ("Working Capital",     ["Working Capital"]),
]
CASH_FLOW_ITEMS = [
    ("Operating Cash Flow", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
    ("Investing Cash Flow", ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"]),
    ("Financing Cash Flow", ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"]),
    ("Capital Expenditure", ["Capital Expenditure"]),
    ("Free Cash Flow",      ["Free Cash Flow"]),
]

MAX_PERIODS = 4

_CACHE = {}
# Fundamentals change far less often than candles and ticker.info makes
# several requests under the hood, so we cache longer (30 min) to keep
# Yahoo happy and pages fast.
_CACHE_TTL_SECONDS = 30 * 60
_cache_lock = threading.Lock()


def _resolve_symbol(raw_symbol):
    s = raw_symbol.strip().upper()
    if s.startswith("^") or "." in s:
        return s
    if s in SYMBOL_MAP:
        return SYMBOL_MAP[s]
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


def _extract_statement(df, item_defs):
    """Turn a yfinance financial-statement DataFrame into the friendly
    label/values shape described in the module docstring, using only the
    first MAX_PERIODS columns (yfinance already orders newest-first)."""
    if df is None or df.empty:
        return []

    cols = list(df.columns)[:MAX_PERIODS]
    out = []
    for friendly_label, candidates in item_defs:
        row_label = next((c for c in candidates if c in df.index), None)
        if row_label is None:
            continue
        values = {}
        for col in cols:
            val = df.loc[row_label, col]
            if val is None or val != val:  # NaN check without importing numpy/pandas
                continue
            values[str(col.date()) if hasattr(col, "date") else str(col)] = round(float(val), 2)
        if values:
            out.append({"label": friendly_label, "values": values})
    return out


def _normalize_percent(value):
    """yfinance has changed dividend-yield units across versions (some
    return 0.0051 for 0.51%, others return 0.51 directly). Real dividend
    yields are always well under 100%, so treat anything under 1 as a
    fraction and scale it up — this covers both cases without guessing
    wrong for any real-world stock."""
    if value is None:
        return None
    return round(value * 100, 2) if value < 1 else round(value, 2)


@fundamentals_bp.route("/api/fundamentals", methods=["GET"])
def get_fundamentals():
    if yf is None:
        return jsonify({"error": "yfinance is not installed on the server"}), 500

    raw_symbol = request.args.get("symbol", "").strip()
    if not raw_symbol:
        return jsonify({"error": "symbol query param is required"}), 400

    yahoo_symbol = _resolve_symbol(raw_symbol)

    cached = _cache_get(yahoo_symbol)
    if cached is not None:
        return jsonify(cached)

    try:
        ticker = yf.Ticker(yahoo_symbol)
        info = ticker.info or {}
    except Exception as exc:
        return jsonify({"error": f"failed to fetch data for {yahoo_symbol}: {exc}"}), 502

    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        return jsonify({"error": f"no data returned for {yahoo_symbol}"}), 502

    stats = {
        "market_cap":      info.get("marketCap"),
        "pe_ratio":        info.get("trailingPE"),
        "forward_pe":      info.get("forwardPE"),
        "pb_ratio":        info.get("priceToBook"),
        "eps":             info.get("trailingEps"),
        "dividend_yield":  _normalize_percent(info.get("dividendYield")),
        "book_value":      info.get("bookValue"),
        "week52_high":     info.get("fiftyTwoWeekHigh"),
        "week52_low":      info.get("fiftyTwoWeekLow"),
        "beta":            info.get("beta"),
    }

    company = {
        "name":     info.get("longName") or info.get("shortName"),
        "sector":   info.get("sector"),
        "industry": info.get("industry"),
    }

    # These four calls can each be slow/flaky on their own — don't let one
    # failure (e.g. no cash flow data for a small-cap) take down the whole
    # response; just return an empty list for that piece.
    statements = {"annual": {}, "quarterly": {}}
    fetchers = [
        ("income_statement", "income_stmt",           "quarterly_income_stmt", INCOME_STATEMENT_ITEMS),
        ("balance_sheet",    "balance_sheet",          "quarterly_balance_sheet", BALANCE_SHEET_ITEMS),
        ("cash_flow",        "cashflow",               "quarterly_cashflow",     CASH_FLOW_ITEMS),
    ]
    for key, annual_attr, quarterly_attr, item_defs in fetchers:
        try:
            statements["annual"][key] = _extract_statement(getattr(ticker, annual_attr), item_defs)
        except Exception:
            statements["annual"][key] = []
        try:
            statements["quarterly"][key] = _extract_statement(getattr(ticker, quarterly_attr), item_defs)
        except Exception:
            statements["quarterly"][key] = []

    payload = {
        "symbol": yahoo_symbol,
        "company": company,
        "stats": stats,
        "statements": statements,
    }
    _cache_set(yahoo_symbol, payload)
    return jsonify(payload)
