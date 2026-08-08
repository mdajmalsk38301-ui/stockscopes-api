
from flask import Blueprint, jsonify
from datetime import datetime, timedelta
import requests, csv, io

corp_bp = Blueprint('corp_actions_merge', __name__)

# Paste your published Google Sheet CSV link here (corp actions tab)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQp5PLGQI-7vv_DP38XBHeR5ZjawoeK_KC1Yqrw1Is26AUP6PC_l5poX8ufGpKVZSU7CTc3qM3YDs0s/pubhtml?gid=297455982&single=true"


def fetch_bse_actions():
    try:
        from bse import BSE

        today = datetime.now()
        future = today + timedelta(days=7)  # weekly window

        b = BSE(download_folder="/tmp/")
        actions = b.actions(
            segment="equity",
            from_date=today,
            to_date=future
        )
        b.exit()

        if not actions:
            return []

        result = []
        for item in actions:
            if not isinstance(item, dict):
                continue
            result.append({
                "comp": item.get("long_name", ""),
                "subject": item.get("Purpose", ""),
                "exDate": item.get("Ex_date", ""),
                "recDate": item.get("RD_Date", ""),
            })
        return result
    except Exception:
        return []


def fetch_sheet_actions():
    try:
        r = requests.get(SHEET_CSV_URL, timeout=10)
        rows = list(csv.DictReader(io.StringIO(r.text)))
        result = []
        for row in rows:
            result.append({
                "comp": row.get("Company") or row.get("comp", ""),
                "subject": row.get("Subject") or row.get("subject", ""),
                "exDate": row.get("ExDate") or row.get("exDate", ""),
                "recDate": row.get("RecDate") or row.get("recDate", ""),
            })
        return result
    except Exception:
        return []


@corp_bp.route('/api/corp-actions/weekly', methods=['GET'])
def merged_corp_actions():
    a = fetch_bse_actions()
    b = fetch_sheet_actions()

    merged = {}
    for item in a:
        key = (item.get("comp", "") + item.get("exDate", "")).upper()
        if key.strip():
            merged[key] = item
    for item in b:
        key = (item.get("comp", "") + item.get("exDate", "")).upper()
        if key.strip():
            merged.setdefault(key, item)

    today = datetime.now()
    week_end = today + timedelta(days=7)

    return jsonify({
        "week_start": today.strftime("%Y-%m-%d"),
        "week_end": week_end.strftime("%Y-%m-%d"),
        "corp_actions": list(merged.values()),
    })
