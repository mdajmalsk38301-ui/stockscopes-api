from flask import Flask, jsonify, request
import requests
from datetime import datetime, timedelta
import time
import yfinance as yf
from yfinance import EquityQuery
import pandas as pd

from candles import candles_bp
from fundamentals import fundamentals_bp
from ipo_merge import ipo_bp
from corp_actions_merge import corp_bp

app = Flask(__name__)
app.register_blueprint(candles_bp)
app.register_blueprint(fundamentals_bp)
app.register_blueprint(ipo_bp)
app.register_blueprint(corp_bp)


@app.after_request
def add_cors_headers(response):
    # Wildcard is fine here — these endpoints are public, read-only,
    # unauthenticated stock data with nothing user-specific to protect.
    # No point debugging exact-origin string matches (www vs non-www,
    # http vs https, trailing slashes) for data anyone can already see.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


# ── caches ──
_movers_cache = {"data": None, "ts": 0}
MOVERS_CACHE_SECONDS = 120

_detail_cache = {}
DETAIL_CACHE_SECONDS = 300

# Used ONLY as a fallback if Yahoo's India screener is unavailable —
# real gainers/losers now come from a live market-wide screen instead.
FALLBACK_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BAJFINANCE.NS", "ITC.NS", "WIPRO.NS", "TATASTEEL.NS", "SUNPHARMA.NS",
    "HINDUNILVR.NS", "SBIN.NS", "AXISBANK.NS", "LT.NS", "MARUTI.NS",
    "KOTAKBANK.NS", "TITAN.NS", "ASIANPAINT.NS", "BHARTIARTL.NS", "ADANIENT.NS",
]

INDEX_SYMBOLS = [
    ("^NSEI", "NIFTY 50"),
    ("^NSEBANK", "BANKNIFTY"),
    ("^BSESN", "SENSEX"),
]


@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running", "version": "10.0"})


@app.route("/ipo-data")
def ipo_data():
    result = []
    for status in ["open", "upcoming"]:
        try:
            url = f"https://api.ipoalerts.in/ipos?status={status}"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                ipos = data.get("ipos", [])
                for item in ipos:
                    result.append({
                        "companyName": item.get("name", ""),
                        "symbol": item.get("symbol", ""),
                        "openDate": item.get("startDate", ""),
                        "closeDate": item.get("endDate", ""),
                        "price": item.get("priceRange", ""),
                        "lotSize": "",
                        "_status": "Open" if status == "open" else "Upcoming",
                        "_category": item.get("type", "IPO")
                    })
            time.sleep(1)
        except Exception as e:
            continue
    return jsonify(result)


@app.route("/corp-actions")
def corp_actions():
    try:
        from bse import BSE

        today = datetime.now()
        future = today + timedelta(days=30)

        b = BSE(download_folder="/tmp/")
        actions = b.actions(
            segment="equity",
            from_date=today,
            to_date=future
        )
        b.exit()

        if not actions:
            return jsonify([])

        result = []
        for item in actions:
            if not isinstance(item, dict):
                continue
            purpose = item.get("Purpose", "")
            result.append({
                "comp": item.get("long_name", ""),
                "subject": purpose,
                "exDate": item.get("Ex_date", ""),
                "recDate": item.get("RD_Date", ""),
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def fmt_quote(label, price, pct):
    return {
        "symbol": label,
        "price": f"{price:,.2f}",
        "pct": f"{'+' if pct >= 0 else ''}{pct:.2f}",
        "up": pct >= 0,
    }


def fetch_indices():
    symbols = [s for s, _ in INDEX_SYMBOLS]
    data = yf.download(
        tickers=" ".join(symbols),
        period="5d",
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=15,
    )
    indices = []
    for symbol, label in INDEX_SYMBOLS:
        try:
            closes = data[symbol]["Close"].dropna()
            if len(closes) < 2:
                continue
            prev_close = float(closes.iloc[-2])
            last = float(closes.iloc[-1])
            pct = ((last - prev_close) / prev_close) * 100
            indices.append(fmt_quote(label, last, pct))
        except Exception:
            continue
    return indices


def screen_movers(direction, limit=5):
    """
    Real, market-wide top gainers/losers via Yahoo Finance's India-region
    equity screener — not limited to a fixed watchlist.
    """
    op = "gt" if direction == "gainers" else "lt"
    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "in"]),
        EquityQuery(op, ["percentchange", 0]),
    ])
    result = yf.screen(
        query,
        sortField="percentchange",
        sortAsc=(direction == "losers"),
        size=limit,
    )
    quotes = result.get("quotes", []) if isinstance(result, dict) else []

    rows = []
    for q in quotes[:limit]:
        symbol = (q.get("symbol") or "").replace(".NS", "").replace(".BO", "")
        price = q.get("regularMarketPrice")
        pct = q.get("regularMarketChangePercent")
        if symbol and price is not None and pct is not None:
            rows.append(fmt_quote(symbol, float(price), float(pct)))
    return rows


def fallback_watchlist_movers():
    """Used only if the live screener fails or returns nothing."""
    data = yf.download(
        tickers=" ".join(FALLBACK_WATCHLIST),
        period="5d",
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=15,
    )
    quotes = []
    for symbol in FALLBACK_WATCHLIST:
        try:
            closes = data[symbol]["Close"].dropna()
            if len(closes) < 2:
                continue
            prev_close = float(closes.iloc[-2])
            last = float(closes.iloc[-1])
            pct = ((last - prev_close) / prev_close) * 100
            label = symbol.replace(".NS", "")
            quotes.append(fmt_quote(label, last, pct))
        except Exception:
            continue

    quotes_sorted = sorted(quotes, key=lambda r: float(r["pct"]), reverse=True)
    gainers = quotes_sorted[:5]
    losers = list(reversed(quotes_sorted[-5:])) if quotes_sorted else []
    return gainers, losers


def build_movers_payload():
    indices = fetch_indices()

    try:
        gainers = screen_movers("gainers", 5)
        losers = screen_movers("losers", 5)
        if not gainers or not losers:
            raise ValueError("screener returned empty results")
    except Exception:
        gainers, losers = fallback_watchlist_movers()

    return {"indices": indices, "gainers": gainers, "losers": losers}


@app.route("/api/market-movers")
def market_movers():
    now = time.time()
    if _movers_cache["data"] is None or (now - _movers_cache["ts"]) > MOVERS_CACHE_SECONDS:
        try:
            _movers_cache["data"] = build_movers_payload()
            _movers_cache["ts"] = now
        except Exception as e:
            if _movers_cache["data"] is None:
                return jsonify({"error": str(e)}), 502

    return jsonify(_movers_cache["data"])


def compute_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    last_val = rsi.iloc[-1]
    return float(last_val) if pd.notna(last_val) else None


def resolve_market_cap(ticker, last_price):
    try:
        fi = ticker.fast_info
        mc = fi.get("market_cap")

        if not mc:
            shares = fi.get("shares")
            if shares:
                mc = float(shares) * last_price

        if not mc:
            info = ticker.info
            mc = info.get("marketCap")

        if mc:
            mc = float(mc)
            if mc >= 1e12:
                return f"₹{mc / 1e12:.2f}L Cr"
            if mc >= 1e7:
                return f"₹{mc / 1e7:,.0f} Cr"
            return f"₹{mc:,.0f}"
    except Exception:
        pass
    return "N/A"


def fetch_history_for_exchange(raw_symbol):
    if raw_symbol.startswith("^") or raw_symbol.endswith(".NS") or raw_symbol.endswith(".BO"):
        candidates = [raw_symbol]
    else:
        candidates = [raw_symbol + ".NS", raw_symbol + ".BO"]

    for candidate in candidates:
        try:
            ticker = yf.Ticker(candidate)
            hist = ticker.history(period="1y")
            if not hist.empty and len(hist) >= 2:
                return candidate, ticker, hist
        except Exception:
            continue

    return None, None, None


@app.route("/api/stock-detail")
def stock_detail():
    raw_symbol = request.args.get("symbol", "").strip().upper()
    if not raw_symbol:
        return jsonify({"error": "symbol query param required"}), 400

    now = time.time()
    cache_key = raw_symbol
    cached = _detail_cache.get(cache_key)
    if cached and (now - cached["ts"]) < DETAIL_CACHE_SECONDS:
        return jsonify(cached["data"])

    yf_symbol, ticker, hist = fetch_history_for_exchange(raw_symbol)

    if ticker is None:
        return jsonify({"error": "no data for symbol on NSE or BSE"}), 404

    try:
        exchange = "BSE" if yf_symbol.endswith(".BO") else "NSE"

        last_row = hist.iloc[-1]
        prev_row = hist.iloc[-2]

        last = float(last_row["Close"])
        prev_close = float(prev_row["Close"])
        pct = ((last - prev_close) / prev_close) * 100 if prev_close else 0

        open_price = float(last_row["Open"])
        day_high = float(last_row["High"])
        day_low = float(last_row["Low"])
        year_high = float(hist["High"].max())
        year_low = float(hist["Low"].min())
        volume = int(last_row["Volume"])

        market_cap = resolve_market_cap(ticker, last)

        closes = hist["Close"]
        sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
        sma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
        rsi = compute_rsi(closes)

        if sma50 and sma200:
            if last > sma50 and last > sma200 and sma50 > sma200:
                trend = "Bullish"
            elif last < sma50 and last < sma200 and sma50 < sma200:
                trend = "Bearish"
            else:
                trend = "Neutral"
        else:
            trend = "N/A"

        range_span = year_high - year_low
        range_position = round(((last - year_low) / range_span) * 100, 1) if range_span else 50.0

        chart_hist = hist.tail(126)
        history = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2)}
            for idx, row in chart_hist.iterrows()
        ]

        data = {
            "symbol": raw_symbol.replace(".NS", "").replace(".BO", ""),
            "exchange": exchange,
            "price": f"{last:,.2f}",
            "pct": f"{'+' if pct >= 0 else ''}{pct:.2f}",
            "up": pct >= 0,
            "open": f"{open_price:,.2f}",
            "dayHigh": f"{day_high:,.2f}",
            "dayLow": f"{day_low:,.2f}",
            "prevClose": f"{prev_close:,.2f}",
            "yearHigh": f"{year_high:,.2f}",
            "yearLow": f"{year_low:,.2f}",
            "volume": f"{volume:,}",
            "marketCap": market_cap,
            "history": history,
            "analytics": {
                "sma50": f"{sma50:,.2f}" if sma50 else "N/A",
                "sma200": f"{sma200:,.2f}" if sma200 else "N/A",
                "rsi": f"{rsi:.1f}" if rsi is not None else "N/A",
                "trend": trend,
                "rangePosition": range_position,
            },
        }

        _detail_cache[cache_key] = {"data": data, "ts": now}
        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(debug=True)
