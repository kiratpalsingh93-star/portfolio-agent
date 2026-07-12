#!/usr/bin/env python3
"""
Portfolio Daily Digest Agent v2
Tracks Indian (NSE) + US equities.

New in v2:
  - RSI (14-day momentum indicator)
  - Relative Strength vs Nifty / S&P 500 (3-month)
  - P/E vs Sector average
  - Redesigned output: grouped by stage, alerts first
  - Market Pulse: news + Reddit + YouTube links
  - Sends email to multiple recipients

Usage:
  python portfolio_agent.py              → run full digest
  python portfolio_agent.py --chat-id    → print your Telegram Chat ID
  python portfolio_agent.py --test       → send a test Telegram ping
"""

import sys
import smtplib
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============================================================
# CREDENTIALS — loaded from environment variables (GitHub Secrets)
# When running locally, you can still hardcode them below as fallback.
# ============================================================
import os

TELEGRAM_TOKEN    = os.environ.get("8516185511",    "8833949026:AAG_O3abIz08W_l714iV8FHPX8UjcD60aOs")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID",  "PASTE_CHAT_ID_HERE")
GMAIL_ADDRESS     = os.environ.get("kiratpalsingh93@gmail.com",     "PASTE_YOUR_GMAIL_HERE")
GMAIL_APP_PASS    = os.environ.get("isaj gcvv avmm rdib",    "PASTE_APP_PASSWORD_HERE")

EMAIL_TO = [
    "kiratpalsingh93@gmail.com",
    "baldeep.singh.bakshi@gmail.com",
]
# ============================================================

# ── WATCHLIST ────────────────────────────────────────────────
# (yf_ticker, display_name, avg_buy_price, currency_symbol)
INDIAN = [
    ("APOLLOHOSP.NS",  "Apollo Hosp",          7235.47, "₹"),
    ("ARE&M.NS",       "Amara Raja",             999.55, "₹"),
    ("ETERNAL.NS",     "Eternal/Zomato",         197.82, "₹"),
    ("FIRSTCRY.NS",    "FirstCry",               465.00, "₹"),
    ("FORTIS.NS",      "Fortis Health",           903.72, "₹"),
    ("GOLDIETF.NS",    "Gold ETF (HDFC)",          119.94, "₹"),
    ("GROWW.NS",       "Groww",                  100.00, "₹"),
    ("HDFCBANK.NS",    "HDFC Bank",              488.95, "₹"),
    ("HINDUNILVR.NS",  "HUL",                   2307.45, "₹"),
    ("IDFCFIRSTB.NS",  "IDFC First Bank",         69.84, "₹"),
    ("ITBEES.NS",      "IT BeES ETF",             38.08, "₹"),
    ("JIOFIN.NS",      "Jio Financial",          249.57, "₹"),
    ("KOTAKBANK.NS",   "Kotak Bank",             355.95, "₹"),
    ("KWIL.NS",        "KWIL",                    44.07, "₹"),
    ("MAXHEALTH.NS",   "Max Healthcare",        1134.77, "₹"),
    ("NIFTYADD.NS",    "Nifty ADD ETF",          239.33, "₹"),
    ("NIFTYBEES.NS",   "Nifty BeES",             264.88, "₹"),
    ("PGEL.NS",        "PG Electroplast",        575.00, "₹"),
    ("SBICARD.NS",     "SBI Card",               750.55, "₹"),
    ("TEJASNET.NS",    "Tejas Networks",         599.93, "₹"),
    ("TITAN.NS",       "Titan",                 3296.15, "₹"),
    ("URBANCO.NS",     "Urban Company",          143.71, "₹"),
]

US = [
    ("ADBE",  "Adobe",         302.33, "$"),
    ("AMZN",  "Amazon",        220.19, "$"),
    ("CRWD",  "CrowdStrike",   102.88, "$"),
    ("DUOL",  "Duolingo",      182.93, "$"),
    ("GOOGL", "Alphabet",      314.35, "$"),
    ("LLY",   "Eli Lilly",    1115.81, "$"),
    ("META",  "Meta",          645.01, "$"),
    ("MSFT",  "Microsoft",     410.40, "$"),
    ("NFLX",  "Netflix",        86.61, "$"),
    ("NOW",   "ServiceNow",    100.95, "$"),
    ("NVDA",  "NVIDIA",        197.07, "$"),
    ("NVO",   "Novo Nordisk",   51.50, "$"),
    ("PANW",  "Palo Alto",     165.27, "$"),
    ("QTEC",  "QTEC ETF",      216.02, "$"),
    ("SOXX",  "Semi ETF",      317.70, "$"),
    ("SPY",   "S&P 500 ETF",   644.58, "$"),
    ("UNH",   "UnitedHealth",  295.15, "$"),
]

# ── SECTOR P/E BENCHMARKS (approximate 2025 market averages) ──
SECTOR_PE = {
    "Technology":              28,
    "Healthcare":              22,
    "Financial Services":      14,
    "Consumer Defensive":      28,
    "Consumer Cyclical":       20,
    "Communication Services":  18,
    "Energy":                  12,
    "Industrials":             20,
    "Basic Materials":         15,
    "Real Estate":             25,
    "Utilities":               16,
}

# ETFs — no P/E ratio applies
ETF_TICKERS = {
    "GOLDIETF.NS", "ITBEES.NS", "NIFTYADD.NS", "NIFTYBEES.NS",
    "QTEC", "SOXX", "SPY",
}

STAGE_LABEL = {
    0: "?",
    1: "Stage 1⚪ Base",
    2: "Stage 2🟢 Uptrend",
    3: "Stage 3🟡 Topping",
    4: "Stage 4🔴 Downtrend",
}

_bench_cache = {}


# ============================================================
# HELPERS
# ============================================================

def _pf(price, sym):
    """Format price with currency symbol."""
    return f"{sym}{price:,.0f}" if sym == "₹" else f"{sym}{price:,.2f}"

def _pct(v, decimals=1):
    arrow = "▲" if v >= 0 else "▼"
    return f"{arrow}{abs(v):.{decimals}f}%"

def _bench_return(symbol, n=63):
    """3-month return for a benchmark ticker (cached)."""
    if symbol in _bench_cache:
        return _bench_cache[symbol]
    try:
        h = yf.Ticker(symbol).history(period="100d")["Close"]
        n = min(n, len(h) - 1)
        ret = (float(h.iloc[-1]) - float(h.iloc[-n])) / float(h.iloc[-n]) * 100
        _bench_cache[symbol] = ret
        return ret
    except Exception:
        _bench_cache[symbol] = None
        return None

def _calc_rsi(close, period=14):
    """14-day RSI from close price series."""
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    val   = 100 - 100 / (1 + rs)
    v = float(val.iloc[-1])
    return None if pd.isna(v) else v

def _stage(price, ma50, ma150, ma250, close):
    """Weinstein stage: 2=uptrend, 4=downtrend, 3=topping, 1=base."""
    if ma50 is None or ma150 is None:
        return 0
    def rising(n):
        s = close.rolling(n).mean()
        return (float(s.iloc[-1]) > float(s.iloc[-21])) if len(s) >= n + 21 else None
    r50   = rising(50)
    ab50  = price > ma50
    ab150 = price > ma150
    ab250 = (price > ma250) if ma250 else True
    if ab50 and ab150 and ab250 and r50:           return 2
    if not ab50 and not ab150 and r50 is False:   return 4
    if ab50 and ab150 and r50 is False:            return 3
    return 1

def _action(stage, pct_buy, rsi):
    """Suggested action string."""
    if stage == 2:
        if rsi and rsi > 75:
            return "Hold (Overbought)"
        return "Add on Dip" if pct_buy < 0 else "Hold"
    if stage == 3: return "Trim / Watch"
    if stage == 4: return "Exit?"
    return "Watch"

def _stage_bg(stage):
    """Email row background color by stage."""
    return {2: "#eaf7ee", 3: "#fff9e6", 4: "#fdecea", 1: "#f8f9fa", 0: "#ffffff"}[stage]


# ============================================================
# ANALYSIS
# ============================================================

def analyse(ticker, name, avg_buy, sym, benchmark):
    """Fetch and analyse one stock. Returns dict."""
    try:
        t    = yf.Ticker(ticker)
        hist = t.history(period="300d")

        if hist is None or hist.empty or len(hist) < 52:
            return {"name": name, "ticker": ticker, "sym": sym, "error": "data unavailable"}

        close = hist["Close"]
        price = float(close.iloc[-1])
        prev  = float(close.iloc[-2]) if len(close) > 1 else price

        def ma(n):
            return float(close.rolling(n).mean().iloc[-1]) if len(close) >= n else None

        ma50, ma150, ma250 = ma(50), ma(150), ma(250)
        rsi   = _calc_rsi(close)
        stage = _stage(price, ma50, ma150, ma250, close)

        # ── Relative strength vs benchmark (3 months ≈ 63 trading days) ──
        n_rs     = min(63, len(close) - 1)
        stock_3m = (price - float(close.iloc[-n_rs])) / float(close.iloc[-n_rs]) * 100
        bench_3m = _bench_return(benchmark, n_rs)
        rs_diff  = (stock_3m - bench_3m) if bench_3m is not None else None

        # ── P/E vs sector average ──
        pe_str = "N/A"
        if ticker not in ETF_TICKERS:
            try:
                info      = t.info
                pe        = info.get("trailingPE") or info.get("forwardPE")
                sector    = info.get("sector", "")
                sector_pe = SECTOR_PE.get(sector)
                if pe and not pd.isna(pe):
                    pe = round(float(pe), 1)
                    if sector_pe:
                        prem = (pe - sector_pe) / sector_pe * 100
                        tag  = "↑ premium" if prem > 15 else ("↓ discount" if prem < -15 else "≈ fair")
                        pe_str = f"{pe}x vs {sector_pe}x ({tag})"
                    else:
                        pe_str = f"{pe}x"
            except Exception:
                pass

        # ── MA icons ──
        ma_icons = []
        for label, val in [("50d", ma50), ("150d", ma150), ("250d", ma250)]:
            if val is None:   ma_icons.append(f"{label}:--")
            elif price > val: ma_icons.append(f"{label}✅")
            else:             ma_icons.append(f"{label}❌")

        pct_buy  = (price - avg_buy) / avg_buy * 100
        hi52     = float(close.tail(252).max())
        lo52     = float(close.tail(252).min())
        pct_hi52 = (price - hi52) / hi52 * 100
        pct_lo52 = (price - lo52) / lo52 * 100
        day_chg  = (price - prev) / prev * 100

        return {
            "ticker":   ticker,
            "name":     name,
            "sym":      sym,
            "price":    price,
            "avg_buy":  avg_buy,
            "pct_buy":  pct_buy,
            "day_chg":  day_chg,
            "ma50":     ma50, "ma150": ma150, "ma250": ma250,
            "ma_icons": "  ".join(ma_icons),
            "hi52":     hi52,  "lo52":     lo52,
            "pct_hi52": pct_hi52, "pct_lo52": pct_lo52,
            "stage":    stage,
            "rsi":      rsi,
            "rs_diff":  rs_diff,
            "pe_str":   pe_str,
            "error":    None,
        }

    except Exception as e:
        return {"name": name, "ticker": ticker, "sym": sym, "error": str(e)[:60]}


def analyse_all():
    """Analyse all 39 stocks in parallel. Returns sorted list of dicts."""
    tasks = (
        [(t, n, b, s, "^NSEI") for t, n, b, s in INDIAN] +
        [(t, n, b, s, "SPY")   for t, n, b, s in US]
    )

    # Pre-warm benchmark cache (sequential, so it's available to all threads)
    _bench_return("^NSEI")
    _bench_return("SPY")

    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(analyse, *args): args for args in tasks}
        for fut in as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda r: (r.get("sym", ""), r.get("name", "")))
    return results


# ============================================================
# NEWS
# ============================================================

def get_news(tickers, max_items=7):
    """Fetch recent news from yfinance (handles old + new API formats)."""
    seen, items = set(), []
    for ticker in tickers:
        if len(items) >= max_items:
            break
        try:
            news_list = yf.Ticker(ticker).news or []
            for n in news_list[:2]:
                # New yfinance format wraps content inside "content" key
                if "content" in n:
                    title = n["content"].get("title", "")
                    link  = (n["content"].get("canonicalUrl") or {}).get("url", "")
                    pub   = (n["content"].get("provider") or {}).get("displayName", "")
                else:
                    title = n.get("title", "")
                    link  = n.get("link", "")
                    pub   = n.get("publisher", "")
                if title and link and title not in seen:
                    seen.add(title)
                    items.append({"title": title, "link": link, "publisher": pub})
        except Exception:
            continue
    return items


# ============================================================
# TELEGRAM OUTPUT
# ============================================================

def _tg_compact(r):
    """One compact two-line block per stock for Telegram."""
    price_f = _pf(r["price"], r["sym"])
    rsi_s   = f"{r['rsi']:.0f}" if r.get("rsi") else "N/A"
    rs_s    = f"{r['rs_diff']:+.0f}%" if r.get("rs_diff") is not None else "N/A"
    return (
        f"*{r['name']}*  {price_f}  {_pct(r['pct_buy'])} vs buy\n"
        f"   {r['ma_icons']}  RSI:{rsi_s}  RS:{rs_s}  PE:{r['pe_str']}"
    )


def build_telegram(results):
    """Returns list of Telegram message strings."""
    today = datetime.now().strftime("%d %b %Y")
    msgs  = []

    for label, sym in [("🇮🇳 India", "₹"), ("🇺🇸 US", "$")]:
        recs = [r for r in results if r.get("sym") == sym]

        # ── Alerts section ──
        alerts = [r for r in recs if not r.get("error") and r.get("stage") in (3, 4)]
        alert_lines = []
        for r in sorted(alerts, key=lambda x: x["stage"], reverse=True):
            icon = "🔴" if r["stage"] == 4 else "🟡"
            a    = _action(r["stage"], r["pct_buy"], r.get("rsi"))
            alert_lines.append(
                f"{icon} *{r['name']}*  {_pf(r['price'], r['sym'])}  "
                f"{_pct(r['pct_buy'])}  →  *{a}*"
            )

        msg = f"📊 *Portfolio — {today}*\n{label}\n"

        if alert_lines:
            msg += "\n⚠️ *ACTION NEEDED*\n" + "\n".join(alert_lines)

        for stage_val, hdr in [
            (2, "\n━━ 🟢 UPTREND — Hold / Add (Stage 2) ━━"),
            (1, "\n━━ ⚪ BASING  — Watch (Stage 1) ━━"),
            (4, "\n━━ 🔴 DOWNTREND — Exit? (Stage 4) ━━"),
            (3, "\n━━ 🟡 TOPPING  — Trim (Stage 3) ━━"),
        ]:
            grp = [r for r in recs if not r.get("error") and r.get("stage") == stage_val]
            if grp:
                msg += f"\n{hdr}\n"
                msg += "\n\n".join(_tg_compact(r) for r in grp)

        errs = [r for r in recs if r.get("error")]
        if errs:
            msg += "\n\n⚫ *Unavailable:* " + ", ".join(r["name"] for r in errs)

        msgs.append(msg)

    return msgs


def build_telegram_news(news_items):
    """Market Pulse message for Telegram."""
    lines = ["📰 *Market Pulse*"]

    for n in news_items:
        pub = f" ({n['publisher']})" if n.get("publisher") else ""
        lines.append(f"• [{n['title']}]({n['link']}){pub}")

    if not news_items:
        lines.append("_(no news fetched today)_")

    lines += [
        "",
        "🔍 [India Market News](https://news.google.com/search?q=India+NSE+stock+market+today)",
        "🔍 [US Market News](https://news.google.com/search?q=US+stock+market+today)",
        "📱 [r/IndiaInvestments](https://reddit.com/r/IndiaInvestments/)",
        "📱 [r/stocks](https://reddit.com/r/stocks/)",
        "📱 [r/investing](https://reddit.com/r/investing/)",
        "▶️ [India stocks YouTube](https://youtube.com/results?search_query=India+stock+market+analysis+today)",
        "▶️ [US stocks YouTube](https://youtube.com/results?search_query=US+stock+market+analysis+today)",
    ]
    return "\n".join(lines)


# ============================================================
# EMAIL OUTPUT
# ============================================================

def _email_row(r):
    """HTML table row for one stock."""
    if r.get("error"):
        return (f"<tr><td colspan='9' style='color:#888;font-style:italic;padding:6px'>"
                f"{r['name']}: {r['error']}</td></tr>")

    bg      = _stage_bg(r["stage"])
    sym     = r["sym"]
    buy_col = "#1a7a3c" if r["pct_buy"] >= 0 else "#a31c1c"
    day_col = "#1a7a3c" if r["day_chg"] >= 0 else "#a31c1c"

    rsi = r.get("rsi")
    rsi_str = f"{rsi:.0f}" if rsi else "N/A"
    rsi_col = ""
    if rsi:
        if rsi >= 70:   rsi_col = "color:#a31c1c;font-weight:bold"
        elif rsi <= 30: rsi_col = "color:#1a7a3c;font-weight:bold"

    rs     = r.get("rs_diff")
    rs_str = f"{rs:+.1f}%" if rs is not None else "N/A"
    rs_col = "color:#1a7a3c" if (rs is not None and rs > 0) else "color:#a31c1c"

    action = _action(r["stage"], r["pct_buy"], rsi)
    stage_emojis = {1: "⚪", 2: "🟢", 3: "🟡", 4: "🔴", 0: "⚫"}
    action_label = f"{stage_emojis.get(r['stage'], '')} {action}"

    return f"""
    <tr style="background:{bg}">
      <td style="padding:6px 10px"><b>{r['name']}</b>
          <br><span style="color:#888;font-size:11px">{r['ticker']}</span></td>
      <td style="padding:6px 10px">{_pf(r['price'], sym)}</td>
      <td style="padding:6px 10px;color:{day_col}">{_pct(r['day_chg'])}</td>
      <td style="padding:6px 10px;color:{buy_col};font-weight:bold">{_pct(r['pct_buy'])}</td>
      <td style="padding:6px 10px;font-size:12px">{r['ma_icons']}</td>
      <td style="padding:6px 10px;{rsi_col}">{rsi_str}</td>
      <td style="padding:6px 10px;{rs_col}">{rs_str}</td>
      <td style="padding:6px 10px;font-size:11px">{r['pe_str']}</td>
      <td style="padding:6px 10px;font-weight:bold">{action_label}</td>
    </tr>"""


def _email_section(title, title_color, recs, stage_filter):
    """HTML block for one stage group."""
    grp = sorted(
        [r for r in recs if not r.get("error") and r.get("stage") == stage_filter],
        key=lambda r: r["pct_buy"], reverse=True
    )
    if not grp:
        return ""
    rows = "".join(_email_row(r) for r in grp)
    return f"""
    <h3 style="color:{title_color};margin:20px 0 6px">{title}</h3>
    <table border="1" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;font-size:13px;width:100%;margin-bottom:8px">
      <thead>
        <tr style="background:#333;color:#fff;text-align:left">
          <th style="padding:7px 10px">Stock</th>
          <th style="padding:7px 10px">Price</th>
          <th style="padding:7px 10px">Day</th>
          <th style="padding:7px 10px">vs Buy</th>
          <th style="padding:7px 10px">50 / 150 / 250d</th>
          <th style="padding:7px 10px">RSI(14)</th>
          <th style="padding:7px 10px">RS vs Index</th>
          <th style="padding:7px 10px">P/E vs Sector</th>
          <th style="padding:7px 10px">Action</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def build_email_html(results, news_items):
    today = datetime.now().strftime("%d %b %Y, %A")
    india = [r for r in results if r.get("sym") == "₹"]
    us    = [r for r in results if r.get("sym") == "$"]

    # ── Alerts box ──
    alerts = [r for r in results if not r.get("error") and r.get("stage") in (3, 4)]
    alert_html = ""
    if alerts:
        rows = []
        for r in sorted(alerts, key=lambda x: x["stage"], reverse=True):
            col = "#a31c1c" if r["stage"] == 4 else "#856404"
            a   = _action(r["stage"], r["pct_buy"], r.get("rsi"))
            rows.append(
                f"<tr>"
                f"<td style='padding:5px 12px;color:{col};font-weight:bold'>"
                f"{'🔴' if r['stage']==4 else '🟡'} {r['name']}</td>"
                f"<td style='padding:5px 12px'>{_pf(r['price'], r['sym'])}</td>"
                f"<td style='padding:5px 12px;color:{col}'>{_pct(r['pct_buy'])}</td>"
                f"<td style='padding:5px 12px'>{STAGE_LABEL[r['stage']]}</td>"
                f"<td style='padding:5px 12px;font-weight:bold;color:{col}'>{a}</td>"
                f"</tr>"
            )
        alert_html = f"""
        <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
                    padding:14px 18px;margin-bottom:22px">
          <h3 style="margin:0 0 12px;color:#856404">⚠️ Action Needed Today</h3>
          <table style="font-size:13px;border-collapse:collapse">
            <thead><tr style="color:#555">
              <th style="padding:4px 12px 4px 0">Stock</th>
              <th style="padding:4px 12px 4px 0">Price</th>
              <th style="padding:4px 12px 4px 0">vs Buy</th>
              <th style="padding:4px 12px 4px 0">Stage</th>
              <th style="padding:4px 12px 4px 0">Suggested Action</th>
            </tr></thead>
            <tbody>{"".join(rows)}</tbody>
          </table>
        </div>"""

    # ── India + US sections ──
    def market_block(label, recs):
        return (
            f"<h2 style='border-bottom:2px solid #333;padding-bottom:6px'>{label}</h2>" +
            _email_section("🟢 Uptrend — Hold / Add on Dips (Stage 2)", "#1a7a3c", recs, 2) +
            _email_section("⚪ Basing — Watch for Breakout (Stage 1)",  "#495057", recs, 1) +
            _email_section("🟡 Topping — Consider Trimming (Stage 3)",  "#856404", recs, 3) +
            _email_section("🔴 Downtrend — Consider Exiting (Stage 4)", "#a31c1c", recs, 4)
        )

    # ── News section ──
    news_html = "<h2 style='border-bottom:2px solid #333;padding-bottom:6px;margin-top:28px'>📰 Market Pulse</h2>"
    if news_items:
        news_html += "<ul style='font-size:13px;line-height:1.9;margin-top:10px'>"
        for n in news_items:
            pub = f" <span style='color:#888'>— {n['publisher']}</span>" if n.get("publisher") else ""
            news_html += f"<li><a href='{n['link']}' style='color:#1a5fa3'>{n['title']}</a>{pub}</li>"
        news_html += "</ul>"

    news_html += """
    <div style="margin-top:14px;font-size:12px;line-height:2.2;color:#333">
      <b>Research links:</b><br>
      🔍 <a href="https://news.google.com/search?q=India+NSE+stock+market+today">India Market News (Google)</a>
         &nbsp;|&nbsp;
      🔍 <a href="https://news.google.com/search?q=US+stock+market+today">US Market News (Google)</a><br>
      📱 <a href="https://reddit.com/r/IndiaInvestments/">r/IndiaInvestments</a>
         &nbsp;|&nbsp;
      📱 <a href="https://reddit.com/r/stocks/">r/stocks</a>
         &nbsp;|&nbsp;
      📱 <a href="https://reddit.com/r/investing/">r/investing</a><br>
      ▶️ <a href="https://youtube.com/results?search_query=India+stock+market+analysis+today">India Stocks — YouTube</a>
         &nbsp;|&nbsp;
      ▶️ <a href="https://youtube.com/results?search_query=US+stock+market+analysis+today">US Stocks — YouTube</a>
    </div>"""

    errs = [r for r in results if r.get("error")]
    err_html = ""
    if errs:
        err_html = (
            "<p style='color:#888;font-size:12px;margin-top:12px'>⚫ Data unavailable: " +
            ", ".join(f"{r['name']} ({r['error']})" for r in errs) +
            "</p>"
        )

    return f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;padding:20px;max-width:1400px;margin:0 auto;color:#222">

<h1 style="margin-bottom:4px">📊 Portfolio Digest — {today}</h1>
<p style="color:#888;font-size:12px;margin-bottom:22px">
  Generated {datetime.now().strftime("%H:%M")} IST &nbsp;·&nbsp;
  Data: Yahoo Finance &nbsp;·&nbsp; Not financial advice
</p>

{alert_html}

{market_block("🇮🇳 Indian Holdings", india)}

{market_block("🇺🇸 US Holdings", us)}

{err_html}
{news_html}

<hr style="margin-top:24px;border:none;border-top:1px solid #ddd">
<p style="font-size:11px;color:#aaa;line-height:1.8">
  <b>Stage guide:</b>
  Stage 2🟢 = price above all MAs &amp; MAs rising (uptrend) &nbsp;|&nbsp;
  Stage 4🔴 = price below all MAs &amp; MAs falling (downtrend) &nbsp;|&nbsp;
  Stage 1⚪ = basing/sideways &nbsp;|&nbsp;
  Stage 3🟡 = topping/distribution<br>
  ✅ = price above that MA &nbsp;|&nbsp; ❌ = price below<br>
  <b>RSI:</b> &lt;30 oversold (potential buy) · &gt;70 overbought (consider trimming)<br>
  <b>RS vs Index:</b> 3-month return of stock minus Nifty (Indian) or S&amp;P 500 (US).
  Positive = outperforming index.
</p>
</body></html>"""


# ============================================================
# SEND
# ============================================================

def send_telegram(text):
    url    = f"https://api.telegram.org/bot{8516185511}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        r = requests.post(url, json={
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     chunk,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": True,
        }, timeout=15)
        if r.status_code != 200:
            print(f"  ⚠️  Telegram error: {r.text[:120]}")
        else:
            print("  ✅ Telegram chunk sent")


def send_email(html_body, subject):
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = kiratpalsingh93@gmail.com
    msg["To"]      = ", ".join(kiratpalsingh93@gmail.com)
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(kiratpalsingh93@gmail.com, isaj gcvv avmm rdib)
        s.sendmail(kiratpalsingh93@gmail.com, kiratpalsingh93@gmail.com, msg.as_string())
    print(f"  ✅ Email sent → {', '.join(kiratpalsingh93@gmail.com)}")


def get_chat_id():
    url  = f"https://api.telegram.org/bot{8516185511}/getUpdates"
    data = requests.get(url, timeout=10).json()
    recs = data.get("result", [])
    if not recs:
        print("No messages found.")
        print("→ Open Telegram, find your bot, send it 'hi', then re-run.")
        return
    for u in recs:
        chat = u.get("message", {}).get("chat", {})
        print(f"Chat ID : {chat.get('id')}")
        print(f"Name    : {chat.get('first_name', '')} {chat.get('last_name', '')}")
        print("→ Copy the Chat ID above into TELEGRAM_CHAT_ID at the top of this script.")
        break


# ============================================================
# MAIN
# ============================================================

def main():
    if "--chat-id" in sys.argv:
        get_chat_id()
        return

    if "--test" in sys.argv:
        print("Sending test Telegram message...")
        send_telegram("✅ Portfolio Agent v2 is live!\nRSI · Relative Strength · P/E vs Sector · News — all enabled.")
        return

    today = datetime.now().strftime("%d %b %Y")
    print(f"\n📊 Running portfolio digest v2 — {today}")
    print(f"   Fetching {len(INDIAN) + len(US)} stocks in parallel...\n")

    # ── Analyse all stocks ──
    results = analyse_all()

    ok  = [r for r in results if not r.get("error")]
    err = [r for r in results if r.get("error")]
    print(f"   ✅ {len(ok)} stocks analysed  ⚫ {len(err)} unavailable")

    # ── News ──
    print("   Fetching news...")
    # Prioritise alert stocks for news
    priority = [r["ticker"] for r in results if not r.get("error") and r.get("stage") in (3, 4)]
    other    = [r["ticker"] for r in results if not r.get("error") and r.get("stage") not in (3, 4)]
    news = get_news(priority + other)
    print(f"   ✅ {len(news)} news items fetched")

    # ── Telegram ──
    print("\nSending Telegram...")
    for msg in build_telegram(results):
        send_telegram(msg)
    send_telegram(build_telegram_news(news))

    # ── Email ──
    print("\nSending email...")
    html = build_email_html(results, news)
    send_email(html, f"📊 Portfolio Digest — {today}")

    print("\n✅ Done.\n")


if __name__ == "__main__":
    main()
