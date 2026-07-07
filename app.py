from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

def get_nse_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    time.sleep(2)
    s.get("https://www.nseindia.com/companies-listing/corporate-filings-actions", timeout=15)
    time.sleep(1)
    return s

@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running", "version": "5.0"})

@app.route("/ipo-data")
def ipo_data():
    try:
        s = get_nse_session()
        result = []
        for status in ["current", "upcoming"]:
            for category in ["ipo", "sme"]:
                try:
                    url = f"https://www.nseindia.com/api/public-offer?category={category}&status={status}"
                    resp = s.get(url, timeout=15)
                    if resp.status_code == 200 and resp.text.strip():
                        data = resp.json()
                        if isinstance(data, list):
                            for item in data:
                                item["_status"] = "Upcoming" if status == "upcoming" else get_status(
                                    item.get("openDate", ""),
                                    item.get("closeDate", "")
                                )
                                item["_category"] = category.upper()
                            result += data
                    time.sleep(1)
                except:
                    continue
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/corp-actions")
def corp_actions():
    try:
        s = get_nse_session()
        today = datetime.now()
        future = today + timedelta(days=30)
        from_date = today.strftime("%d-%m-%Y")
        to_date = future.strftime("%d-%m-%Y")
        url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={from_date}&to_date={to_date}"
        resp = s.get(url, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return jsonify(data)
        return jsonify({"error": f"NSE returned {resp.status_code}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_status(open_date, close_date):
    try:
        today = datetime.now().date()
        fmt = "%d-%b-%Y"
        o = datetime.strptime(open_date, fmt).date()
        c = datetime.strptime(close_date, fmt).date()
        if today < o: return "Upcoming"
        if today <= c: return "Open"
        return "Closed"
    except:
        return "Closed"

if __name__ == "__main__":
    app.run(debug=True)


from jugaad_data.nse import NSELive

@app.route('/market-movers')
def market_movers():
    try:
        n = NSELive()
        data = n.market_status()  # confirms market open/closed
        gainers = n.pre_open_market('NIFTY GAINERS')
        losers = n.pre_open_market('NIFTY LOSERS')
        indices = n.index_option_chain('NIFTY') if False else None  # placeholder, optional
        return jsonify({
            "gainers": gainers,
            "losers": losers
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
