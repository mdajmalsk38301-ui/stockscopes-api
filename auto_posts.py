"""
auto_posts.py - runs all StockScopes auto-post jobs inside Flask (no n8n needed).

Needs these files in the same repo: market_wrap.py, fii_dii.py,
results_calendar.py, stock_spotlight.py (and your existing /api/ipo/merged
and /api/corp-actions/weekly routes).

Register in your main app:
    from auto_posts import auto_bp
    app.register_blueprint(auto_bp)

Trigger a job (from cron-job.org):
    https://YOUR-APP.onrender.com/api/auto/market-wrap?key=YOUR_AUTO_KEY
Jobs: daily, market-wrap, fii-dii, results, weekly, ipo-recap, spotlight, telegram
Check what happened:  /api/auto/status?key=YOUR_AUTO_KEY

Render environment variables:
    AUTO_KEY, GEMINI_API_KEY, WP_URL (https://www.stockscopes.in),
    WP_USER, WP_APP_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL (@yourchannel)
Optional: TELEGRAM_OWNER_CHAT_ID (private "draft ready" alerts), GEMINI_MODEL
"""
import hmac
import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Blueprint, jsonify, request, current_app

from market_wrap import _build as _market
from fii_dii import _build as _fiidii
from results_calendar import _build as _results
from stock_spotlight import _pick_and_build as _spot

auto_bp = Blueprint("auto_posts", __name__)

IST = timezone(timedelta(hours=5, minutes=30))
_status = {"recent": []}
_lock = threading.Lock()

OUT_SPEC = (
    'Return ONLY valid JSON: {"title": "...", "excerpt": "max 160 chars", "html": "..."} '
    "Use clean HTML (h2, p, ul, table), no markdown."
)
RULES = (
    "Rules: neutral tone, no buy/sell/hold advice, no predictions, never invent numbers, "
    "dates, companies or reasons. Use ONLY the data given."
)
BANNED = re.compile(
    r"\b(undervalued|overvalued|bargain|strong buy|target price|multibagger)\b", re.I
)
DISCLAIMER = (
    "<hr><p><em>Disclaimer: This content is AI-assisted and for informational purposes only. "
    "It is not investment advice. StockScopes is not a SEBI-registered investment adviser. "
    "Data comes from NSE, Yahoo Finance and other public sources and may be delayed or "
    "incomplete; verify with official sources before acting.</em></p>"
)


# ---------- helpers ----------
def _log(job, state, detail=""):
    with _lock:
        _status[job] = {"state": state, "detail": str(detail)[:300],
                        "time": datetime.now(IST).isoformat(timespec="seconds")}
        _status["recent"] = ([f"{datetime.now(IST).strftime('%d %b %H:%M')} {job}: {state}"]
                             + _status["recent"])[:20]


def _authed():
    expected = os.environ.get("AUTO_KEY", "")
    given = request.args.get("key", "")
    return bool(expected) and hmac.compare_digest(expected, given)


def _internal(app, path):
    try:
        r = app.test_client().get(path)
        return r.get_json() if r.status_code == 200 else None
    except Exception:
        return None


def _today():
    return datetime.now(IST).date()


def _nice(d):
    return d.strftime("%d %B %Y").lstrip("0")


def _gemini(prompt):
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last = None
    for _ in range(3):
        try:
            r = requests.post(
                url, timeout=120,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json",
                                           "temperature": 0.2}},
            )
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except Exception as e:
            last = e
            time.sleep(8)
    raise RuntimeError(f"Gemini failed: {last}")


def _wp(path, method="get", **kw):
    base = os.environ["WP_URL"].rstrip("/") + "/wp-json/wp/v2"
    auth = (os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])
    return requests.request(method, base + path, auth=auth, timeout=60, **kw)


def _tg(chat_id, text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
    )
    r.raise_for_status()


def _clean(s):
    s = re.sub(r"<[^>]*>", " ", str(s or ""))
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = (s.replace("&nbsp;", " ").replace("&quot;", '"').replace("&lt;", "<")
         .replace("&gt;", ">").replace("&amp;", "&"))
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- prompt builders (return a prompt string, or None to skip) ----------
def _p_daily(app):
    ipo = _internal(app, "/api/ipo/merged")
    corp = _internal(app, "/api/corp-actions/weekly")
    if not ipo and not corp:
        return None
    return (
        "You are a market content writer for StockScopes.in, an Indian stock market site. "
        f"Today is {_nice(_today())}.\nWrite ONE daily post.\n\n"
        f"IPO DATA:\n{json.dumps(ipo)[:9000]}\n\nCORPORATE ACTIONS DATA:\n{json.dumps(corp)[:9000]}\n\n"
        "Structure: 1) 2-sentence intro 2) IPO Watch: open, upcoming and listing-soon IPOs with "
        "dates, price band, lot size, GMP if present 3) Corporate Actions: dividends, splits, "
        "bonus, buybacks, rights with record/ex dates as an HTML table 4) Key Takeaways: 3 neutral bullets.\n"
        f"{RULES}\n{OUT_SPEC}"
    )


def _p_market(app):
    d = _market()
    if not d.get("is_trading_day"):
        return None
    return (
        "You are a market content writer for StockScopes.in.\n"
        f"Write the end-of-day market wrap for {_nice(datetime.strptime(d['market_date'], '%Y-%m-%d'))}. "
        "If you do not know why the market moved, do not guess.\n\n"
        f"DATA:\n{json.dumps(d)[:9000]}\n\n"
        "Structure: 1) 2-sentence headline: how Nifty 50, Sensex, Bank Nifty closed (points and %) "
        "2) Index table 3) Sector performance, best and worst 4) Top gainers and losers tables "
        "5) Breadth: advances vs declines 6) Quick takeaway: 2 neutral sentences.\n"
        f"{RULES}\n{OUT_SPEC}"
    )


def _p_fiidii(app):
    d = _fiidii()
    if not d.get("is_trading_day"):
        return None
    return (
        "You are a market content writer for StockScopes.in.\n"
        f"Write a short FII/DII activity post for {_nice(datetime.strptime(d['data_date'], '%Y-%m-%d'))} "
        "(values in INR crore, cash market). Never explain WHY institutions bought or sold.\n\n"
        f"DATA:\n{json.dumps(d)}\n\n"
        "Structure: 1) 2-sentence summary: net buyers or sellers and by how much 2) table: category, "
        "gross buy, gross sell, net 3) 2-3 sentences explaining in plain words what FII and DII net "
        "flows represent, without predicting the market 4) 1 neutral takeaway sentence. "
        "Positive net = net buying, negative = net selling.\n"
        f"{RULES}\n{OUT_SPEC}"
    )


def _p_results(app):
    d = _results()
    if not d.get("today") and not d.get("upcoming"):
        return None
    payload = {"today": d["today"], "upcoming": d["upcoming"], "recently_held": d["recently_held"]}
    return (
        "You are a market content writer for StockScopes.in.\n"
        f"Write the quarterly results calendar post for {_nice(_today())}. Data is NSE board meeting "
        "dates for financial results. Do not mention revenue, profit or expectations.\n\n"
        f"DATA:\n{json.dumps(payload)[:10000]}\n\n"
        "Structure: 1) 2-sentence intro with counts 2) Results Today table 3) Results This Week table "
        "by date 4) Recently Held Meetings: names only, note their figures are not covered here "
        "5) 1 sentence: dates can be rescheduled.\n"
        f"{RULES}\n{OUT_SPEC}"
    )


def _p_weekly(app):
    ipo = _internal(app, "/api/ipo/merged")
    corp = _internal(app, "/api/corp-actions/weekly")
    try:
        r = _results()
        res = {"today": r["today"], "upcoming": r["upcoming"]}
    except Exception:
        res = None
    parts = []
    if ipo:
        parts.append("IPO DATA:\n" + json.dumps(ipo)[:7000])
    if corp:
        parts.append("CORPORATE ACTIONS DATA:\n" + json.dumps(corp)[:7000])
    if res:
        parts.append("RESULTS BOARD MEETINGS DATA:\n" + json.dumps(res)[:6000])
    if not parts:
        return None
    mon = _today() + timedelta(days=1)
    fri = _today() + timedelta(days=5)
    return (
        "You are a market content writer for StockScopes.in.\n"
        f"Write the Weekly Market Outlook for the week {_nice(mon)} to {_nice(fri)}. Do not invent "
        "macro events, economic data, central bank meetings or global events. Skip sections with no data.\n\n"
        + "\n\n".join(parts) +
        "\n\nStructure: 1) 2-sentence intro 2) IPOs This Week table 3) Corporate Actions This Week "
        "table sorted by date 4) Results This Week table 5) Week at a Glance: Monday to Friday list "
        "of only the events above 6) 1 sentence: dates can change.\n"
        f"{RULES}\n{OUT_SPEC}"
    )


def _p_iporecap(app):
    ipo = _internal(app, "/api/ipo/merged")
    if not ipo:
        return None
    t = _today().isoformat()
    return (
        "You are a market content writer for StockScopes.in.\n"
        f"Today is {t}. From the IPO data find ONLY IPOs whose listing date is TODAY and that already "
        'have a listing price. If none, return exactly {"skip": true}.\n\n'
        f"IPO DATA:\n{json.dumps(ipo)[:14000]}\n\n"
        "If there are listings: 1) 2-sentence intro 2) per IPO a small table: issue price, listing price, "
        "gain/loss % vs issue price (only if both prices exist, calculate carefully, 2 decimals), "
        "exchange, subscription and GMP only if present 3) 1 neutral sentence that listing gains do not "
        "predict future performance.\n"
        f"{RULES}\n{OUT_SPEC} (or the skip JSON)"
    )


def _p_spotlight(app):
    d = _spot()
    d["as_of"] = _today().isoformat()
    return (
        "You are a market content writer for StockScopes.in.\n"
        f"Write an educational 'Stock Spotlight' fundamentals snapshot for {d['name']} (NSE: {d['symbol']}). "
        "The company was chosen by a fixed rotation through the Nifty 50, NOT because it is a good or bad "
        "investment. Omit any null metric. Never say cheap, expensive, undervalued or overvalued.\n\n"
        f"DATA:\n{json.dumps(d)}\n\n"
        "Structure: 1) About the Company: 2-3 sentences paraphrasing the summary, plus sector 2) Key "
        "Numbers table: metric, value, one-line plain meaning (price, market cap in INR crore, P/E, P/B, "
        "ROE %, margins %, growth %, debt-to-equity, 52-week range) 3) How to Read These Numbers: 3 bullets "
        "about comparing with peers and history 4) 1 sentence: educational snapshot chosen by rotation, "
        "not a recommendation. Title must start with 'Stock Spotlight:'.\n"
        f"{RULES}\n{OUT_SPEC}"
    )


JOBS = {
    "daily": _p_daily,
    "market-wrap": _p_market,
    "fii-dii": _p_fiidii,
    "results": _p_results,
    "weekly": _p_weekly,
    "ipo-recap": _p_iporecap,
    "spotlight": _p_spotlight,
}


# ---------- job runners ----------
def _run_post_job(app, job):
    prompt = JOBS[job](app)
    if not prompt:
        _log(job, "skipped", "no data or not a trading day")
        return
    out = _gemini(prompt)
    if out.get("skip"):
        _log(job, "skipped", "nothing to report today")
        return
    title, body = out.get("title"), out.get("html")
    if not title or not body:
        raise RuntimeError("Gemini output missing title/html")
    if BANNED.search(title) or BANNED.search(body):
        raise RuntimeError("Advice-like wording detected; not posted")
    r = _wp("/posts", "post", json={
        "title": title, "content": body + DISCLAIMER,
        "excerpt": (out.get("excerpt") or "")[:160], "status": "draft",
    })
    r.raise_for_status()
    post_id = r.json().get("id")
    _log(job, "draft created", f"post {post_id}: {title}")
    owner = os.environ.get("TELEGRAM_OWNER_CHAT_ID")
    if owner:
        edit = os.environ["WP_URL"].rstrip("/") + f"/wp-admin/post.php?post={post_id}&action=edit"
        try:
            _tg(owner, f"Draft ready: <b>{_clean(title)}</b>\n{edit}")
        except Exception:
            pass


def _run_telegram(app):
    channel = os.environ["TELEGRAM_CHANNEL"]
    slug = "tg-shared"
    first = False
    r = _wp("/tags", params={"slug": slug})
    r.raise_for_status()
    found = r.json()
    if found:
        tag_id = found[0]["id"]
    else:
        c = _wp("/tags", "post", json={"name": slug, "slug": slug})
        c.raise_for_status()
        tag_id, first = c.json()["id"], True  # first run: mark existing posts, share nothing
    pr = _wp("/posts", params={"status": "publish", "per_page": 10, "orderby": "date",
                               "order": "desc", "_fields": "id,title,link,excerpt,tags"})
    pr.raise_for_status()
    sent = 0
    for p in reversed(pr.json()):
        tags = p.get("tags", [])
        if tag_id in tags:
            continue
        if not first:
            ex = _clean(p["excerpt"]["rendered"])
            ex = ex if len(ex) <= 220 else ex[:217].rstrip() + "..."
            text = (f"<b>{_clean(p['title']['rendered'])}</b>\n\n{ex}\n\n{p['link']}\n\n"
                    "#StockScopes #IndianStockMarket #Nifty")
            _tg(channel, text)
            sent += 1
        _wp(f"/posts/{p['id']}", "post", json={"tags": tags + [tag_id]}).raise_for_status()
    _log("telegram", "ok", f"shared {sent}" + (" (first run: marked existing posts)" if first else ""))


def _worker(app, job):
    _log(job, "running")
    try:
        if job == "telegram":
            _run_telegram(app)
        else:
            _run_post_job(app, job)
    except Exception as e:
        _log(job, "FAILED", e)


@auto_bp.route("/api/auto/<job>")
def auto(job):
    if not _authed():
        return jsonify(error="unauthorized"), 401
    if job == "status":
        with _lock:
            return jsonify(_status)
    if job not in JOBS and job != "telegram":
        return jsonify(error="unknown job", jobs=list(JOBS) + ["telegram"]), 404
    app = current_app._get_current_object()
    # Run in background so cron-job.org gets an instant reply (its timeout is short)
    threading.Thread(target=_worker, args=(app, job), daemon=True).start()
    return jsonify(started=job), 202
