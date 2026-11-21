"""
Télécharge les cours de clôture des ~500 actions du S&P 500 et produit
les fichiers data/donnees_bourse.json et data/donnees_europe.json lus par l’appli « Road to Million ».
Lancé automatiquement chaque soir par GitHub Actions (.github/workflows/mise-a-jour.yml).

Installation (une seule fois) :
    pip install yfinance pandas lxml requests

Utilisation manuelle (facultatif) :
    python telecharger_sp500.py
"""
import json
import os
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

NB_JOURS = 450  # 200 jours pour la MM200 + 250 séances d’historique du classement
SORTIE = "data/donnees_bourse.json"
SORTIE_EUROPE = "data/donnees_europe.json"


def liste_sp500():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    table = pd.read_html(StringIO(html))[0]
    table["Ticker"] = table["Symbol"].str.replace(".", "-", regex=False)  # BRK.B -> BRK-B
    return table[["Ticker", "Security", "GICS Sector"]]


def sp500():
    societes = liste_sp500()
    tickers = societes["Ticker"].tolist()
    print(f"S&P 500 : {len(tickers)} actions trouvées, téléchargement des cours…")

    cours = yf.download(
        tickers, period="2y", interval="1d",
        auto_adjust=True, progress=False, threads=True,
    )["Close"]
    cours = cours.dropna(how="all").tail(NB_JOURS)

    series = {}
    for _, ligne in societes.iterrows():
        t = ligne["Ticker"]
        if t not in cours.columns:
            continue
        valeurs = [None if pd.isna(v) else round(float(v), 2) for v in cours[t]]
        if sum(v is not None for v in valeurs) < 220:
            continue
        series[t] = {"n": ligne["Security"], "s": ligne["GICS Sector"], "c": valeurs}

    donnees = {
        "source": "S&P 500 (Yahoo Finance)",
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "dates": [d.strftime("%Y-%m-%d") for d in cours.index],
        "series": series,
    }
    if len(series) < 400:
        raise RuntimeError(f"Seulement {len(series)} actions S&P 500 récupérées : fichier non remplacé.")
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK : {len(series)} actions enregistrées dans {SORTIE}")


PLACES = {  # place de cotation (Wikipedia) -> suffixe Yahoo Finance et devise
    "London Stock Exchange": (".L", "GBP"), "Frankfurt Stock Exchange": (".DE", "EUR"),
    "SIX Swiss Exchange": (".SW", "CHF"), "Borsa Italiana": (".MI", "EUR"), "Bolsa de Madrid": (".MC", "EUR"),
    "Nasdaq Stockholm": (".ST", "SEK"), "Nasdaq Copenhagen": (".CO", "DKK"), "Nasdaq Helsinki": (".HE", "EUR"),
    "Warsaw Stock Exchange": (".WA", "PLN"), "Wiener Börse": (".VI", "EUR"), "Euronext Athens": (".AT", "EUR"),
}
EURONEXT = {"France": (".PA", "EUR"), "Netherlands": (".AS", "EUR"), "Belgium": (".BR", "EUR"),
            "Ireland": (".IR", "EUR"), "Portugal": (".LS", "EUR"), "Norway": (".OL", "NOK"), "Italy": (".MI", "EUR")}
PAYS = {"United Kingdom": "Royaume-Uni", "France": "France", "Germany": "Allemagne", "Switzerland": "Suisse",
        "Sweden": "Suède", "Italy": "Italie", "Netherlands": "Pays-Bas", "Spain": "Espagne", "Denmark": "Danemark",
        "Norway": "Norvège", "Finland": "Finlande", "Poland": "Pologne", "Belgium": "Belgique", "Greece": "Grèce",
        "Austria": "Autriche", "Ireland": "Irlande", "Portugal": "Portugal", "Luxembourg": "Luxembourg"}
SECTEURS = {"Industrial Goods and Services": "Industrie", "Banks": "Banques", "Healthcare": "Santé",
            "Financial Services": "Services financiers", "Technology": "Technologie",
            "Construction and Materials": "Construction et matériaux", "Utilities": "Services aux collectivités",
            "Insurance": "Assurance", "Consumer Products and Services": "Biens et services de consommation",
            "Energy": "Énergie", "Real Estate": "Immobilier", "Food, Beverage and Tobacco": "Alimentation, boissons et tabac",
            "Basic Resources": "Matières premières", "Telecommunications": "Télécoms", "Chemicals": "Chimie",
            "Personal Care, Drug and Grocery Stores": "Distribution et soins", "Automobiles and Parts": "Automobile",
            "Travel and Leisure": "Voyages et loisirs", "Retail": "Commerce", "Media": "Médias"}


def symbole_yahoo(ticker, place, pays):
    t = str(ticker).strip()
    if place == "Euronext":
        suffixe, devise = EURONEXT.get(pays, (".PA", "EUR"))
    elif place in PLACES:
        suffixe, devise = PLACES[place]
    else:
        return None, None
    if suffixe == ".L":
        t = t.rstrip(".").replace(".", "-")        # « BP. » -> BP.L ; « BT.A » -> BT-A.L
    t = t.replace(" ", "-")                        # « NOVO B » -> NOVO-B.CO ; « NDA FI » -> NDA-FI.HE
    return t + suffixe, devise


def stoxx600():
    url = "https://en.wikipedia.org/wiki/STOXX_Europe_600"
    html = requests.get(url, headers={"User-Agent": "RoadToMillion/1.0 (usage personnel)"}, timeout=30).text
    for table in pd.read_html(StringIO(html)):
        cols = [str(c) for c in table.columns]
        if {"Company", "Ticker", "Main listing", "Country"} <= set(cols):
            break
    else:
        raise RuntimeError("Tableau des sociétés du STOXX Europe 600 introuvable sur Wikipedia.")
    base = []
    for _, l in table.iterrows():
        t, devise = symbole_yahoo(l["Ticker"], str(l["Main listing"]), str(l["Country"]))
        if not t:
            continue
        base.append({"t": t, "n": str(l["Company"]), "pays": PAYS.get(str(l["Country"]), str(l["Country"])),
                     "secteur": SECTEURS.get(str(l.get("Supersector", "")), str(l.get("Supersector", ""))), "cur": devise})
    if len(base) < 500:
        raise RuntimeError(f"Seulement {len(base)} sociétés lues dans le tableau du STOXX Europe 600.")
    return base


def europe():
    """Télécharge les ~600 actions du STOXX Europe 600 (liste lue chaque soir sur Wikipedia).
    Chacun choisit ensuite dans l'appli celles qu'il veut suivre."""
    base = stoxx600()
    tickers = [b["t"] for b in base]
    print(f"Europe : {len(tickers)} actions du STOXX Europe 600, téléchargement des cours…")
    morceaux = []
    for k in range(0, len(tickers), 100):
        lot = tickers[k:k + 100]
        df = yf.download(lot, period="2y", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
        if isinstance(df, pd.Series):
            df = df.to_frame(lot[0])
        morceaux.append(df)
    cours = pd.concat(morceaux, axis=1).sort_index().dropna(how="all").tail(NB_JOURS)

    # taux de change : unités de chaque devise pour 1 euro
    devises = sorted({b["cur"] for b in base} - {"EUR"})
    fx = {"EUR": 1.0}
    try:
        taux = yf.download([f"EUR{d}=X" for d in devises], period="5d", progress=False)["Close"].ffill().iloc[-1]
        for d in devises:
            v = float(taux[f"EUR{d}=X"])
            if v > 0:
                fx[d] = round(v, 5)
    except Exception as e:
        print(f"  taux de change indisponibles : {e}")

    def arrondi(v):
        return round(v, 2) if v >= 100 else round(v, 3) if v >= 1 else round(v, 4)

    series = {}
    for b in base:
        t = b["t"]
        ok = t in cours.columns
        valeurs = []
        if ok:
            div = 100.0 if t.endswith(".L") else 1.0   # Londres : cours en pence -> livres
            valeurs = [None if pd.isna(v) else arrondi(float(v) / div) for v in cours[t]]
        b["ok"] = ok and sum(v is not None for v in valeurs) >= 220 and b["cur"] in fx
        if b["ok"]:
            series[t] = {"n": b["n"], "s": b["secteur"] + " · " + b["pays"], "cur": b["cur"], "c": valeurs}

    donnees = {
        "source": "STOXX Europe 600 (Wikipedia) + Yahoo Finance",
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "dates": [d.strftime("%Y-%m-%d") for d in cours.index],
        "fx": fx,
        "base": base,
        "series": series,
    }
    if len(series) < len(base) // 2:
        raise RuntimeError(f"Seulement {len(series)} actions européennes récupérées : fichier non remplacé.")
    with open(SORTIE_EUROPE, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, separators=(",", ":"))
    absentes = [b["t"] for b in base if not b["ok"]]
    print(f"OK : {len(series)} actions européennes enregistrées dans {SORTIE_EUROPE}"
          + (f" ({len(absentes)} sans cours : {', '.join(absentes)})" if absentes else ""))


def main():
    erreurs = []
    for nom, etape in (("S&P 500", sp500), ("Europe", europe)):
        try:
            etape()
        except Exception as e:  # une partie en échec n'empêche pas l'autre
            print(f"ÉCHEC {nom} : {e}")
            erreurs.append(nom)
    if erreurs:
        raise SystemExit("Échec pour : " + ", ".join(erreurs))


if __name__ == "__main__":
    main()
