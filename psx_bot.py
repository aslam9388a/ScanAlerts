from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import requests

NEAR_LOW_PCT = 5.0          # 52-week low se kitne % tak ko near low maan'na hai
SEND_WHEN_NO_MATCH = True   # Test ke liye True; jab sirf matches par email chahiye ho toh False kar dein
MAX_WORKERS = 15            # 500+ symbols ko tez scan karne ke liye parallel threads

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://dps.psx.com.pk/",
}


def get_all_portal_symbols():
    """PSX Data Portal (dps.psx.com.pk) se tamam listed symbols nikaalta hai."""
    symbols = []
    seen = set()

    def add_sym(raw):
        if not isinstance(raw, str):
            return
        # XD, XR, NC waghera suffixes saaf karke clean symbol lein
        sym = re.sub(r"[^A-Za-z0-9]", "", raw.split("-")[0]).upper()
        if 2 <= len(sym) <= 10 and not sym.isdigit() and sym not in seen:
            seen.add(sym)
            symbols.append(sym)

    # 1) Data Portal ka official symbols JSON endpoint
    try:
        r = requests.get("https://dps.psx.com.pk/symbols", headers=HEADERS, timeout=20)
        print(f"Portal /symbols HTTP {r.status_code}, len={len(r.text)}")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        # Debt/ETF/Bonds filter karke equity symbols lena ya sab lena
                        sym_type = str(item.get("type", "")).upper()
                        if sym_type in ("", "EQUITY", "STOCK", "GEM"):
                            add_sym(item.get("symbol") or item.get("ticker"))
                    elif isinstance(item, str):
                        add_sym(item)
            if len(symbols) >= 100:
                print(f"Loaded {len(symbols)} symbols from PSX Data Portal /symbols")
                return symbols
    except Exception as e:
        print(f"/symbols endpoint error: {e}")

    # 2) Backup: Data Portal market-watch AJAX endpoint (GET aur POST dono try karein)
    for method in ("GET", "POST"):
        try:
            req_fn = requests.get if method == "GET" else requests.post
            r = req_fn("https://dps.psx.com.pk/market-watch", headers=HEADERS, timeout=20)
            print(f"Portal /market-watch ({method}) HTTP {r.status_code}, len={len(r.text)}")
            if r.status_code == 200 and len(r.text) > 500:
                soup = BeautifulSoup(r.text, "html.parser")
                for row in soup.select("tr"):
                    attr_sym = row.get("data-symbol") or row.get("data-ticker")
                    if attr_sym:
                        add_sym(attr_sym)
                    cols = [c.get_text(strip=True) for c in row.find_all("td")]
                    if cols:
                        add_sym(cols[0])
                if len(symbols) >= 100:
                    print(f"Loaded {len(symbols)} symbols from /market-watch ({method})")
                    return symbols
        except Exception as e:
            print(f"/market-watch ({method}) error: {e}")

    print(f"Total portal symbols collected: {len(symbols)}")
    return symbols


def check_symbol_52w(symbol):
    """Ek symbol ka EOD timeseries fetch karke 52-week low aur current price check karta hai."""
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        payload = r.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not rows or not isinstance(rows, list):
            return None

        cutoff_sec = time.time() - (370 * 86400)
        closes = []
        lows = []

        for item in rows:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            ts = item[0]
            if isinstance(ts, (int, float)):
                ts_sec = ts / 1000.0 if ts > 1e11 else float(ts)
                if ts_sec < cutoff_sec:
                    continue

            # PSX EOD format: [timestamp, open, high, low, close, volume]
            prices = [float(x) for x in item[1:5] if isinstance(x, (int, float)) and x > 0]
            if not prices:
                continue
            low_val = float(item[3]) if isinstance(item[3], (int, float)) and item[3] > 0 else min(prices)
            close_val = float(item[4]) if isinstance(item[4], (int, float)) and item[4] > 0 else prices[-1]
            lows.append(low_val)
            closes.append(close_val)

        if not closes or not lows:
            return None

        current_price = closes[-1]
        low_52w = min(lows)
        if low_52w <= 0:
            return None

        diff_pct = ((current_price - low_52w) / low_52w) * 100.0
        return (symbol, current_price, low_52w, diff_pct)
    except Exception:
        return None


def scan_entire_portal():
    print("Fetching full symbol list from PSX Data Portal...")
    symbols = get_all_portal_symbols()
    if not symbols:
        raise RuntimeError("PSX Data Portal se koi symbol nahi mila.")

    print(f"Scanning {len(symbols)} PSX Data Portal symbols in parallel...")
    exact_lows = []
    near_lows = []
    checked = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol_52w, sym): sym for sym in symbols}
        for future in as_completed(futures):
            res = future.result()
            if not res:
                continue
            checked += 1
            sym, price, low_52w, diff_pct = res
            if diff_pct <= 0.1:
                exact_lows.append(res)
            elif diff_pct <= NEAR_LOW_PCT:
                near_lows.append(res)

    print(
        f"Scan Complete | Checked: {checked}/{len(symbols)} | "
        f"At 52W Low: {len(exact_lows)} | Near 52W Low (<={NEAR_LOW_PCT}%): {len(near_lows)}"
    )
    return len(symbols), checked, exact_lows, near_lows


def send_email(subject, body_text):
    sender = os.environ.get("SENDER_EMAIL", "").strip()
    password = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    receiver = os.environ.get("RECEIVER_EMAIL", "").strip()

    if not sender or not password or not receiver:
        raise RuntimeError("Missing SENDER_EMAIL, EMAIL_APP_PASSWORD, or RECEIVER_EMAIL")

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

    print(f"Email sent successfully to {receiver}")


def main():
    total_syms, checked, exact_lows, near_lows = scan_entire_portal()
    total_matches = len(exact_lows) + len(near_lows)

    if total_matches == 0 and not SEND_WHEN_NO_MATCH:
        print("No matches found and SEND_WHEN_NO_MATCH is False. Email skipped.")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"PSX Data Portal 52-Week Low Scan ({now_str})",
        f"Portal Symbols Found: {total_syms} | Valid History Checked: {checked}",
        f"At 52W Low: {len(exact_lows)} | Near 52W Low (<= {NEAR_LOW_PCT}%): {len(near_lows)}",
        "",
        "=== AT 52-WEEK LOW ===",
    ]

    if exact_lows:
        for sym, price, low_52w, diff_pct in sorted(exact_lows, key=lambda x: x[3]):
            lines.append(f"{sym:<10} | Price: {price:>8.2f} | 52W Low: {low_52w:>8.2f} | +{diff_pct:.2f}%")
    else:
        lines.append("None")

    lines.extend(["", f"=== NEAR 52-WEEK LOW (<= {NEAR_LOW_PCT}%) ==="])

    if near_lows:
        for sym, price, low_52w, diff_pct in sorted(near_lows, key=lambda x: x[3]):
            lines.append(f"{sym:<10} | Price: {price:>8.2f} | 52W Low: {low_52w:>8.2f} | +{diff_pct:.2f}%")
    else:
        lines.append("None")

    subject = f"PSX Portal 52W Low Scan: {len(exact_lows)} At Low, {len(near_lows)} Near Low ({checked} Checked)"
    send_email(subject, "\n".join(lines))


if __name__ == "__main__":
    main()
