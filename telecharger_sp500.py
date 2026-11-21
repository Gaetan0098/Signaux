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


SECTEURS_GICS = {"Information Technology": "Technologie", "Health Care": "Santé", "Financials": "Finance",
                 "Consumer Discretionary": "Consommation discrétionnaire", "Communication Services": "Communication",
                 "Industrials": "Industrie", "Consumer Staples": "Consommation de base", "Energy": "Énergie",
                 "Utilities": "Services aux collectivités", "Real Estate": "Immobilier", "Materials": "Matériaux"}


def anciennes_actions(fichier):
    """Nombre d'actions enregistré la veille, pour ne rien perdre si Yahoo ne répond pas."""
    try:
        with open(fichier, encoding="utf-8") as f:
            return {b["t"]: b.get("shares") for b in json.load(f).get("base", []) if b.get("shares")}
    except Exception:
        return {}


def sp500():
    societes = liste_sp500()
    tickers = societes["Ticker"].tolist()
    print(f"S&P 500 : {len(tickers)} actions trouvées, téléchargement des cours…")

    cours = yf.download(
        tickers, period="2y", interval="1d",
        auto_adjust=True, progress=False, threads=True,
    )["Close"]
    cours = cours.dropna(how="all").tail(NB_JOURS)
    actions = nombre_actions([t for t in tickers if t in cours.columns], anciennes_actions(SORTIE))

    series, base = {}, []
    for _, ligne in societes.iterrows():
        t = ligne["Ticker"]
        secteur = SECTEURS_GICS.get(ligne["GICS Sector"], ligne["GICS Sector"])
        b = {"t": t, "n": ligne["Security"], "pays": "États-Unis", "secteur": secteur, "cur": "USD", "ok": False}
        base.append(b)
        if t not in cours.columns:
            continue
        valeurs = [None if pd.isna(v) else round(float(v), 2) for v in cours[t]]
        if sum(v is not None for v in valeurs) < 220:
            continue
        b["ok"] = True
        series[t] = {"n": ligne["Security"], "s": secteur, "c": valeurs}
        dernier = next((v for v in reversed(valeurs) if v is not None), None)
        if actions.get(t) and dernier:
            b["shares"] = actions[t]
            b["cap"] = round(actions[t] * dernier)   # en dollars

    donnees = {
        "source": "S&P 500 (Yahoo Finance)",
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "dates": [d.strftime("%Y-%m-%d") for d in cours.index],
        "base": base,
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


SYMBOLES_FORCES = {"MT.PA": "MT.AS"}   # ArcelorMittal : Yahoo n'a l'historique que sur Amsterdam
EURONEXT_SUFFIXES = [".PA", ".AS", ".BR", ".LS", ".IR"]


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
    return SYMBOLES_FORCES.get(t + suffixe, t + suffixe), devise


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


def nombre_actions(tickers, anciennes):
    """Nombre d'actions en circulation de chaque société (Yahoo Finance), en parallèle.
    Si Yahoo ne répond pas pour une société, on garde la valeur de la veille."""
    from concurrent.futures import ThreadPoolExecutor

    def lire(t):
        try:
            v = yf.Ticker(t).fast_info["shares"]
            return t, float(v) if v and v > 0 else None
        except Exception:
            return t, None

    resultat = dict(anciennes)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for t, v in pool.map(lire, tickers):
            if v:
                resultat[t] = v
    print(f"  capitalisation connue pour {sum(1 for t in tickers if resultat.get(t))}/{len(tickers)} actions")
    return resultat


def europe():
    """Télécharge les ~600 actions du STOXX Europe 600 (liste lue chaque soir sur Wikipedia).
    Chacun choisit ensuite dans l'appli celles qu'il veut suivre."""
    base = stoxx600()
    tickers = list(dict.fromkeys(b["t"] for b in base))
    print(f"Europe : {len(tickers)} actions du STOXX Europe 600, téléchargement des cours…")
    morceaux = []
    for k in range(0, len(tickers), 100):
        lot = tickers[k:k + 100]
        df = yf.download(lot, period="2y", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
        if isinstance(df, pd.Series):
            df = df.to_frame(lot[0])
        morceaux.append(df)
    cours = pd.concat(morceaux, axis=1).sort_index().dropna(how="all").tail(NB_JOURS)
    cours = cours.loc[:, ~cours.columns.duplicated()]

    # actions Euronext sans cours : on essaie les autres places Euronext (Paris, Amsterdam, Bruxelles…)
    def complet(df, t):
        return t in df.columns and df[t].notna().sum() >= 220
    essais = {}
    for b in base:
        racine, _, suf = b["t"].rpartition(".")
        if not complet(cours, b["t"]) and "." + suf in EURONEXT_SUFFIXES:
            essais[b["t"]] = [racine + s for s in EURONEXT_SUFFIXES if s != "." + suf]
    if essais:
        autres = sorted({a for lst in essais.values() for a in lst})
        try:
            df2 = yf.download(autres, period="2y", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
            if isinstance(df2, pd.Series):
                df2 = df2.to_frame(autres[0])
            df2 = df2.reindex(cours.index)
            for b in base:
                for a in essais.get(b["t"], []):
                    if complet(df2, a):
                        print(f"  {b['n']} : {b['t']} sans cours, remplacé par {a}")
                        cours[a] = df2[a]
                        b["t"] = a
                        break
        except Exception as e:
            print(f"  autres places Euronext indisponibles : {e}")

    # taux de change : unités de chaque devise pour 1 euro
    devises = sorted({b["cur"] for b in base} - {"EUR"})
    fx = {"EUR": 1.0}
    try:
        if not devises:
            raise RuntimeError("aucune devise hors euro")
        taux = yf.download([f"EUR{d}=X" for d in devises], period="5d", progress=False)["Close"].ffill().iloc[-1]
        for d in devises:
            v = float(taux[f"EUR{d}=X"])
            if v > 0:
                fx[d] = round(v, 5)
    except Exception as e:
        print(f"  taux de change indisponibles : {e}")

    # capitalisation = nombre d'actions × dernier cours, convertie en euros
    actions = nombre_actions([b["t"] for b in base if b["t"] in cours.columns], anciennes_actions(SORTIE_EUROPE))

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
            dernier = next((v for v in reversed(valeurs) if v is not None), None)
            if actions.get(t) and dernier:
                b["shares"] = actions[t]
                b["cap"] = round(actions[t] * dernier / fx[b["cur"]])

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
