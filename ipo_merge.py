from flask import Blueprint, jsonify
import requests, csv, io

ipo_bp = Blueprint('ipo_merge', __name__)
IPOALERTS_URL = "https://ipoalerts.in/api/ipos?status=upcoming"
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQp5PLGQI-7vv_DP38XBHeR5ZjawoeK_KC1Yqrw1Is26AUP6PC_l5poX8ufGpKVZSU7CTc3qM3YDs0s/pub?gid=0&single=true&output=csv"


def fetch_ipoalerts():
    try:
        return requests.get(IPOALERTS_URL, timeout=10).json().get('ipos', [])
    except Exception:
        return []


def fetch_sheet():
    try:
        r = requests.get(SHEET_CSV_URL, timeout=10)
        rows = list(csv.DictReader(io.StringIO(r.text)))
        result = []
        for row in rows:
            name = row.get('Company Name', '').strip()
            if not name:
                continue
            result.append({
                'company_name': name,
                'symbol': name,  # sheet has no ticker column, use name as key
                'openDate': row.get('Open Date', ''),
                'closeDate': row.get('Close Date', ''),
                'status': row.get('Status', ''),
                'gmp': row.get('GMP', ''),
                'blogLink': row.get('Blog Link', ''),
                'price': row.get('Price', ''),
                'lotSize': row.get('Lot Size', ''),
            })
        return result
    except Exception:
        return []


@ipo_bp.route('/api/ipo/merged', methods=['GET'])
def merged_ipos():
    a, b = fetch_ipoalerts(), fetch_sheet()
    merged = {}
    for ipo in a:
        key = (ipo.get('symbol') or ipo.get('company_name', '')).upper().strip()
        if key:
            merged[key] = ipo
    for ipo in b:
        key = (ipo.get('symbol') or ipo.get('company_name', '')).upper().strip()
        if key:
            merged.setdefault(key, ipo)
    return jsonify(list(merged.values()))
    
