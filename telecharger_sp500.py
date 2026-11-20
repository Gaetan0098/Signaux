"""
Télécharge les cours de clôture des ~500 actions du S&P 500 et produit
le fichier data/donnees_bourse.json lu par la PWA « Signaux MM20/MM200 ».
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

NB_JOURS = 300  # 200 jours pour la MM200 + marge pour détecter les croisements
SORTIE = "data/donnees_bourse.json"


def liste_sp500():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    table = pd.read_html(StringIO(html))[0]
    table["Ticker"] = table["Symbol"].str.replace(".", "-", regex=False)  # BRK.B -> BRK-B
    return table[["Ticker", "Security", "GICS Sector"]]


def main():
    societes = liste_sp500()
    tickers = societes["Ticker"].tolist()
    print(f"{len(tickers)} actions trouvées, téléchargement des cours…")

    cours = yf.download(
        tickers, period="16mo", interval="1d",
        auto_adjust=True, progress=True, threads=True,
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
        raise SystemExit(f"Seulement {len(series)} actions récupérées : fichier non remplacé.")
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK : {len(series)} actions enregistrées dans {SORTIE}")


if __name__ == "__main__":
    main()
