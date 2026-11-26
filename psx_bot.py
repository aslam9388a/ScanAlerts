from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
import requests

NEAR_LOW_PCT = 20.0         # 52-week range low se kitne % tak ko match maan'na hai
EXACT_LOW_PCT = 0.5         # 52-week range low ke bilkul qareeb/barabar maan'ne ki limit
SEND_WHEN_NO_MATCH = True   # Test ke liye True; jab sirf matches par email chahiye ho toh False kar dein
MAX_WORKERS = 15            # 228 symbols ko tez scan karne ke liye parallel threads

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*; q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://dps.psx.com.pk/",
}

# Kul 228 PSX symbols jo har run mein scan honge
PSX_SYMBOLS = [
    "PASLNC", "WTL", "ZALR", "SSGC", "DCL", "LSEFSL", "CNERGY", "YOUW", "TPLL", "STPL",
    "LSECL", "TPLRF1", "BOP", "KOSM", "PRL", "PACE", "FNEL", "TELE", "TPLP", "CLVL",
    "TISL", "TREET", "PIBTL", "LIVEN", "MLCF", "TPL", "PIAHCLA", "PREMA", "DFMLNC", "NGLEL",
    "GCIL", "KEL", "TSBL", "TSPL", "GATM", "TPLT", "TRG", "LOADS", "UNITY", "ARUJNC",
    "AHCL", "ORM", "PGLC", "PSO", "KTML", "FDPL", "FCCL", "BLUEX", "ENGROH", "MDTL",
    "DGKC", "FPRM", "NBP", "AICL", "AGHA", "RPL", "DNCC", "SPEL", "EFERT", "ASL",
    "ILP", "SNGPXD", "BECO", "UCAPM", "ZUMA", "PAKRI", "BNL", "ZTL", "HUBC", "PTC",
    "HASCOLNC", "NML", "CLCPS", "CLOV", "NRL", "PAEL", "FIBLM", "OBOYNC", "THCCLXD", "OGDC",
    "FFL", "BAFL", "GDL", "TSMF", "FCSC", "GGGL", "PPL", "CSIL", "LOTCHEM", "PILNC",
    "OML", "GWLC", "UVICR1", "PICT", "NCL", "TRSM", "AGP", "WAVESAPP", "DBCINC", "SELECT",
    "CPPL", "ZALXR", "SYS", "SLM", "WASL", "BBFL", "GGL", "NCPL", "FEM", "MZNPETF",
    "TOMCL", "BPLXD", "FCL", "SPSL", "SLGL", "STCL", "ATRL", "QUICE", "LSEVL", "DSLNC",
    "TBL", "ITANZ", "ABHI", "FANM", "JSMFETF", "BAHL", "SEARL", "FATIMA", "LUCK", "DSIL",
    "ANL", "UDLI", "PASMNC", "MACFL", "KAPCO", "FCEPL", "FFC", "WAVES", "KOHC", "CWHC",
    "MARI", "AMTEXNC", "ICCI", "UBL", "RUPL", "IREIT", "WAHDAT", "PPVCNC", "NPL", "STL",
    "MEBL", "NRSL", "DWSMNC", "CEPB", "MFL", "AIRLINK", "HBL", "MIIETF", "AKBL", "HWQSNC",
    "EPQL", "GSPMNC", "HUMNL", "TGL", "EPCL", "CTMNC", "DEL", "GCWL", "FABL", "DFSMNC",
    "TCORP", "DCR", "PSX", "GLAXO", "ALTN", "MUGHAL", "ISL", "UBLPETF", "HCAR", "CHBLNC",
    "AMBL", "MCB", "MEHT", "GUSMWU", "AVN", "BML", "GAL", "ECOP", "BCL", "SPWL",
    "FPJM", "UNIC", "ASCNC", "GATI", "BGL", "JSBL", "IPAK", "HRPL", "BIPL", "KOHTM",
    "POWER", "CJPLWU", "SPAC2", "KML", "SIBL", "DLL", "SZTM", "ITTEFAQ", "SERTNC", "NETSOL",
    "ASHT", "QTECH", "ASTL", "BAPL", "GOC", "MTL", "PRWM", "CCM", "ICL", "CPHL",
    "RUBYNC", "FFLMNC", "IBLHL", "LPGL", "HTL", "BRRG", "JVDC", "NATF", "ADMM", "ARMG",
    "AGSML", "786", "GTYR", "LEUL", "FECM", "SYM", "KOIL", "JSCL", "SINDM", "AKDSL",
    "STML", "SHNI", "FCIBL", "NCMLNC", "INIL", "MIRKS", "KOHE", "IMAGEXD", "BERG", "MRNS",
    "ANSM", "OCTOPUS", "SAIF", "ATBA", "PAKQATAR", "SGF", "FML", "JATM", "PKGI", "UVIC",
    "OTSU", "SAZEW", "SNAI", "SMCPL", "DAAG", "FTMM", "KOHP", "IDRT", "CHCC", "GHNI",
    "PIOC", "POL", "BFAGRO", "HIFA", "GHGL", "REDCO", "MSCL", "GRR", "KSTMNC", "SBL",
    "SNBL", "EMCO", "NNAR", "HALEON", "KSBP", "PABC", "TOWL", "CSAP", "FTSM", "GADT",
    "BELANC", "DIIL", "BFMOD", "POML", "HBLTETF", "SOYASUP", "BFBIO", "ARCTM", "PAKL", "IMS",
    "DOL", "MWMP", "LCI", "AGL", "JSIL", "ASTM", "SSML", "SHDT", "SKRSNC", "UBDL",
    "HMB", "HINO", "MERIT", "HICL", "NATM", "PTL", "PNSC", "ALIFE", "ICIBL", "PRET",
    "PQGTL", "MSOT", "PINL", "CRTM", "FHAM", "TATM", "JDMT", "FCELNC", "ABL", "UDPL",
    "LPL", "BWCL", "JKSM", "GAMONNC", "MACTER", "BIFO", "WAFI", "JAZZSURE", "NBPGETF", "OLPL",
    "FLYNG", "AATM", "SLYTWU", "HGFA", "EXIDE", "FECTC", "ALAC", "COLG", "JSRR", "ESBLNC",
    "CENI", "BOK", "THALL", "ZAHID", "APL", "ASICR2", "CHAS", "BWHL", "SPLNC", "GFILNC",
    "SARC", "IGIHL", "GRYL", "PSYL", "JGICL", "IDYM", "SRR", "ANLNV", "TICL", "DWAENC",
    "BUXL", "SHEZ", "HABSM", "PSEL", "QUETNC", "ATIL", "NAGC", "SHFA", "FRCL", "JSGBETF",
    "DADX", "SUTM", "PIM", "TSML", "BNWM", "NITGETF", "AHL", "ALNRS", "SRVI", "EPCLPS",
    "NICL", "EFUL", "INKL", "HAEL", "SURC", "NONS", "JSML", "WAHN", "DYNO", "INDU",
    "AGTL", "TRIPF", "SEPL", "PKGP", "ACPL", "AGIC", "HINOON", "NEXT", "DINT", "AGIL",
    "PAKD", "SEL", "CFL", "PPP", "PAKT", "MQTM", "SSOM", "FEROZ", "SHCM", "DWTMWU",
    "IGIL", "PAKOXY", "PKGS", "BAFS", "RICL", "NSRM", "MFFL", "AKGL", "SMLNC", "SCBPL",
    "ANTM", "ARPL", "STJT", "KPUS", "ADAMS", "ABOT", "HUSI", "JLICL", "BHAT", "KHTC",
    "FZCM", "GVGL", "FSWLNC", "ARPAK", "ACIETF", "ATLH", "SUHJNC", "SITC", "JUBSNC", "SHSML",
    "ASLPS", "AABS", "ASIC", "PECONC", "KCL", "IMLNC", "JDWS", "SHJS", "NESTLE", "PCAL",
    "IDEAL", "SANSM", "FRSM", "MCBIM", "FASM", "BATA", "FILNC", "AKDHL", "ZIL", "JSGCL",
    "PMRS", "MUREB", "IBFL", "EFUG", "FIMM", "DMC", "OLPM", "HPL", "AHTM", "SIEM",
    "SAPT", "UPFL", "SFL", "RMPL", "POWERPS", "REWM", "ELSM", "BTL", "KHYT", "HAFL",
    "SASML", "ELCM", "PIAHCLB", "ASLCPS", "ISIL", "JVDCPS", "STYLERS",
]


def _extract_52w_range_from_eod(payload):
    """Pichle ~52 weeks (365 din) ke data se 52-Week Range Low, High aur current price nikaalta hai."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        return None

    cutoff_sec = time.time() - (365 * 86400)
    closes = []
    lows = []
    highs = []

    for item in rows:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        ts = item[0]
        if isinstance(ts, (int, float)):
            ts_sec = ts / 1000.0 if ts > 1e11 else float(ts)
            if ts_sec < cutoff_sec:
                continue

        # PSX EOD format: [timestamp, open, high, low, close, volume]
        high_val = float(item[2]) if isinstance(item[2], (int, float)) and item[2] > 0 else None
        low_val = float(item[3]) if isinstance(item[3], (int, float)) and item[3] > 0 else None
        close_val = float(item[4]) if isinstance(item[4], (int, float)) and item[4] > 0 else None

        if low_val is not None:
            lows.append(low_val)
        if high_val is not None:
            highs.append(high_val)
        if close_val is not None:
            closes.append(close_val)

    if not closes or not lows:
        return None

    current_price = closes[-1]
    range_52w_low = min(lows)
    range_52w_high = max(highs) if highs else None

    if range_52w_low <= 0:
        return None

    above_low_pct = ((current_price - range_52w_low) / range_52w_low) * 100.0
    return current_price, range_52w_low, range_52w_high, above_low_pct


def _extract_52w_range_from_company_page(symbol):
    """Fallback: /company/{symbol} page se 52-Week Range Low/High aur current price nikaalta hai."""
    url = f"https://dps.psx.com.pk/company/{symbol}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code != 200 or not r.text:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    price_match = re.search(r"(?:PKR|Rs\.?)\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", text, re.IGNORECASE)
    range_match = re.search(
        r"52[-\s]*W(?:eek)?\s*(?:Range|Low\s*/\s*High)?[^\d]{0,25}([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*[-–/to]+\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)",
        text,
        re.IGNORECASE,
    )

    if not price_match or not range_match:
        return None

    current_price = float(price_match.group(1).replace(",", ""))
    range_52w_low = float(range_match.group(1).replace(",", ""))
    range_52w_high = float(range_match.group(2).replace(",", ""))

    if current_price <= 0 or range_52w_low <= 0:
        return None

    above_low_pct = ((current_price - range_52w_low) / range_52w_low) * 100.0
    return current_price, range_52w_low, range_52w_high, above_low_pct


def check_symbol_52w_range_low(symbol):
    """Symbol ki 52-Week Range Low se distance check karta hai."""
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and r.text.strip().startswith(("{", "[")):
            parsed = _extract_52w_range_from_eod(r.json())
            if parsed:
                price, low_52w, high_52w, above_low_pct = parsed
                return (symbol, price, low_52w, high_52w, above_low_pct)
    except Exception:
        pass

    try:
        fallback = _extract_52w_range_from_company_page(symbol)
        if fallback:
            price, low_52w, high_52w, above_low_pct = fallback
            return (symbol, price, low_52w, high_52w, above_low_pct)
    except Exception:
        pass

    return None


def scan_52w_range_lows(symbols):
    clean_symbols = []
    seen = set()
    for s in symbols:
        sym = str(s).strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            clean_symbols.append(sym)

    print(
        f"Scanning {len(clean_symbols)} symbols for 52-Week Range Low "
        f"(At Low <= {EXACT_LOW_PCT}%, Near Low <= {NEAR_LOW_PCT}%)..."
    )

    at_52w_low = []
    near_52w_low = []
    scanned_list = sorted(clean_symbols)
    data_found_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol_52w_range_low, sym): sym for sym in scanned_list}
        for future in as_completed(futures):
            res = future.result()
            if not res:
                continue
            data_found_count += 1
            _, _, _, _, above_low_pct = res
            if above_low_pct <= EXACT_LOW_PCT:
                at_52w_low.append(res)
            elif above_low_pct <= NEAR_LOW_PCT:
                near_52w_low.append(res)

    print(
        f"Scan Complete | Symbols: {len(scanned_list)} | Data Found: {data_found_count} | "
        f"At 52W Low: {len(at_52w_low)} | Within {NEAR_LOW_PCT}% of 52W Low: {len(near_52w_low)}"
    )
    return scanned_list, data_found_count, at_52w_low, near_52w_low


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


def _format_match_line(item):
    sym, price, low_52w, high_52w, above_low_pct = item
    high_str = f"{high_52w:>8.2f}" if isinstance(high_52w, (int, float)) else "     N/A"
    return (
        f"{sym:<10} | Price: {price:>8.2f} | "
        f"52W Low: {low_52w:>8.2f} | 52W High: {high_str} | +{above_low_pct:.2f}% above low"
    )


def main():
    scanned_list, data_found_count, at_52w_low, near_52w_low = scan_52w_range_lows(PSX_SYMBOLS)
    total_matches = len(at_52w_low) + len(near_52w_low)

    if total_matches == 0 and not SEND_WHEN_NO_MATCH:
        print("No 52-week range low matches found and SEND_WHEN_NO_MATCH is False. Email skipped.")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"PSX 52-Week Range Low Scan Report ({now_str})",
        f"Total Symbols Scanned: {len(scanned_list)} (52W Data Found: {data_found_count})",
        f"At 52W Range Low (<= {EXACT_LOW_PCT}%): {len(at_52w_low)} | Within {NEAR_LOW_PCT}% of 52W Low: {len(near_52w_low)}",
        "",
        f"=== AT 52-WEEK RANGE LOW (<= {EXACT_LOW_PCT}%) ===",
    ]

    if at_52w_low:
        for item in sorted(at_52w_low, key=lambda x: x[4]):
            lines.append(_format_match_line(item))
    else:
        lines.append("None")

    lines.extend(["", f"=== NEAR 52-WEEK RANGE LOW (<= {NEAR_LOW_PCT}%) ==="])

    if near_52w_low:
        for item in sorted(near_52w_low, key=lambda x: x[4]):
            lines.append(_format_match_line(item))
    else:
        lines.append("None")

    lines.extend([
        "",
        f"=== SCANNED SYMBOLS LIST ({len(scanned_list)}) ===",
        ", ".join(scanned_list) if scanned_list else "None",
    ])

    subject = (
        f"PSX 52W Range Low Scan: {len(at_52w_low)} At Low, "
        f"{len(near_52w_low)} Within {NEAR_LOW_PCT:.0f}% ({len(scanned_list)} Symbols)"
    )
    send_email(subject, "\n".join(lines))


if __name__ == "__main__":
    main()
