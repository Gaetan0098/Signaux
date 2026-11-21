#!/usr/bin/env python3
"""
Backtest du process de l'appli Road to Million, 2021-2025, sur le S&P 500 et le STOXX Europe 600.

Process (identique à l'appli) :
  - classement par ratio MM20/MM200 des actions dont la MM20 est au-dessus de la MM200 ;
  - achat des meilleurs ratios non détenus, à parts égales, 10 lignes maximum ;
  - vente si l'action sort du classement (taille = 10 % de l'univers), si la MM20 passe
    sous la MM200 (croisement baissier) ou si l'action quitte l'indice ;
  - signaux à la clôture, ordres à l'ouverture suivante. Pas de stop.

Variante comparée : même process + stop au prix d'achat, activé dès qu'une clôture
dépasse le prix d'achat d'au moins 10 %.

    python backtest/backtest_process.py          (vraies données, sur GitHub Actions)
    python backtest/backtest_process.py --demo   (cours fictifs, pour tester le programme)
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
sys.path.insert(0, os.path.dirname(ICI))
import backtest as BT  # noqa: E402  (fonctions communes : données S&P 500, mesures, graphiques)

P = dict(capital=10_000.0, lignes=10, frais_fixe=1.0, frais_change=0.0025, glissement=0.0005,
         seuil_stop=0.10, part_classement=0.10)
DEBUT, FIN = "2021-01-01", "2025-12-31"
DEBUT_DONNEES = "2019-10-01"


# ---------------------------------------------------------------- données
def donnees_us():
    print("S&P 500 : composition historique…")
    actuels, changements, noms, _ = BT.lire_wikipedia()
    actuels = [BT.symbole(t) for t in actuels]
    histo = BT.composition_historique()
    snapshots, ajouts = None, []
    if histo:
        nom_src, snaps = histo
        d0 = pd.Timestamp(DEBUT_DONNEES)
        avant = [s for s in snaps if s[0] <= d0]
        snapshots = (avant[-1:] if avant else []) + [s for s in snaps if s[0] > d0]
        ajouts = [(d, BT.symbole(a)) for d, a, r in changements if a and d > snapshots[-1][0]]
        anciens = set().union(*[s[1] for s in snapshots])
        composition = f"historique ({nom_src})"
    else:
        anciens = {BT.symbole(r) for d, a, r in changements if r and d >= pd.Timestamp(DEBUT_DONNEES)}
        composition = "partielle (Wikipedia)"
    tickers = sorted(set(actuels) | anciens)
    print(f"  {len(tickers)} actions, téléchargement des cours…")
    px = BT.telecharger(tickers, DEBUT_DONNEES)
    bench = BT.telecharger(["SPY"], DEBUT_DONNEES)
    c = px["Close"]
    c = c.loc[:, c.notna().sum() > 0]
    if snapshots:
        membres = BT.appartenance_releves(c.index, snapshots, ajouts, list(c.columns))
    else:
        membres = BT.appartenance(c.index, actuels, changements, list(c.columns))
    return dict(nom="S&P 500", px={k: v.reindex(columns=c.columns) for k, v in px.items()},
                membres=membres, devises={t: "USD" for t in c.columns}, fx=None,
                bench_nom="SPY (S&P 500, dividendes réinvestis)",
                bench_close=bench["Close"]["SPY"], bench_open=bench["Open"]["SPY"],
                taille=max(P["lignes"], round(len(actuels) * P["part_classement"])),
                composition=f"composition {composition}", noms=noms)


def donnees_eu():
    import telecharger_sp500 as TS
    print("STOXX Europe 600 : composition actuelle…")
    base = TS.stoxx600()
    tickers = list(dict.fromkeys(b["t"] for b in base))
    print(f"  {len(tickers)} actions, téléchargement des cours…")
    px = BT.telecharger(tickers, DEBUT_DONNEES)
    # actions Euronext sans cours : autres places Euronext
    manquants = [t for t in tickers if t not in px["Close"].columns or px["Close"][t].notna().sum() < 250]
    essais = {}
    for t in manquants:
        racine, _, suf = t.rpartition(".")
        if "." + suf in TS.EURONEXT_SUFFIXES:
            essais[t] = [racine + s for s in TS.EURONEXT_SUFFIXES if s != "." + suf]
    if essais:
        px2 = BT.telecharger(sorted({a for l in essais.values() for a in l}), DEBUT_DONNEES)
        for b in base:
            for a in essais.get(b["t"], []):
                if a in px2["Close"].columns and px2["Close"][a].notna().sum() >= 250:
                    for f in px:
                        px[f][a] = px2[f][a].reindex(px[f].index)
                    print(f"  {b['n']} : {b['t']} remplacé par {a}")
                    b["t"] = a
                    break
    devises = {b["t"]: b["cur"] for b in base}
    c = px["Close"]
    garde = [t for t in c.columns if t in devises and c[t].notna().sum() > 0]
    px = {k: v.reindex(columns=garde).copy() for k, v in px.items()}
    for t in garde:                       # Londres : pence -> livres
        if t.endswith(".L"):
            for f in px:
                px[f][t] = px[f][t] / 100.0
    autres = sorted({devises[t] for t in garde} - {"EUR"})
    fx = pd.DataFrame(index=px["Close"].index)
    if autres:
        brut = BT.telecharger([f"EUR{d}=X" for d in autres], DEBUT_DONNEES)["Close"]
        for d in autres:
            fx[d] = brut[f"EUR{d}=X"].reindex(fx.index).ffill().bfill()
    fx["EUR"] = 1.0
    bench_nom, bench = "EXSA.DE (ETF STOXX Europe 600)", None
    for sym, nom in (("EXSA.DE", "EXSA.DE (ETF STOXX Europe 600)"), ("^STOXX", "^STOXX (indice, hors dividendes)")):
        try:
            b = BT.telecharger([sym], DEBUT_DONNEES)
            if b["Close"][sym].notna().sum() > 500:
                bench, bench_nom = b, nom
                break
        except Exception as e:
            print(f"  référence {sym} indisponible : {e}")
    sym = bench_nom.split(" ")[0]
    return dict(nom="STOXX Europe 600", px=px, membres=np.ones(px["Close"].shape, bool),
                devises={t: devises[t] for t in garde}, fx=fx,
                bench_nom=bench_nom, bench_close=bench["Close"][sym], bench_open=bench["Open"][sym],
                taille=max(P["lignes"], round(len(base) * P["part_classement"])),
                composition="composition actuelle uniquement (biais du survivant : les sociétés sorties de l’indice depuis 2021 ne sont pas testées)",
                noms={b["t"]: b["n"] for b in base})


def donnees_demo(marche):
    rng = np.random.default_rng(11 if marche == "us" else 12)
    idx = pd.bdate_range(DEBUT_DONNEES, "2025-12-31")
    N, K = len(idx), 160
    tick = [f"{marche.upper()}{k:03d}" for k in range(K)]
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
    mk = lambda a: pd.DataFrame(a, index=idx, columns=tick)
    bench = pd.Series(np.nanmean(C / C[0], axis=1) * 100, index=idx)
    devises = {t: ("GBP" if (marche == "eu" and k % 3 == 0) else ("USD" if marche == "us" else "EUR")) for k, t in enumerate(tick)}
    fx = None if marche == "us" else pd.DataFrame({"EUR": 1.0, "GBP": 0.85}, index=idx)
    return dict(nom=("S&P 500" if marche == "us" else "STOXX Europe 600") + " (démo)", px=dict(Open=mk(O), High=mk(H), Low=mk(L), Close=mk(C)),
                membres=np.ones((N, K), bool), devises=devises, fx=fx, bench_nom="référence fictive",
                bench_close=bench, bench_open=bench.shift(1).bfill(), taille=16, composition="fictive",
                noms={t: t for t in tick})


# ---------------------------------------------------------------- préparation
def preparer(D):
    c = D["px"]["Close"].sort_index()
    tick = list(c.columns)
    idx = c.index
    cf = c.ffill(limit=5)                                   # jours fériés d'une bourse : on garde la veille
    ma20 = cf.rolling(20, min_periods=20).mean()
    ma200 = cf.rolling(200, min_periods=200).mean()
    membres = np.asarray(D["membres"], bool)
    valide = membres & cf.notna().values & ma200.notna().values & (ma20 > ma200).values
    ratio = (ma20 / ma200).where(pd.DataFrame(valide, index=idx, columns=tick))
    rang = ratio.rank(axis=1, ascending=False, method="first")
    if D["fx"] is not None:
        fx = np.column_stack([D["fx"][D["devises"][t]].reindex(idx).ffill().bfill().values for t in tick])
    else:
        fx = np.ones((len(idx), len(tick)))
    dernier = c.apply(lambda s: s.last_valid_index())
    dernier_i = np.array([idx.get_loc(d) for d in dernier])
    fin_cote = np.where(dernier_i < len(idx) - 6, dernier_i, 10 ** 9)
    f = lambda x: np.asarray(x.reindex(columns=tick), dtype=float)
    return dict(index=idx, tick=tick, O=f(D["px"]["Open"]), H=f(D["px"]["High"]), L=f(D["px"]["Low"]),
                C=np.asarray(cf, float), rang=np.asarray(rang, float), valide=valide, membres=membres,
                fx=fx, change=np.array([D["devises"][t] != "EUR" for t in tick]), fin_cote=fin_cote,
                taille=D["taille"], bench_close=D["bench_close"].reindex(idx).ffill(),
                bench_open=D["bench_open"].reindex(idx).ffill())


# ---------------------------------------------------------------- simulation
def simuler(I, avec_stop):
    idx = I["index"]
    i0, i1 = idx.searchsorted(pd.Timestamp(DEBUT)), idx.searchsorted(pd.Timestamp(FIN), side="right")
    O, L, C, FX, RG = I["O"], I["L"], I["C"], I["fx"], I["rang"]
    X, nl = I["taille"], P["lignes"]
    st = dict(cash=P["capital"], frais=0.0)
    pos, trades, courbe, investi = {}, [], [], []
    a_vendre, a_acheter = {}, []

    def taux(k):
        return (P["frais_change"] if I["change"][k] else 0.0) + P["glissement"]

    def vendre(k, px, i, raison):
        p = pos.pop(k)
        montant = p["n"] * px / FX[i, k]
        cout = P["frais_fixe"] + montant * taux(k)
        st["cash"] += montant - cout
        st["frais"] += cout
        res = montant - cout - p["mise"]
        trades.append(dict(action=I["tick"][k], entree=str(idx[p["i"]].date()), sortie=str(idx[i].date()),
                           prix_entree=round(p["px"], 3), prix_sortie=round(px, 3), quantite=p["n"],
                           resultat=round(res, 2), resultat_pct=round(res / p["mise"], 4),
                           jours=int(i - p["i"]), raison=raison, stop_actif=p["actif"]))

    for i in range(i0, i1):
        eq_veille = courbe[-1] if courbe else P["capital"]
        # 1. ventes décidées la veille, à l'ouverture (si la bourse est fermée, on attend)
        for k, raison in list(a_vendre.items()):
            if k not in pos:
                del a_vendre[k]
            elif np.isfinite(O[i, k]):
                vendre(k, O[i, k], i, raison)
                del a_vendre[k]
        # 2. achats décidés la veille, à l'ouverture
        for k in a_acheter:
            if k in pos or len(pos) >= nl or not np.isfinite(O[i, k]) or O[i, k] <= 0:
                continue
            prix_eur = O[i, k] / FX[i, k]
            budget = min(eq_veille / nl, st["cash"] - P["frais_fixe"])
            n = math.floor(budget / (prix_eur * (1 + taux(k))))
            if n < 1:
                continue
            montant = n * prix_eur
            cout = P["frais_fixe"] + montant * taux(k)
            st["cash"] -= montant + cout
            st["frais"] += cout
            pos[k] = dict(n=n, px=O[i, k], i=i, mise=montant + cout, actif=False, dernier=O[i, k])
        a_acheter = []
        # 3. variante : stop au prix d'achat, une fois activé
        if avec_stop:
            for k in list(pos):
                p = pos[k]
                if p["actif"] and np.isfinite(L[i, k]) and L[i, k] <= p["px"]:
                    o = O[i, k]
                    vendre(k, min(o, p["px"]) if np.isfinite(o) else p["px"], i, "stop au prix d’achat")
                    a_vendre.pop(k, None)
        # 4. clôture : mise à jour et signaux de vente
        for k in list(pos):
            p = pos[k]
            if i >= I["fin_cote"][k]:
                vendre(k, p["dernier"], i, "fin de cotation")
                a_vendre.pop(k, None)
                continue
            c = C[i, k]
            if not np.isfinite(c):
                continue
            p["dernier"] = c
            if avec_stop and not p["actif"] and c >= p["px"] * (1 + P["seuil_stop"]):
                p["actif"] = True
            if not I["membres"][i, k]:
                a_vendre[k] = "sortie de l’indice"
            elif not I["valide"][i, k]:
                a_vendre[k] = "croisement baissier"
            elif RG[i, k] > X:
                a_vendre[k] = f"sortie du top {X}"
        # 5. clôture : achats pour le lendemain
        libres = nl - (len(pos) - len(a_vendre))
        if libres > 0 and i + 1 < i1:
            ordre = np.argsort(np.where(np.isfinite(RG[i]), RG[i], np.inf))
            for k in ordre:
                if not np.isfinite(RG[i, k]) or len(a_acheter) >= libres:
                    break
                if k not in pos:
                    a_acheter.append(k)
        val = sum(p["n"] * p["dernier"] / FX[i, k] for k, p in pos.items())
        courbe.append(st["cash"] + val)
        investi.append(val / courbe[-1] if courbe[-1] > 0 else 0)
    eq = pd.Series(courbe, index=idx[i0:i1])
    return eq, trades, st["frais"], float(np.mean(investi)), len(pos)


def reference(I):
    idx = I["index"]
    i0, i1 = idx.searchsorted(pd.Timestamp(DEBUT)), idx.searchsorted(pd.Timestamp(FIN), side="right")
    frais = P["frais_fixe"] + P["capital"] * P["glissement"]
    n = (P["capital"] - frais) / float(I["bench_open"].iloc[i0])
    return pd.Series(n * I["bench_close"].iloc[i0:i1].values, index=idx[i0:i1])


# ---------------------------------------------------------------- rapport
def raisons(trades):
    r = {}
    for t in trades:
        r[t["raison"]] = r.get(t["raison"], 0) + 1
    return dict(sorted(r.items(), key=lambda x: -x[1]))


def bloc_marche(nom, I, ref, res, D):
    lignes = BT.ligne(D["bench_nom"], ref, True) + "".join(BT.ligne(n, r["mesures"]) for n, r in res.items())
    annees = sorted(ref["annees"])
    t_an = ("<tr><th>Stratégie</th>" + "".join(f"<th>{a}</th>" for a in annees) + "</tr>"
            + "<tr class=ref><td>Référence</td>" + "".join(f"<td>{BT.pc(ref['annees'][a])}</td>" for a in annees) + "</tr>"
            + "".join("<tr><td>" + n + "</td>" + "".join(f"<td>{BT.pc(r['mesures']['annees'].get(a))}</td>" for a in annees) + "</tr>"
                      for n, r in res.items()))
    t_rs = ("<tr><th>Motif de vente</th>" + "".join(f"<th>{n}</th>" for n in res) + "</tr>")
    tous = []
    for r in res.values():
        tous += [k for k in r["raisons"] if k not in tous]
    t_rs += "".join("<tr><td>" + k + "</td>" + "".join(f"<td>{r['raisons'].get(k, 0)}</td>" for r in res.values()) + "</tr>" for k in tous)
    st = res["Process + stop au prix d’achat"]
    sauves = [t for t in st["trades"] if t["raison"] == "stop au prix d’achat"]
    img = BT.graphique([("Référence", st["ref_eq"], dict(color="#888", lw=2, ls="--"))]
                       + [(n, r["courbe"], dict(lw=1.6)) for n, r in res.items()], f"{nom} — évolution de 10 000 €")
    return f"""<h2>{nom}</h2>
<p class="mut">Classement de {I['taille']} actions (10 % de l’univers) · {D['composition']} · {len(I['tick'])} actions avec cours</p>
<div class="card wrap"><table>{BT.ENTETE}{lignes}</table></div>
<div class="card wrap"><table>{t_an}</table></div>
<div class="card wrap"><table>{t_rs}</table>
<p class="mut">Avec la variante, {len(sauves)} ventes ont été déclenchées par le stop au prix d’achat
(résultat moyen de ces ventes : {BT.nb(np.mean([t['resultat'] for t in sauves]) if sauves else 0, 0)} € par vente, frais compris).</p></div>
<div class="card">{img}</div>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--marches", default="us,eu")
    a = ap.parse_args()
    blocs, sortie = [], dict(periode=[DEBUT, FIN], parametres=P, marches={})
    for m in a.marches.split(","):
        try:
            D = donnees_demo(m) if a.demo else (donnees_us() if m == "us" else donnees_eu())
        except Exception as e:
            print(f"ÉCHEC du chargement {m} : {e}")
            blocs.append(f"<h2>{m}</h2><p class='card alerte'>Données indisponibles : {e}</p>")
            continue
        I = preparer(D)
        ref_eq = reference(I)
        ref = BT.mesures(ref_eq)
        res = {}
        for nom, stop in (("Process actuel", False), ("Process + stop au prix d’achat", True)):
            eq, tr, fr, ex, ouv = simuler(I, stop)
            res[nom] = dict(mesures=BT.mesures(eq, tr, fr, ex), courbe=eq, trades=tr, raisons=raisons(tr), ref_eq=ref_eq)
            print(f"  {D['nom']} · {nom} : {res[nom]['mesures']['cagr'] * 100:.1f} %/an, pire chute {res[nom]['mesures']['chute_max'] * 100:.0f} %")
        print(f"  {D['nom']} · référence : {ref['cagr'] * 100:.1f} %/an")
        blocs.append(bloc_marche(D["nom"], I, ref, res, D))
        sortie["marches"][D["nom"]] = dict(reference=dict(nom=D["bench_nom"], mesures=ref), taille_classement=I["taille"],
                                           composition=D["composition"],
                                           strategies={n: dict(mesures=r["mesures"], raisons=r["raisons"]) for n, r in res.items()})
    hyp = (f"Capital 10 000 € · {P['lignes']} lignes à parts égales · frais {BT.nb(P['frais_fixe'])} € par ordre"
           f" + {BT.nb(P['frais_change'] * 100)} % de change (actions hors euro) + {BT.nb(P['glissement'] * 100)} % de glissement")
    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest du process 2021-2025</title><style>{BT.CSS}</style></head><body><main><h1>Backtest du process — 2021 à 2025</h1>
<p class="mut">Généré le {datetime.now(timezone.utc):%d/%m/%Y à %H:%M} UTC · {hyp}</p>
<p class="card">Process actuel : achat des meilleurs ratios MM20/MM200, vente à la sortie du classement, au croisement baissier ou à la sortie de l’indice.
Variante : même process, plus un stop au prix d’achat dès qu’une clôture a dépassé ce prix d’au moins {P['seuil_stop'] * 100:.0f} %.</p>
{''.join(blocs)}
<h2>Hypothèses</h2><div class="card mut"><p>Signaux à la clôture, ordres à l’ouverture suivante ; stop exécuté à son niveau, ou à l’ouverture si le cours ouvre en dessous.
Nombre entier d’actions ; cours ajustés des dividendes. S&amp;P 500 : dollars traités comme des euros (change ignoré), comme dans les backtests précédents.
Europe : cours dans la devise de chaque bourse, convertis en euros au cours du jour pour la valeur du portefeuille.
L’année 2025 faisait partie de la période de validation : ce test compare deux variantes, il ne remplace pas une validation.
Ce n’est pas un conseil en investissement.</p></div></main></body></html>"""
    pre = "demo_" if a.demo else ""
    with open(os.path.join(ICI, f"{pre}rapport_process.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(ICI, f"{pre}resultats_process.json"), "w", encoding="utf-8") as f:
        json.dump(sortie, f, ensure_ascii=False, indent=1, default=str)
    print("Terminé.")


if __name__ == "__main__":
    main()
