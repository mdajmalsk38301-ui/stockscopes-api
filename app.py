from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import time

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"status": "StockScopes API running", "version": "8.0"})

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

        # Pass datetime objects directly
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
            result.append({
                "comp": (item.get("long_name") or item.get("LONG_NAME") or
                        item.get("scrip_cd") or item.get("SCRIP_CD") or ""),
                "subject": (item.get("purpose") or item.get("PURPOSE") or
                           item.get("subject") or ""),
                "exDate": (item.get("ex_date") or item.get("EX_DATE") or
                          item.get("exDate") or ""),
                "recDate": (item.get("record_date") or item.get("REC_DT") or
                           item.get("recDate") or ""),
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
