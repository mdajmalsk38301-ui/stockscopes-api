from flask import Flask, jsonify, request
import requests
from datetime import datetime, timedelta
import time
import yfinance as yf

app = Flask(__name__)

# ── caches ──
_movers_cache = {"data": None, "ts": 0}
MOVERS_CACHE_SECONDS = 120

_detail_cache = {}
DETAIL_CACHE_SECONDS = 300

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


def fmt_quote(label, price, pct):
    return {
        "symbol": label,
        "price": f"{price:,.2f}",
        "pct": f"{'+' if pct >= 0 else ''}{pct:.2f}",
        "up": pct >= 0,
    }


def build_movers_payload():
    all_symbols = WATCHLIST + [s for s, _ in INDEX_SYMBOLS]

    data = yf.download(
        tickers=" ".join(all_symbols),
        period="5d",
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=15,
    )

    def pct_change_for(symbol):
        closes = data[symbol]["Close"].dropna()
        if len(closes) < 2:
            return None
        prev_close = float(closes.iloc[-2])
        last = float(closes.iloc[-1])
        pct = ((last - prev_close) / prev_close) * 100
        return last, pct

    quotes = []
    for symbol in WATCHLIST:
        try:
            result = pct_change_for(symbol)
            if result is None:
                continue
            last, pct = result
            label = symbol.replace(".NS", "")
            quotes.append(fmt_quote(label, last, pct))
        except Exception:
            continue

    quotes_sorted = sorted(quotes, key=lambda r: float(r["pct"]), reverse=True)
    gainers = quotes_sorted[:5]
    losers = list(reversed(quotes_sorted[-5:])) if quotes_sorted else []

    indices = []
    for symbol, label in INDEX_SYMBOLS:
        try:
            result = pct_change_for(symbol)
            if result is None:
                continue
            last, pct = result
            indices.append(fmt_quote(label, last, pct))
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


@app.route("/api/stock-detail")
def stock_detail():
    raw_symbol = request.args.get("symbol", "").strip().upper()
    if not raw_symbol:
        return jsonify({"error": "symbol query param required"}), 400

    yf_symbol = raw_symbol if (raw_symbol.endswith(".NS") or raw_symbol.startswith("^")) else raw_symbol + ".NS"

    now = time.time()
    cached = _detail_cache.get(yf_symbol)
    if cached and (now - cached["ts"]) < DETAIL_CACHE_SECONDS:
        return jsonify(cached["data"])

    try:
        ticker = yf.Ticker(yf_symbol)
        # 1 year of daily OHLCV — used both for the chart AND for all the
        # stat boxes below. This is far more reliable than fast_info, whose
        # day_high/day_low/year_high/year_low/volume fields are frequently
        # empty for NSE tickers.
        hist = ticker.history(period="1y")

        if hist.empty or len(hist) < 2:
            return jsonify({"error": "no data for symbol"}), 404

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

        # Market cap needs shares-outstanding data that OHLCV history doesn't
        # have — best-effort only, never let it block the rest of the response.
        market_cap = "N/A"
        try:
            fi = ticker.fast_info
            mc = fi.get("market_cap")
            if mc:
                market_cap = f"{float(mc):,.0f}"
        except Exception:
            pass

        # Chart only needs the last ~6 months, no need to ship the full year.
        chart_hist = hist.tail(126)
        history = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2)}
            for idx, row in chart_hist.iterrows()
        ]

        data = {
            "symbol": raw_symbol.replace(".NS", ""),
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
        }

        _detail_cache[yf_symbol] = {"data": data, "ts": now}
        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(debug=True)
    
