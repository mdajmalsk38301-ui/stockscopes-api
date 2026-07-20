from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time

app = Flask(__name__)

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

if __name__ == "__main__":
    app.run(debug=True)
