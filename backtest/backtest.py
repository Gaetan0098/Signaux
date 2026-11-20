#!/usr/bin/env python3
"""
Backtest des règles de trading sur le S&P 500 (composition historique).

Deux temps, à respecter :
  python backtest/backtest.py --periode developpement
      Teste toutes les combinaisons de règles sur 2021-2024 uniquement.
  python backtest/backtest.py --periode validation
      Lance UNE SEULE configuration, figée dans backtest/config_retenue.json,
      sur 2025-2026, des données que l'algorithme n'a jamais vues.

Option : --demo  utilise des cours fictifs (pour vérifier que tout fonctionne).

Dépendances : pip install yfinance pandas numpy lxml requests matplotlib
"""
import argparse
import base64
import hashlib
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

ICI = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- paramètres
P = dict(
    capital=10_000.0,        # capital de départ (€, traité comme des $ : change ignoré)
    max_lignes=10,           # nombre maximum d'actions détenues en même temps
    risque_par_ligne=0.01,   # perte maximale visée par ligne si le stop est touché (1 % du capital)
    poids_max=0.20,          # aucune ligne ne dépasse 20 % du capital
    frais_fixe=1.0,          # frais par ordre (€)
    frais_change=0.0025,     # frais de change par ordre (0,25 %)
    glissement=0.0005,       # écart entre prix théorique et prix obtenu (0,05 %)
    surext=0.08,             # famille « tendance » : cours au plus 8 % au-dessus de la MM20
    rang_sortie=100,         # vente si l'action sort des 100 premières (contrôle hebdomadaire)
    jours_avant_resultats=5, # filtre résultats : pas d'achat dans les 5 séances avant une publication
    attente_apres_stop=20,   # pas de rachat d'une action dans les 20 séances après un stop-loss
)
DEBUT_DONNEES = "2019-10-01"            # marge pour la MM200 et le momentum 12 mois
DEV = ("2021-01-01", "2024-12-31")
VAL_DEBUT = "2025-01-01"
GRILLE = dict(
    famille=["tendance", "momentum", "combine"],
    chandeliers=[False, True],
    resultats=[False, True],
    atr_k=[2.5, 3.5, 5.0],
)
NOMS_FAMILLES = {"tendance": "Tendance MM20/MM200", "momentum": "Momentum 12 mois",
                 "combine": "Tendance + momentum"}


def nom_config(c):
    return (f"{NOMS_FAMILLES[c['famille']]} · chandeliers {'oui' if c['chandeliers'] else 'non'}"
            f" · filtre résultats {'oui' if c['resultats'] else 'non'} · stop {c['atr_k']}×ATR")


# ---------------------------------------------------------------- données réelles
def lire_wikipedia():
    import requests
    from io import StringIO
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    tables = pd.read_html(StringIO(html))
    act = tables[0]
    actuels = act["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    noms = dict(zip(actuels, act["Security"].astype(str)))

    ch = tables[1].copy()
    ch.columns = [" ".join(dict.fromkeys(str(x) for x in c)) if isinstance(c, tuple) else str(c)
                  for c in ch.columns]
    c_date = next(c for c in ch.columns if "Date" in c)
    c_aj = next(c for c in ch.columns if "Added" in c and "Ticker" in c)
    c_re = next(c for c in ch.columns if "Removed" in c and "Ticker" in c)
    c_reN = next((c for c in ch.columns if "Removed" in c and "Security" in c), None)

    def propre(v):
        if pd.isna(v):
            return None
        v = str(v).strip()
        return None if v in ("", "nan") else v.replace(".", "-")

    changements = []
    for _, r in ch.iterrows():
        d = pd.to_datetime(r[c_date], errors="coerce")
        if pd.isna(d):
            continue
        a, rt = propre(r[c_aj]), propre(r[c_re])
        changements.append((d.normalize(), a, rt))
        if rt and c_reN and rt not in noms:
            noms[rt] = str(r[c_reN])
    return actuels, changements, noms


def telecharger(tickers, debut):
    import yfinance as yf
    morceaux = []
    for k in range(0, len(tickers), 100):
        lot = tickers[k:k + 100]
        for essai in range(3):
            try:
                df = yf.download(lot, start=debut, auto_adjust=True, progress=False,
                                 threads=True, group_by="column")
                if df is not None and len(df):
                    if not isinstance(df.columns, pd.MultiIndex):
                        df.columns = pd.MultiIndex.from_product([df.columns, lot])
                    morceaux.append(df)
                    break
            except Exception as e:
                print(f"  lot {k // 100 + 1} : erreur {e}, nouvel essai…")
            time.sleep(5 * (essai + 1))
        print(f"  {min(k + 100, len(tickers))}/{len(tickers)} actions demandées")
    data = pd.concat(morceaux, axis=1).sort_index()
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    return {f: data[f] for f in ("Open", "High", "Low", "Close")}


def dates_resultats(tickers):
    import yfinance as yf
    sortie = {}
    for n, t in enumerate(tickers):
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=40)
            if df is not None and len(df):
                sortie[t] = sorted({pd.Timestamp(x).tz_localize(None).normalize()
                                    if pd.Timestamp(x).tzinfo else pd.Timestamp(x).normalize()
                                    for x in df.index})
        except Exception:
            pass
        if n % 100 == 99:
            print(f"  dates de résultats : {n + 1}/{len(tickers)}")
    return sortie


def charger_reel():
    print("Lecture de la composition du S&P 500…")
    actuels, changements, noms = lire_wikipedia()
    anciens = {r for d, a, r in changements if r and d >= pd.Timestamp(DEBUT_DONNEES)}
    tickers = sorted(set(actuels) | anciens)
    print(f"{len(actuels)} membres actuels + {len(anciens - set(actuels))} anciens membres depuis 2019")
    print("Téléchargement des cours (quelques minutes)…")
    px = telecharger(tickers, DEBUT_DONNEES)
    spy = telecharger(["SPY"], DEBUT_DONNEES)
    print("Téléchargement des dates de publication des résultats…")
    res = dates_resultats([t for t in tickers if t in px["Close"].columns])
    return dict(px=px, spy=spy["Close"]["SPY"], spy_open=spy["Open"]["SPY"],
                actuels=actuels, changements=changements, noms=noms, tickers_demandes=tickers,
                resultats=res, source="Yahoo Finance + Wikipedia (composition historique)")


# ---------------------------------------------------------------- données fictives
def charger_demo():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(DEBUT_DONNEES, "2026-10-30")
    N, K = len(idx), 150
    tick = [f"DEMO{k:03d}" for k in range(K)]
    C = np.empty((N, K))
    for k in range(K):
        p, cassure = 20 + rng.random() * 300, rng.integers(200, N - 100)
        d1, d2, v = (rng.random() - .45) * .002, (rng.random() - .45) * .003, .01 + rng.random() * .015
        for i in range(N):
            p *= math.exp((d1 if i < cassure else d2) + v * rng.standard_normal())
            C[i, k] = p
    O = np.vstack([C[:1], C[:-1]]) * np.exp(rng.normal(0, .004, (N, K)))
    H = np.maximum(O, C) * (1 + np.abs(rng.normal(0, .006, (N, K))))
    L = np.minimum(O, C) * (1 - np.abs(rng.normal(0, .006, (N, K))))
    C[700:, 3] = np.nan  # une société « rachetée » en cours de route
    O[700:, 3] = H[700:, 3] = L[700:, 3] = np.nan
    mk = lambda a: pd.DataFrame(a, index=idx, columns=tick)
    px = dict(Open=mk(O), High=mk(H), Low=mk(L), Close=mk(C))
    spy = pd.Series(np.nanmean(C / C[0], axis=1) * 300, index=idx)
    changements = [(idx[600], tick[149], "DEMOX"), (idx[400], None, tick[148])]
    actuels = tick[:148] + [tick[149]]
    res = {t: list(idx[rng.integers(0, 63)::63]) for t in tick}
    return dict(px=px, spy=spy, spy_open=spy.shift(1).fillna(spy), actuels=actuels,
                changements=changements, noms={t: f"Société fictive {t[4:]}" for t in tick},
                tickers_demandes=tick + ["DEMOX"], resultats=res, source="Données fictives (démo)")


# ---------------------------------------------------------------- indicateurs
def appartenance(index, actuels, changements, tickers):
    S, ch, j = set(actuels), sorted(changements, key=lambda x: x[0], reverse=True), 0
    pos = {t: k for k, t in enumerate(tickers)}
    arr = np.zeros((len(index), len(tickers)), bool)
    for i in range(len(index) - 1, -1, -1):
        d = index[i]
        while j < len(ch) and ch[j][0] > d:   # on « défait » les changements postérieurs à d
            _, a, r = ch[j]
            if a:
                S.discard(a)
            if r:
                S.add(r)
            j += 1
        for t in S:
            k = pos.get(t)
            if k is not None:
                arr[i, k] = True
    return arr


def preparer(D):
    c = D["px"]["Close"]
    c = c.loc[:, c.notna().sum() > 0]
    tick = list(c.columns)
    o, h, l = (D["px"][f].reindex(columns=tick) for f in ("Open", "High", "Low"))
    idx = c.index
    spy = D["spy"].reindex(idx).ffill()

    ma20 = c.rolling(20, min_periods=20).mean()
    ma200 = c.rolling(200, min_periods=200).mean()
    pc = c.shift(1)
    tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
    atr = tr.rolling(14, min_periods=14).mean()
    mom = c.shift(21) / c.shift(252) - 1
    ratio = ma20 / ma200
    ecart = c / ma20 - 1

    # figures de chandeliers haussières : marteau après repli, avalement haussier
    corps = (c - o).abs()
    ombre_basse = np.minimum(o, c) - l
    ombre_haute = h - np.maximum(o, c)
    marteau = (ombre_basse >= 2 * corps) & (ombre_haute <= corps) & ((h - l) > 0) & (c < ma20)
    avalement = (c.shift(1) < o.shift(1)) & (c > o) & (o <= c.shift(1)) & (c >= o.shift(1))
    confirm = (marteau | avalement).astype(float).rolling(3, min_periods=1).max() > 0

    membres = appartenance(idx, D["actuels"], D["changements"], tick)
    valide = membres & c.notna().values & ma200.notna().values & atr.notna().values
    V = pd.DataFrame(valide, index=idx, columns=tick)

    scores = {
        "tendance": ratio.where(V),
        "momentum": mom.where(V),
        "combine": 0.5 * ratio.where(V).rank(axis=1, pct=True) + 0.5 * mom.where(V).rank(axis=1, pct=True),
    }
    e_t = V & (ratio > 1) & (c > ma20) & (ecart <= P["surext"]) & (c > ma200)
    e_m = V & (mom > 0) & (c > ma200)
    elig = {"tendance": e_t, "momentum": e_m, "combine": e_t & (mom > 0)}

    # publications de résultats dans les N prochaines séances
    proche = np.zeros((len(idx), len(tick)), bool)
    n_res = 0
    for k, t in enumerate(tick):
        dates = D["resultats"].get(t)
        if not dates:
            continue
        n_res += 1
        for e in dates:
            p = idx.searchsorted(e)
            if p < len(idx) + P["jours_avant_resultats"]:
                proche[max(0, p - P["jours_avant_resultats"]):min(len(idx), p + 1), k] = True

    dernier = c.apply(lambda s: s.last_valid_index())
    dernier_i = np.array([idx.get_loc(d) for d in dernier])
    sortie_cote = np.where(dernier_i < len(idx) - 6, dernier_i, 10 ** 9)

    iso = idx.isocalendar()
    semaine = iso.year.to_numpy(dtype=int) * 100 + iso.week.to_numpy(dtype=int)
    fin_semaine = np.r_[semaine[1:] != semaine[:-1], True]

    f = lambda x: np.asarray(x, dtype=float)
    return dict(
        index=idx, tick=tick, noms=D["noms"], O=f(o), H=f(h), L=f(l), C=f(c), MA20=f(ma20), MA200=f(ma200),
        ATR=f(atr), membres=membres, confirm=confirm.values, proche=proche, sortie_cote=sortie_cote,
        fin_semaine=fin_semaine, marche_ok=(spy > spy.rolling(200).mean()).values,
        spy=spy, spy_open=D["spy_open"].reindex(idx).ffill(),
        score={k: f(v) for k, v in scores.items()},
        rang={k: f(v.rank(axis=1, ascending=False)) for k, v in scores.items()},
        elig={k: v.values for k, v in elig.items()},
        couverture_resultats=n_res / max(1, len(tick)),
        n_demandes=len(D["tickers_demandes"]), n_obtenus=len(tick), source=D["source"],
    )


# ---------------------------------------------------------------- simulation
def simuler(I, cfg, debut, fin):
    idx = I["index"]
    i0, i1 = idx.searchsorted(pd.Timestamp(debut)), idx.searchsorted(pd.Timestamp(fin), side="right")
    O, H, L, C, ATR = I["O"], I["H"], I["L"], I["C"], I["ATR"]
    S, RK = I["score"][cfg["famille"]], I["rang"][cfg["famille"]]
    E = I["elig"][cfg["famille"]].copy()
    if cfg["chandeliers"]:
        E &= I["confirm"]
    if cfg["resultats"]:
        E &= ~I["proche"]
    k_atr = cfg["atr_k"]
    st = dict(cash=P["capital"], frais=0.0)
    pos, trades, courbe, investi = {}, [], [], []
    a_vendre, a_acheter, bloque = {}, [], {}

    def cout(montant):
        return P["frais_fixe"] + montant * (P["frais_change"] + P["glissement"])

    def vendre(k, px, i, raison):
        p = pos.pop(k)
        montant = p["n"] * px
        c_ = cout(montant)
        st["cash"] += montant - c_
        st["frais"] += c_
        mise = p["n"] * p["px"] + p["frais"]
        trades.append(dict(action=I["tick"][k], entree=str(idx[p["i"]].date()), sortie=str(idx[i].date()),
                           prix_entree=round(p["px"], 2), prix_sortie=round(px, 2), quantite=p["n"],
                           resultat=round(montant - c_ - mise, 2), resultat_pct=round((montant - c_) / mise - 1, 4),
                           jours=int(i - p["i"]), raison=raison))
        if raison == "stop-loss":
            bloque[k] = i + P["attente_apres_stop"]

    for i in range(i0, i1):
        eq_veille = courbe[-1] if courbe else P["capital"]
        # 1. ventes décidées la veille, à l'ouverture
        for k, raison in a_vendre.items():
            if k in pos:
                px = O[i, k] if np.isfinite(O[i, k]) else pos[k]["dernier"]
                vendre(k, px, i, raison)
        a_vendre = {}
        # 2. achats décidés la veille, à l'ouverture
        for k, atr_ref in a_acheter:
            if k in pos or len(pos) >= P["max_lignes"]:
                continue
            px = O[i, k]
            if not (np.isfinite(px) and px > 0):
                continue
            dist = k_atr * atr_ref
            n = math.floor(min(P["risque_par_ligne"] * eq_veille / dist, P["poids_max"] * eq_veille / px))
            n = min(n, math.floor((st["cash"] - P["frais_fixe"]) / (px * (1 + P["frais_change"] + P["glissement"]))))
            if n < 1:
                continue
            c_ = cout(n * px)
            st["cash"] -= n * px + c_
            st["frais"] += c_
            pos[k] = dict(n=n, px=px, i=i, stop=px - dist, haut=px, frais=c_, dernier=px)
        a_acheter = []
        # 3. stops touchés dans la journée
        for k in list(pos):
            p = pos[k]
            if np.isfinite(L[i, k]) and L[i, k] <= p["stop"]:
                o_ = O[i, k]
                vendre(k, min(o_, p["stop"]) if np.isfinite(o_) else p["stop"], i, "stop-loss")
        # 4. à la clôture : stop suiveur et signaux de sortie
        for k in list(pos):
            p = pos[k]
            if i >= I["sortie_cote"][k]:
                vendre(k, p["dernier"], i, "fin de cotation")
                continue
            c_ = C[i, k]
            if not np.isfinite(c_):
                continue
            p["dernier"] = c_
            p["haut"] = max(p["haut"], c_)
            if np.isfinite(ATR[i, k]):
                p["stop"] = max(p["stop"], p["haut"] - k_atr * ATR[i, k])
            if I["MA20"][i, k] < I["MA200"][i, k]:
                a_vendre[k] = "croisement baissier"
            elif not I["membres"][i, k]:
                a_vendre[k] = "sortie de l’indice"
            elif I["fin_semaine"][i] and not RK[i, k] <= P["rang_sortie"]:
                a_vendre[k] = "perte de rang"
        # 5. à la clôture : achats pour le lendemain
        if I["marche_ok"][i] and i + 1 < i1:
            libres = P["max_lignes"] - (len(pos) - len(a_vendre))
            if libres > 0:
                cand = [k for k in np.flatnonzero(E[i]) if k not in pos and bloque.get(k, -1) < i
                        and np.isfinite(ATR[i, k]) and ATR[i, k] > 0]
                cand.sort(key=lambda k: -S[i, k])
                a_acheter = [(k, ATR[i, k]) for k in cand[:libres]]
        val = sum(p["n"] * p["dernier"] for p in pos.values())
        courbe.append(st["cash"] + val)
        investi.append(val / courbe[-1] if courbe[-1] > 0 else 0)

    eq = pd.Series(courbe, index=idx[i0:i1])
    ouvertes = [dict(action=I["tick"][k], entree=str(idx[p["i"]].date()), prix_entree=round(p["px"], 2),
                     quantite=p["n"], stop=round(p["stop"], 2), dernier=round(p["dernier"], 2)) for k, p in pos.items()]
    return eq, trades, st["frais"], float(np.mean(investi)) if investi else 0.0, ouvertes


def indice(I, debut, fin):
    idx = I["index"]
    i0, i1 = idx.searchsorted(pd.Timestamp(debut)), idx.searchsorted(pd.Timestamp(fin), side="right")
    px = float(I["spy_open"].iloc[i0])
    frais = P["frais_fixe"] + P["capital"] * (P["frais_change"] + P["glissement"])
    n = (P["capital"] - frais) / px  # ETF : fractions acceptées pour la comparaison
    return pd.Series(n * I["spy"].iloc[i0:i1].values, index=idx[i0:i1])


def mesures(eq, trades=None, frais=None, expo=None):
    r = eq.pct_change().dropna()
    ans = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / ans) - 1
    dd = float((eq / eq.cummax() - 1).min())
    sd = r.std()
    fin_an = eq.groupby(eq.index.year).last()
    base = fin_an.shift(1)
    base.iloc[0] = P["capital"]
    m = dict(final=round(float(eq.iloc[-1]), 2), total=float(eq.iloc[-1] / P["capital"] - 1), cagr=float(cagr),
             chute_max=dd, sharpe=float(r.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan"),
             calmar=float(cagr / abs(dd)) if dd < 0 else float("nan"),
             annees={int(a): float(v) for a, v in (fin_an / base - 1).items()})
    if trades is not None:
        g = [t["resultat"] for t in trades]
        gagnants = [x for x in g if x > 0]
        perdants = [x for x in g if x <= 0]
        m.update(trades=len(g), reussite=len(gagnants) / len(g) if g else float("nan"),
                 gain_moyen=float(np.mean(gagnants)) if gagnants else 0.0,
                 perte_moyenne=float(np.mean(perdants)) if perdants else 0.0,
                 duree_moyenne=float(np.mean([t["jours"] for t in trades])) if trades else 0.0,
                 frais=round(frais, 2), exposition=expo)
    return m


# ---------------------------------------------------------------- rapport HTML
CSS = """
:root{--bg:#F4F6F9;--fg:#13233A;--mut:#5B6B80;--ln:#D5DCE5;--card:#fff;--pos:#12795A;--neg:#B42318;--hl:#E8EEF8;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0F1722;--fg:#E6ECF3;--mut:#93A3B8;--ln:#26344A;--card:#172131;--pos:#3CC48F;--neg:#F07167;--hl:#1E2C44;color-scheme:dark}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:1100px;margin:0 auto;padding:24px 16px 60px}h1{font-size:1.8rem;margin:0 0 4px}h2{margin:32px 0 8px;font-size:1.2rem}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:14px 16px;margin:12px 0}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{padding:7px 9px;border-bottom:1px solid var(--ln);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal;min-width:16rem}th{color:var(--mut);font-weight:600}
tr.ref td{background:var(--hl);font-weight:600}.pos{color:var(--pos)}.neg{color:var(--neg)}img{max-width:100%;height:auto;background:#fff;border-radius:8px}
.alerte{border-color:var(--neg);color:var(--neg);font-weight:600}
"""


def pc(v, signe=True):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    cl = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<span class="{cl}">{"+" if signe and v > 0 else ""}{v * 100:.1f} %</span>'.replace(".", ",")


def nb(v, d=2):
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def graphique(series, titre):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=110)
    for nom, s, style in series:
        ax.plot(s.index, s.values, label=nom, **style)
    ax.set_title(titre, fontsize=11)
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, loc="upper left")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f} €".replace(",", " ")))
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    return f'<img alt="{titre}" src="data:image/png;base64,{base64.b64encode(b.getvalue()).decode()}">'


def ligne(nom, m, ref=False):
    t = m.get("trades")
    return (f'<tr{" class=ref" if ref else ""}><td>{nom}</td><td>{pc(m["cagr"])}</td><td>{pc(m["chute_max"])}</td>'
            f'<td>{nb(m["sharpe"])}</td><td>{nb(m["calmar"])}</td><td>{nb(m["final"], 0)} €</td>'
            f'<td>{t if t is not None else "–"}</td><td>{pc(m.get("reussite"), False) if t else "–"}</td>'
            f'<td>{nb(m["frais"], 0) + " €" if t is not None else "–"}</td></tr>')


ENTETE = ("<tr><th>Stratégie</th><th>Rendement / an</th><th>Pire chute</th><th>Sharpe</th><th>Calmar</th>"
          "<th>Capital final</th><th>Trades</th><th>Gagnants</th><th>Frais</th></tr>")


def page(titre, corps, I):
    hyp = (f"Capital {nb(P['capital'], 0)} € · {P['max_lignes']} lignes max · risque {P['risque_par_ligne'] * 100:.0f} % par ligne"
           f" · pas de rachat {P['attente_apres_stop']} séances après un stop · frais {nb(P['frais_fixe'])} € + {nb(P['frais_change'] * 100)} % de change + {nb(P['glissement'] * 100)} % de glissement par ordre")
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titre}</title><style>{CSS}</style></head><body><main><h1>{titre}</h1>
<p class="mut">Généré le {datetime.now(timezone.utc):%d/%m/%Y à %H:%M} UTC · {I['source']}<br>{hyp}<br>
Couverture : {I['n_obtenus']} actions avec cours sur {I['n_demandes']} ayant fait partie de l’indice · dates de résultats pour {I['couverture_resultats'] * 100:.0f} % d’entre elles</p>
{corps}
<h2>Comment lire ce rapport</h2><div class="card mut">
<p><b>Rendement / an</b> : croissance annuelle moyenne du capital. <b>Pire chute</b> : plus forte baisse entre un sommet et le creux suivant, ce qu’il aurait fallu supporter. <b>Sharpe</b> : rendement rapporté aux variations (au-dessus de 1, c’est bon). <b>Calmar</b> : rendement annuel divisé par la pire chute.</p>
<p>Hypothèses : signaux calculés à la clôture, ordres exécutés à l’ouverture suivante ; stops exécutés au niveau du stop, ou à l’ouverture si le cours ouvre en dessous ; nombre entier d’actions ; cours ajustés des dividendes ; variations euro/dollar ignorées. Les sociétés rachetées ou disparues manquent souvent chez Yahoo : un léger biais du survivant subsiste, qui flatte un peu les résultats.</p>
<p>Un bon résultat passé ne garantit pas les résultats futurs. Ce n’est pas un conseil en investissement.</p></div>
</main></body></html>"""


# ---------------------------------------------------------------- deux modes
def developpement(I, dossier, prefixe):
    debut, fin = DEV
    configs = [dict(zip(GRILLE, v)) for v in product(*GRILLE.values())]
    if I["couverture_resultats"] < 0.5:
        print("Dates de résultats insuffisantes : variantes « filtre résultats » ignorées.")
        configs = [c for c in configs if not c["resultats"]]
    res = []
    for n, c in enumerate(configs, 1):
        eq, tr, fr, ex, _ = simuler(I, c, debut, fin)
        res.append(dict(config=c, nom=nom_config(c), mesures=mesures(eq, tr, fr, ex), courbe=eq, trades=tr))
        print(f"  [{n}/{len(configs)}] {nom_config(c)} : {res[-1]['mesures']['cagr'] * 100:.1f} %/an")
    ref = mesures(indice(I, debut, fin))
    ref_eq = indice(I, debut, fin)
    res.sort(key=lambda r: -np.nan_to_num(r["mesures"]["sharpe"], nan=-9))

    lignes = ligne("Référence : S&P 500 acheté et conservé (SPY)", ref, True) + "".join(ligne(r["nom"], r["mesures"]) for r in res)
    annees = sorted(ref["annees"])
    t_an = ("<tr><th>Stratégie</th>" + "".join(f"<th>{a}</th>" for a in annees) + "</tr>" +
            "<tr class=ref><td>S&P 500</td>" + "".join(f"<td>{pc(ref['annees'][a])}</td>" for a in annees) + "</tr>" +
            "".join("<tr><td>" + r["nom"] + "</td>" + "".join(f"<td>{pc(r['mesures']['annees'].get(a))}</td>" for a in annees) + "</tr>" for r in res[:5]))
    styles = [dict(lw=1.6), dict(lw=1.2), dict(lw=1.2)]
    img = graphique([("S&P 500 (SPY)", ref_eq, dict(color="#888", lw=2, ls="--"))] +
                    [(r["nom"], r["courbe"], s) for r, s in zip(res[:3], styles)], "Évolution de 10 000 € — période de mise au point")
    corps = f"""<p class="card">Période de mise au point : <b>{debut[:4]} à {fin[:4]}</b>. Les données 2025-2026 n’ont pas été utilisées.
Choisissez une configuration robuste (pas seulement la première : ses voisines doivent aussi être bonnes), puis figez-la dans <code>backtest/config_retenue.json</code>.</p>
<h2>Classement des {len(res)} configurations (par ratio de Sharpe)</h2><div class="card wrap"><table>{ENTETE}{lignes}</table></div>
<h2>Année par année — les 5 premières</h2><div class="card wrap"><table>{t_an}</table></div>
<h2>Courbes de capital — les 3 premières</h2><div class="card">{img}</div>"""
    with open(os.path.join(dossier, f"{prefixe}rapport_developpement.html"), "w", encoding="utf-8") as f:
        f.write(page("Backtest — mise au point (2021-2024)", corps, I))
    with open(os.path.join(dossier, f"{prefixe}resultats_developpement.json"), "w", encoding="utf-8") as f:
        json.dump(dict(periode=DEV, parametres=P, reference=ref,
                       configurations=[dict(nom=r["nom"], config=r["config"], mesures=r["mesures"]) for r in res]),
                  f, ensure_ascii=False, indent=1)
    print(f"\nMeilleure configuration : {res[0]['nom']}")


def validation(I, dossier, prefixe):
    chemin = os.path.join(dossier, "config_retenue.json")
    if not os.path.exists(chemin):
        sys.exit("Il manque backtest/config_retenue.json : choisissez d'abord une configuration après la mise au point.")
    with open(chemin, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg = {k: cfg[k] for k in GRILLE}
    empreinte = hashlib.sha1(json.dumps([cfg, P], sort_keys=True).encode()).hexdigest()[:10]
    journal = os.path.join(dossier, f"{prefixe}journal_validation.json")
    deja = json.load(open(journal, encoding="utf-8")) if os.path.exists(journal) else []
    autres = [d for d in deja if d["empreinte"] != empreinte]

    fin_val = str(I["index"][-1].date())
    blocs, periodes = [], [("Mise au point", *DEV), ("Validation", VAL_DEBUT, fin_val)]
    sortie = dict(config=cfg, nom=nom_config(cfg), empreinte=empreinte, parametres=P)
    graphs = []
    for titre, d, f_ in periodes:
        eq, tr, fr, ex, ouv = simuler(I, cfg, d, f_)
        m, ref = mesures(eq, tr, fr, ex), mesures(indice(I, d, f_))
        sortie[titre] = dict(debut=d, fin=f_, mesures=m, reference=ref, trades=tr, positions_ouvertes=ouv)
        blocs.append(f'<h2>{titre} : {d[:4]} → {f_[:7]}</h2><div class="card wrap"><table>{ENTETE}'
                     f'{ligne("S&P 500 (SPY)", ref, True)}{ligne(nom_config(cfg), m)}</table></div>')
        graphs.append(graphique([("S&P 500 (SPY)", indice(I, d, f_), dict(color="#888", lw=2, ls="--")),
                                 (nom_config(cfg), eq, dict(lw=1.6))], f"{titre} — évolution de 10 000 €"))
    v = sortie["Validation"]
    derniers = sorted(v["trades"], key=lambda t: t["sortie"])[-25:]
    t_tr = ("<tr><th>Action</th><th>Entrée</th><th>Sortie</th><th>Prix entrée</th><th>Prix sortie</th><th>Résultat</th><th>Raison</th></tr>" +
            "".join(f'<tr><td>{t["action"]}</td><td>{t["entree"]}</td><td>{t["sortie"]}</td><td>{nb(t["prix_entree"])}</td>'
                    f'<td>{nb(t["prix_sortie"])}</td><td>{pc(t["resultat_pct"])}</td><td>{t["raison"]}</td></tr>' for t in reversed(derniers)))
    avert = (f'<p class="card alerte">Attention : la validation a déjà été lancée {len(autres)} fois avec d’autres réglages. '
             f'Chaque nouvel essai sur 2025-2026 réduit la valeur de ce test.</p>') if autres else ""
    corps = (f'{avert}<p class="card">Configuration figée : <b>{nom_config(cfg)}</b> (empreinte {empreinte}).<br>'
             f'Si les résultats de validation ressemblent à ceux de la mise au point, la règle est probablement solide. '
             f'S’ils s’effondrent, elle a appris le passé par cœur.</p>' + "".join(blocs) +
             '<h2>Courbes de capital</h2><div class="card">' + "".join(graphs) + "</div>" +
             f'<h2>Derniers trades de la période de validation</h2><div class="card wrap"><table>{t_tr}</table></div>')
    with open(os.path.join(dossier, f"{prefixe}rapport_validation.html"), "w", encoding="utf-8") as f:
        f.write(page("Backtest — validation (2025-2026)", corps, I))
    with open(os.path.join(dossier, f"{prefixe}resultats_validation.json"), "w", encoding="utf-8") as f:
        json.dump(sortie, f, ensure_ascii=False, indent=1, default=str)
    if not any(d["empreinte"] == empreinte for d in deja):
        deja.append(dict(empreinte=empreinte, nom=nom_config(cfg), date=datetime.now(timezone.utc).isoformat(timespec="minutes")))
        with open(journal, "w", encoding="utf-8") as f:
            json.dump(deja, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--periode", choices=["developpement", "validation"], default="developpement")
    ap.add_argument("--demo", action="store_true", help="cours fictifs, pour tester le programme")
    a = ap.parse_args()
    D = charger_demo() if a.demo else charger_reel()
    print("Calcul des indicateurs…")
    I = preparer(D)
    if not a.demo and I["n_obtenus"] < 400:
        sys.exit(f"Seulement {I['n_obtenus']} actions téléchargées : résultats non fiables, réessayez plus tard.")
    prefixe = "demo_" if a.demo else ""
    (developpement if a.periode == "developpement" else validation)(I, ICI, prefixe)
    print("Terminé.")


if __name__ == "__main__":
    main()
