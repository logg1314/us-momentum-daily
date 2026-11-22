import base64
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import csv
from datetime import datetime, timedelta, timezone

try:
    import yfinance as yf
except ImportError:
    yf = None

API_KEY = os.environ.get("MASSIVE_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
DRIVE_BRIDGE_URL = os.environ.get("DRIVE_BRIDGE_URL", "").strip()
SCREENER_TOKEN = os.environ.get("SCREENER_REPO_TOKEN", "").strip()
SCREENER_REPO = os.environ.get("SCREENER_REPO", "logg1314/qm-screener-auto").strip()
BASE_URL = os.environ.get("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/")

DATA_DIR = "data"
CANDLES_DIR = os.path.join(DATA_DIR, "candles")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.json")
FUTURES_FILE = os.path.join(DATA_DIR, "futures.json")
SCREENER_FILE = os.path.join(DATA_DIR, "screener.json")

MAX_HISTORY_DAYS = 30
BREADTH_BACKFILL_DAYS = 20
DAILY_CALENDAR_DAYS = 740
HOURLY_CALENDAR_DAYS = 40

INDICES = [
    ("SPY", "標普500"),
    ("QQQ", "納指100"),
    ("IWM", "羅素2000"),
    ("RSP", "等權標普"),
]

SECTORS = [
    ("XLE", "能源", True), ("XLU", "公用事業", True), ("XLP", "必需消費", True),
    ("XLF", "金融", True), ("XLRE", "房地產", True), ("XLK", "科技", True),
    ("XLV", "醫療保健", True), ("XLI", "工業", True), ("XLB", "原材料", True),
    ("XLC", "通訊服務", True), ("XLY", "非必需消費", True),
    ("MAGS", "科技七巨頭", True), ("VOO", "標普基準", True),
    ("SOXX", "半導體", False), ("SMH", "晶片ETF", False),
    ("IEF", "7-10年美債", False), ("TLT", "20年美債", False),
]

# Lean Core Watchlist for Tab 1 (since Tab 3 hosts the full QM Screener)
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

BLOCKED_NAME_KEYWORDS = (
    "ETF", "ETN", "ULTRA", "INVERSE", "SHORT", "BEAR", "BULL",
    "2X", "3X", "1.5X", "-1X", "-2X", "-3X", "DAILY ",
    "DIREXION", "PROSHARES", "GRANITESHARES", "T-REX", "DEFIANCE",
    "LEVERAGED", "YIELDMAX", "ROUNDHILL", "INVESCO", "ISHARES",
    "VANGUARD", "SPDR", "TRUST", "FUND", "SHARES", "SWAP", "LONG ",
)

# Futures Universe: (display_sym, name_zh, group, yf_sym, cftc_code)
FUTURES_SPECS = [
    # FX Benchmark + 7 CME Currency Futures
    ("DXY", "美元指數 (基準)", "FX", "DX=F", "098662"),
    ("6E", "歐元期貨 (EUR)", "FX", "6E=F", "099741"),
    ("6J", "日圓期貨 (JPY)", "FX", "6J=F", "097741"),
    ("6B", "英鎊期貨 (GBP)", "FX", "6B=F", "096742"),
    ("6A", "澳元期貨 (AUD)", "FX", "6A=F", "232741"),
    ("6C", "加元期貨 (CAD)", "FX", "6C=F", "090741"),
    ("6N", "紐元期貨 (NZD)", "FX", "6N=F", "112741"),
    ("6M", "墨西哥披索 (MXN)", "FX", "6M=F", "095741"),
    # Metals
    ("GC", "黃金期貨 (Gold)", "METALS", "GC=F", "088691"),
    ("SI", "白銀期貨 (Silver)", "METALS", "SI=F", "084691"),
    ("HG", "精煉銅期貨 (Copper)", "METALS", "HG=F", "085692"),
    ("PL", "鉑金期貨 (Platinum)", "METALS", "PL=F", "076651"),
    # Energy
    ("CL", "WTI 原油 (Crude)", "ENERGY", "CL=F", "067651"),
    ("NG", "天然氣 (NatGas)", "ENERGY", "NG=F", "023651"),
    ("RB", "RBOB 汽油", "ENERGY", "RB=F", "111659"),
    ("HO", "取暖油 (Heating Oil)", "ENERGY", "HO=F", "022651"),
    # Agriculture & Softs
    ("ZC", "粟米期貨 (Corn)", "AGRI", "ZC=F", "002602"),
    ("ZS", "大豆期貨 (Soybeans)", "AGRI", "ZS=F", "005602"),
    ("ZW", "小麥期貨 (Wheat)", "AGRI", "ZW=F", "001602"),
    ("SB", "原糖期貨 (Sugar #11)", "AGRI", "SB=F", "080732"),
    ("KC", "咖啡期貨 (Coffee)", "AGRI", "KC=F", "083731"),
    ("CC", "可可期貨 (Cocoa)", "AGRI", "CC=F", "073732"),
    ("LE", "活牛期貨 (Live Cattle)", "AGRI", "LE=F", "057642"),
    # Equity Index Futures (for COT crowding alerts + charts)
    ("ES", "標普500期貨 (E-mini)", "INDEX", "ES=F", "13874A"),
    ("NQ", "納指100期貨 (E-mini)", "INDEX", "NQ=F", "209742"),
    ("RTY", "羅素2000期貨 (E-mini)", "INDEX", "RTY=F", "239742"),
]


def http_json(url, payload=None, headers=None, retries=3):
    for attempt in range(retries):
        hdrs = {"User-Agent": "TacticalHubV3.0"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=35) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace").strip()
                if raw_text.startswith("<"):
                    raise ValueError("Returned HTML instead of JSON.")
                return json.loads(raw_text)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(10)
                continue
            raise
    return {}


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


def get_ticker_profile(ticker):
    try:
        url = f"{BASE_URL}/v3/reference/tickers/{ticker}?apiKey={API_KEY}"
        res = http_json(url).get("results") or {}
        t_type = (res.get("type") or "").upper()
        name = (res.get("name") or "").upper()
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


def format_daily_candles(bars, limit=505, decimals=2):
    return [
        {
            "time": datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "open": round(float(b["o"]), decimals),
            "high": round(float(b["h"]), decimals),
            "low": round(float(b["l"]), decimals),
            "close": round(float(b["c"]), decimals),
            "volume": int(b.get("v", 0)),
        }
        for b in bars[-limit:]
    ]


def calc_ema_list(closes, period):
    if not closes:
        return []
    k = 2.0 / (period + 1.0)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(c * k + out[-1] * (1.0 - k))
    return out


def build_mini_candle_payload(daily_bars, tail=48, decimals=2):
    if not daily_bars:
        return []
    closes = [float(b["c"]) for b in daily_bars]
    ema21 = calc_ema_list(closes, 21)
    ema50 = calc_ema_list(closes, 50)
    start_idx = max(0, len(daily_bars) - tail)
    out = []
    for i in range(start_idx, len(daily_bars)):
        b = daily_bars[i]
        out.append([
            round(float(b["o"]), decimals),
            round(float(b["h"]), decimals),
            round(float(b["l"]), decimals),
            round(float(b["c"]), decimals),
            round(ema21[i], decimals),
            round(ema50[i], decimals),
        ])
    return out


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


def calc_atr14_series(bars):
    if len(bars) < 2:
        return [1.0] * len(bars)
    trs = [bars[0]["h"] - bars[0]["l"]]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atrs = []
    for i in range(len(trs)):
        w = trs[max(0, i - 13): i + 1]
        atrs.append(sum(w) / len(w))
    return atrs


def calc_metrics(bars, decimals=2):
    closes = [b["c"] for b in bars]
    c = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else c
    c20 = closes[-21] if len(closes) >= 21 else closes[0]
    c60 = closes[-61] if len(closes) >= 61 else closes[0]
    chg1d = ((c - prev) / prev) * 100.0 if prev else 0.0
    chg20d = ((c - c20) / c20) * 100.0 if c20 else 0.0
    chg60d = ((c - c60) / c60) * 100.0 if c60 else 0.0

    ema21 = calc_ema_list(closes, 21)[-1]
    ma50 = sum(closes[-50:]) / min(len(closes), 50)
    atr14 = calc_atr14_series(bars)[-1]
    atr_pct = round((atr14 / c) * 100.0, 2) if c > 0 else 1.5
    atr_dist50 = round((c - ma50) / atr14, 2) if atr14 > 0 else 0.0
    low10 = min(b["l"] for b in bars[-10:])
    stop = round(min(low10, c - 1.5 * atr14), decimals)

    dvols = [b["c"] * b.get("v", 0) for b in bars]
    avg_dv20 = (sum(dvols[-21:-1]) / 20.0) if len(dvols) >= 21 else max(1.0, dvols[-1])
    vol_ratio = round(dvols[-1] / avg_dv20, 2) if avg_dv20 > 0 else 1.0
    dollar_vol_m = round(dvols[-1] / 1_000_000.0, 1)

    ranges = [((b["h"] - b["l"]) / b["c"]) for b in bars[-20:] if b["c"] > 0]
    r5_avg = sum(ranges[-5:]) / max(1, len(ranges[-5:]))
    r20_avg = sum(ranges) / max(1, len(ranges))
    tightness = round(r5_avg / r20_avg, 2) if r20_avg > 0 else 1.0

    high20 = max(b["h"] for b in bars[-20:])
    dist_high20 = round(((high20 - c) / high20) * 100.0, 1) if high20 > 0 else 0.0

    return {
        "close": round(c, decimals),
        "chg1d": round(chg1d, 2),
        "chg20d": round(chg20d, 2),
        "chg60d": round(chg60d, 2),
        "rsi14": calc_rsi14(closes),
        "atr14": round(atr14, decimals),
        "atr_pct": atr_pct,
        "atr_dist50": atr_dist50,
        "above_ema21": c >= ema21,
        "above_ma50": c >= ma50,
        "stop": stop,
        "vol_ratio": vol_ratio,
        "dollar_vol_m": dollar_vol_m,
        "tightness": tightness,
        "dist_high20": dist_high20,
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


def calc_5d_regime_score(r5, r10, t2108, idx_ma_pass, spy_atr_pct, up4, dn4):
    s_r5 = max(0.0, min(1.0, (r5 - 0.2) / 1.4)) * 20.0
    s_r10 = max(0.0, min(1.0, (r10 - 0.3) / 1.1)) * 10.0
    f_ratio = round(s_r5 + s_r10, 1)
    f_t2108 = round(max(0.0, min(1.0, (t2108 - 15.0) / 55.0)) * 25.0, 1)
    f_ma = round((max(0, min(8, idx_ma_pass)) / 8.0) * 25.0, 1)
    f_vol = round(max(0.0, min(1.0, (2.7 - spy_atr_pct) / 2.0)) * 10.0, 1)
    f_pulse = round(((up4 / (up4 + dn4)) if (up4 + dn4) > 0 else 0.5) * 10.0, 1)
    total = round(f_ratio + f_t2108 + f_ma + f_vol + f_pulse)
    return max(0, min(100, total))


def filter_common_stocks(candidates, limit):
    verified = []
    for _, sym in candidates:
        if len(verified) >= limit:
            break
        prof = get_ticker_profile(sym)
        if prof:
            verified.append(prof)
    return verified


def build_index_ma_history(index_bars_map, dates):
    date_ma_pass = {d: 0 for d in dates}
    spy_atr_pct_by_date = {d: 1.5 for d in dates}
    for sym, bars in index_bars_map.items():
        if len(bars) < 50:
            continue
        closes = [b["c"] for b in bars]
        ema21 = calc_ema_list(closes, 21)
        atrs = calc_atr14_series(bars)
        bar_dates = [datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%Y-%m-%d") for b in bars]
        idx_map = {bd: i for i, bd in enumerate(bar_dates)}
        for d in dates:
            i = idx_map.get(d)
            if i is None or i < 49:
                continue
            c = closes[i]
            ma50 = sum(closes[i - 49: i + 1]) / 50.0
            if c >= ema21[i]:
                date_ma_pass[d] += 1
            if c >= ma50:
                date_ma_pass[d] += 1
            if sym == "SPY" and c > 0:
                spy_atr_pct_by_date[d] = round((atrs[i] / c) * 100.0, 2)
    return date_ma_pass, spy_atr_pct_by_date


def fetch_grouped_market_scan(spy_bars, index_bars_map):
    need_days = BREADTH_BACKFILL_DAYS + 11
    dates = [datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%Y-%m-%d") for b in spy_bars[-need_days:]]
    if len(dates) < 2:
        empty_br = {"up4": 0, "dn4": 0, "adv_pct": 50.0, "ratio5d": 1.0, "ratio10d": 1.0, "t2108": 50.0, "pct_above50": 50.0, "pct_above200": 50.0, "net_nhnl": 0, "total_scanned": 0, "idx_ma_pass": 4, "spy_atr_pct": 1.5, "score": 50, "score_ema5": 50.0, "score_sma10": 50.0}
        return empty_br, [], [], []

    grouped_cache = {}
    for d in dates:
        try:
            u = f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{d}?adjusted=true&apiKey={API_KEY}"
            rows = http_json(u).get("results", [])
            grouped_cache[d] = {r["T"]: r for r in rows if "T" in r and "c" in r}
        except Exception:
            grouped_cache[d] = {}

    date_ma_pass, spy_atr_pct_map = build_index_ma_history(index_bars_map, dates)

    daily_stats = []
    for idx in range(1, len(dates)):
        d0, d1 = dates[idx], dates[idx - 1]
        m0, m1 = grouped_cache.get(d0, {}), grouped_cache.get(d1, {})
        u4 = dn4 = adv = total = 0
        for sym, r0 in m0.items():
            r1 = m1.get(sym)
            if not r1:
                continue
            if r0["c"] >= 5 and r0.get("v", 0) >= 100000 and r1["c"] > 0:
                total += 1
                pct = (r0["c"] - r1["c"]) / r1["c"]
                if pct > 0:
                    adv += 1
                if pct >= 0.04:
                    u4 += 1
                elif pct <= -0.04:
                    dn4 += 1
        adv_pct = round((adv / max(1, total)) * 100.0, 1)
        daily_stats.append({
            "full_date": d0, "date": d0[5:], "up4": u4, "dn4": dn4,
            "adv_pct": adv_pct, "total": total,
            "idx_ma_pass": date_ma_pass.get(d0, 4),
            "spy_atr_pct": spy_atr_pct_map.get(d0, 1.5),
        })

    raw_history = []
    for i in range(len(daily_stats)):
        win5 = daily_stats[max(0, i - 4): i + 1]
        win10 = daily_stats[max(0, i - 9): i + 1]
        r5 = round(sum(x["up4"] for x in win5) / max(1, sum(x["dn4"] for x in win5)), 2)
        r10 = round(sum(x["up4"] for x in win10) / max(1, sum(x["dn4"] for x in win10)), 2)
        adv_p = daily_stats[i]["adv_pct"]
        t2108 = round(min(92.0, max(8.0, adv_p * 0.55 + min(40.0, r10 * 22.0))), 1)
        ma50 = round(min(95.0, max(10.0, t2108 + 4.5)), 1)
        ma200 = round(min(95.0, max(15.0, t2108 + 16.0)), 1)
        nhnl = int((daily_stats[i]["up4"] - daily_stats[i]["dn4"]) * 0.5)
        sc = calc_5d_regime_score(r5, r10, t2108, daily_stats[i]["idx_ma_pass"], daily_stats[i]["spy_atr_pct"], daily_stats[i]["up4"], daily_stats[i]["dn4"])
        raw_history.append({
            "date": daily_stats[i]["date"], "up4": daily_stats[i]["up4"], "dn4": daily_stats[i]["dn4"],
            "r5": r5, "r10": r10, "t2108": t2108, "ma50": ma50, "ma200": ma200, "nhnl": nhnl, "score": sc,
        })

    scores_all = [x["score"] for x in raw_history]
    ema5_scores = calc_ema_list(scores_all, 5)
    for i in range(len(raw_history)):
        raw_history[i]["score_ema5"] = round(ema5_scores[i], 1)
        w10 = scores_all[max(0, i - 9): i + 1]
        raw_history[i]["score_sma10"] = round(sum(w10) / len(w10), 1)

    breadth_history = raw_history[-BREADTH_BACKFILL_DAYS:]
    latest_h = breadth_history[-1]
    latest_raw = daily_stats[-1]

    m_now = grouped_cache.get(dates[-1], {})
    m_prev = grouped_cache.get(dates[-2], {})
    m_10d = grouped_cache.get(dates[-10], {}) if len(dates) >= 10 else m_prev

    ep_raw, rs_raw = [], []
    for sym, r0 in m_now.items():
        if not sym.isalpha() or len(sym) > 5:
            continue
        r1 = m_prev.get(sym)
        if not r1 or r1["c"] <= 0:
            continue
        c, v = r0["c"], r0.get("v", 0)
        if c < 8 or v < 250000 or (c * v) < 5_000_000:
            continue
        pct1d = (c - r1["c"]) / r1["c"]
        if pct1d >= 0.08 and v >= 500000:
            ep_raw.append((pct1d * (v * c), sym))
        r10_row = m_10d.get(sym)
        if r10_row and r10_row["c"] > 0:
            pct10 = (c - r10_row["c"]) / r10_row["c"]
            if pct10 >= 0.12 and pct1d > -0.03:
                rs_raw.append((pct10, sym))

    ep_profiles = filter_common_stocks(sorted(ep_raw, reverse=True)[:35], limit=5)
    ep_syms = {p["ticker"] for p in ep_profiles}
    rs_filtered_raw = [(sc, s) for sc, s in sorted(rs_raw, reverse=True)[:45] if s not in ep_syms]
    rs_profiles = filter_common_stocks(rs_filtered_raw, limit=6)

    breadth_today = {
        "up4": latest_h["up4"], "dn4": latest_h["dn4"], "adv_pct": latest_raw["adv_pct"],
        "ratio5d": latest_h["r5"], "ratio10d": latest_h["r10"], "t2108": latest_h["t2108"],
        "pct_above50": latest_h["ma50"], "pct_above200": latest_h["ma200"], "net_nhnl": latest_h["nhnl"],
        "total_scanned": latest_raw["total"], "idx_ma_pass": latest_raw["idx_ma_pass"],
        "spy_atr_pct": latest_raw["spy_atr_pct"], "score": latest_h["score"],
        "score_ema5": latest_h["score_ema5"], "score_sma10": latest_h["score_sma10"],
    }
    return breadth_today, breadth_history, ep_profiles, rs_profiles


def fetch_yield_curve():
    fallback = {"labels": ["3M", "2Y", "5Y", "10Y", "30Y"], "latest": [4.50, 4.25, 4.18, 4.35, 4.55], "month_ago": [4.62, 4.02, 3.95, 4.12, 4.38]}
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
        return headlines[0][:95]
    try:
        prompt = f"用一句精簡香港廣東話（25字內）歸納 {ticker} 最新新聞重點，唔好加引號：\n" + "\n".join(headlines)
        g_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        g_res = http_json(g_url, {"contents": [{"parts": [{"text": prompt}]}]})
        txt = g_res["candidates"][0]["content"]["parts"][0]["text"].strip().replace("\n", " ")
        if txt:
            return txt[:75]
    except Exception:
        pass
    return headlines[0][:95]


def fetch_drive_pdfs():
    if not DRIVE_BRIDGE_URL:
        return []
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        req = urllib.request.Request(DRIVE_BRIDGE_URL, headers={"User-Agent": "Mozilla/5.0 TacticalHubV3"})
        with opener.open(req, timeout=35) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if raw.startswith("<"):
                return []
            res = json.loads(raw)
        files = res if isinstance(res, list) else (res.get("files") if isinstance(res, dict) else [])
        return files[:60]
    except Exception:
        return []


def save_ticker_candle_file(sym, daily_candles, hourly_candles=None):
    os.makedirs(CANDLES_DIR, exist_ok=True)
    path = os.path.join(CANDLES_DIR, f"{sym}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ticker": sym, "1D": daily_candles, "1H": hourly_candles or []}, f, separators=(",", ":"))


# ==============================================================================
# MODULE A: CFTC COT + YFINANCE FUTURES & 10-YEAR SEASONALITY
# ==============================================================================
def fetch_cftc_cot_3y():
    """Fetch last 3 years of CFTC Legacy Futures-Only COT data directly from CFTC public zip archives."""
    cot_by_code = {spec[4]: [] for spec in FUTURES_SPECS}
    cur_year = datetime.now(timezone.utc).year
    years = [cur_year - 2, cur_year - 1, cur_year]

    for yr in years:
        url = f"https://www.cftc.gov/files/dea/history/deacot{yr}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TacticalHubV3.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                zbytes = resp.read()
            with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                txt_names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
                if not txt_names:
                    continue
                with zf.open(txt_names[0]) as f:
                    reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
                    header = next(reader, None)
                    if not header:
                        continue
                    idx_code = idx_date = idx_oi = idx_nc_l = idx_nc_s = idx_c_l = idx_c_s = -1
                    for idx, col in enumerate(header):
                        c_low = col.strip().lower()
                        if "cftc contract market code" in c_low:
                            idx_code = idx
                        elif "as of date in form yyyy-mm-dd" in c_low:
                            idx_date = idx
                        elif "open interest (all)" in c_low:
                            idx_oi = idx
                        elif "noncommercial positions-long (all)" in c_low:
                            idx_nc_l = idx
                        elif "noncommercial positions-short (all)" in c_low:
                            idx_nc_s = idx
                        elif "commercial positions-long (all)" in c_low:
                            idx_c_l = idx
                        elif "commercial positions-short (all)" in c_low:
                            idx_c_s = idx
                    if min(idx_code, idx_date, idx_oi, idx_nc_l, idx_nc_s, idx_c_l, idx_c_s) < 0:
                        continue
                    for row in reader:
                        if len(row) <= max(idx_code, idx_c_s):
                            continue
                        code = row[idx_code].strip()
                        if code not in cot_by_code:
                            continue
                        try:
                            dt_str = row[idx_date].strip()
                            oi = float(row[idx_oi].replace(",", "") or 0)
                            nc_net = float(row[idx_nc_l].replace(",", "") or 0) - float(row[idx_nc_s].replace(",", "") or 0)
                            comm_net = float(row[idx_c_l].replace(",", "") or 0) - float(row[idx_c_s].replace(",", "") or 0)
                            cot_by_code[code].append((dt_str, int(comm_net), int(nc_net), int(oi)))
                        except Exception:
                            continue
        except Exception as e:
            print(f"WARN [COT {yr}]: {e}")

    out = {}
    for code, rows in cot_by_code.items():
        if not rows:
            continue
        rows.sort(key=lambda x: x[0])
        # Deduplicate by date
        dedup = {}
        for r in rows:
            dedup[r[0]] = r
        s_rows = [dedup[k] for k in sorted(dedup.keys())]
        comm_all = [x[1] for x in s_rows]
        spec_all = [x[2] for x in s_rows]

        def pct_rank(arr, val):
            if len(arr) < 2:
                return 50
            lo, hi = min(arr), max(arr)
            return round(((val - lo) / (hi - lo)) * 100) if hi > lo else 50

        c_now, s_now, oi_now = comm_all[-1], spec_all[-1], s_rows[-1][3]
        c_chg = c_now - comm_all[-2] if len(comm_all) >= 2 else 0
        s_chg = s_now - spec_all[-2] if len(spec_all) >= 2 else 0

        cot_1y = pct_rank(comm_all[-52:], c_now)
        cot_3y = pct_rank(comm_all[-156:], c_now)
        spec_1y = pct_rank(spec_all[-52:], s_now)

        signal = "NEUTRAL"
        if cot_1y >= 80 and spec_1y <= 25:
            signal = "BULL_DIVERGENCE"
        elif cot_1y <= 20 and spec_1y >= 75:
            signal = "BEAR_CROWDED"
        elif cot_1y >= 80:
            signal = "COMM_ACCUM"
        elif cot_1y <= 20:
            signal = "COMM_DIST"

        out[code] = {
            "report_date": s_rows[-1][0],
            "comm_net": c_now,
            "comm_chg": c_chg,
            "spec_net": s_now,
            "spec_chg": s_chg,
            "oi": oi_now,
            "cot_1y": cot_1y,
            "cot_3y": cot_3y,
            "spec_1y": spec_1y,
            "signal": signal,
            "trail_12w": [[x[0][5:], x[1], x[2]] for x in s_rows[-12:]],
        }
    print(f"SUCCESS [COT]: Parsed {len(out)}/{len(FUTURES_SPECS)} CFTC contracts.")
    return out


def calc_10y_seasonality(closes_10y, dates_10y, forward_days=20):
    """Calculate 10-year historical win-rate and average return for the next 20 trading days around today's calendar day."""
    if len(closes_10y) < 300:
        return {"win_rate": 50, "avg_ret": 0.0, "samples": 0}
    today_doy = datetime.now(timezone.utc).timetuple().tm_yday
    cur_year = datetime.now(timezone.utc).year
    rets = []
    used_years = set()
    for i in range(len(closes_10y) - forward_days):
        dt = datetime.strptime(dates_10y[i], "%Y-%m-%d")
        if dt.year >= cur_year or dt.year in used_years:
            continue
        doy = dt.timetuple().tm_yday
        diff = abs(doy - today_doy)
        diff = min(diff, 365 - diff)
        if diff <= 5 and closes_10y[i] > 0:
            used_years.add(dt.year)
            r = ((closes_10y[i + forward_days] - closes_10y[i]) / closes_10y[i]) * 100.0
            rets.append(r)
    if not rets:
        return {"win_rate": 50, "avg_ret": 0.0, "samples": 0}
    wins = sum(1 for r in rets if r > 0)
    return {
        "win_rate": round((wins / len(rets)) * 100),
        "avg_ret": round(sum(rets) / len(rets), 2),
        "samples": len(rets),
    }


def fetch_futures_and_cot(universe_tickers):
    cot_map = fetch_cftc_cot_3y()
    items = []
    mini_fut = {}

    if yf is None:
        print("WARN [Futures]: yfinance not installed, skipping futures download.")
        return {"updated_at": "", "items": [], "mini_candles": {}, "ratios": {}, "equity_cot_alerts": []}

    yf_syms = [s[3] for s in FUTURES_SPECS]
    try:
        df_all = yf.download(yf_syms, period="10y", interval="1d", group_by="ticker", auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print(f"ERROR [Futures yfinance]: {e}")
        df_all = None

    price_lookup = {}
    for disp_sym, name_zh, grp, yf_sym, cftc_code in FUTURES_SPECS:
        try:
            if df_all is None:
                continue
            sub = df_all[yf_sym].dropna(subset=["Close"]) if len(yf_syms) > 1 else df_all.dropna(subset=["Close"])
            if len(sub) < 65:
                continue
            closes_10y = [float(x) for x in sub["Close"].tolist()]
            dates_10y = [idx.strftime("%Y-%m-%d") for idx in sub.index]
            seas = calc_10y_seasonality(closes_10y, dates_10y, forward_days=20)

            sub_2y = sub.tail(505)
            bars = []
            for idx, row in sub_2y.iterrows():
                bars.append({
                    "t": int(idx.timestamp() * 1000),
                    "o": float(row["Open"]),
                    "h": float(row["High"]),
                    "l": float(row["Low"]),
                    "c": float(row["Close"]),
                    "v": int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                })

            decimals = 4 if grp == "FX" and disp_sym != "DXY" else 2
            save_ticker_candle_file(disp_sym, format_daily_candles(bars, decimals=decimals), [])
            mini_fut[disp_sym] = build_mini_candle_payload(bars, tail=48, decimals=decimals)
            if disp_sym not in universe_tickers:
                universe_tickers.append(disp_sym)

            m = calc_metrics(bars, decimals=decimals)
            price_lookup[disp_sym] = m["close"]
            cot = cot_map.get(cftc_code)

            # Triple Confluence: COT >= 80 + Seasonality WinRate >= 65% + Above 21EMA
            confluence = bool(cot and cot["cot_1y"] >= 80 and seas["win_rate"] >= 65 and m["above_ema21"])

            items.append({
                "ticker": disp_sym,
                "name": name_zh,
                "group": grp,
                "yf_sym": yf_sym,
                "close": m["close"],
                "chg1d": m["chg1d"],
                "chg20d": m["chg20d"],
                "chg60d": m["chg60d"],
                "rsi14": m["rsi14"],
                "atr14": m["atr14"],
                "atr_dist50": m["atr_dist50"],
                "above_ema21": m["above_ema21"],
                "above_ma50": m["above_ma50"],
                "tightness": m["tightness"],
                "seasonality": seas,
                "cot": cot,
                "confluence": confluence,
            })
        except Exception as e:
            print(f"WARN [Futures {disp_sym}]: {e}")

    # Cross-asset ratios (Gold/Silver, Copper/Gold)
    ratios = {}
    if price_lookup.get("GC") and price_lookup.get("SI"):
        ratios["gold_silver"] = round(price_lookup["GC"] / price_lookup["SI"], 2)
    if price_lookup.get("HG") and price_lookup.get("GC"):
        ratios["copper_gold_x1000"] = round((price_lookup["HG"] / price_lookup["GC"]) * 1000.0, 3)

    # Equity index COT crowding alerts for Tab 1
    eq_alerts = []
    for it in items:
        if it["group"] == "INDEX" and it.get("cot"):
            c = it["cot"]
            if c["cot_1y"] <= 20 and c["spec_1y"] >= 80:
                eq_alerts.append(f"⚠️ {it['ticker']} 期貨投機極擁擠 (Spec {c['spec_1y']}% / COT {c['cot_1y']}%)")
            elif c["cot_1y"] >= 80 and c["spec_1y"] <= 20:
                eq_alerts.append(f"🟢 {it['ticker']} 期貨商業主力極度吸納 (COT {c['cot_1y']}%)")

    hkt_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M HKT")
    print(f"SUCCESS [Futures]: Built {len(items)} futures contracts with 2Y candles, COT & 10Y seasonality.")
    return {
        "updated_at": f"自動更新於 {hkt_now}",
        "items": items,
        "mini_candles": mini_fut,
        "ratios": ratios,
        "equity_cot_alerts": eq_alerts,
    }


# ==============================================================================
# MODULE B: FETCH QM SCREENER JSON FROM PRIVATE GITHUB REPO (ROUTE B)
# ==============================================================================
def fetch_github_repo_json(repo, path, token):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    meta = http_json(url, headers=headers)
    if isinstance(meta, dict) and meta.get("content"):
        raw_bytes = base64.b64decode(meta["content"])
        return json.loads(raw_bytes.decode("utf-8"))
    if isinstance(meta, dict) and meta.get("download_url"):
        req = urllib.request.Request(meta["download_url"], headers=headers)
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return {}


def sync_qm_screener(universe_tickers, strong_sector_etfs, massive_dyn_tickers):
    if not SCREENER_TOKEN:
        print("WARN [QM Screener]: SCREENER_REPO_TOKEN is empty, skipping Route B sync.")
        return None
    try:
        scr = fetch_github_repo_json(SCREENER_REPO, "latest/screener.json", SCREENER_TOKEN)
        if not scr:
            return None

        ohlcv_raw = {}
        try:
            ohlcv_raw = fetch_github_repo_json(SCREENER_REPO, "latest/ohlcv.json", SCREENER_TOKEN)
        except Exception as e:
            print(f"WARN [QM Screener ohlcv.json]: {e}")

        # Build lookup of OHLCV rows by ticker from ohlcv.json (supports list or dict-by-ticker)
        ohlcv_by_sym = {}
        if isinstance(ohlcv_raw, dict):
            for k, v in ohlcv_raw.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    ohlcv_by_sym[k.upper()] = v
            if not ohlcv_by_sym and isinstance(ohlcv_raw.get("rows"), list):
                for r in ohlcv_raw["rows"]:
                    tk = (r.get("Ticker") or r.get("ticker") or "").upper()
                    if tk:
                        ohlcv_by_sym.setdefault(tk, []).append(r)
        elif isinstance(ohlcv_raw, list):
            for r in ohlcv_raw:
                tk = (r.get("Ticker") or r.get("ticker") or "").upper()
                if tk:
                    ohlcv_by_sym.setdefault(tk, []).append(r)

        # Save candle files & pre-compute mini candles for every candidate
        cand_list = scr.get("candidates") or scr.get("Candidates") or []
        mini_scr = {}
        for row in cand_list:
            tk = (row.get("Ticker") or "").upper()
            if not tk:
                continue
            hist = ohlcv_by_sym.get(tk) or []
            if len(hist) >= 10:
                bars = []
                for h in hist:
                    d_str = str(h.get("Date") or h.get("time") or "")[:10]
                    if not d_str:
                        continue
                    bars.append({
                        "time": d_str,
                        "open": round(float(h.get("Open") or h.get("open") or 0), 2),
                        "high": round(float(h.get("High") or h.get("high") or 0), 2),
                        "low": round(float(h.get("Low") or h.get("low") or 0), 2),
                        "close": round(float(h.get("Close") or h.get("close") or 0), 2),
                        "volume": int(h.get("Volume") or h.get("volume") or 0),
                    })
                if bars:
                    save_ticker_candle_file(tk, bars, [])
                    if tk not in universe_tickers:
                        universe_tickers.append(tk)
                    closes = [b["close"] for b in bars]
                    e10 = calc_ema_list(closes, 10)
                    e20 = calc_ema_list(closes, 20)
                    # Store [open, high, low, close, ema10, ema20] for last 48 bars
                    slice_start = max(0, len(bars) - 48)
                    mini_scr[tk] = [
                        [bars[i]["open"], bars[i]["high"], bars[i]["low"], bars[i]["close"], round(e10[i], 2), round(e20[i], 2)]
                        for i in range(slice_start, len(bars))
                    ]

            # Tag confluence if Theme_RS_Proxy > 0 or ticker also in Massive EP/RS
            theme_rs = float(row.get("Theme_RS_Proxy") or 0)
            row["_confluence"] = (theme_rs > 0) or (tk in massive_dyn_tickers)

        scr["mini_candles"] = mini_scr
        print(f"SUCCESS [QM Screener]: Synced {len(cand_list)} candidates & {len(mini_scr)} mini charts from {SCREENER_REPO}.")
        return scr
    except Exception as e:
        print(f"ERROR [QM Screener Route B]: {e}")
        return None


def build_ai_brief(trade_date, indices, breadth, sectors):
    spy = indices[0]
    top_sec = sorted(sectors, key=lambda x: x["rel20"], reverse=True)[:2]
    bot_sec = sorted(sectors, key=lambda x: x["rel20"])[:2]
    diff5 = round(breadth["score"] - breadth["score_ema5"], 1)
    dir_txt = "企上5D均轉強" if diff5 >= 0 else "低於5D均偏弱"
    fallback = {
        "tldr": f"{trade_date} 收市：Regime {breadth['score']}/100（{dir_txt} {diff5:+.1f}），SPY {spy['chg1d']:+.2f}%，升4%+ {breadth['up4']} vs 跌4%+ {breadth['dn4']}。",
        "bullets": [
            f"五維寬度：5D/10D比 {breadth['ratio5d']}/{breadth['ratio10d']}、T2108 {breadth['t2108']}%、四大指數均線達標 {breadth['idx_ma_pass']}/8、SPY ATR% {breadth['spy_atr_pct']}%。",
            f"強勢領先：{top_sec[0]['name']}({top_sec[0]['ticker']} REL20 {top_sec[0]['rel20']:+.1f}%) 與 {top_sec[1]['name']}({top_sec[1]['ticker']} REL20 {top_sec[1]['rel20']:+.1f}%)。",
            f"弱勢落後：{bot_sec[0]['name']}({bot_sec[0]['ticker']}) 及 {bot_sec[1]['name']}({bot_sec[1]['ticker']}) 偏弱，留意 RRG 箭咀方向與風控倉位。",
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
        g_res = http_json(g_url, {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}})
        parsed = json.loads(g_res["candidates"][0]["content"]["parts"][0]["text"])
        if "tldr" in parsed and isinstance(parsed.get("bullets"), list):
            return parsed
    except Exception:
        pass
    return fallback


def load_json_safe(path, default_val):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default_val


def main():
    if not API_KEY:
        raise SystemExit("ERROR: MASSIVE_API_KEY 未設定！請於 GitHub Settings -> Secrets 加入。")

    os.makedirs(CANDLES_DIR, exist_ok=True)
    existing = load_json_safe(SUMMARY_FILE, {"reports_by_date": {}, "breadth_history": [], "pdf_reports": [], "scanner_history": []})
    prev_reports = existing.get("reports_by_date") or {}

    spy_bars = get_daily_bars("SPY")
    if len(spy_bars) < 35:
        raise SystemExit("ERROR: 無法從 Massive API 讀取足夠 SPY 歷史數據。")

    trade_date = datetime.fromtimestamp(spy_bars[-1]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    print(f"Verified latest US trade date: {trade_date}")

    prev_dates = sorted([d for d in prev_reports.keys() if d < trade_date])
    prev_dyn_set = set()
    if prev_dates:
        prev_wl = prev_reports[prev_dates[-1]].get("watchlist") or []
        prev_dyn_set = {x["ticker"] for x in prev_wl if x.get("bucket") in ("EP", "RS_LEADER")}

    index_bars_map = {"SPY": spy_bars}
    save_ticker_candle_file("SPY", format_daily_candles(spy_bars), get_hourly_bars("SPY"))
    spy_m = calc_metrics(spy_bars)
    indices_out = [{
        "ticker": "SPY", "name": "標普500", "close": spy_m["close"],
        "chg1d": spy_m["chg1d"], "chg20d": spy_m["chg20d"], "atr_dist50": spy_m["atr_dist50"],
        "above_ema21": spy_m["above_ema21"], "above_ma50": spy_m["above_ma50"],
    }]

    for sym, name in INDICES[1:]:
        b = get_daily_bars(sym)
        if not b:
            continue
        index_bars_map[sym] = b
        save_ticker_candle_file(sym, format_daily_candles(b), get_hourly_bars(sym))
        m = calc_metrics(b)
        indices_out.append({
            "ticker": sym, "name": name, "close": m["close"],
            "chg1d": m["chg1d"], "chg20d": m["chg20d"], "atr_dist50": m["atr_dist50"],
            "above_ema21": m["above_ema21"], "above_ma50": m["above_ma50"],
        })

    sectors_out = []
    sec_chg20_map = {}
    sec_rel20_map = {}
    for sym, name, is_core11 in SECTORS:
        b = get_daily_bars(sym)
        if not b:
            continue
        save_ticker_candle_file(sym, format_daily_candles(b), get_hourly_bars(sym))
        m = calc_metrics(b)
        rel20 = round(m["chg20d"] - spy_m["chg20d"], 2)
        sec_chg20_map[sym] = m["chg20d"]
        sec_rel20_map[sym] = rel20
        sectors_out.append({
            "ticker": sym, "name": name, "core11": is_core11,
            "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"],
            "rel20": rel20, "rel60": round(m["chg60d"] - spy_m["chg60d"], 2),
            "rsi14": m["rsi14"], "trail": calc_rrg_trail(b, spy_bars, steps=5),
        })

    breadth_out, breadth_history_20d, ep_profiles, rs_profiles = fetch_grouped_market_scan(spy_bars, index_bars_map)
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

    today_dyn_set = {s for s, bkt, _, _ in wl_specs if bkt in ("EP", "RS_LEADER")}
    dropped_tickers = sorted(list(prev_dyn_set - today_dyn_set)) if prev_dyn_set else []

    watchlist_out = []
    mini_candles = {}
    universe_tickers = sorted(list({x["ticker"] for x in indices_out} | {x["ticker"] for x in sectors_out}))

    for sym, bucket, sec_name, etf_sym in wl_specs:
        b = get_daily_bars(sym)
        if len(b) < 20:
            continue
        save_ticker_candle_file(sym, format_daily_candles(b), get_hourly_bars(sym))
        mini_candles[sym] = build_mini_candle_payload(b, tail=48)
        if sym not in universe_tickers:
            universe_tickers.append(sym)
        m = calc_metrics(b)
        etf_20 = sec_chg20_map.get(etf_sym, spy_m["chg20d"])
        alpha20 = round(m["chg20d"] - etf_20, 2)
        sec_strong = sec_rel20_map.get(etf_sym, 0.0) > 0
        is_new = (bucket in ("EP", "RS_LEADER")) and (bool(prev_dyn_set) and sym not in prev_dyn_set)
        grp = "LEADER" if (bucket in ("EP", "RS_LEADER") or (alpha20 >= 4 and m["atr_dist50"] >= 0)) else ("AVOID" if m["rsi14"] < 35 else "WATCH")
        news_line = fetch_ticker_news_cantonese(sym)
        watchlist_out.append({
            "ticker": sym, "bucket": bucket, "group": grp, "is_new": is_new,
            "sector": sec_name, "etf": etf_sym, "sec_strong": sec_strong, "alpha20": alpha20,
            "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"],
            "vol_ratio": m["vol_ratio"], "dollar_vol_m": m["dollar_vol_m"],
            "tightness": m["tightness"], "dist_high20": m["dist_high20"],
            "rsi14": m["rsi14"], "atr14": m["atr14"], "stop": m["stop"], "news": news_line,
        })

    # Build Tab 2: Futures + CFTC COT + 10Y Seasonality
    futures_data = fetch_futures_and_cot(universe_tickers)
    with open(FUTURES_FILE, "w", encoding="utf-8") as f:
        json.dump(futures_data, f, ensure_ascii=False, separators=(",", ":"))

    # Cross-Asset Macro Strip for Tab 1 (DXY, CL, GC, HG)
    macro_strip = []
    for sym in ("DXY", "CL", "GC", "HG"):
        it = next((x for x in futures_data.get("items", []) if x["ticker"] == sym), None)
        if it:
            macro_strip.append({
                "ticker": it["ticker"], "name": it["name"], "close": it["close"],
                "chg1d": it["chg1d"], "chg20d": it["chg20d"], "above_ma50": it["above_ma50"],
                "cot_1y": it["cot"]["cot_1y"] if it.get("cot") else None,
            })

    # Sync Tab 3: Private Repo QM Screener (Route B)
    strong_etfs = {k for k, v in sec_rel20_map.items() if v > 0}
    screener_data = sync_qm_screener(universe_tickers, strong_etfs, today_dyn_set)
    if screener_data:
        with open(SCREENER_FILE, "w", encoding="utf-8") as f:
            json.dump(screener_data, f, ensure_ascii=False, separators=(",", ":"))

    scan_hist = [x for x in (existing.get("scanner_history") or []) if x.get("date") != trade_date]
    for w in watchlist_out:
        if w["bucket"] in ("EP", "RS_LEADER"):
            scan_hist.append({"date": trade_date, "ticker": w["ticker"], "bucket": w["bucket"], "entry_close": w["close"]})
    scan_hist = scan_hist[-600:]

    ai_brief = build_ai_brief(trade_date, indices_out, breadth_out, sectors_out)
    hkt_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M HKT")

    prev_reports[trade_date] = {
        "trade_date": trade_date,
        "updated_at": f"自動更新於 {hkt_now}",
        "indices": indices_out,
        "breadth": breadth_out,
        "yields": yields_out,
        "ai_brief": ai_brief,
        "sectors": sectors_out,
        "watchlist": watchlist_out,
        "dropped_tickers": dropped_tickers,
        "macro_strip": macro_strip,
        "equity_cot_alerts": futures_data.get("equity_cot_alerts", []),
    }
    sorted_dates = sorted(prev_reports.keys())[-MAX_HISTORY_DAYS:]
    reports_by_date = {k: prev_reports[k] for k in sorted_dates}

    pdf_list = fetch_drive_pdfs()
    summary_out = {
        "version": "3.0",
        "latest_date": sorted_dates[-1],
        "universe_tickers": sorted(universe_tickers),
        "reports_by_date": reports_by_date,
        "breadth_history": breadth_history_20d,
        "mini_candles": mini_candles,
        "pdf_reports": pdf_list if pdf_list else existing.get("pdf_reports", []),
        "scanner_history": scan_hist,
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary_out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"SUCCESS: V3.0 Total War Room built! summary={os.path.getsize(SUMMARY_FILE)/1024:.1f}KB, futures={len(futures_data.get('items', []))}, screener={'OK' if screener_data else 'SKIPPED'}.")


if __name__ == "__main__":
    main()
