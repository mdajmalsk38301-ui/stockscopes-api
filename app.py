from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time

app = Flask(__name__)

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

def get_nse_session():
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    # Hit homepage first
    s.get("https://www.nseindia.com", timeout=15)
    time.sleep(2)
    # Hit the specific page to get cookies
    s.get("https://www.nseindia.com/companies-listing/corporate-filings-actions", timeout=15)
    time.sleep(1)
    return s

@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running", "version": "3.0"})

@app.route("/ipo-data")
def ipo_data():
    try:
        s = get_nse_session()
        result = []
        for status in ["current", "upcoming"]:
            try:
                url = f"https://www.nseindia.com/api/public-offer?category=ipo&status={status}"
                resp = s.get(url, timeout=15)
                if resp.status_code == 200 and resp.text.strip():
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data:
                            item["_status"] = "Upcoming" if status == "upcoming" else get_status(
                                item.get("openDate", ""),
                                item.get("closeDate", "")
                            )
                        result += data
                time.sleep(1)
            except Exception as e:
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

        # Try multiple NSE endpoints
        urls = [
            f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={from_date}&to_date={to_date}",
            f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date={from_date}&to_date={to_date}&csv=false",
        ]

        for url in urls:
            try:
                resp = s.get(url, timeout=15)
                if resp.status_code == 200 and resp.text.strip() and resp.text.strip() != "[]":
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        return jsonify(data)
                time.sleep(2)
            except Exception as e:
                continue

        # Fallback: BSE corporate actions
        return fetch_bse_corp_actions(from_date, to_date)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def fetch_bse_corp_actions(from_date, to_date):
    try:
        s = requests.Session()
        s.headers.update(BASE_HEADERS)
        s.headers.update({"Referer": "https://www.bseindia.com/"})
        s.get("https://www.bseindia.com", timeout=15)
        time.sleep(2)
        url = f"https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?scripcode=&segment=Equity&status=Active&from_date={from_date}&to_date={to_date}&mydata="
        resp = s.get(url, timeout=15)

if resp.status_code == 200 and resp.text.strip():
            raw = resp.json()
            # Handle both list and dict responses
            if isinstance(raw, list):
                data = raw
            elif isinstance(raw, dict):
                data = raw.get("Table", raw.get("data", []))
            else:
                data = []
            result = []
            for item in data:
                if isinstance(item, dict):
                    result.append({
                        "comp": item.get("LONG_NAME", item.get("comp", "")),
                        "symbol": item.get("SCRIP_CD", item.get("symbol", "")),
                        "subject": item.get("PURPOSE", item.get("subject", "")),
                        "exDate": item.get("EX_DATE", item.get("exDate", "")),
                        "recDate": item.get("REC_DT", item.get("recDate", "")),
                    })
            return jsonify(result)



        
        return jsonify({"error": "BSE also returned no data"}), 500
    except Exception as e:
        return jsonify({"error": "BSE fallback failed: " + str(e)}), 500

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
