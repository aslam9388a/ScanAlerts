import os
import re
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import requests

NEAR_LOW_PCT = 5.0  # 52-week low se kitne % tak ko near low maan'na hai
SEND_WHEN_NO_MATCH = True  # Test verification ke liye True; sirf alerts ke liye False karein

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SYMBOL_URLS = [
    "https://dps.psx.com.pk/",
    "https://dps.psx.com.pk/market-summary",
    "https://dps.psx.com.pk/market-watch",
]


def _extract_symbols_from_html(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    symbols = []
    seen = set()

    # 1) Table rows se pehla column try karein
    for row in soup.select("table tr"):
        cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if cols:
            sym = cols[0].strip().upper()
            if re.fullmatch(r"[A-Z0-9]{2,10}", sym) and sym not in seen:
                seen.add(sym)
                symbols.append(sym)

    # 2) Agar table se symbols kam aayein toh /company/ ya /symbols/ links check karein
    if len(symbols) < 10:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            m = re.search(r"/(?:company|symbol|quotes)/([A-Za-z0-9]{2,10})", href)
            if m:
                sym = m.group(1).upper()
                if sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)

    return symbols


def get_psx_symbols():
    """Multiple PSX pages se symbols ki list nikaalta hai."""
    for url in SYMBOL_URLS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"Skipping {url} -> HTTP {resp.status_code}")
                continue

            syms = _extract_symbols_from_html(resp.text)
            print(f"Fetched {len(syms)} symbols from {url}")
            if syms:
                return syms
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            continue

    raise RuntimeError("Could not fetch PSX symbols from any endpoint.")


def get_52w_low_info(symbol):
    """Kisi symbol ke liye current price aur taqreeban 52-week low nikaalta hai."""
    start_ts = int((datetime.utcnow() - timedelta(days=370)).timestamp())
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        rows = data.get("data") if isinstance(data, dict) else data
        if not rows or not isinstance(rows, list):
            return None

        closes = []
        lows = []

        for item in rows:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            ts = item[0]
            if isinstance(ts, (int, float)) and ts < start_ts:
                continue
            low_val = item[3]
            close_val = item[4]
            if isinstance(low_val, (int, float)) and low_val > 0:
                lows.append(float(low_val))
            if isinstance(close_val, (int, float)) and close_val > 0:
                closes.append(float(close_val))

        if closes and lows:
            current_price = closes[-1]
            low_52w = min(lows)
            diff_pct = ((current_price - low_52w) / low_52w) * 100.0
            return current_price, low_52w, diff_pct
    except Exception:
        return None

    return None


def scan_psx():
    print("Starting PSX 52-week low scan...")
    symbols = get_psx_symbols()
    print(f"Total symbols to scan: {len(symbols)}")

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

    print(
        f"Checked {checked} symbols | 52W Low: {len(exact_lows)} | Near 52W Low (<={NEAR_LOW_PCT}%): {len(near_lows)}"
    )
    return checked, exact_lows, near_lows


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
    checked, exact_lows, near_lows = scan_psx()
    total_matches = len(exact_lows) + len(near_lows)

    if total_matches == 0 and not SEND_WHEN_NO_MATCH:
        print("No matching stocks found and SEND_WHEN_NO_MATCH is False. Skipping email.")
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
