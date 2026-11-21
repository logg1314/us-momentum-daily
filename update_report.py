import glob
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_KEY = os.environ.get("MASSIVE_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
BASE_URL = os.environ.get("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/")
HTML_FILE = "index.html"
MAX_HISTORY_DAYS = 30

INDICES = [
    ("SPY", "標普500"),
    ("QQQ", "納指100"),
    ("IWM", "羅素2000"),
    ("RSP", "等權標普"),
]

SECTORS = [
    ("XLE", "能源"), ("XLU", "公用事業"), ("XLP", "必需消費"),
    ("XLF", "金融"), ("XLRE", "房地產"), ("XLK", "科技"),
    ("XLV", "醫療保健"), ("XLI", "工業"), ("XLB", "原材料"),
    ("XLC", "通訊服務"), ("XLY", "非必需消費"),
]

WATCHLIST = ["PANW", "WMT", "COST", "DLTR", "XOM", "LLY", "ABBV", "UNH", "NVDA", "AMD"]


def http_json(url, payload=None, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "USMomentumHub/2.0"})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(15)
                continue
            raise
    return {}


def get_bars(ticker, days=120):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=500&apiKey={API_KEY}"
    )
    return http_json(url).get("results", [])


def format_candles(bars, limit=60):
    out = []
    for b in bars[-limit:]:
        d_str = datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%m-%d")
        out.append({
            "t": d_str,
            "o": round(float(b["o"]), 2),
            "h": round(float(b["h"]), 2),
            "l": round(float(b["l"]), 2),
            "c": round(float(b["c"]), 2),
            "v": int(b.get("v", 0)),
        })
    return out


def calc_rsi14(closes):
    if len(closes) < 16:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:14]) / 14.0
    avg_loss = sum(losses[:14]) / 14.0
    for i in range(14, len(gains)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14.0
        avg_loss = (avg_loss * 13 + losses[i]) / 14.0
    if avg_loss == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + (avg_gain / avg_loss))), 1)


def calc_atr14(bars):
    if len(bars) < 15:
        return 1.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-14:]) / 14.0


def calc_metrics(bars):
    closes = [b["c"] for b in bars]
    c = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else c
    c20 = closes[-21] if len(closes) >= 21 else closes[0]
    chg1d = ((c - prev) / prev) * 100.0
    chg20d = ((c - c20) / c20) * 100.0
    ma50 = sum(closes[-50:]) / min(len(closes), 50)
    atr14 = calc_atr14(bars)
    atr_dist50 = round((c - ma50) / atr14, 2) if atr14 > 0 else 0.0
    low10 = min(b["l"] for b in bars[-10:])
    stop = round(min(low10, c - 1.5 * atr14), 2)
    return {
        "close": round(c, 2),
        "chg1d": round(chg1d, 2),
        "chg20d": round(chg20d, 2),
        "rsi14": calc_rsi14(closes),
        "atr_dist50": atr_dist50,
        "stop": stop,
    }


def fetch_breadth(date_t0, date_t1):
    try:
        u0 = f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{date_t0}?adjusted=true&apiKey={API_KEY}"
        u1 = f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{date_t1}?adjusted=true&apiKey={API_KEY}"
        r0 = http_json(u0).get("results", [])
        r1 = http_json(u1).get("results", [])
        prev_map = {x["T"]: x["c"] for x in r1 if "T" in x and "c" in x}
        up4 = dn4 = adv = total = 0
        for row in r0:
            sym = row.get("T", "")
            c, v, pc = row.get("c", 0), row.get("v", 0), prev_map.get(sym, 0)
            if c >= 5 and v >= 100000 and pc > 0:
                total += 1
                pct = (c - pc) / pc
                if pct > 0:
                    adv += 1
                if pct >= 0.04:
                    up4 += 1
                elif pct <= -0.04:
                    dn4 += 1
        adv_pct = round((adv / total) * 100.0, 1) if total > 0 else 50.0
        return {"up4": up4, "dn4": dn4, "adv_pct": adv_pct, "total_scanned": total}
    except Exception:
        return {"up4": 0, "dn4": 0, "adv_pct": 50.0, "total_scanned": 0}


def calc_score(up4, dn4, adv_pct, spy_atr):
    ratio_score = (up4 / (up4 + dn4)) * 40 if (up4 + dn4) > 0 else 20
    adv_score = max(0, min(100, adv_pct)) * 0.35
    atr_score = max(0, min(25, ((spy_atr + 2.5) / 5) * 25))
    return round(ratio_score + adv_score + atr_score)


def build_ai_brief(trade_date, indices, breadth, sectors):
    spy = indices[0]
    top_sec = sorted(sectors, key=lambda x: x["rel20"], reverse=True)[:2]
    bot_sec = sorted(sectors, key=lambda x: x["rel20"])[:2]
    fallback = {
        "tldr": f"{trade_date} 收市：SPY {spy['chg1d']:+.2f}%（20D {spy['chg20d']:+.2f}%），升4%+ {breadth['up4']} 家 vs 跌4%+ {breadth['dn4']} 家。",
        "bullets": [
            f"趨勢與均線：SPY 收 ${spy['close']}，距 50MA {spy['atr_dist50']} ATR；QQQ 20D {indices[1]['chg20d']:+.2f}%、IWM 20D {indices[2]['chg20d']:+.2f}%。",
            f"強勢板塊：{top_sec[0]['name']}({top_sec[0]['ticker']} REL20 {top_sec[0]['rel20']:+.1f}%) 與 {top_sec[1]['name']}({top_sec[1]['ticker']} REL20 {top_sec[1]['rel20']:+.1f}%) 跑贏。",
            f"弱勢板塊：{bot_sec[0]['name']}({bot_sec[0]['ticker']}) 及 {bot_sec[1]['name']}({bot_sec[1]['ticker']}) 偏弱，優先看多日趨勢再出手。",
        ],
    }
    if not GEMINI_KEY:
        return fallback
    try:
        prompt = (
            "你係香港資深美股動能交易員。請嚴格根據以下已核實數據，用貼地香港廣東話輸出純 JSON "
            '{"tldr": "一句35字內總定調", "bullets": ["重點1", "重點2", "重點3"]}，嚴禁捏造數據：\n'
            + json.dumps({"date": trade_date, "indices": indices, "breadth": breadth, "top_sectors": top_sec, "bottom_sectors": bot_sec}, ensure_ascii=False)
        )
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        g_res = http_json(g_url, {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        })
        parsed = json.loads(g_res["candidates"][0]["content"]["parts"][0]["text"])
        if "tldr" in parsed and isinstance(parsed.get("bullets"), list):
            return parsed
    except Exception:
        pass
    return fallback


def scan_pdfs():
    pdfs = []
    for path in sorted(glob.glob("reports/*.pdf"), reverse=True):
        fname = os.path.basename(path)
        pdfs.append({
            "name": fname,
            "date": fname[:10] if len(fname) >= 10 else "",
            "url": f"reports/{fname}",
        })
    return pdfs


def extract_existing_hub(html):
    s_tag, e_tag = "/*__HUB_DATA_START__*/", "/*__HUB_DATA_END__*/"
    if s_tag in html and e_tag in html:
        raw = html.split(s_tag)[1].split(e_tag)[0].strip()
        prefix = "const HUB_DATA ="
        idx = raw.find(prefix)
        if idx != -1:
            js_obj = raw[idx + len(prefix):].strip()
            if js_obj.endswith(";"):
                js_obj = js_obj[:-1]
            return json.loads(js_obj)
    return {"reports_by_date": {}, "trend_history": [], "candles": {}, "pdf_reports": []}


def main():
    if not API_KEY:
        raise SystemExit("ERROR: MASSIVE_API_KEY 未設定！")

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    hub = extract_existing_hub(html)

    spy_bars = get_bars("SPY", days=130)
    if len(spy_bars) < 2:
        raise SystemExit("ERROR: 無法讀取 SPY 數據。")

    trade_date = datetime.fromtimestamp(spy_bars[-1]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    prev_date = datetime.fromtimestamp(spy_bars[-2]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")

    candles_map = {"SPY": format_candles(spy_bars)}
    spy_m = calc_metrics(spy_bars)
    indices_out = [{"ticker": "SPY", "name": "標普500", "close": spy_m["close"], "chg1d": spy_m["chg1d"], "chg20d": spy_m["chg20d"], "atr_dist50": spy_m["atr_dist50"]}]

    for sym, name in INDICES[1:]:
        b = get_bars(sym, days=130)
        candles_map[sym] = format_candles(b)
        m = calc_metrics(b)
        indices_out.append({"ticker": sym, "name": name, "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"], "atr_dist50": m["atr_dist50"]})

    sectors_out = []
    for sym, name in SECTORS:
        b = get_bars(sym, days=130)
        candles_map[sym] = format_candles(b)
        m = calc_metrics(b)
        sectors_out.append({
            "ticker": sym, "name": name, "close": m["close"],
            "chg1d": m["chg1d"], "chg20d": m["chg20d"],
            "rel20": round(m["chg20d"] - spy_m["chg20d"], 2), "rsi14": m["rsi14"]
        })

    watchlist_out = []
    for sym in WATCHLIST:
        b = get_bars(sym, days=130)
        candles_map[sym] = format_candles(b)
        m = calc_metrics(b)
        rel = m["chg20d"] - spy_m["chg20d"]
        if rel >= 4 and m["atr_dist50"] >= 0:
            grp, note = "LEADER", "跑贏大市且企穩 50MA 上方"
        elif m["atr_dist50"] < -1.2 or m["rsi14"] < 35:
            grp, note = "AVOID", "弱勢跌穿均線，暫時避開"
        else:
            grp, note = "WATCH", "觀察均線承接與縮量整理"
        watchlist_out.append({
            "ticker": sym, "group": grp, "close": m["close"],
            "chg1d": m["chg1d"], "chg20d": m["chg20d"], "rsi14": m["rsi14"],
            "stop": m["stop"], "note": note
        })

    breadth_out = fetch_breadth(trade_date, prev_date)
    ai_brief = build_ai_brief(trade_date, indices_out, breadth_out, sectors_out)
    hkt_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M HKT")

    reports_by_date = hub.get("reports_by_date") or {}
    reports_by_date[trade_date] = {
        "trade_date": trade_date,
        "updated_at": f"自動更新於 {hkt_now}",
        "data_source": "Massive API",
        "indices": indices_out,
        "breadth": breadth_out,
        "ai_brief": ai_brief,
        "sectors": sectors_out,
        "watchlist": watchlist_out,
    }
    sorted_dates = sorted(reports_by_date.keys())[-MAX_HISTORY_DAYS:]
    reports_by_date = {k: reports_by_date[k] for k in sorted_dates}

    sc = calc_score(breadth_out["up4"], breadth_out["dn4"], breadth_out["adv_pct"], spy_m["atr_dist50"])
    hist = [x for x in (hub.get("trend_history") or []) if x.get("date") != trade_date[5:]]
    hist.append({
        "date": trade_date[5:],
        "score": sc,
        "spy_chg": spy_m["chg20d"],
        "up4": breadth_out["up4"],
        "dn4": breadth_out["dn4"],
    })
    hist = hist[-MAX_HISTORY_DAYS:]

    hub_out = {
        "latest_date": sorted_dates[-1],
        "reports_by_date": reports_by_date,
        "trend_history": hist,
        "candles": candles_map,
        "pdf_reports": scan_pdfs(),
    }

    s_tag, e_tag = "/*__HUB_DATA_START__*/", "/*__HUB_DATA_END__*/"
    injected = f"{s_tag}\nconst HUB_DATA = {json.dumps(hub_out, ensure_ascii=False, indent=2)};\n{e_tag}"
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html.split(s_tag)[0] + injected + html.split(e_tag)[1])

    print("SUCCESS: Hub updated with history, candlesticks, and PDFs.")


if __name__ == "__main__":
    main()
