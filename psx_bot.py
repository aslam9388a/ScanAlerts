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
MAX_WORKERS = 12            # Parallel threads

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*; q=0.8",
    "Referer": "https://dps.psx.com.pk/",
}

# Verified liquid PSX symbols list (aap ismein apni marzi ke symbols add/remove kar sakte hain)
PSX_SYMBOLS = [
    "OGDC", "PPL", "MARI", "PSO", "SNGP", "SSGC", "HUBC", "KEL", "MEBL", "HBL",
    "UBL", "MCB", "NBP", "BAFL", "AKBL", "FABL", "LUCK", "DGKC", "MLCF", "FCCL",
    "CHCC", "PIOC", "ENGRO", "FFC", "FFBL", "EFERT", "FATIMA", "SYS", "TRG", "NETSOL",
    "AVN", "PAEL", "GATM", "NML", "ILP", "KTML", "SEARL", "GLAXO", "HALEON", "ATRL",
    "PRL", "NRL", "BYCO", "CNERGY", "GHNI", "INDU", "HCAR", "PSMC", "MTL", "EPCL",
    "LOTCHEM", "PTC", "TPL", "AIRLINK", "HUMNL", "WTL", "PIBTL", "KOSM", "UNITY", "FFL",
]


def _parse_52w_from_eod_json(payload):
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
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
    return current_price, low_52w, diff_pct


def _parse_52w_from_company_page(symbol):
    """Fallback: /company/{symbol} page se 52-week range aur current price nikaalne ki koshish."""
    url = f"https://dps.psx.com.pk/company/{symbol}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code != 200 or not r.text:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Current price pattern (e.g., PKR 123.45 or Rs. 123.45)
    price_match = re.search(r"(?:PKR|Rs\.?)\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", text, re.IGNORECASE)
    # 52-week range pattern (e.g., 80.00 - 150.00)
    range_match = re.search(
        r"52[-\s]*W(?:eek)?\s*(?:Range|Low\s*/\s*High)?[^\d]{0,25}([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*[-–/to]+\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)",
        text,
        re.IGNORECASE,
    )

    if not price_match or not range_match:
        return None

    current_price = float(price_match.group(1).replace(",", ""))
    low_52w = float(range_match.group(1).replace(",", ""))
    if current_price <= 0 or low_52w <= 0:
        return None

    diff_pct = ((current_price - low_52w) / low_52w) * 100.0
    return current_price, low_52w, diff_pct


def check_symbol_52w(symbol):
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and r.text.strip().startswith(("{", "[")):
            parsed = _parse_52w_from_eod_json(r.json())
            if parsed:
                price, low_52w, diff_pct = parsed
                return (symbol, price, low_52w, diff_pct)
    except Exception:
        pass

    try:
        fallback = _parse_52w_from_company_page(symbol)
        if fallback:
            price, low_52w, diff_pct = fallback
            return (symbol, price, low_52w, diff_pct)
    except Exception:
        pass

    return None


def scan_symbols(symbols):
    clean_symbols = []
    seen = set()
    for s in symbols:
        sym = str(s).strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            clean_symbols.append(sym)

    print(f"Scanning {len(clean_symbols)} symbols...")
    exact_lows = []
    near_lows = []
    scanned_list = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol_52w, sym): sym for sym in clean_symbols}
        for future in as_completed(futures):
            sym = futures[future]
            scanned_list.append(sym)
            res = future.result()
            if not res:
                continue
            _, _, _, diff_pct = res
            if diff_pct <= 0.1:
                exact_lows.append(res)
            elif diff_pct <= NEAR_LOW_PCT:
                near_lows.append(res)

    scanned_list.sort()
    print(
        f"Scan Complete | Attempted: {len(scanned_list)} | "
        f"At 52W Low: {len(exact_lows)} | Near 52W Low (<={NEAR_LOW_PCT}%): {len(near_lows)}"
    )
    return scanned_list, exact_lows, near_lows


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
    scanned_list, exact_lows, near_lows = scan_symbols(PSX_SYMBOLS)
    total_matches = len(exact_lows) + len(near_lows)

    if total_matches == 0 and not SEND_WHEN_NO_MATCH:
        print("No matches found and SEND_WHEN_NO_MATCH is False. Email skipped.")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"PSX 52-Week Low Scan Report ({now_str})",
        f"Total Symbols Scanned: {len(scanned_list)}",
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

    lines.extend([
        "",
        f"=== SCANNED SYMBOLS LIST ({len(scanned_list)}) ===",
        ", ".join(scanned_list) if scanned_list else "None",
    ])

    subject = f"PSX 52W Low Scan: {len(exact_lows)} At Low, {len(near_lows)} Near Low ({len(scanned_list)} Symbols)"
    send_email(subject, "\n".join(lines))


if __name__ == "__main__":
    main()
