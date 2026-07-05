from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/"
}

def get_nse_session():
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
    return s

@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running"})

@app.route("/ipo-data")
def ipo_data():
    try:
        s = get_nse_session()
        current = s.get("https://www.nseindia.com/api/public-offer?category=ipo&status=current", headers=HEADERS, timeout=10).json()
        upcoming = s.get("https://www.nseindia.com/api/public-offer?category=ipo&status=upcoming", headers=HEADERS, timeout=10).json()
        result = []
        if isinstance(current, list):
            for i in current:
                i["_status"] = get_status(i.get("openDate",""), i.get("closeDate",""))
            result += current
        if isinstance(upcoming, list):
            for i in upcoming:
                i["_status"] = "Upcoming"
            result += upcoming
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
        data = s.get(url, headers=HEADERS, timeout=10).json()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_status(open_date, close_date):
    try:
        today = datetime.now().date()
        o = datetime.strptime(open_date[:10], "%Y-%m-%d").date()
        c = datetime.strptime(close_date[:10], "%Y-%m-%d").date()
        if today < o: return "Upcoming"
        if today <= c: return "Open"
        return "Closed"
    except:
        return "Closed"

if __name__ == "__main__":
    app.run(debug=True)
