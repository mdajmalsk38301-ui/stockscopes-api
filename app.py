from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time
from nsetools import Nse

app = Flask(__name__)
nse = Nse()

# ── simple in-memory cache for market movers ──
_movers_cache = {"data": None, "ts": 0}
MOVERS_CACHE_SECONDS = 60


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


def fmt_row(row, symbol_key="symbol", price_key="ltp", pct_key="netPrice"):
    """Normalise an nsetools row into the shape the WP ticker plugin expects."""
    pct = row.get(pct_key, 0)
    return {
        "symbol": row.get(symbol_key, ""),
        "price": f"{row.get(price_key, 0):,.2f}",
        "pct": f"{'+' if pct >= 0 else ''}{pct:.2f}",
        "up": pct >= 0,
    }


def build_movers_payload():
    gainers_raw = nse.get_top_gainers()
    losers_raw = nse.get_top_losers()

    gainers = [fmt_row(r) for r in gainers_raw[:5]]
    losers = [fmt_row(r) for r in losers_raw[:5]]

    indices = []
    for idx_symbol, label in [("NIFTY 50", "NIFTY 50"), ("NIFTY BANK", "BANKNIFTY")]:
        try:
            q = nse.get_index_quote(idx_symbol)
            pct = float(q.get("percentChange", 0))
            indices.append({
                "symbol": label,
                "price": f"{float(q.get('last', 0)):,.2f}",
                "pct": f"{'+' if pct >= 0 else ''}{pct:.2f}",
                "up": pct >= 0,
            })
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
    
