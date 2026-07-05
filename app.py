from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
    "X-Requested-With": "XMLHttpRequest"
}

def get_nse_session():
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=HEADERS, timeout=15)
    s.get("https://www.nseindia.com/market-data/all-upcoming-issues-ipo", headers=HEADERS, timeout=15)
    return s

@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running", "version": "2.0"})

@app.route("/ipo-data")
def ipo_data():
    try:
        s = get_nse_session()
        result = []

        for status in ["current", "upcoming", "closed"]:
            try:
                url = f"https://www.nseindia.com/api/public-offer?category=ipo&status={status}"
                resp = s.get(url, headers=HEADERS, timeout=15)
                if resp.status_code == 200 and resp.text.strip():
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data:
                            item["_status"] = get_status(
                                item.get("openDate", ""),
                                item.get("closeDate", "")
                            ) if status != "upcoming" else "Upcoming"
                        result += data
            except Exception as e:
                continue

        if not result:
            # Fallback: try Chittorgarh
            result = fetch_ipo_chittorgarh()

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def fetch_ipo_chittorgarh():
    try:
        url = "https://www.chittorgarh.com/report/ipo-subscription-status-live-data/93/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            if isinstance(data, list):
                return data
        return []
    except:
        return []

@app.route("/corp-actions")
def corp_actions():
    try:
        s = get_nse_session()
        today = datetime.now()
        future = today + timedelta(days=30)
        from_date = today.strftime("%d-%m-%Y")
        to_date = future.strftime("%d-%m-%Y")
        url = f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={from_date}&to_date={to_date}"
        resp = s.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            return jsonify(resp.json())
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
