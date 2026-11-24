"""
us_markets.py - US stock data for StockScopes (heatmap, indices, movers).

Endpoints
  GET /api/us/quotes   ~100 large US stocks: price, 1-day / 1-month / 3-month change, sector, approx size
  GET /api/us/indices  S&P 500, Nasdaq, Dow, Russell 2000, VIX, US 10Y yield, gold, crude, dollar index

Data comes from yfinance (daily bars) in ONE batch download, cached in memory (15 min / 10 min),
so a page view never triggers 100 separate requests. Delayed data, not live quotes.
"""
import threading
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from cache_util import cache_get, cache_set

us_bp = Blueprint("us_markets", __name__)
_lock = threading.Lock()

# ticker, name, sector, approx market value in $ billions (only used for tile size)
UNIVERSE = [
    ("AAPL", "Apple", "Technology", 3800), ("MSFT", "Microsoft", "Technology", 3700), ("NVDA", "NVIDIA", "Technology", 4300),
    ("AVGO", "Broadcom", "Technology", 1600), ("ORCL", "Oracle", "Technology", 700), ("CRM", "Salesforce", "Technology", 250),
    ("AMD", "AMD", "Technology", 350), ("ADBE", "Adobe", "Technology", 150), ("CSCO", "Cisco", "Technology", 280),
    ("ACN", "Accenture", "Technology", 160), ("IBM", "IBM", "Technology", 270), ("INTC", "Intel", "Technology", 150),
    ("QCOM", "Qualcomm", "Technology", 170), ("TXN", "Texas Instruments", "Technology", 170), ("NOW", "ServiceNow", "Technology", 190),
    ("INTU", "Intuit", "Technology", 180), ("MU", "Micron", "Technology", 200), ("AMAT", "Applied Materials", "Technology", 160),
    ("PLTR", "Palantir", "Technology", 400),
    ("GOOGL", "Alphabet", "Communication", 3500), ("META", "Meta", "Communication", 1700), ("NFLX", "Netflix", "Communication", 480),
    ("DIS", "Disney", "Communication", 200), ("TMUS", "T-Mobile US", "Communication", 260), ("VZ", "Verizon", "Communication", 180),
    ("T", "AT&T", "Communication", 170), ("CMCSA", "Comcast", "Communication", 130),
    ("AMZN", "Amazon", "Consumer Cyclical", 2400), ("TSLA", "Tesla", "Consumer Cyclical", 1400), ("HD", "Home Depot", "Consumer Cyclical", 380),
    ("MCD", "McDonald's", "Consumer Cyclical", 210), ("NKE", "Nike", "Consumer Cyclical", 100), ("LOW", "Lowe's", "Consumer Cyclical", 140),
    ("SBUX", "Starbucks", "Consumer Cyclical", 100), ("BKNG", "Booking", "Consumer Cyclical", 170), ("TJX", "TJX", "Consumer Cyclical", 160),
    ("UBER", "Uber", "Consumer Cyclical", 190),
    ("WMT", "Walmart", "Consumer Defensive", 800), ("COST", "Costco", "Consumer Defensive", 400), ("PG", "Procter & Gamble", "Consumer Defensive", 350),
    ("KO", "Coca-Cola", "Consumer Defensive", 300), ("PEP", "PepsiCo", "Consumer Defensive", 200), ("PM", "Philip Morris", "Consumer Defensive", 240),
    ("MDLZ", "Mondelez", "Consumer Defensive", 80), ("CL", "Colgate-Palmolive", "Consumer Defensive", 70),
    ("BRK-B", "Berkshire Hathaway", "Financials", 1050), ("JPM", "JPMorgan Chase", "Financials", 850), ("V", "Visa", "Financials", 650),
    ("MA", "Mastercard", "Financials", 500), ("BAC", "Bank of America", "Financials", 370), ("WFC", "Wells Fargo", "Financials", 270),
    ("GS", "Goldman Sachs", "Financials", 240), ("MS", "Morgan Stanley", "Financials", 240), ("AXP", "American Express", "Financials", 240),
    ("BLK", "BlackRock", "Financials", 160), ("C", "Citigroup", "Financials", 180), ("SCHW", "Charles Schwab", "Financials", 180),
    ("LLY", "Eli Lilly", "Healthcare", 800), ("UNH", "UnitedHealth", "Healthcare", 300), ("JNJ", "Johnson & Johnson", "Healthcare", 480),
    ("ABBV", "AbbVie", "Healthcare", 380), ("MRK", "Merck", "Healthcare", 230), ("PFE", "Pfizer", "Healthcare", 150),
    ("TMO", "Thermo Fisher", "Healthcare", 210), ("ABT", "Abbott", "Healthcare", 230), ("AMGN", "Amgen", "Healthcare", 160),
    ("ISRG", "Intuitive Surgical", "Healthcare", 190), ("GILD", "Gilead", "Healthcare", 140),
    ("XOM", "Exxon Mobil", "Energy", 480), ("CVX", "Chevron", "Energy", 290), ("COP", "ConocoPhillips", "Energy", 120),
    ("SLB", "Schlumberger", "Energy", 55), ("EOG", "EOG Resources", "Energy", 65),
    ("GE", "GE Aerospace", "Industrials", 300), ("CAT", "Caterpillar", "Industrials", 230), ("RTX", "RTX", "Industrials", 220),
    ("HON", "Honeywell", "Industrials", 140), ("UNP", "Union Pacific", "Industrials", 140), ("BA", "Boeing", "Industrials", 150),
    ("DE", "Deere", "Industrials", 130), ("LMT", "Lockheed Martin", "Industrials", 110), ("UPS", "UPS", "Industrials", 85),
    ("NEE", "NextEra Energy", "Utilities", 160), ("SO", "Southern Co", "Utilities", 100), ("DUK", "Duke Energy", "Utilities", 95),
    ("AMT", "American Tower", "Real Estate", 100), ("PLD", "Prologis", "Real Estate", 110), ("EQIX", "Equinix", "Real Estate", 80),
    ("LIN", "Linde", "Materials", 210), ("SHW", "Sherwin-Williams", "Materials", 90), ("FCX", "Freeport-McMoRan", "Materials", 70),
]

INDICES = [
    ("^GSPC", "S&P 500", "idx"), ("^IXIC", "Nasdaq Composite", "idx"), ("^DJI", "Dow Jones", "idx"), ("^RUT", "Russell 2000", "idx"),
    ("^VIX", "VIX (fear gauge)", "vix"), ("^TNX", "US 10-year yield", "yield"), ("GC=F", "Gold (USD/oz)", "cmd"),
    ("CL=F", "Crude oil WTI (USD/bbl)", "cmd"), ("DX-Y.NYB", "US dollar index", "fx"),
]


def _download(tickers, period):
    import yfinance as yf
    return yf.download(tickers, period=period, interval="1d", group_by="ticker", auto_adjust=True,
                       progress=False, threads=True)


def _closes(df, t):
    try:
        s = df[t]["Close"].dropna()
    except Exception:
        return None
    return s if len(s) >= 2 else None


def _pct(a, b):
    return None if not b else round((a / b - 1) * 100, 2)


def _stamp(s):
    try:
        return str(s.index[-1].date())
    except Exception:
        return None


@us_bp.route("/api/us/quotes")
def us_quotes():
    hit = cache_get("us_quotes")
    if hit is not None:
        return jsonify(hit)
    with _lock:
        hit = cache_get("us_quotes")
        if hit is not None:
            return jsonify(hit)
        try:
            df = _download([u[0] for u in UNIVERSE], "5mo")
        except Exception as e:  # network / Yahoo problem
            return jsonify({"rows": [], "error": "US data is unavailable right now", "detail": str(e)[:120]}), 503
        rows, asof = [], None
        for t, name, sector, cap in UNIVERSE:
            s = _closes(df, t)
            if s is None:
                continue
            last = float(s.iloc[-1])
            row = {"symbol": t, "name": name, "sector": sector, "cap_b": cap, "price": round(last, 2),
                   "pct": _pct(last, float(s.iloc[-2])),
                   "ret_1m": _pct(last, float(s.iloc[-22])) if len(s) > 22 else None,
                   "ret_3m": _pct(last, float(s.iloc[-64])) if len(s) > 64 else None}
            rows.append(row)
            asof = asof or _stamp(s)
        if len(rows) < 20:
            return jsonify({"rows": [], "error": "US data is unavailable right now"}), 503
        out = {"rows": rows, "asof": asof, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "note": "Daily bars from Yahoo Finance via yfinance. Delayed; the latest bar may be mid-session. Tile size uses approximate market values."}
        cache_set("us_quotes", out, 15 * 60)
        return jsonify(out)


@us_bp.route("/api/us/indices")
def us_indices():
    hit = cache_get("us_indices")
    if hit is not None:
        return jsonify(hit)
    with _lock:
        hit = cache_get("us_indices")
        if hit is not None:
            return jsonify(hit)
        try:
            df = _download([i[0] for i in INDICES], "1mo")
        except Exception as e:
            return jsonify({"rows": [], "error": "US data is unavailable right now", "detail": str(e)[:120]}), 503
        rows = []
        for t, name, kind in INDICES:
            s = _closes(df, t)
            if s is None:
                continue
            last, prev = float(s.iloc[-1]), float(s.iloc[-2])
            rows.append({"symbol": t, "name": name, "kind": kind, "price": round(last, 2), "change": round(last - prev, 2),
                         "pct": _pct(last, prev), "asof": _stamp(s)})
        if not rows:
            return jsonify({"rows": [], "error": "US data is unavailable right now"}), 503
        out = {"rows": rows, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        cache_set("us_indices", out, 10 * 60)
        return jsonify(out)
