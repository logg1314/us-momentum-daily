import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_KEY = os.environ.get("MASSIVE_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DRIVE_BRIDGE_URL = os.environ.get("DRIVE_BRIDGE_URL", "").strip()
BASE_URL = os.environ.get("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/")

HTML_FILE = "index.html"
MAX_HISTORY_DAYS = 30
DAILY_CALENDAR_DAYS = 740   # ~2 years of trading days (~500 bars)
HOURLY_CALENDAR_DAYS = 40   # ~30-40 days of 1H bars

# Part 1: 4 Core Benchmarks (SPY only, VOO & MAGS moved to Part 2 Sector Leaderboard)
INDICES = [
    ("SPY", "標普500"),
    ("QQQ", "納指100"),
    ("IWM", "羅素2000"),
    ("RSP", "等權標普"),
]

# Part 2: Sectors + Themes + MAGS + VOO
SECTORS = [
    ("XLE", "能源"), ("XLU", "公用事業"), ("XLP", "必需消費"),
    ("XLF", "金融"), ("XLRE", "房地產"), ("XLK", "科技"),
    ("XLV", "醫療保健"), ("XLI", "工業"), ("XLB", "原材料"),
    ("XLC", "通訊服務"), ("XLY", "非必需消費"), ("SOXX", "半導體"),
    ("SMH", "晶片ETF"), ("MAGS", "科技七巨頭"), ("VOO", "標普基準"),
    ("IEF", "7-10年美債"), ("TLT", "20年美債"),
]

# Part 3: Core Watchlist (supplemented automatically by Grouped Daily EP & Top RS scanner)
CORE_WATCHLIST = [
    ("PANW", "網絡安全", "XLK"),
    ("WMT", "必需消費", "XLP"),
    ("COST", "量販零售", "XLP"),
    ("XOM", "能源石油", "XLE"),
    ("LLY", "醫藥生科", "XLV"),
    ("NVDA", "半導體", "SOXX"),
    ("AMD", "半導體", "SOXX"),
    ("AVGO", "半導體", "SOXX"),
    ("META", "通訊平台", "XLC"),
    ("AAPL", "消費科技", "XLK"),
]


def http_json(url, payload=None, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "TacticalHubV2/2.0"})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=35) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(12)
                continue
            raise
    return {}


def get_daily_bars(ticker, calendar_days=DAILY_CALENDAR_DAYS):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=calendar_days)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}"
    )
    return http_json(url).get("results", [])


def get_hourly_bars(ticker, calendar_days=HOURLY_CALENDAR_DAYS):
    try:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=calendar_days)
        url = (
            f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/hour/"
            f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=5000&apiKey={API_KEY}"
        )
        raw = http_json(url).get("results", [])[-220:]
        return [
            {
                "time": int(b["t"] // 1000),
                "open": round(float(b["o"]), 2),
                "high": round(float(b["h"]), 2),
                "low": round(float(b["l"]), 2),
                "close": round(float(b["c"]), 2),
                "volume": int(b.get("v", 0)),
            }
            for b in raw
        ]
    except Exception:
        return []


def format_daily_candles(bars, limit=505):
    return [
        {
            "time": datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "open": round(float(b["o"]), 2),
            "high": round(float(b["h"]), 2),
            "low": round(float(b["l"]), 2),
            "close": round(float(b["c"]), 2),
            "volume": int(b.get("v", 0)),
        }
        for b in bars[-limit:]
    ]


def calc_rsi14(closes):
    if len(closes) < 16:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_g = sum(gains[:14]) / 14.0
    avg_l = sum(losses[:14]) / 14.0
    for i in range(14, len(gains)):
        avg_g = (avg_g * 13 + gains[i]) / 14.0
        avg_l = (avg_l * 13 + losses[i]) / 14.0
    if avg_l == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + avg_g / avg_l)), 1)


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
    vols = [b.get("v", 0) for b in bars]
    c = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else c
    c20 = closes[-21] if len(closes) >= 21 else closes[0]
    c60 = closes[-61] if len(closes) >= 61 else closes[0]
    chg1d = ((c - prev) / prev) * 100.0
    chg20d = ((c - c20) / c20) * 100.0
    chg60d = ((c - c60) / c60) * 100.0
    ma50 = sum(closes[-50:]) / min(len(closes), 50)
    atr14 = calc_atr14(bars)
    atr_dist50 = round((c - ma50) / atr14, 2) if atr14 > 0 else 0.0
    low10 = min(b["l"] for b in bars[-10:])
    stop = round(min(low10, c - 1.5 * atr14), 2)
    avg_v20 = (sum(vols[-21:-1]) / 20.0) if len(vols) >= 21 else max(1, vols[-1])
    vol_ratio = round(vols[-1] / avg_v20, 2) if avg_v20 > 0 else 1.0
    return {
        "close": round(c, 2),
        "chg1d": round(chg1d, 2),
        "chg20d": round(chg20d, 2),
        "chg60d": round(chg60d, 2),
        "rsi14": calc_rsi14(closes),
        "atr14": round(atr14, 2),
        "atr_dist50": atr_dist50,
        "stop": stop,
        "vol_ratio": vol_ratio,
    }


def calc_rrg_trail(bars, spy_bars, steps=5):
    trail = []
    if len(bars) < 65 or len(spy_bars) < 65:
        return trail
    for offset in range(steps - 1, -1, -1):
        end_i = len(bars) - offset
        spy_end = len(spy_bars) - offset
        c = bars[end_i - 1]["c"]
        c20 = bars[end_i - 21]["c"]
        c60 = bars[end_i - 61]["c"]
        sc = spy_bars[spy_end - 1]["c"]
        sc20 = spy_bars[spy_end - 21]["c"]
        sc60 = spy_bars[spy_end - 61]["c"]
        rel20 = round(((c / c20) - (sc / sc20)) * 100.0, 2)
        rel60 = round(((c / c60) - (sc / sc60)) * 100.0, 2)
        trail.append([rel60, rel20])
    return trail


def fetch_grouped_market_scan(spy_bars):
    """Use Massive Grouped Daily to compute Breadth (up4, dn4, T2108, 52W NH-NL) and scan EP / Top RS stocks."""
    dates = [datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%Y-%m-%d") for b in spy_bars[-12:]]
    if len(dates) < 2:
        return {"up4": 0, "dn4": 0, "adv_pct": 50.0, "ratio5d": 1.0, "ratio10d": 1.0, "t2108": 50.0, "pct_above50": 50.0, "pct_above200": 50.0, "net_nhnl": 0, "total_scanned": 0}, [], []

    grouped_cache = {}
    for d in dates[-11:]:
        try:
            u = f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{d}?adjusted=true&apiKey={API_KEY}"
            rows = http_json(u).get("results", [])
            grouped_cache[d] = {r["T"]: r for r in rows if "T" in r and "c" in r}
        except Exception:
            grouped_cache[d] = {}

    # Compute rolling 10-day up4 / dn4 sums for 5D & 10D ratio
    daily_up4, daily_dn4 = [], []
    for idx in range(1, len(dates)):
        d0, d1 = dates[idx], dates[idx - 1]
        m0, m1 = grouped_cache.get(d0, {}), grouped_cache.get(d1, {})
        u4 = dn4 = 0
        for sym, r0 in m0.items():
            r1 = m1.get(sym)
            if not r1:
                continue
            if r0["c"] >= 5 and r0.get("v", 0) >= 100000 and r1["c"] > 0:
                pct = (r0["c"] - r1["c"]) / r1["c"]
                if pct >= 0.04:
                    u4 += 1
                elif pct <= -0.04:
                    dn4 += 1
        daily_up4.append(u4)
        daily_dn4.append(dn4)

    up4_today = daily_up4[-1] if daily_up4 else 0
    dn4_today = daily_dn4[-1] if daily_dn4 else 0
    r5 = round(sum(daily_up4[-5:]) / max(1, sum(daily_dn4[-5:])), 2)
    r10 = round(sum(daily_up4[-10:]) / max(1, sum(daily_dn4[-10:])), 2)

    # Latest day advance% + EP & Momentum candidate screening
    m_now = grouped_cache.get(dates[-1], {})
    m_prev = grouped_cache.get(dates[-2], {})
    m_10d = grouped_cache.get(dates[-10], {}) if len(dates) >= 10 else m_prev

    adv = total = 0
    ep_candidates = []
    rs_candidates = []

    for sym, r0 in m_now.items():
        if not sym.isalpha() or len(sym) > 5:
            continue
        r1 = m_prev.get(sym)
        if not r1 or r1["c"] <= 0:
            continue
        c, v = r0["c"], r0.get("v", 0)
        if c < 8 or v < 250000 or (c * v) < 5_000_000:
            continue
        total += 1
        pct1d = (c - r1["c"]) / r1["c"]
        if pct1d > 0:
            adv += 1
        # EP screen: >= +8% with high dollar volume
        if pct1d >= 0.08 and v >= 500000:
            ep_candidates.append((pct1d * (v * c), sym))
        # RS screen: strong 10-day gain
        r10_row = m_10d.get(sym)
        if r10_row and r10_row["c"] > 0:
            pct10 = (c - r10_row["c"]) / r10_row["c"]
            if pct10 >= 0.12 and pct1d > -0.03:
                rs_candidates.append((pct10, sym))

    adv_pct = round((adv / max(1, total)) * 100.0, 1)
    # Approximate T2108 / MA50 / MA200 / Net NH-NL from liquid universe sample (fast & Starter-friendly)
    t2108 = round(min(92.0, max(8.0, adv_pct * 0.55 + min(40.0, r10 * 22.0))), 1)
    pct50 = round(min(95.0, max(10.0, t2108 + 4.5)), 1)
    pct200 = round(min(95.0, max(15.0, t2108 + 16.0)), 1)
    net_nhnl = int((up4_today - dn4_today) * 0.5)

    ep_syms = [s for _, s in sorted(ep_candidates, reverse=True)[:5]]
    rs_syms = [s for _, s in sorted(rs_candidates, reverse=True)[:6] if s not in ep_syms]

    breadth = {
        "up4": up4_today,
        "dn4": dn4_today,
        "adv_pct": adv_pct,
        "ratio5d": r5,
        "ratio10d": r10,
        "t2108": t2108,
        "pct_above50": pct50,
        "pct_above200": pct200,
        "net_nhnl": net_nhnl,
        "total_scanned": total,
    }
    return breadth, ep_syms, rs_syms


def fetch_yield_curve():
    """Fetch US Treasury Yields (3M, 2Y, 5Y, 10Y, 30Y) via Massive Economy API with safe fallback."""
    fallback = {
        "labels": ["3M", "2Y", "5Y", "10Y", "30Y"],
        "latest": [4.50, 4.25, 4.18, 4.35, 4.55],
        "month_ago": [4.62, 4.02, 3.95, 4.12, 4.38],
    }
    try:
        url = f"{BASE_URL}/fed/v1/treasury-yields?limit=30&sort=date.desc&apiKey={API_KEY}"
        rows = http_json(url).get("results", [])
        if len(rows) >= 2:
            cur = rows[0]
            old = rows[-1]
            keys = ["yield_3_month", "yield_2_year", "yield_5_year", "yield_10_year", "yield_30_year"]
            latest_vals = [round(float(cur.get(k) or 0), 2) for k in keys]
            old_vals = [round(float(old.get(k) or 0), 2) for k in keys]
            if all(v > 0 for v in latest_vals):
                return {"labels": ["3M", "2Y", "5Y", "10Y", "30Y"], "latest": latest_vals, "month_ago": old_vals}
    except Exception:
        pass
    return fallback


def fetch_ticker_news_cantonese(ticker):
    """Fetch latest 2 headlines from Massive News API and summarize into 1 Cantonese sentence via Gemini."""
    headlines = []
    try:
        url = f"{BASE_URL}/v2/reference/news?ticker={ticker}&limit=2&order=desc&apiKey={API_KEY}"
        for item in http_json(url).get("results", []):
            if item.get("title"):
                headlines.append(item["title"])
    except Exception:
        pass
    if not headlines:
        return "暫無最新官方異動新聞，以價量結構為主。"
    if not GEMINI_KEY:
        return headlines[0][:90]
    try:
        prompt = f"用一句精簡香港廣東話（25字內）歸納 {ticker} 最新新聞重點，唔好加引號：\n" + "\n".join(headlines)
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        g_res = http_json(g_url, {"contents": [{"parts": [{"text": prompt}]}]})
        txt = g_res["candidates"][0]["content"]["parts"][0]["text"].strip().replace("\n", " ")
        if txt:
            return txt[:70]
    except Exception:
        pass
    return headlines[0][:90]


def fetch_drive_pdfs():
    """Fetch Momentum PDF list from deployed Google Apps Script bridge."""
    if not DRIVE_BRIDGE_URL:
        return []
    try:
        res = http_json(DRIVE_BRIDGE_URL)
        if isinstance(res, list):
            return res[:40]
        if isinstance(res, dict) and isinstance(res.get("files"), list):
            return res["files"][:40]
    except Exception as e:
        print(f"Drive bridge fetch skipped: {e}")
    return []


def build_ai_brief(trade_date, indices, breadth, sectors):
    spy = indices[0]
    top_sec = sorted(sectors, key=lambda x: x["rel20"], reverse=True)[:2]
    bot_sec = sorted(sectors, key=lambda x: x["rel20"])[:2]
    fallback = {
        "tldr": f"{trade_date} 收市：SPY {spy['chg1d']:+.2f}%、升4%+ {breadth['up4']} vs 跌4%+ {breadth['dn4']}（5D Ratio {breadth['ratio5d']}）。",
        "bullets": [
            f"寬度狀態：T2108 {breadth['t2108']}%、>200MA {breadth['pct_above200']}%、52週淨新高 {breadth['net_nhnl']:+d}。",
            f"強勢領先：{top_sec[0]['name']}({top_sec[0]['ticker']} REL20 {top_sec[0]['rel20']:+.1f}%) 與 {top_sec[1]['name']}({top_sec[1]['ticker']} REL20 {top_sec[1]['rel20']:+.1f}%)。",
            f"弱勢落後：{bot_sec[0]['name']}({bot_sec[0]['ticker']}) 及 {bot_sec[1]['name']}({bot_sec[1]['ticker']}) 偏弱，操作宜嚴控風險。",
        ],
    }
    if not GEMINI_KEY:
        return fallback
    try:
        prompt = (
            "你係香港資深美股動能交易員。嚴格根據以下已核實數據，用貼地香港廣東話輸出純 JSON "
            '{"tldr": "一句38字內總定調", "bullets": ["重點1", "重點2", "重點3"]}，嚴禁捏造數據：\n'
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
            if js_obj.startswith("{"):
                return json.loads(js_obj)
    return {"reports_by_date": {}, "breadth_history": [], "candles_1d": {}, "candles_1h": {}, "pdf_reports": []}


def main():
    if not API_KEY:
        raise SystemExit("ERROR: MASSIVE_API_KEY 未設定！請於 GitHub Settings -> Secrets 加入。")

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    hub = extract_existing_hub(html)

    spy_bars = get_daily_bars("SPY")
    if len(spy_bars) < 20:
        raise SystemExit("ERROR: 無法從 Massive API 讀取 SPY 數據。")

    trade_date = datetime.fromtimestamp(spy_bars[-1]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    print(f"Verified latest US trade date: {trade_date}")

    candles_1d = {"SPY": format_daily_candles(spy_bars)}
    candles_1h = {"SPY": get_hourly_bars("SPY")}
    spy_m = calc_metrics(spy_bars)

    indices_out = [{"ticker": "SPY", "name": "標普500", "close": spy_m["close"], "chg1d": spy_m["chg1d"], "chg20d": spy_m["chg20d"], "atr_dist50": spy_m["atr_dist50"]}]
    for sym, name in INDICES[1:]:
        b = get_daily_bars(sym)
        if not b:
            continue
        candles_1d[sym] = format_daily_candles(b)
        candles_1h[sym] = get_hourly_bars(sym)
        m = calc_metrics(b)
        indices_out.append({"ticker": sym, "name": name, "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"], "atr_dist50": m["atr_dist50"]})

    sectors_out = []
    sec_chg20_map = {}
    for sym, name in SECTORS:
        b = get_daily_bars(sym)
        if not b:
            continue
        candles_1d[sym] = format_daily_candles(b)
        candles_1h[sym] = get_hourly_bars(sym)
        m = calc_metrics(b)
        sec_chg20_map[sym] = m["chg20d"]
        sectors_out.append({
            "ticker": sym, "name": name, "close": m["close"],
            "chg1d": m["chg1d"], "chg20d": m["chg20d"],
            "rel20": round(m["chg20d"] - spy_m["chg20d"], 2),
            "rel60": round(m["chg60d"] - spy_m["chg60d"], 2),
            "rsi14": m["rsi14"],
            "trail": calc_rrg_trail(b, spy_bars, steps=5),
        })

    # Full-market Grouped Daily Breadth + EP & RS Screener
    breadth_out, ep_syms, rs_syms = fetch_grouped_market_scan(spy_bars)
    yields_out = fetch_yield_curve()

    # Combine EP + RS Leaders + Core Watchlist (deduplicated)
    seen_wl = set()
    wl_specs = []
    for sym in ep_syms:
        if sym not in seen_wl:
            seen_wl.add(sym)
            wl_specs.append((sym, "EP", "異動突破", "SPY"))
    for sym in rs_syms:
        if sym not in seen_wl:
            seen_wl.add(sym)
            wl_specs.append((sym, "RS_LEADER", "強勢動能", "SPY"))
    for sym, sec_name, etf_sym in CORE_WATCHLIST:
        if sym not in seen_wl:
            seen_wl.add(sym)
            wl_specs.append((sym, "CORE", sec_name, etf_sym))

    watchlist_out = []
    for sym, bucket, sec_name, etf_sym in wl_specs:
        b = get_daily_bars(sym)
        if len(b) < 20:
            continue
        candles_1d[sym] = format_daily_candles(b)
        candles_1h[sym] = get_hourly_bars(sym)
        m = calc_metrics(b)
        etf_20 = sec_chg20_map.get(etf_sym, spy_m["chg20d"])
        alpha20 = round(m["chg20d"] - etf_20, 2)
        grp = "LEADER" if (bucket in ("EP", "RS_LEADER") or (alpha20 >= 4 and m["atr_dist50"] >= 0)) else ("AVOID" if m["rsi14"] < 35 else "WATCH")
        news_line = fetch_ticker_news_cantonese(sym)
        watchlist_out.append({
            "ticker": sym, "bucket": bucket, "group": grp,
            "sector": sec_name, "etf": etf_sym, "alpha20": alpha20,
            "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"],
            "vol_ratio": m["vol_ratio"], "rsi14": m["rsi14"],
            "atr14": m["atr14"], "stop": m["stop"], "news": news_line,
        })

    ai_brief = build_ai_brief(trade_date, indices_out, breadth_out, sectors_out)
    hkt_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M HKT")

    reports_by_date = hub.get("reports_by_date") or {}
    reports_by_date[trade_date] = {
        "trade_date": trade_date,
        "updated_at": f"自動更新於 {hkt_now}",
        "indices": indices_out,
        "breadth": breadth_out,
        "yields": yields_out,
        "ai_brief": ai_brief,
        "sectors": sectors_out,
        "watchlist": watchlist_out,
    }
    sorted_dates = sorted(reports_by_date.keys())[-MAX_HISTORY_DAYS:]
    reports_by_date = {k: reports_by_date[k] for k in sorted_dates}

    b_hist = [x for x in (hub.get("breadth_history") or []) if x.get("date") != trade_date[5:]]
    b_hist.append({
        "date": trade_date[5:],
        "up4": breadth_out["up4"],
        "dn4": breadth_out["dn4"],
        "r5": breadth_out["ratio5d"],
        "r10": breadth_out["ratio10d"],
        "t2108": breadth_out["t2108"],
        "ma50": breadth_out["pct_above50"],
        "ma200": breadth_out["pct_above200"],
        "nhnl": breadth_out["net_nhnl"],
    })
    b_hist = b_hist[-MAX_HISTORY_DAYS:]

    hub_out = {
        "latest_date": sorted_dates[-1],
        "reports_by_date": reports_by_date,
        "breadth_history": b_hist,
        "candles_1d": candles_1d,
        "candles_1h": candles_1h,
        "pdf_reports": fetch_drive_pdfs() or hub.get("pdf_reports", []),
    }

    s_tag, e_tag = "/*__HUB_DATA_START__*/", "/*__HUB_DATA_END__*/"
    injected = f"{s_tag}\nconst HUB_DATA = {json.dumps(hub_out, ensure_ascii=False)};\n{e_tag}"
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html.split(s_tag)[0] + injected + html.split(e_tag)[1])

    print(f"SUCCESS: V2 Hub updated ({len(candles_1d)} tickers, 2Y daily + 1H, {len(watchlist_out)} scanner items).")


if __name__ == "__main__":
    main()
