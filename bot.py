import os
import yfinance as yf
import requests
from datetime import datetime, timedelta

# --- Config ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Gevestigde aandelen + ETFs (S&P500, Europa, wereldwijd)
WATCHLIST = [
    # S&P 500 blue chips
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "JNJ",
    "PG", "V", "UNH", "HD", "MA", "BAC", "XOM", "CVX", "ABBV",
    "PFE", "KO", "PEP", "TMO", "COST", "AVGO", "MRK", "LLY",
    "WMT", "MCD", "NEE", "ACN", "ABT", "DHR", "TXN", "PM",
    # Europese blue chips (op Amerikaanse beurzen)
    "ASML", "SAP", "SHEL", "TTE", "NVO", "AZN", "NESN", "NOVN",
    "ROG", "UL", "BP", "HSBC", "BUD", "DEO",
    # Wereldwijde ETFs
    "VTI", "VOO", "SPY", "QQQ", "IWM", "EFA", "VEA", "VWO",
    "IEFA", "ACWI", "VT", "SCHD", "VIG", "BND", "AGG",
    # Sector ETFs (stabiel)
    "XLK", "XLF", "XLV", "XLE", "XLU", "XLP", "XLI",
]

MIN_DALING = 8.0        # % daling minimum
MIN_MARKTCAP = 10e9     # minimaal €10 miljard marktcap
MIN_LEEFTIJD_JAAR = 10  # bedrijf minimaal 10 jaar oud

def stuur_telegram(tekst):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": tekst, "parse_mode": "Markdown"})

def haal_chat_id():
    """Hulpfunctie om chat_id te vinden (eenmalig)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url)
    print(r.json())

def check_aandeel(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # Marktcap check
        marktcap = info.get("marketCap", 0)
        if marktcap < MIN_MARKTCAP:
            return None

        # Leeftijd check
        opgericht = info.get("founded") or info.get("startDate")
        if not opgericht:
            # Gebruik firstTradeDateEpochUtc als fallback
            eerste_handel = info.get("firstTradeDateEpochUtc")
            if eerste_handel:
                jaar = datetime.fromtimestamp(eerste_handel).year
                if datetime.now().year - jaar < MIN_LEEFTIJD_JAAR:
                    return None

        # Koersdata ophalen (2 dagen)
        hist = stock.history(period="2d")
        if len(hist) < 2:
            return None

        prijs_gisteren = hist["Close"].iloc[-2]
        prijs_nu = hist["Close"].iloc[-1]
        daling_pct = ((prijs_nu - prijs_gisteren) / prijs_gisteren) * 100

        if daling_pct > -MIN_DALING:
            return None  # Niet genoeg gedaald

        # Extra kwaliteitscheck
        pe_ratio = info.get("trailingPE")
        sector = info.get("sector", "Onbekend")
        naam = info.get("longName") or ticker

        # Vermijd risicovolle sectoren
        if sector in ["Basic Materials", "Communication Services"]:
            return None

        # Vermijd bedrijven met negatieve P/E (verlieslatend)
        if pe_ratio and pe_ratio < 0:
            return None

        return {
            "ticker": ticker,
            "naam": naam,
            "daling": round(daling_pct, 2),
            "prijs": round(prijs_nu, 2),
            "marktcap_miljard": round(marktcap / 1e9, 1),
            "sector": sector,
            "pe": round(pe_ratio, 1) if pe_ratio else "n.v.t.",
        }

    except Exception as e:
        print(f"Fout bij {ticker}: {e}")
        return None

def main():
    print(f"Check gestart: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    kansen = []

    for ticker in WATCHLIST:
        resultaat = check_aandeel(ticker)
        if resultaat:
            kansen.append(resultaat)
            print(f"✅ Gevonden: {ticker} {resultaat['daling']}%")

    # Sorteer op grootste daling
    kansen.sort(key=lambda x: x["daling"])

    if not kansen:
        print("Geen kansen gevonden.")
        return

    # Stuur alleen de beste (max 3)
    beste = kansen[:1]

    for k in beste:
        bericht = (
            f"📉 *{k['naam']}* ({k['ticker']})\n"
            f"Daling: *{k['daling']}%* in 24u\n"
            f"Prijs: ${k['prijs']} | Cap: €{k['marktcap_miljard']}B\n"
            f"Sector: {k['sector']} | P/E: {k['pe']}\n"
            f"_Vraag me om meer info als je wilt._"
        )
        stuur_telegram(bericht)
        print(f"Melding verstuurd: {k['ticker']}")

if __name__ == "__main__":
    main()
