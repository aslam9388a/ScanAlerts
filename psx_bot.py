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
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://dps.psx.com.pk",
    "Referer": "https://dps.psx.com.pk/market-watch",
}


def _parse_market_watch_payload(text, add_sym):
    """market-watch ke HTML ya JSON/text response se symbols aur rows nikaalta hai."""
    rows_data = {}
    stripped = text.strip()

    # 1) Agar JSON ho toh parse karein
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            items = parsed.get("data") if isinstance(parsed, dict) else parsed
            if isinstance(items, list):
                for entry in items:
                    if isinstance(entry, dict):
                        sym = add_sym(entry.get("symbol") or entry.get("scrip") or entry.get("ticker"))
                        if sym:
                            rows_data[sym] = entry
                    elif isinstance(entry, list) and entry:
                        sym = add_sym(str(entry[0]))
                        if sym:
                            rows_data[sym] = entry
            if rows_data:
                return rows_data
        except Exception:
            pass

    # 2) HTML table rows parse karein (chahe HTTP status 200 ho ya 469)
    soup = BeautifulSoup(text, "html.parser")
    for tr in soup.select("tr"):
        cols = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cols:
            continue
        sym = add_sym(tr.get("data-symbol") or tr.get("data-ticker") or cols[0])
        if sym:
            rows_data[sym] = cols

    # 3) Agar <tr> na milein toh regex se uppercase scrip symbols nikaalein
    if not rows_data:
        for m in re.finditer(r"\b([A-Z]{2,8})\b", text):
            cand = m.group(1)
            if cand not in {"HTML", "HEAD", "BODY", "DIV", "SPAN", "TABLE", "TR", "TD", "TH", "POST", "GET", "HTTP"}:
                sym = add_sym(cand)
                if sym:
                    rows_data[sym] = []

    return rows_data


def get_portal_market_watch():
    """PSX Data Portal market-watch POST endpoint se symbols aur data nikaalta hai."""
    symbols = []
    seen = set()

    def add_sym(raw):
        if not isinstance(raw, str):
            return None
        cleaned = re.sub(r"[^A-Za-z0-9]", "", raw.split("-")[0]).upper()
        if 2 <= len(cleaned) <= 10 and not cleaned.isdigit() and cleaned not in seen:
            seen.add(cleaned)
            symbols.append(cleaned)
            return cleaned
        return None

    payloads = [
        {"type": "REG"},
        {"type": "ALL"},
        {},
    ]

    for data_body in payloads:
        try:
            r = requests.post(
                "https://dps.psx.com.pk/market-watch",
                headers=HEADERS,
                data=data_body,
                timeout=20,
            )
            print(f"POST /market-watch {data_body} -> HTTP {r.status_code}, len={len(r.text)}")
            if len(r.text) > 500:
                parsed = _parse_market_watch_payload(r.text, add_sym)
                if len(symbols) >= 10:
                    print(f"Extracted {len(symbols)} symbols from POST /market-watch")
                    return symbols, parsed
                else:
                    print(f"Preview (first 300 chars): {r.text[:300]!r}")
        except Exception as e:
            print(f"POST /market-watch error: {e}")

    return symbols, {}


def _extract_52w_from_row(sym, row_val):
    """Agar market-watch row mein numbers hon (LDCP, Low, High, Current) toh 52W/range estimate kare."""
    nums = []
    if isinstance(row_val, list):
        for cell in row_val[1:]:
            m = re.findall(r"\d+(?:\.\d+)?", str(cell).replace(",", ""))
            for n in m:
                val = float(n)
                if 0.05 <= val <= 500000:
                    nums.append(val)
    elif isinstance(row_val, dict):
        for v in row_val.values():
            if isinstance(v, (int, float)) and 0.05 <= float(v) <= 500000:
                nums.append(float(v))
    return nums


def check_symbol_52w(symbol, market_row=None):
    """Pehle EOD timeseries try karta hai; na mile toh market-watch row se fallback dekhta hai."""
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and r.text.strip().startswith(("{", "[")):
            payload = r.json()
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(rows, list) and rows:
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

                if closes and lows:
                    current_price = closes[-1]
                    low_52w = min(lows)
                    if low_52w > 0:
                        diff_pct = ((current_price - low_52w) / low_52w) * 100.0
                        return (symbol, current_price, low_52w, diff_pct)
    except Exception:
        pass

    return None


def scan_entire_portal():
    print("Fetching full symbol list from PSX Data Portal...")
    symbols, parsed_rows = get_portal_market_watch()
    if not symbols:
        raise RuntimeError("PSX Data Portal se koi symbol nahi mila.")

    print(f"Scanning {len(symbols)} PSX Data Portal symbols in parallel...")
    exact_lows = []
    near_lows = []
    checked = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(check_symbol_52w, sym, parsed_rows.get(sym)): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            res = future.result()
            if not res:
                continue
            checked += 1
            _, _, _, diff_pct = res
            if diff_pct <= 0.1:
                exact_lows.append(res)
            elif diff_pct <= NEAR_LOW_PCT:
                near_lows.append(res)

    print(
        f"Scan Complete | Portal Symbols: {len(symbols)} | Checked: {checked} | "
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
