from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time
import yfinance as yf

app = Flask(__name__)

# ── simple in-memory cache for market movers ──
_movers_cache = {"data": None, "ts": 0}
MOVERS_CACHE_SECONDS = 90

# A fixed watchlist since yfinance has no "top gainers/losers" endpoint of its own.
# Add/remove symbols here — .NS suffix is required for NSE tickers on Yahoo Finance.
WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BAJFINANCE.NS", "ITC.NS", "WIPRO.NS", "TATASTEEL.NS", "SUNPHARMA.NS",
    "HINDUNILVR.NS", "SBIN.NS", "AXISBANK.NS", "LT.NS", "MARUTI.NS",
    "KOTAKBANK.NS", "TITAN.NS", "ASIANPAINT.NS", "BHARTIARTL.NS", "ADANIENT.NS",
]

INDEX_SYMBOLS = [
    ("^NSEI", "NIFTY 50"),
    ("^NSEBANK", "BANKNIFTY"),
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


def fmt_quote(symbol, label, price, pct):
    return {
        "symbol": label,
        "price": f"{price:,.2f}",
        "pct": f"{'+' if pct >= 0 else ''}{pct:.2f}",
        "up": pct >= 0,
    }


def build_movers_payload():
    # Batch-download via yf.Tickers — friendlier to Yahoo's rate limits than
    # one request per symbol.
    tickers = yf.Tickers(" ".join(WATCHLIST))
    quotes = []

    for symbol in WATCHLIST:
        try:
            hist = tickers.tickers[symbol].history(period="2d")
            if len(hist) < 2:
                continue
            prev_close = hist["Close"].iloc[-2]
            last = hist["Close"].iloc[-1]
            pct = ((last - prev_close) / prev_close) * 100
            label = symbol.replace(".NS", "")
            quotes.append(fmt_quote(symbol, label, last, pct))
        except Exception:
            continue

    quotes_sorted = sorted(quotes, key=lambda r: float(r["pct"]), reverse=True)
    gainers = quotes_sorted[:5]
    losers = list(reversed(quotes_sorted[-5:]))

    indices = []
    for symbol, label in INDEX_SYMBOLS:
        try:
            hist = yf.Ticker(symbol).history(period="2d")
            if len(hist) < 2:
                continue
            prev_close = hist["Close"].iloc[-2]
            last = hist["Close"].iloc[-1]
            pct = ((last - prev_close) / prev_close) * 100
            indices.append(fmt_quote(symbol, label, last, pct))
        except Exception:
            continue

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


if __name__ == "__main__":
    app.run(debug=True)
