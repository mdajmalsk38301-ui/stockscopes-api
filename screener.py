"""
StockScopes Screener blueprint.

Add to your Flask app (app.py):
    from screener import screener_bp
    app.register_blueprint(screener_bp)

Env vars (Render dashboard):
    SCREENER_KEY   secret for the refresh endpoint (any long random string)

Endpoints:
    GET /api/screener            filter + sort + paginate cached data.
                                 Legacy params (pe_max=..) still work, and the
                                 powerful way is repeated rules:
                                   ?f=rsi14<30&f=price>sma200&f=vol_ratio>=2
                                 right side can be a number, a field, or field*number
    GET /api/screener/fields     field catalogue (for building the UI)
    GET /api/screener/sectors    list of sectors (for the dropdown)
    GET /api/screener/refresh?key=SECRET&batch=25&fund=5
                                 refreshes price data for the next `batch` Nifty 500
                                 stocks (one bulk call) and fundamentals for `fund`
                                 stocks that are missing/older than 7 days. Call it
                                 every 2 minutes from cron-job.org.
"""
import json
import os
import threading
import time

from flask import Blueprint, jsonify, request

screener_bp = Blueprint("screener", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "screener_cache.json")
_lock = threading.Lock()

# ---- Universe: add/remove NSE symbols freely (without the .NS suffix) ----
UNIVERSE = """
RELIANCE TCS HDFCBANK INFY ICICIBANK HINDUNILVR ITC SBIN BHARTIARTL KOTAKBANK
LT AXISBANK BAJFINANCE ASIANPAINT MARUTI HCLTECH SUNPHARMA TITAN ULTRACEMCO
WIPRO NESTLEIND ONGC NTPC POWERGRID M&M TATAMOTORS TATASTEEL JSWSTEEL ADANIENT
ADANIPORTS COALINDIA BAJAJFINSV TECHM GRASIM HINDALCO DRREDDY CIPLA EICHERMOT
BRITANNIA APOLLOHOSP DIVISLAB HEROMOTOCO BPCL INDUSINDBK SBILIFE HDFCLIFE
TATACONSUM PIDILITIND DABUR GODREJCPG HAVELLS SIEMENS ABB BEL HAL IRCTC
ZOMATO DMART TRENT PNB BANKBARODA CANBK VEDL SAIL NMDC GAIL IOC
""".split()

# ---- Field catalogue: key -> (label, group) ----
FIELDS = {
    "price": ("Price", "Price"), "chg_1d": ("Change 1D %", "Price"),
    "chg_1w": ("Change 1W %", "Price"), "chg_1m": ("Change 1M %", "Price"),
    "chg_3m": ("Change 3M %", "Price"), "prev_close": ("Previous close", "Price"),
    "high20": ("20-day high (prior)", "Price"), "low20": ("20-day low (prior)", "Price"),
    "high52": ("52W high", "Price"), "low52": ("52W low", "Price"),
    "dist_high52": ("Distance from 52W high %", "Price"),
    "rsi14": ("RSI (14)", "Technical"), "rsi_prev": ("RSI previous day", "Technical"),
    "sma20": ("SMA 20", "Technical"), "sma50": ("SMA 50", "Technical"),
    "sma200": ("SMA 200", "Technical"),
    "sma20_prev": ("SMA 20 prev day", "Technical"),
    "sma50_prev": ("SMA 50 prev day", "Technical"),
    "sma200_prev": ("SMA 200 prev day", "Technical"),
    "macd_hist": ("MACD histogram", "Technical"),
    "macd_hist_prev": ("MACD histogram prev day", "Technical"),
    "atr_pct": ("ATR % of price", "Technical"),
    "bb_width": ("Bollinger width %", "Technical"),
    "volume": ("Volume", "Volume"), "vol_avg20": ("Avg volume 20D", "Volume"),
    "vol_ratio": ("Volume / 20D avg", "Volume"),
    "mcap_cr": ("Market cap Rs Cr", "Fundamental"), "pe": ("P/E", "Fundamental"),
    "pb": ("P/B", "Fundamental"), "roe": ("ROE %", "Fundamental"),
    "de": ("Debt/Equity", "Fundamental"), "margin": ("Net margin %", "Fundamental"),
    "rev_growth": ("Revenue growth %", "Fundamental"),
    "eps_growth": ("Earnings growth %", "Fundamental"),
    "div_yield": ("Dividend yield %", "Fundamental"),
    "pos52": ("52W position % (0 low, 100 high)", "Fundamental"),
}
NUM_FIELDS = list(FIELDS)


def _clean(v, nd=2):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v or v in (float("inf"), float("-inf")) else round(v, nd)


def compute_tech(df):
    """df: daily OHLCV DataFrame (Open/High/Low/Close/Volume), oldest first."""
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return {}
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    n = len(df)

    def pct(k):
        return (c.iloc[-1] / c.iloc[-1 - k] - 1) * 100 if n > k else None

    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, float("nan")))
    rsi = rsi.where(dn != 0, 100.0)

    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()

    sma = {w: c.rolling(w).mean() for w in (20, 50, 200)}
    tr = (h - l).combine((h - c.shift()).abs(), max).combine((l - c.shift()).abs(), max)
    atr = tr.rolling(14).mean()
    std20 = c.rolling(20).std()
    vavg = v.shift(1).rolling(20).mean()
    w52 = min(n, 252)
    hi52, lo52 = h.iloc[-w52:].max(), l.iloc[-w52:].min()

    out = {
        "price": c.iloc[-1], "prev_close": c.iloc[-2],
        "chg_1d": pct(1), "chg_1w": pct(5), "chg_1m": pct(21), "chg_3m": pct(63),
        "high20": h.iloc[-21:-1].max(), "low20": l.iloc[-21:-1].min(),
        "high52": hi52, "low52": lo52, "dist_high52": (c.iloc[-1] / hi52 - 1) * 100,
        "rsi14": rsi.iloc[-1], "rsi_prev": rsi.iloc[-2],
        "macd_hist": hist.iloc[-1], "macd_hist_prev": hist.iloc[-2],
        "atr_pct": atr.iloc[-1] / c.iloc[-1] * 100,
        "bb_width": 4 * std20.iloc[-1] / sma[20].iloc[-1] * 100,
        "volume": v.iloc[-1], "vol_avg20": vavg.iloc[-1],
        "vol_ratio": v.iloc[-1] / vavg.iloc[-1] if vavg.iloc[-1] else None,
    }
    for w in (20, 50, 200):
        out["sma%d" % w] = sma[w].iloc[-1]
        out["sma%d_prev" % w] = sma[w].iloc[-2]
    return {k: _clean(x, 0 if k in ("volume", "vol_avg20") else 2) for k, x in out.items()}


# ---- Rule engine (no eval; only field/number/field*number) ----
import re
_RULE = re.compile(r"^\s*([a-z0-9_]+)\s*(>=|<=|>|<|=)\s*(.+?)\s*$")
_OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b, "=": lambda a, b: abs(a - b) < 1e-9}


def _operand(text):
    """Returns a function row -> number|None, or None if text is invalid."""
    text = text.strip().lower()
    try:
        num = float(text)
        return lambda r: num
    except ValueError:
        pass
    if text in FIELDS:
        return lambda r: r.get(text)
    m = re.match(r"^([a-z0-9_]+)\s*\*\s*(-?[0-9.]+)$", text)
    if m and m.group(1) in FIELDS:
        fld, k = m.group(1), float(m.group(2))
        return lambda r: None if r.get(fld) is None else r[fld] * k
    return None


def parse_rules(raw):
    """Returns (rules, bad) where bad lists rule texts that could not be parsed."""
    rules, bad = [], []
    for text in raw[:8]:
        m = _RULE.match(text.lower())
        rhs = _operand(m.group(3)) if m else None
        if not m or m.group(1) not in FIELDS or rhs is None:
            bad.append(text[:60])
            continue
        rules.append((m.group(1), _OPS[m.group(2)], rhs))
    return rules, bad


def apply_rules(rows, rules):
    out = []
    for r in rows:
        ok = True
        for key, op, rhs in rules:
            a, b = r.get(key), rhs(r)
            if a is None or b is None or not op(a, b):  # missing data never matches
                ok = False
                break
        if ok:
            out.append(r)
    return out


def _load():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated": 0, "cursor": 0, "stocks": {}}


def _save(data):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE_FILE)


def _pct(v):
    return round(v * 100, 2) if isinstance(v, (int, float)) else None


def _num(v, nd=2):
    return round(v, nd) if isinstance(v, (int, float)) else None


# ---- Universe: Nifty 500 from the official index file, cached for 7 days ----
UNIVERSE_FILE = os.path.join(BASE_DIR, "universe.json")
UNIVERSE_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
]
UNIVERSE_TTL = 7 * 86400
FUND_TTL = 7 * 86400  # fundamentals change slowly; refresh weekly


def parse_universe_csv(text):
    import csv
    import io
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        sym = (r.get("Symbol") or "").strip()
        if sym:
            out.append({"symbol": sym, "name": (r.get("Company Name") or sym).strip(),
                        "industry": (r.get("Industry") or "Other").strip()})
    return out


def get_universe():
    """List of {symbol, name, industry}. Never raises; falls back to the built-in list."""
    cached = None
    try:
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if time.time() - cached.get("ts", 0) < UNIVERSE_TTL and cached.get("items"):
            return cached["items"]
    except Exception:
        pass
    try:
        import requests
        for url in UNIVERSE_URLS:
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                items = parse_universe_csv(r.text) if r.ok else []
                if len(items) >= 100:
                    with open(UNIVERSE_FILE, "w", encoding="utf-8") as f:
                        json.dump({"ts": time.time(), "items": items}, f)
                    return items
            except Exception:
                continue
    except Exception:
        pass
    if cached and cached.get("items"):
        return cached["items"]  # stale but real
    return [{"symbol": s, "name": s, "industry": "Other"} for s in UNIVERSE]


# ---- Data fetching ----
def split_tech(df, symbols):
    """Turn one multi-ticker yf.download frame into {symbol: technicals}."""
    import pandas as pd
    out = {}
    for s in symbols:
        try:
            sub = df[s + ".NS"] if isinstance(df.columns, pd.MultiIndex) else df
            out[s] = compute_tech(sub[["Open", "High", "Low", "Close", "Volume"]])
        except Exception:
            out[s] = {}
    return out


def download_tech(symbols):
    import yfinance as yf
    df = yf.download([s + ".NS" for s in symbols], period="1y", interval="1d",
                     group_by="ticker", auto_adjust=False, progress=False, threads=True)
    return split_tech(df, symbols)


def fetch_fund(symbol):
    import yfinance as yf
    info = yf.Ticker(symbol + ".NS").info or {}
    mcap = info.get("marketCap")
    de = info.get("debtToEquity")  # yfinance gives percent (45 => 0.45x)
    return {
        "mcap_cr": round(mcap / 1e7) if mcap else None,
        "pe": _num(info.get("trailingPE")),
        "pb": _num(info.get("priceToBook")),
        "roe": _pct(info.get("returnOnEquity")),
        "de": _num(de / 100) if de is not None else None,
        "margin": _pct(info.get("profitMargins")),
        "rev_growth": _pct(info.get("revenueGrowth")),
        "eps_growth": _pct(info.get("earningsGrowth")),
        "div_yield": _pct(info.get("trailingAnnualDividendYield")),
    }


def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(int(request.args.get(name, default)), hi))
    except ValueError:
        return default


@screener_bp.route("/api/screener/refresh")
def refresh():
    secret = os.environ.get("SCREENER_KEY", "")
    if not secret or request.args.get("key") != secret:
        return jsonify({"error": "forbidden"}), 403
    started = time.time()
    batch = _int_arg("batch", 25, 1, 60)   # stocks whose price data is refreshed per call
    nfund = _int_arg("fund", 5, 0, 15)     # stocks whose fundamentals are refreshed per call
    with _lock:
        data = _load()
        uni = get_universe()
        meta = {u["symbol"]: u for u in uni}
        cur = data.get("cursor", 0) % len(uni)
        symbols = [uni[(cur + i) % len(uni)]["symbol"] for i in range(min(batch, len(uni)))]

        failed = []
        try:
            tech = download_tech(symbols)
        except Exception:
            tech = {}
        for s in symbols:
            t = tech.get(s)
            if not t:
                failed.append(s)
                continue
            row = data["stocks"].setdefault(s, {"symbol": s, "name": meta[s]["name"],
                                                "sector": meta[s]["industry"]})
            row.update(t)
            lo, hi = row.get("low52"), row.get("high52")
            if lo is not None and hi and hi > lo:
                row["pos52"] = round((row["price"] - lo) / (hi - lo) * 100, 1)
        data["cursor"] = (cur + len(symbols)) % len(uni)

        # fundamentals: oldest/missing first, only while there is time budget left
        now = int(time.time())
        due = sorted((r for r in data["stocks"].values()
                      if now - r.get("fund_ts", 0) > FUND_TTL),
                     key=lambda r: r.get("fund_ts", 0))[:nfund]
        fund_done = 0
        for row in due:
            if time.time() - started > 18:
                break
            try:
                row.update(fetch_fund(row["symbol"]))
                row["fund_ts"] = now
                fund_done += 1
            except Exception:
                row["fund_ts"] = now - FUND_TTL + 6 * 3600  # retry in ~6 hours
            time.sleep(0.2)

        data["updated"] = now
        _save(data)
    return jsonify({
        "price_updated": len(symbols) - len(failed), "failed": failed,
        "fundamentals_updated": fund_done,
        "stocks_cached": len(data["stocks"]), "universe": len(uni),
        "with_fundamentals": sum(1 for r in data["stocks"].values() if r.get("fund_ts")),
        "seconds": round(time.time() - started, 1)})


def _f(name):
    v = request.args.get(name)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@screener_bp.route("/api/screener")
def screen():
    data = _load()
    rows = list(data["stocks"].values())

    # (query param, field, mode)  min_* means field >= value, max_* means <=
    rules = [("pe_min", "pe", "min"), ("pe_max", "pe", "max"),
             ("pb_max", "pb", "max"), ("roe_min", "roe", "min"),
             ("de_max", "de", "max"), ("margin_min", "margin", "min"),
             ("rev_growth_min", "rev_growth", "min"),
             ("eps_growth_min", "eps_growth", "min"),
             ("div_min", "div_yield", "min"),
             ("mcap_min", "mcap_cr", "min"), ("mcap_max", "mcap_cr", "max"),
             ("pos52_max", "pos52", "max"), ("pos52_min", "pos52", "min")]
    for param, field, mode in rules:
        val = _f(param)
        if val is None:
            continue
        # a stock with missing data never passes a filter on that field
        if mode == "min":
            rows = [r for r in rows if r.get(field) is not None and r[field] >= val]
        else:
            rows = [r for r in rows if r.get(field) is not None and r[field] <= val]

    rules, bad = parse_rules(request.args.getlist("f"))
    if bad:
        return jsonify({"error": "Invalid rule: " + "; ".join(bad)}), 400
    rows = apply_rules(rows, rules)

    sector = request.args.get("sector")
    if sector:
        rows = [r for r in rows if r["sector"] == sector]
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [r for r in rows if q in r["symbol"].lower() or q in r["name"].lower()]

    sort = request.args.get("sort", "mcap_cr")
    if sort not in NUM_FIELDS + ["symbol", "name"]:
        sort = "mcap_cr"
    desc = request.args.get("order", "desc") != "asc"
    have = [r for r in rows if r.get(sort) is not None]
    missing = [r for r in rows if r.get(sort) is None]
    have.sort(key=lambda r: r[sort], reverse=desc)
    rows = have + missing

    total = len(rows)
    limit = max(1, min(int(request.args.get("limit", 50)), 200))
    offset = max(0, int(request.args.get("offset", 0)))
    resp = jsonify({"total": total, "updated": data.get("updated", 0),
                    "rows": rows[offset:offset + limit]})
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@screener_bp.route("/api/screener/sectors")
def sectors():
    data = _load()
    return jsonify(sorted({r["sector"] for r in data["stocks"].values()}))


@screener_bp.route("/api/screener/fields")
def fields():
    return jsonify([{"key": k, "label": v[0], "group": v[1]} for k, v in FIELDS.items()])
