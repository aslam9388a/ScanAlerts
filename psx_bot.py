import json
import os
import re
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import requests

NEAR_LOW_PCT = 5.0
SEND_WHEN_NO_MATCH = True

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,*/*;q=0.8",
    "Referer": "https://dps.psx.com.pk/",
}

FALLBACK_SYMBOLS = [
    "OGDC", "PPL", "MARI", "PSO", "SNGP", "SSGC", "HUBC", "KEL", "MEBL", "HBL",
    "UBL", "MCB", "NBP", "BAFL", "AKBL", "FABL", "LUCK", "DGKC", "MLCF", "FCCL",
    "ENGRO", "FFC", "FFBL", "EFERT", "SYS", "TRG", "PAEL", "SEARL", "ATRL", "PRL",
]


def get_psx_symbols():
    try:
        resp = requests.get("https://dps.psx.com.pk/market-summary", headers=HEADERS, timeout=20)
        print(f"market-summary status: {resp.status_code}, len: {len(resp.text)}")
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            syms = []
            seen = set()
            for row in soup.select("table tr"):
                cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if cols:
                    sym = re.sub(r"[^A-Za-z]", "", cols[0]).upper()
                    if 2 <= len(sym) <= 10 and sym not in seen:
                        seen.add(sym)
                        syms.append(sym)
            print(f"Parsed HTML table symbols: {len(syms)}")
            if len(syms) >= 5:
                return syms
    except Exception as e:
        print(f"market-summary error: {e}")

    print(f"Using fallback list: {len(FALLBACK_SYMBOLS)} symbols")
    return FALLBACK_SYMBOLS


def debug_one_symbol(symbol="OGDC"):
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"Sample {symbol} -> HTTP {r.status_code}, preview: {r.text[:300]}")
    except Exception as e:
        print(f"Sample {symbol} request failed: {e}")


def get_52w_low_info(symbol):
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        rows = data.get("data") if isinstance(data, dict) else data
        if not rows or not isinstance(rows, list):
            return None

        now_sec = time.time()
        cutoff_sec = now_sec - (370 * 86400)

        closes = []
        lows = []

        for item in rows:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            ts = item[0]
            if isinstance(ts, (int, float)):
                # Handle both seconds and millisecond timestamps
                ts_sec = ts / 1000.0 if ts > 1e11 else float(ts)
                if ts_sec < cutoff_sec:
                    continue

            # Common EOD layouts: [ts, open, high, low, close, vol] or [ts, open, close, low, high, vol]
            vals = [x for x in item[1:5] if isinstance(x, (int, float)) and x > 0]
            if len(vals) >= 2:
                lows.append(min(vals))
                closes.append(item[4] if isinstance(item[4], (int, float)) and item[4] > 0 else vals[-1])

        if closes and lows:
            current_price = closes[-1]
            low_52w = min(lows)
            diff_pct = ((current_price - low_52w) / low_52w) * 100.0
            return current_price, low_52w, diff_pct
    except Exception:
        return None

    return None


def scan_psx():
    debug_one_symbol("OGDC")
    symbols = get_psx_symbols()
    exact_lows = []
    near_lows = []
    checked = 0

    for sym in symbols:
        info = get_52w_low_info(sym)
        if not info:
            continue
        checked += 1
        price, low_52w, diff_pct = info
        if diff_pct <= 0.1:
            exact_lows.append((sym, price, low_52w, diff_pct))
        elif diff_pct <= NEAR_LOW_PCT:
            near_lows.append((sym, price, low_52w, diff_pct))
        if checked % 25 == 0:
            time.sleep(0.5)

    print(f"Checked {checked} symbols | At Low: {len(exact_lows)} | Near Low: {len(near_lows)}")
    return checked, exact_lows, near_lows


def send_email(subject, body_text):
    sender = os.environ.get("SENDER_EMAIL", "").strip()
    password = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    receiver = os.environ.get("RECEIVER_EMAIL", "").strip()

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender, password)
        server.send_message(msg)
    print(f"Email sent to {receiver}")


def main():
    checked, exact_lows, near_lows = scan_psx()
    total_matches = len(exact_lows) + len(near_lows)
    if total_matches == 0 and not SEND_WHEN_NO_MATCH:
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"PSX 52-Week Low Scan Report ({now_str})",
        f"Symbols checked: {checked}",
        f"At 52W Low: {len(exact_lows)} | Near 52W Low (<= {NEAR_LOW_PCT}%): {len(near_lows)}",
        "",
        "--- AT 52-WEEK LOW ---",
    ]
    if exact_lows:
        for sym, price, low_52w, diff_pct in sorted(exact_lows, key=lambda x: x[3]):
            lines.append(f"{sym}: Price={price:.2f}, 52W Low={low_52w:.2f} (+{diff_pct:.2f}%)")
    else:
        lines.append("None")

    lines.extend(["", f"--- NEAR 52-WEEK LOW (<= {NEAR_LOW_PCT}%) ---"])
    if near_lows:
        for sym, price, low_52w, diff_pct in sorted(near_lows, key=lambda x: x[3]):
            lines.append(f"{sym}: Price={price:.2f}, 52W Low={low_52w:.2f} (+{diff_pct:.2f}%)")
    else:
        lines.append("None")

    subject = f"PSX 52W Low Scan: {len(exact_lows)} At Low, {len(near_lows)} Near Low"
    send_email(subject, "\n".join(lines))


if __name__ == "__main__":
    main()
