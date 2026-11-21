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
REPORTS_DIR = "reports"
MAX_HISTORY_DAYS = 30
CANDLE_DAYS_TO_KEEP = 180

INDICES = [
    ("SPY", "標普500"),
    ("VOO", "Vanguard標普"),
    ("QQQ", "納指100"),
    ("MAGS", "科技七巨頭"),
    ("IWM", "羅素2000"),
    ("RSP", "等權標普"),
]

SECTORS = [
    ("XLE", "能源"), ("XLU", "公用事業"), ("XLP", "必需消費"),
    ("XLF", "金融"), ("XLRE", "房地產"), ("XLK", "科技"),
    ("XLV", "醫療保健"), ("XLI", "工業"), ("XLB", "原材料"),
    ("XLC", "通訊服務"), ("XLY", "非必需消費"), ("SOXX", "半導體"),
    ("SMH", "晶片ETF"), ("IEF", "7-10年美債"), ("TLT", "20年美債"),
]

# (Ticker, Sector Name, Benchmark ETF)
WATCHLIST_META = [
    ("PANW", "網絡安全", "XLK"),
    ("WMT", "必需消費", "XLP"),
    ("COST", "量販零售", "XLP"),
    ("DLTR", "折扣零售", "XLP"),
    ("PG", "日用消費", "XLP"),
    ("XOM", "能源石油", "XLE"),
    ("CVX", "能源石油", "XLE"),
    ("LLY", "醫藥生科", "XLV"),
    ("ABBV", "醫藥生科", "XLV"),
    ("UNH", "醫療保險", "XLV"),
    ("NVDA", "半導體", "SOXX"),
    ("AMD", "半導體", "SOXX"),
    ("AVGO", "半導體", "SOXX"),
    ("META", "通訊平台", "XLC"),
    ("AAPL", "消費科技", "XLK"),
]


def http_json(url, payload=None, retries=3):
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "TacticalHubBot/3.0"})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                print("Rate limit hit (429), sleeping 15s...")
                time.sleep(15)
                continue
            raise
    return {}


def get_bars(ticker, calendar_days=300):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=calendar_days)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=500&apiKey={API_KEY}"
    )
    return http_json(url).get("results", [])


def format_candles(bars, limit=CANDLE_DAYS_TO_KEEP):
    out = []
    for b in bars[-limit:]:
        d_str = datetime.fromtimestamp(b["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
        out.append({
            "time": d_str,
            "open": round(float(b["o"]), 2),
            "high": round(float(b["h"]), 2),
            "low": round(float(b["l"]), 2),
            "close": round(float(b["c"]), 2),
            "volume": int(b.get("v", 0)),
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
    except Exception as e:
        print(f"Breadth fallback: {e}")
        return {"up4": 0, "dn4": 0, "adv_pct": 50.0, "total_scanned": 0}


def calc_score(up4, dn4, adv_pct, spy_atr):
    ratio_score = (up4 / (up4 + dn4)) * 40 if (up4 + dn4) > 0 else 20
    adv_score = max(0, min(100, adv_pct)) * 0.35
    atr_score = max(0, min(25, ((spy_atr + 2.5) / 5) * 25))
    return round(ratio_score + adv_score + atr_score)


def build_ai_brief(trade_date, indices, breadth, sectors):
    spy = indices[0]
    mags = next((x for x in indices if x["ticker"] == "MAGS"), spy)
    voo = next((x for x in indices if x["ticker"] == "VOO"), spy)
    top_sec = sorted(sectors, key=lambda x: x["rel20"], reverse=True)[:2]
    bot_sec = sorted(sectors, key=lambda x: x["rel20"])[:2]
    fallback = {
        "tldr": f"{trade_date} 收市：SPY {spy['chg1d']:+.2f}%、VOO {voo['chg1d']:+.2f}%、MAGS {mags['chg1d']:+.2f}%；升4%+ {breadth['up4']} vs 跌4%+ {breadth['dn4']}。",
        "bullets": [
            f"基準對比：SPY 20D {spy['chg20d']:+.2f}%（{spy['atr_dist50']} ATR）、MAGS 20D {mags['chg20d']:+.2f}%、VOO 20D {voo['chg20d']:+.2f}%。",
            f"強勢板塊：{top_sec[0]['name']}({top_sec[0]['ticker']} REL20 {top_sec[0]['rel20']:+.1f}%) 與 {top_sec[1]['name']}({top_sec[1]['ticker']} REL20 {top_sec[1]['rel20']:+.1f}%) 領先。",
            f"弱勢板塊：{bot_sec[0]['name']}({bot_sec[0]['ticker']}) 及 {bot_sec[1]['name']}({bot_sec[1]['ticker']}) 偏弱，留意 180 日型態支持。",
        ],
    }
    if not GEMINI_KEY:
        return fallback
    try:
        prompt = (
            "你係香港資深美股動能交易員。請嚴格根據以下已核實數據，用貼地香港廣東話輸出純 JSON "
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


def generate_daily_pdf(trade_date, score, indices, breadth, sectors, candles_map, watchlist):
    """Automatically generate a 2-page Tactical PDF report in reports/ without manual upload."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except ImportError:
        print("matplotlib not installed; skipping auto PDF generation.")
        return

    os.makedirs(REPORTS_DIR, exist_ok=True)
    pdf_path = os.path.join(REPORTS_DIR, f"{trade_date}_Tactical_Momentum_Brief.pdf")

    plt.rcParams["axes.edgecolor"] = "#334d42"
    plt.rcParams["axes.linewidth"] = 0.8

    with PdfPages(pdf_path) as pdf:
        # Page 1: Benchmarks + Breadth + Sector REL20 Bar Chart
        fig = plt.figure(figsize=(8.27, 11.69), facecolor="#0a100e")
        fig.suptitle(f"US MOMENTUM TACTICAL BRIEF  |  {trade_date}", color="#d4af37", fontsize=15, fontweight="bold", y=0.96)
        fig.text(0.5, 0.93, f"Regime Score: {score}/100  |  Up 4%+: {breadth['up4']}  |  Dn 4%+: {breadth['dn4']}  |  Adv%: {breadth['adv_pct']}%",
                 ha="center", color="#cbd5e1", fontsize=10)

        # Subplot 1: 6 Benchmarks 20D% Bar Chart
        ax1 = fig.add_axes([0.10, 0.63, 0.82, 0.24], facecolor="#0f1815")
        idx_labels = [x["ticker"] for x in indices]
        idx_vals = [x["chg20d"] for x in indices]
        idx_cols = ["#10b981" if v >= 0 else "#f43f5e" for v in idx_vals]
        ax1.bar(idx_labels, idx_vals, color=idx_cols, width=0.55)
        ax1.axhline(0, color="#64748b", linewidth=0.8)
        ax1.set_title("6 Core Benchmarks (20D Return %)", color="#f1f5f9", fontsize=11, pad=8)
        ax1.tick_params(colors="#cbd5e1", labelsize=9)
        for i, v in enumerate(idx_vals):
            ax1.text(i, v + (0.3 if v >= 0 else -0.9), f"{v:+.1f}%", ha="center", color="#f8fafc", fontsize=8, fontweight="bold")

        # Subplot 2: Sector REL20 Horizontal Diverging Bar Chart
        ax2 = fig.add_axes([0.14, 0.08, 0.78, 0.48], facecolor="#0f1815")
        sec_sorted = sorted(sectors, key=lambda x: x["rel20"])
        s_labels = [s["ticker"] for s in sec_sorted]
        s_vals = [s["rel20"] for s in sec_sorted]
        s_cols = ["#10b981" if v >= 0 else "#f43f5e" for v in s_vals]
        ax2.barh(s_labels, s_vals, color=s_cols, height=0.65)
        ax2.axvline(0, color="#94a3b8", linewidth=0.9)
        ax2.set_title("Sector & Theme ETF Leaderboard (REL20 vs SPY %)", color="#f1f5f9", fontsize=11, pad=8)
        ax2.tick_params(colors="#cbd5e1", labelsize=8.5)
        for i, v in enumerate(s_vals):
            ax2.text(v + (0.15 if v >= 0 else -0.15), i, f"{v:+.1f}%", va="center",
                     ha="left" if v >= 0 else "right", color="#f8fafc", fontsize=7.5)

        pdf.savefig(fig, facecolor=fig.get_facecolor())
        plt.close(fig)

        # Page 2: 2x2 Focus Candlestick Charts (Last 60 Trading Days)
        fig2, axes = plt.subplots(2, 2, figsize=(8.27, 11.69), facecolor="#0a100e")
        fig2.suptitle(f"TOP FOCUS 60-DAY CANDLESTICKS  |  {trade_date}", color="#d4af37", fontsize=14, fontweight="bold", y=0.96)
        top4 = sorted(watchlist, key=lambda x: x["chg20d"], reverse=True)[:4]
        for ax, item in zip(axes.flat, top4):
            sym = item["ticker"]
            ax.set_facecolor("#0f1815")
            bars = candles_map.get(sym, [])[-60:]
            if bars:
                xs = list(range(len(bars)))
                closes = [b["close"] for b in bars]
                for i, b in enumerate(bars):
                    col = "#10b981" if b["close"] >= b["open"] else "#f43f5e"
                    ax.plot([i, i], [b["low"], b["high"]], color=col, linewidth=0.8)
                    ax.bar(i, max(0.05, abs(b["close"] - b["open"])), bottom=min(b["open"], b["close"]), color=col, width=0.6)
                # 21 EMA
                k = 2 / 22.0
                ema, prev = [], closes[0]
                for c in closes:
                    prev = c * k + prev * (1 - k)
                    ema.append(prev)
                ax.plot(xs, ema, color="#2dd4bf", linewidth=1.2, label="21 EMA")
            ax.set_title(f"{sym}  ${item['close']}  (1D {item['chg1d']:+.1f}% | 20D {item['chg20d']:+.1f}%)", color="#f1f5f9", fontsize=10)
            ax.tick_params(colors="#94a3b8", labelsize=7.5)
            ax.grid(color="#1a2822", linestyle="--", linewidth=0.5)

        fig2.subplots_adjust(left=0.09, right=0.95, top=0.90, bottom=0.06, hspace=0.28, wspace=0.22)
        pdf.savefig(fig2, facecolor=fig2.get_facecolor())
        plt.close(fig2)

    print(f"Auto-generated PDF report: {pdf_path}")


def scan_pdfs(limit=30):
    pdfs = []
    for path in sorted(glob.glob(f"{REPORTS_DIR}/*.pdf"), reverse=True)[:limit]:
        fname = os.path.basename(path)
        pdfs.append({
            "name": fname,
            "date": fname[:10] if len(fname) >= 10 else "",
            "url": f"{REPORTS_DIR}/{fname}",
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
            if js_obj.startswith("{"):
                return json.loads(js_obj)
    return {"reports_by_date": {}, "trend_history": [], "candles": {}, "pdf_reports": []}


def main():
    if not API_KEY:
        raise SystemExit("ERROR: MASSIVE_API_KEY 未設定！請喺 GitHub Settings -> Secrets 加入。")

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    hub = extract_existing_hub(html)

    spy_bars = get_bars("SPY", calendar_days=300)
    if len(spy_bars) < 2:
        raise SystemExit("ERROR: 無法從 Massive API 讀取 SPY 數據。")

    trade_date = datetime.fromtimestamp(spy_bars[-1]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    prev_date = datetime.fromtimestamp(spy_bars[-2]["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    print(f"Latest US trade date: {trade_date} (prev: {prev_date})")

    candles_map = {"SPY": format_candles(spy_bars)}
    spy_m = calc_metrics(spy_bars)
    indices_out = [{"ticker": "SPY", "name": "標普500", "close": spy_m["close"], "chg1d": spy_m["chg1d"], "chg20d": spy_m["chg20d"], "atr_dist50": spy_m["atr_dist50"]}]

    for sym, name in INDICES[1:]:
        b = get_bars(sym, calendar_days=300)
        if not b:
            continue
        candles_map[sym] = format_candles(b)
        m = calc_metrics(b)
        indices_out.append({"ticker": sym, "name": name, "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"], "atr_dist50": m["atr_dist50"]})

    sectors_out = []
    sec_chg20_map = {}
    for sym, name in SECTORS:
        b = get_bars(sym, calendar_days=300)
        if not b:
            continue
        candles_map[sym] = format_candles(b)
        m = calc_metrics(b)
        sec_chg20_map[sym] = m["chg20d"]
        sectors_out.append({
            "ticker": sym, "name": name, "close": m["close"],
            "chg1d": m["chg1d"], "chg20d": m["chg20d"],
            "rel20": round(m["chg20d"] - spy_m["chg20d"], 2), "rsi14": m["rsi14"]
        })

    watchlist_out = []
    for sym, sector_name, etf_sym in WATCHLIST_META:
        b = get_bars(sym, calendar_days=300)
        if not b:
            continue
        candles_map[sym] = format_candles(b)
        m = calc_metrics(b)
        rel_spy = m["chg20d"] - spy_m["chg20d"]
        etf_20d = sec_chg20_map.get(etf_sym, spy_m["chg20d"])
        alpha20 = round(m["chg20d"] - etf_20d, 2)
        if rel_spy >= 4 and m["atr_dist50"] >= 0:
            grp, note = "LEADER", f"跑贏大市與 {etf_sym}，企穩 50MA 上方"
        elif m["atr_dist50"] < -1.2 or m["rsi14"] < 35:
            grp, note = "AVOID", "弱勢跌穿均線，暫時避開"
        else:
            grp, note = "WATCH", "觀察均線承接與縮量整理"
        watchlist_out.append({
            "ticker": sym, "group": grp, "sector": sector_name, "etf": etf_sym, "alpha20": alpha20,
            "close": m["close"], "chg1d": m["chg1d"], "chg20d": m["chg20d"], "rsi14": m["rsi14"],
            "stop": m["stop"], "note": note
        })

    breadth_out = fetch_breadth(trade_date, prev_date)
    ai_brief = build_ai_brief(trade_date, indices_out, breadth_out, sectors_out)
    sc = calc_score(breadth_out["up4"], breadth_out["dn4"], breadth_out["adv_pct"], spy_m["atr_dist50"])

    # Auto-generate daily PDF into reports/
    generate_daily_pdf(trade_date, sc, indices_out, breadth_out, sectors_out, candles_map, watchlist_out)

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

    hist = [x for x in (hub.get("trend_history") or []) if x.get("date") != trade_date[5:]]
    hist.append({"date": trade_date[5:], "score": sc, "spy_chg": spy_m["chg20d"]})
    hist = hist[-MAX_HISTORY_DAYS:]

    hub_out = {
        "latest_date": sorted_dates[-1],
        "reports_by_date": reports_by_date,
        "trend_history": hist,
        "candles": candles_map,
        "pdf_reports": scan_pdfs(),
    }

    s_tag, e_tag = "/*__HUB_DATA_START__*/", "/*__HUB_DATA_END__*/"
    injected = f"{s_tag}\nconst HUB_DATA = {json.dumps(hub_out, ensure_ascii=False)};\n{e_tag}"
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html.split(s_tag)[0] + injected + html.split(e_tag)[1])

    print("SUCCESS: index.html & daily PDF updated.")


if __name__ == "__main__":
    main()
