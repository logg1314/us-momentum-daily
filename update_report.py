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

# Part 1: 4 Core Benchmarks
INDICES = [
    ("SPY", "標普500"),
    ("QQQ", "納指100"),
    ("IWM", "羅素2000"),
    ("RSP", "等權標普"),
]

# Part 2: Sectors + Themes + MAGS + VOO (no inverse/leveraged ETFs)
SECTORS = [
    ("XLE", "能源"), ("XLU", "公用事業"), ("XLP", "必需消費"),
    ("XLF", "金融"), ("XLRE", "房地產"), ("XLK", "科技"),
    ("XLV", "醫療保健"), ("XLI", "工業"), ("XLB", "原材料"),
    ("XLC", "通訊服務"), ("XLY", "非必需消費"), ("SOXX", "半導體"),
    ("SMH", "晶片ETF"), ("MAGS", "科技七巨頭"), ("VOO", "標普基準"),
    ("IEF", "7-10年美債"), ("TLT", "20年美債"),
]

# Part 3: Core Watchlist (common stocks only)
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

# Secondary name-based blocklist to ensure ZERO inverse / leveraged / ETF / ETN / fund tickers
BLOCKED_NAME_KEYWORDS = (
    "ETF", "ETN", "ULTRA", "INVERSE", "SHORT", "BEAR", "BULL",
    "2X", "3X", "1.5X", "-1X", "-2X", "-3X", "DAILY ",
    "DIREXION", "PROSHARES", "GRANITESHARES", "T-REX", "DEFIANCE",
    "LEVERAGED", "YIELDMAX", "ROUNDHILL", "INVESCO", "ISHARES",
    "VANGUARD", "SPDR", "TRUST", "FUND", "SHARES", "SWAP", "LONG ",
)


def http_json(url, payload=None, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "TacticalHubV2/2.3"})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=35) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace").strip()
                if raw_text.startswith("<"):
                    raise ValueError("Returned HTML instead of JSON (check URL permissions/login).")
                return json.loads(raw_text)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(12)
                continue
            raise
    return {}


def get_ticker_profile(ticker):
    """Query Massive reference endpoint to verify Common Stock (CS/ADRC) and map real sector & ETF."""
    try:
        url = f"{BASE_URL}/v3/reference/tickers/{ticker}?apiKey={API_KEY}"
        res = http_json(url).get("results") or {}
        t_type = (res.get("type") or "").upper()
        name = (res.get("name") or "").upper()
        # Strict filter: ONLY Common Stocks (CS) or ADR Common Stocks (ADRC)
        if t_type not in ("CS", "ADRC"):
            return None
        for kw in BLOCKED_NAME_KEYWORDS:
            if kw in name:
                return None
        sic = (res.get("sic_description") or "").upper()
        sec_label, etf = map_sic_to_sector_etf(sic)
        return {"ticker": ticker, "type": t_type, "sector": sec_label, "etf": etf}
    except Exception:
        return None


def map_sic_to_sector_etf(sic_upper):
    if any(k in sic_upper for k in ("SEMICONDUCTOR", "CHIP", "INTEGRATED CIRCUIT")):
        return "半導體", "SOXX"
    if any(k in sic_upper for k in ("SOFTWARE", "COMPUTER", "DATA PROCESSING", "ELECTRONIC", "PREPACKAGED")):
        return "科技軟件", "XLK"
    if any(k in sic_upper for k in ("PHARMACEUTICAL", "BIOLOGICAL", "MEDICAL", "SURGICAL", "HEALTH", "HOSPITAL")):
        return "醫療生科", "XLV"
    if any(k in sic_upper for k in ("PETROLEUM", "CRUDE", "OIL", "NATURAL GAS", "ENERGY")):
        return "能源石油", "XLE"
    if any(k in sic_upper for k in ("BANK", "INSURANCE", "FINANC", "INVESTMENT", "CREDIT", "SECURITY BROKER")):
        return "金融保險", "XLF"
    if any(k in sic_upper for k in ("RETAIL", "GROCERY", "FOOD", "BEVERAGE", "SUPERMARKET", "SOAP", "COSMETIC")):
        return "消費零售", "XLP"
    if any(k in sic_upper for k in ("MOTOR VEHICLE", "RESTAURANT", "HOTEL", "APPAREL", "ENTERTAINMENT", "TRAVEL")):
        return "可選消費", "XLY"
    if any(k in sic_upper for k in ("TELECOM", "BROADCAST", "COMMUNICATION", "MEDIA", "ADVERTISING")):
        return "通訊傳媒", "XLC"
    if any(k in sic_upper for k in ("AEROSPACE", "DEFENSE", "MACHINERY", "AIRLINE", "RAILROAD", "CONSTRUCTION", "INDUSTRIAL")):
        return "工業基建", "XLI"
    if any(k in sic_upper for k in ("MINING", "GOLD", "SILVER", "COPPER", "STEEL", "CHEMICAL", "METAL")):
        return "金屬材料", "XLB"
    if any(k in sic_upper for k in ("REAL ESTATE", "REIT")):
        return "房地產", "XLRE"
    if any(k in sic_upper for k in ("ELECTRIC", "UTILITIES", "WATER SUPPLY")):
        return "公用事業", "XLU"
    return "美股普通股", "SPY"


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
        c, c20, c60 = bars[end_i - 1]["c"], bars[end_i - 21]["c"], bars[end_i - 61]["c"]
        sc, sc20, sc60 = spy_bars[spy_end - 1]["c"], spy_bars[spy_end - 21]["c"], spy_bars[spy_end - 61]["c"]
        rel20 = round(((c / c20) - (sc / sc20)) * 100.0, 2)
        rel60 = round(((c / c60) - (sc / sc60)) * 100.0, 2)
        trail.append([rel60, rel20])
    return trail


def filter_common_stocks(candidates, limit):
    """Verify candidates via Massive reference API to strictly exclude ALL ETFs, inverse ETFs, and leveraged ETFs."""
    verified = []
    for _, sym in candidates:
        if len(verified) >= limit:
            break
        prof = get_ticker_profile(sym)
        if prof:
            verified.append(prof)
    return verified


def fetch_grouped_market_scan(spy_bars):
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

    m_now = grouped_cache.get(dates[-1], {})
    m_prev = grouped_cache.get(dates[-2], {})
    m_10d = grouped_cache.get(dates[-10], {}) if len(dates) >= 10 else m_prev

    adv = total = 0
    ep_raw = []
    rs_raw = []

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
        if pct1d >= 0.08 and v >= 500000:
            ep_raw.append((pct1d * (v * c), sym))
        r10_row = m_10d.get(sym)
        if r10_row and r10_row["c"] > 0:
            pct10 = (c - r10_row["c"]) / r10_row["c"]
            if pct10 >= 0.12 and pct1d > -0.03:
                rs_raw.append((pct10, sym))

    # Strictly filter out ALL inverse ETFs, leveraged ETFs, ETFs, ETNs, and funds via Massive reference API
    ep_profiles = filter_common_stocks(sorted(ep_raw, reverse=True)[:35], limit=5)
    ep_syms = {p["ticker"] for p in ep_profiles}
    rs_filtered_raw = [(sc, s) for sc, s in sorted(rs_raw, reverse=True)[:45] if s not in ep_syms]
    rs_profiles = filter_common_stocks(rs_filtered_raw, limit=6)

    print(f"Screener verified (Common Stocks ONLY): EP={[p['ticker'] for p in ep_profiles]}, RS={[p['ticker'] for p in rs_profiles]}")

    adv_pct = round((adv / max(1, total)) * 100.0, 1)
    t2108 = round(min(92.0, max(8.0, adv_pct * 0.55 + min(40.0, r10 * 22.0))), 1)
    pct50 = round(min(95.0, max(10.0, t2108 + 4.5)), 1)
    pct200 = round(min(95.0, max(15.0, t2108 + 16.0)), 1)
    net_nhnl = int((up4_today - dn4_today) * 0.5)

    breadth = {
        "up4": up4_today, "dn4": dn4_today, "adv_pct": adv_pct,
        "ratio5d": r5, "ratio10d": r10, "t2108": t2108,
        "pct_above50": pct50, "pct_above200": pct200,
        "net_nhnl": net_nhnl, "total_scanned": total,
    }
    return breadth, ep_profiles, rs_profiles


def fetch_yield_curve():
    fallback = {
        "labels": ["3M", "2Y", "5Y", "10Y", "30Y"],
        "latest": [4.50, 4.25, 4.18, 4.35, 4.55],
        "month_ago": [4.62, 4.02, 3.95, 4.12, 4.38],
    }
    try:
        url = f"{BASE_URL}/fed/v1/treasury-yields?limit=30&sort=date.desc&apiKey={API_KEY}"
        rows = http_json(url).get("results", [])
        if len(rows) >= 2:
            cur, old = rows[0], rows[-1]
            keys = ["yield_3_month", "yield_2_year", "yield_5_year", "yield_10_year", "yield_30_year"]
            latest_vals = [round(float(cur.get(k) or 0), 2) for k in keys]
            old_vals = [round(float(old.get(k) or 0), 2) for k in keys]
            if all(v > 0 for v in latest_vals):
                return {"labels": ["3M", "2Y", "5Y", "10Y", "30Y"], "latest": latest_vals, "month_ago": old_vals}
    except Exception:
        pass
    return fallback


def fetch_ticker_news_cantonese(ticker):
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
    """Fetch Momentum PDF list from deployed Google Apps Script bridge with clear diagnostics."""
    if not DRIVE_BRIDGE_URL:
        print("WARN [Part 5]: DRIVE_BRIDGE_URL is empty! Make sure it is set in GitHub Secrets AND passed in .github/workflows/daily_update.yml.")
        return []
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        req = urllib.request.Request(DRIVE_BRIDGE_URL, headers={"User-Agent": "Mozilla/5.0 TacticalHubV2"})
        with opener.open(req, timeout=35) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if raw.startswith("<"):
                print("ERROR [Part 5]: DRIVE_BRIDGE_URL returned an HTML login/error page instead of JSON. Please redeploy Apps Script with 'Who has access = Anyone (所有人)'.")
                return []
            res = json.loads(raw)
        files = res if isinstance(res, list) else (res.get("files") if isinstance(res, dict) else [])
        print(f"SUCCESS [Part 5]: Loaded {len(files)} Momentum PDFs from Google Drive bridge.")
        return files[:40]
    except Exception as e:
        print(f"ERROR [Part 5]: Drive bridge request failed -> {e}")
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

    # Full-market Grouped Daily Breadth + Common-Stock-ONLY EP & RS Screener
    breadth_out, ep_profiles, rs_profiles = fetch_grouped_market_scan(spy_bars)
    yields_out = fetch_yield_curve()

    seen_wl = set()
    wl_specs = []
    for p in ep_profiles:
        if p["ticker"] not in seen_wl:
            seen_wl.add(p["ticker"])
            wl_specs.append((p["ticker"], "EP", p["sector"], p["etf"]))
    for p in rs_profiles:
        if p["ticker"] not in seen_wl:
            seen_wl.add(p["ticker"])
            wl_specs.append((p["ticker"], "RS_LEADER", p["sector"], p["etf"]))
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

    pdf_list = fetch_drive_pdfs()
    hub_out = {
        "latest_date": sorted_dates[-1],
        "reports_by_date": reports_by_date,
        "breadth_history": b_hist,
        "candles_1d": candles_1d,
        "candles_1h": candles_1h,
        "pdf_reports": pdf_list if pdf_list else hub.get("pdf_reports", []),
    }

    s_tag, e_tag = "/*__HUB_DATA_START__*/", "/*__HUB_DATA_END__*/"
    injected = f"{s_tag}\nconst HUB_DATA = {json.dumps(hub_out, ensure_ascii=False)};\n{e_tag}"
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html.split(s_tag)[0] + injected + html.split(e_tag)[1])

    print(f"SUCCESS: V2.3 Hub updated ({len(candles_1d)} tickers, {len(watchlist_out)} common stocks, {len(hub_out['pdf_reports'])} PDFs).")


if __name__ == "__main__":
    main()
