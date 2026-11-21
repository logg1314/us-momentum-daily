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
        req = urllib.request.Request(url, headers={"User-Agent": "USMomentumBot/1.0"})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                print("Hit rate limit (429), sleeping 15s...")
                time.sleep(15)
                continue
            print(f"HTTP Error {e.code} for {url.split('?')[0]}")
            raise
    return {}


def get_bars(ticker, days=120):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=500&apiKey={API_KEY}"
    )
    res = http_json(url)
    return res.get("results", [])


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
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


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
            c = row.get("c", 0)
            v = row.get("v", 0)
            pc = prev_map.get(sym, 0)
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
    except Exception as e:
        print(f"Breadth fallback triggered: {e}")
        return {"up4": 0, "dn4": 0, "adv_pct": 50.0, "total_scanned": 0}


def build_ai_brief(trade_date, indices, breadth, sectors):
    spy = indices[0]
    top_sec = sorted(sectors, key=lambda x: x["rel20"], reverse=True)[:2]
    bot_sec = sorted(sectors, key=lambda x: x["rel20"])[:2]
    fallback = {
        "tldr": f"{trade_date} 美股收市：SPY {spy['chg1d']:+.2f}%（${spy['close']}），全場升4%+ {breadth['up4']} 家 vs 跌4%+ {breadth['dn4']} 家。",
        "bullets": [
            f"大盤走勢：SPY {spy['chg1d']:+.2f}%、QQQ {indices[1]['chg1d']:+.2f}%、IWM {indices[2]['chg1d']:+.2f}%，SPY 距 50MA {spy['atr_dist50']} ATR。",
            f"強勢板塊：{top_sec[0]['name']}({top_sec[0]['ticker']} REL20 {top_sec[0]['rel20']:+.1f}%) 與 {top_sec[1]['name']}({top_sec[1]['ticker']} REL20 {top_sec[1]['rel20']:+.1f}%) 領先。",
            f"弱勢板塊：{bot_sec[0]['name']}({bot_sec[0]['ticker']}) 及 {bot_sec[1]['name']}({bot_sec[1]['ticker']}) 相對落後，操作宜嚴控止損。",
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
        txt = g_res["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(txt)
        if "tldr" in parsed and isinstance(parsed.get("bullets"), list):
            return parsed
    except Exception as e:
        print(f"Gemini skipped, using rule-based brief: {e}")
    return fallback


def main():
    if not API_KEY:
        raise SystemExit("ERROR: MASSIVE_API_KEY 未設定！請先喺 GitHub Settings -> Secrets 加入。")

    # 先拉 SPY 鎖定最新交易日與前一個交易日
    spy_bars = get_bars("SPY")
    if len(spy_bars) < 2:
        raise SystemExit("ERROR: 無法從 Massive API 讀取 SPY 數據，請檢查 API Key 或 Plan 權限。")

    trade_date = datetime.fromtimestamp(spy_bars[-1]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    prev_date = datetime.fromtimestamp(spy_bars[-2]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    print(f"Verified latest US trade date: {trade_date} (prev: {prev_date})")

    spy_m = calc_metrics(spy_bars)
    indices_out = [{"ticker": "SPY", "name": "標普500", "close": spy_m["close"], "chg1d": spy_m["chg1d"], "chg20d": spy_m["chg20d"], "atr_dist50": spy_m["atr_dist50"]}]

    for sym, name in INDICES[1:]:
        m = calc_metrics(get_bars(sym))
        indices_out.append({"ticker": sym, "name": name, "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"], "atr_dist50": m["atr_dist50"]})

    sectors_out = []
    for sym, name in SECTORS:
        m = calc_metrics(get_bars(sym))
        sectors_out.append({
            "ticker": sym, "name": name, "close": m["close"],
            "chg1d": m["chg1d"], "rel20": round(m["chg20d"] - spy_m["chg20d"], 2), "rsi14": m["rsi14"]
        })

    watchlist_out = []
    for sym in WATCHLIST:
        m = calc_metrics(get_bars(sym))
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
    report = {
        "trade_date": trade_date,
        "updated_at": f"自動更新於 {hkt_now}",
        "data_source": "Massive API",
        "indices": indices_out,
        "breadth": breadth_out,
        "ai_brief": ai_brief,
        "sectors": sectors_out,
        "watchlist": watchlist_out,
    }

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    start_tag, end_tag = "/*__DATA_START__*/", "/*__DATA_END__*/"
    if start_tag not in html or end_tag not in html:
        raise SystemExit("ERROR: index.html 缺少注入標記 /*__DATA_START__*/。")

    prefix = html.split(start_tag)[0]
    suffix = html.split(end_tag)[1]
    injected = f"{start_tag}\nconst REPORT_DATA = {json.dumps(report, ensure_ascii=False, indent=2)};\n{end_tag}"
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(prefix + injected + suffix)

    print("SUCCESS: index.html updated with latest Massive API data!")


if __name__ == "__main__":
    main()
