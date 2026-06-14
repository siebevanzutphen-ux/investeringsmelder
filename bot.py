import os
import html
import yfinance as yf
import requests
import pandas as pd
from datetime import datetime

# --- Config ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Testmodus: stuurt een voorbeeldbericht met live data, ongeacht de daling.
# Wordt aangezet via de "Run workflow"-knop op GitHub (input test_bericht=true).
TEST_MODE = os.environ.get("TEST_MODE", "").lower() in ("1", "true", "yes", "ja")
TEST_TICKER = os.environ.get("TEST_TICKER", "AAPL").upper()

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

# Welke tickers zijn ETF's / indexfondsen (breed gespreid, veiliger).
# Wordt aangevuld met automatische detectie via quoteType.
ETF_TICKERS = {
    "VTI", "VOO", "SPY", "QQQ", "IWM", "EFA", "VEA", "VWO",
    "IEFA", "ACWI", "VT", "SCHD", "VIG", "BND", "AGG",
    "XLK", "XLF", "XLV", "XLE", "XLU", "XLP", "XLI",
}

MIN_DALING_AANDEEL = 8.0  # % daling minimum voor losse aandelen
MIN_DALING_ETF = 4.0      # % daling minimum voor ETF's/indexfondsen (eerder melden: veiliger)
MIN_MARKTCAP = 10e9       # minimaal €10 miljard marktcap (losse aandelen)
MIN_ETF_OMVANG = 1e9      # minimaal €1 miljard fondsomvang (ETF's)
MIN_LEEFTIJD_JAAR = 10    # bedrijf minimaal 10 jaar oud

VALUTA_SYMBOOL = {"USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}


# ----------------- Hulpfuncties opmaak -----------------

def esc(tekst):
    return html.escape(str(tekst)) if tekst is not None else ""


def val_sym(valuta):
    return VALUTA_SYMBOOL.get(valuta, (valuta + " ") if valuta else "$")


def geld(bedrag, valuta="USD", decimals=2):
    if bedrag is None:
        return "n.v.t."
    try:
        return f"{val_sym(valuta)}{bedrag:,.{decimals}f}".replace(",", "·").replace(".", ",").replace("·", ".")
    except Exception:
        return "n.v.t."


def pct(waarde, decimals=1, plus=False):
    if waarde is None:
        return "n.v.t."
    try:
        teken = "+" if plus else ""
        return f"{waarde:{teken}.{decimals}f}%".replace(".", ",")
    except Exception:
        return "n.v.t."


def getal(waarde, decimals=1):
    if waarde is None:
        return "n.v.t."
    try:
        return f"{waarde:.{decimals}f}".replace(".", ",")
    except Exception:
        return "n.v.t."


def cap_str(bedrag, valuta="USD"):
    if not bedrag:
        return "n.v.t."
    s = val_sym(valuta)
    if bedrag >= 1e12:
        return f"{s}{getal(bedrag / 1e12, 2)} biljoen"
    if bedrag >= 1e9:
        return f"{s}{getal(bedrag / 1e9, 1)} mld"
    if bedrag >= 1e6:
        return f"{s}{getal(bedrag / 1e6, 1)} mln"
    return f"{s}{getal(bedrag, 0)}"


def groot_getal(waarde):
    if not waarde:
        return "n.v.t."
    if waarde >= 1e12:
        return f"{getal(waarde / 1e12, 2)} biljoen"
    if waarde >= 1e9:
        return f"{getal(waarde / 1e9, 1)} mld"
    if waarde >= 1e6:
        return f"{getal(waarde / 1e6, 1)} mln"
    if waarde >= 1e3:
        return f"{getal(waarde / 1e3, 1)}k"
    return getal(waarde, 0)


# ----------------- Performance uit koershistorie -----------------

def perf_sinds_dagen(closes, dagen):
    if closes.empty:
        return None
    doel = closes.index[-1] - pd.Timedelta(days=dagen)
    eerder = closes[closes.index <= doel]
    if eerder.empty:
        return None
    return (closes.iloc[-1] / eerder.iloc[-1] - 1) * 100


def perf_ytd(closes):
    if closes.empty:
        return None
    laatste = closes.index[-1]
    jan1 = pd.Timestamp(year=laatste.year, month=1, day=1, tz=closes.index.tz)
    dit_jaar = closes[closes.index >= jan1]
    if dit_jaar.empty:
        return None
    return (closes.iloc[-1] / dit_jaar.iloc[0] - 1) * 100


# ----------------- Telegram -----------------

def stuur_telegram(tekst):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": tekst,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    if not r.ok:
        print(f"Telegram-fout {r.status_code}: {r.text}")
    return r.ok


def haal_chat_id():
    """Hulpfunctie om chat_id te vinden (eenmalig)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url)
    print(r.json())


# ----------------- Analyse -----------------

def analyse_aandeel(ticker, forceer=False):
    """Haalt alle data op. forceer=True slaat de daling-/kwaliteitsfilters over (testmodus)."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        quote_type = (info.get("quoteType") or "").upper()
        is_etf = quote_type in ("ETF", "MUTUALFUND") or ticker in ETF_TICKERS

        marktcap = info.get("marketCap", 0) or 0
        fonds_omvang = info.get("totalAssets") or info.get("netAssets")

        # Grootte-check (verschilt voor ETF's en aandelen)
        if not forceer:
            if is_etf:
                if fonds_omvang and fonds_omvang < MIN_ETF_OMVANG:
                    return None
            else:
                if marktcap < MIN_MARKTCAP:
                    return None
                # Leeftijd check (alleen losse aandelen)
                eerste_handel = info.get("firstTradeDateEpochUtc")
                if eerste_handel:
                    jaar = datetime.fromtimestamp(eerste_handel).year
                    if datetime.now().year - jaar < MIN_LEEFTIJD_JAAR:
                        return None

        # Koershistorie (1 jaar) voor koers + performance + 52 weken
        hist = stock.history(period="1y")
        closes = hist["Close"].dropna() if not hist.empty else pd.Series(dtype="float64")
        if len(closes) < 2:
            return None

        prijs_nu = float(closes.iloc[-1])
        prijs_vorig = float(closes.iloc[-2])
        daling_pct = (prijs_nu - prijs_vorig) / prijs_vorig * 100

        drempel = MIN_DALING_ETF if is_etf else MIN_DALING_AANDEEL
        if not forceer:
            if daling_pct > -drempel:
                return None  # Niet genoeg gedaald
            # Kwaliteitsfilters alleen voor losse aandelen
            if not is_etf:
                sector = info.get("sector", "Onbekend")
                if sector in ["Basic Materials", "Communication Services"]:
                    return None
                pe_check = info.get("trailingPE")
                if pe_check and pe_check < 0:
                    return None

        valuta = info.get("currency", "USD") or "USD"

        # 52-weken
        hoog_52 = info.get("fiftyTwoWeekHigh") or float(closes.max())
        laag_52 = info.get("fiftyTwoWeekLow") or float(closes.min())
        onder_top = (prijs_nu / hoog_52 - 1) * 100 if hoog_52 else None
        boven_bodem = (prijs_nu / laag_52 - 1) * 100 if laag_52 else None

        # Dividend (betrouwbaar uit rate/prijs)
        div_rate = info.get("dividendRate")
        div_rend = (div_rate / prijs_nu * 100) if (div_rate and prijs_nu) else None

        # Analisten
        doel = info.get("targetMeanPrice")
        opwaarts = (doel / prijs_nu - 1) * 100 if (doel and prijs_nu) else None

        return {
            "ticker": ticker,
            "naam": info.get("longName") or info.get("shortName") or ticker,
            "is_etf": is_etf,
            "quote_type": quote_type,
            "fonds_omvang": fonds_omvang,
            "categorie": info.get("category"),
            "fondshuis": info.get("fundFamily"),
            "kostenratio": info.get("annualReportExpenseRatio") or info.get("netExpenseRatio"),
            "etf_yield": (info.get("yield") * 100) if info.get("yield") else None,
            "valuta": valuta,
            "daling": round(daling_pct, 2),
            "prijs": prijs_nu,
            "prijs_vorig": prijs_vorig,
            "dag_hoog": info.get("dayHigh"),
            "dag_laag": info.get("dayLow"),
            "open": info.get("open"),
            "marktcap": marktcap,
            "sector": info.get("sector", "Onbekend"),
            "industrie": info.get("industry"),
            "land": info.get("country"),
            "werknemers": info.get("fullTimeEmployees"),
            "beta": info.get("beta"),
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg": info.get("trailingPegRatio") or info.get("pegRatio"),
            "koers_boek": info.get("priceToBook"),
            "eps": info.get("trailingEps"),
            "div_rend": div_rend,
            "payout": (info.get("payoutRatio") * 100) if info.get("payoutRatio") else None,
            # Performance
            "p_week": perf_sinds_dagen(closes, 7),
            "p_maand": perf_sinds_dagen(closes, 30),
            "p_kwartaal": perf_sinds_dagen(closes, 90),
            "p_ytd": perf_ytd(closes),
            "p_jaar": perf_sinds_dagen(closes, 365),
            # 52 weken
            "hoog_52": hoog_52,
            "laag_52": laag_52,
            "onder_top": onder_top,
            "boven_bodem": boven_bodem,
            # Financieel
            "omzetgroei": (info.get("revenueGrowth") * 100) if info.get("revenueGrowth") is not None else None,
            "winstmarge": (info.get("profitMargins") * 100) if info.get("profitMargins") is not None else None,
            "roe": (info.get("returnOnEquity") * 100) if info.get("returnOnEquity") is not None else None,
            "schuld_ev": info.get("debtToEquity"),
            "vrije_cashflow": info.get("freeCashflow"),
            # Volume
            "volume": info.get("volume") or info.get("regularMarketVolume"),
            "gem_volume": info.get("averageVolume"),
            # Advies + omschrijving
            "doel": doel,
            "opwaarts": opwaarts,
            "advies": info.get("recommendationKey"),
            "n_analisten": info.get("numberOfAnalystOpinions"),
            "omschrijving": info.get("longBusinessSummary"),
            "website": info.get("website"),
        }

    except Exception as e:
        print(f"Fout bij {ticker}: {e}")
        return None


ADVIES_NL = {
    "strong_buy": "sterk kopen", "buy": "kopen", "hold": "houden",
    "underperform": "onderpresteren", "sell": "verkopen", "none": "geen advies",
}


def kort_omschrijving(tekst, maxlen=320):
    if not tekst:
        return None
    tekst = tekst.strip()
    if len(tekst) <= maxlen:
        return tekst
    afgekapt = tekst[:maxlen]
    laatste_punt = afgekapt.rfind(". ")
    if laatste_punt > 120:
        return afgekapt[:laatste_punt + 1]
    return afgekapt.rstrip() + "…"


def format_bericht(k, test=False):
    v = k["valuta"]
    regels = []

    is_etf = k.get("is_etf")

    # ---- Kop: korte samenvatting (zoals voorheen) ----
    if test:
        regels.append("🧪 <b>TESTBERICHT — je melder werkt!</b>")
    if is_etf:
        regels.append("🛡️ <b>ETF / INDEXFONDS</b> — breed gespreid, veiliger instappunt")
    kop_emoji = "📉" if k["daling"] < 0 else "📈"
    regels.append(f"{kop_emoji} <b>{esc(k['naam'])}</b> ({esc(k['ticker'])})")
    beweging = "Daling" if k["daling"] < 0 else "Stijging"
    regels.append(f"{beweging}: <b>{pct(k['daling'], plus=True)}</b> sinds vorige slotkoers")
    if is_etf:
        regels.append(f"Prijs: <b>{geld(k['prijs'], v)}</b>  |  Fondsomvang: <b>{cap_str(k['fonds_omvang'], v)}</b>")
        fonds_regel = []
        if k.get("categorie"):
            fonds_regel.append(esc(k["categorie"]))
        if k.get("fondshuis"):
            fonds_regel.append(esc(k["fondshuis"]))
        if fonds_regel:
            regels.append("Categorie: " + " · ".join(fonds_regel))
    else:
        regels.append(f"Prijs: <b>{geld(k['prijs'], v)}</b>  |  Cap: <b>{cap_str(k['marktcap'], v)}</b>")
        sector_regel = esc(k["sector"])
        if k.get("industrie"):
            sector_regel += f" · {esc(k['industrie'])}"
        regels.append(f"Sector: {sector_regel}")

    # ---- Koers & dag ----
    regels.append("")
    regels.append("📊 <b>Koers vandaag</b>")
    regels.append(f"• Vorige slot: {geld(k['prijs_vorig'], v)}")
    if k.get("open"):
        regels.append(f"• Open: {geld(k['open'], v)}")
    if k.get("dag_laag") and k.get("dag_hoog"):
        regels.append(f"• Dagbereik: {geld(k['dag_laag'], v)} – {geld(k['dag_hoog'], v)}")
    if k.get("volume"):
        vol = f"• Volume: {groot_getal(k['volume'])}"
        if k.get("gem_volume"):
            verhouding = k["volume"] / k["gem_volume"] if k["gem_volume"] else None
            vol += f" (gem. {groot_getal(k['gem_volume'])}"
            if verhouding:
                vol += f", {getal(verhouding, 1)}× normaal"
            vol += ")"
        regels.append(vol)

    # ---- Rendement over tijd ----
    regels.append("")
    regels.append("📈 <b>Rendement</b>")
    regels.append(f"• 1 week: {pct(k['p_week'], plus=True)}   |   1 maand: {pct(k['p_maand'], plus=True)}")
    regels.append(f"• 3 maanden: {pct(k['p_kwartaal'], plus=True)}   |   dit jaar: {pct(k['p_ytd'], plus=True)}")
    regels.append(f"• 1 jaar: {pct(k['p_jaar'], plus=True)}")

    # ---- 52 weken ----
    regels.append("")
    regels.append("🎯 <b>52 weken</b>")
    regels.append(f"• Hoog: {geld(k['hoog_52'], v)}  |  Laag: {geld(k['laag_52'], v)}")
    if k.get("onder_top") is not None:
        regels.append(f"• {pct(k['onder_top'], plus=True)} t.o.v. jaartop")
    if k.get("boven_bodem") is not None:
        regels.append(f"• {pct(k['boven_bodem'], plus=True)} t.o.v. jaarbodem")

    if is_etf:
        # ---- Fonds-info (ETF / indexfonds) ----
        fonds = []
        if k.get("kostenratio") is not None:
            fonds.append(f"• Kostenratio (TER): {pct(k['kostenratio'] * 100)} per jaar")
        if k.get("etf_yield") is not None:
            fonds.append(f"• Dividendrendement: {pct(k['etf_yield'])}")
        elif k.get("div_rend") is not None:
            fonds.append(f"• Dividendrendement: {pct(k['div_rend'])}")
        if k.get("beta") is not None:
            fonds.append(f"• Beta (beweeglijkheid): {getal(k['beta'], 2)}")
        if fonds:
            regels.append("")
            regels.append("🧺 <b>Fonds-info</b>")
            regels.extend(fonds)

        # ---- Over dit fonds ----
        omschrijving = kort_omschrijving(k.get("omschrijving"))
        if omschrijving:
            regels.append("")
            regels.append("ℹ️ <b>Over dit fonds</b>")
            regels.append(esc(omschrijving))
    else:
        # ---- Waardering ----
        regels.append("")
        regels.append("💰 <b>Waardering</b>")
        regels.append(f"• K/W (P/E): {getal(k['pe'], 1)}   |   verwacht: {getal(k['forward_pe'], 1)}")
        if k.get("peg"):
            regels.append(f"• PEG: {getal(k['peg'], 2)}")
        if k.get("koers_boek"):
            regels.append(f"• Koers/boekwaarde: {getal(k['koers_boek'], 2)}")
        if k.get("eps") is not None:
            regels.append(f"• Winst per aandeel: {geld(k['eps'], v)}")
        if k.get("div_rend") is not None:
            div = f"• Dividendrendement: {pct(k['div_rend'])}"
            if k.get("payout") is not None:
                div += f" (uitkering {pct(k['payout'])} v.d. winst)"
            regels.append(div)

        # ---- Bedrijfsgezondheid ----
        gezond = []
        if k.get("omzetgroei") is not None:
            gezond.append(f"• Omzetgroei (j-o-j): {pct(k['omzetgroei'], plus=True)}")
        if k.get("winstmarge") is not None:
            gezond.append(f"• Winstmarge: {pct(k['winstmarge'])}")
        if k.get("roe") is not None:
            gezond.append(f"• Rendement eigen vermogen (ROE): {pct(k['roe'])}")
        if k.get("schuld_ev") is not None:
            gezond.append(f"• Schuld/eigen vermogen: {getal(k['schuld_ev'], 0)}")
        if k.get("beta") is not None:
            gezond.append(f"• Beta (beweeglijkheid): {getal(k['beta'], 2)}")
        if gezond:
            regels.append("")
            regels.append("🏦 <b>Bedrijfsgezondheid</b>")
            regels.extend(gezond)

        # ---- Analisten ----
        if k.get("doel") or k.get("advies"):
            regels.append("")
            regels.append("👥 <b>Analisten</b>")
            if k.get("doel"):
                doelregel = f"• Koersdoel (gem.): {geld(k['doel'], v)}"
                if k.get("opwaarts") is not None:
                    doelregel += f"  →  {pct(k['opwaarts'], plus=True)} potentieel"
                regels.append(doelregel)
            if k.get("advies"):
                advies = ADVIES_NL.get(k["advies"], k["advies"])
                n = f" ({k['n_analisten']} analisten)" if k.get("n_analisten") else ""
                regels.append(f"• Advies: {esc(advies)}{n}")

        # ---- Over het bedrijf ----
        extra = []
        if k.get("land"):
            extra.append(esc(k["land"]))
        if k.get("werknemers"):
            extra.append(f"{groot_getal(k['werknemers'])} werknemers")
        omschrijving = kort_omschrijving(k.get("omschrijving"))
        if extra or omschrijving:
            regels.append("")
            regels.append("ℹ️ <b>Over het bedrijf</b>")
            if extra:
                regels.append("• " + " · ".join(extra))
            if omschrijving:
                regels.append(esc(omschrijving))

    # ---- Links ----
    regels.append("")
    yahoo = f"https://finance.yahoo.com/quote/{k['ticker']}"
    links = f'📎 <a href="{yahoo}">Yahoo Finance</a>'
    if k.get("website"):
        links += f' · <a href="{esc(k["website"])}">Website</a>'
    regels.append(links)

    return "\n".join(regels)


def main():
    print(f"Check gestart: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ---- Testmodus: één voorbeeldbericht met live data ----
    if TEST_MODE:
        print(f"TESTMODUS aan — voorbeeldbericht voor {TEST_TICKER}")
        k = analyse_aandeel(TEST_TICKER, forceer=True)
        if not k:
            stuur_telegram(f"🧪 Testbericht: kon geen data ophalen voor {esc(TEST_TICKER)}. "
                           f"yfinance/Yahoo gaf niets terug.")
            print("Geen data voor testticker.")
            return
        stuur_telegram(format_bericht(k, test=True))
        print(f"Testbericht verstuurd: {TEST_TICKER}")
        return

    # ---- Normale modus: scan watchlist ----
    kansen = []
    for ticker in WATCHLIST:
        resultaat = analyse_aandeel(ticker)
        if resultaat:
            kansen.append(resultaat)
            print(f"✅ Gevonden: {ticker} {resultaat['daling']}%")

    # Sorteer: ETF's/indexfondsen eerst (veiliger, eerder kopen), dan op grootste daling
    kansen.sort(key=lambda x: (not x.get("is_etf"), x["daling"]))

    if not kansen:
        print("Geen kansen gevonden.")
        return

    # Stuur de belangrijkste kans (ETF gaat voor) met volledige info
    beste = kansen[0]
    bericht = format_bericht(beste)

    # Andere dalers kort vermelden, zodat je niets mist (ETF's met schild)
    if len(kansen) > 1:
        andere = "  •  ".join(
            f"{'🛡️ ' if x.get('is_etf') else ''}{esc(x['ticker'])} {pct(x['daling'], plus=True)}"
            for x in kansen[1:6]
        )
        bericht += f"\n\n🔎 <b>Ook gedaald vandaag:</b> {andere}"

    stuur_telegram(bericht)
    print(f"Melding verstuurd: {beste['ticker']} (ETF: {beste.get('is_etf')})")


if __name__ == "__main__":
    main()
