from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time

app = Flask(__name__)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

def get_nse_session():
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    time.sleep(2)
    s.get("https://www.nseindia.com/companies-listing/corporate-filings-actions", timeout=15)
    time.sleep(1)
    return s

@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running", "version": "6.0"})

@app.route("/ipo-data")
def ipo_data():
    result = []

    # Try ipoalerts.in for each status (no API key needed for basic data)
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

    # Fallback to NSE if ipoalerts returns nothing
    if not result:
        try:
            s = get_nse_session()
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
        except:
            pass

    return jsonify(result)

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
