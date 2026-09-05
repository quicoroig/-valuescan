#!/usr/bin/env python3
"""ValueScan — escáner diario. Escribe picks.json junto a app.html.

  ODDSPAPI_KEY=xxx python3 scan.py              → un escaneo
  ODDSPAPI_KEY=xxx python3 scan.py --daily 09:00 → se queda corriendo y escanea cada día a esa hora
Opciones: --minval 20 --minsure 10 --bank 1000 --hours 48 --books bet365,winamax
"""
import os, sys, json, time, re, argparse, datetime, urllib.request, urllib.parse

API = "https://api.oddspapi.io/v4"
EU = set("""spain england italy germany france portugal netherlands belgium scotland turkey greece austria
switzerland denmark sweden norway poland czech-republic czechia croatia serbia ukraine russia romania hungary
bulgaria slovakia slovenia ireland wales northern-ireland iceland finland cyprus israel bosnia-and-herzegovina
bosnia north-macedonia albania montenegro kosovo lithuania latvia estonia belarus georgia armenia azerbaijan
luxembourg malta moldova faroe-islands andorra gibraltar san-marino liechtenstein europe international world uefa""".split())
BAD = re.compile(r"women|femen|feminine|u-?1\d|u-?2[0-3]|youth|junior|reserve|amateur|friendly|amistoso|esports|simulated|virtual|itf|challenger|doubles|dobles|futures|legends|indoor|beach|3x3", re.I)
RULES = {
  "football": lambda t: any(x in t["slug"] for x in TOP_LEAGUE_SLUGS),
}
SPORT_KEYS = {"football": ["football", "soccer"]}
TOP_LEAGUE_SLUGS = ["laliga", "la-liga", "segunda", "laliga2", "laliga-2", "hypermotion", "primera-rfef", "primerafef", "primera-federacion"]
_last = 0
def api(path, **params):
    global _last
    w = 1.1 - (time.time() - _last)
    if w > 0: time.sleep(w)
    _last = time.time()
    params["apiKey"] = KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "ValueScan/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:600]
        shown = {k: v for k, v in params.items() if k != "apiKey"}
        print(f"\n[API {e.code}] {path} {shown}\n{body}\n")
        raise

def find_book(bms, want, es=True):
    slugs = [b.get("bookmakerSlug") or b.get("slug") or "" for b in bms]
    hit = [s for s in slugs if want in s and (not es or re.search(r"(-|_|\.)es$|spain|espa", s))]
    return (hit or [s for s in slugs if want in s] or [None])[0]

def pick_tournaments(k, ts):
    out = []
    for t in ts:
        d = {**t, "cat": (t.get("categorySlug") or t.get("categoryName") or "").lower(),
             "slug": (t.get("tournamentSlug") or t.get("tournamentName") or "").lower(),
             "n": (t.get("futureFixtures") or 0) + (t.get("upcomingFixtures") or 0) + (t.get("liveFixtures") or 0)}
        if d["n"] > 0 and not BAD.search(d["slug"] + " " + (t.get("tournamentName") or "")) and RULES[k](d):
            out.append(d)
    out.sort(key=lambda t: -t["n"])
    return out[:5]

BUDGET = 60  # margen de seguridad bajo el límite del plan gratuito (250 total)
_calls = [0]
_orig_api = api
def api(path, **kw):
    if _calls[0] >= BUDGET:
        raise SystemExit(f"Presupuesto de {BUDGET} peticiones agotado en este escaneo para no gastar todo tu plan mensual. Reduce ligas o sube de plan.")
    _calls[0] += 1
    return _orig_api(path, **kw)

def scan(a):
    try:
        acc = api("/account")
        sub = acc.get("subscription") or acc
        rc = sub.get("request_count", acc.get("request_count"))
        rl = sub.get("request_limit", acc.get("request_limit"))
        print(f"Cuenta OddsPapi: {rc}/{rl} peticiones usadas este periodo — {json.dumps(acc)[:300]}")
    except Exception as e:
        print("No se pudo leer /account:", e)
    now = time.time(); lim = now + a.hours * 3600
    sports = api("/sports")
    sport_ids = {}
    for s in sports:
        slug = (s.get("sportSlug") or s.get("sportName") or "").lower()
        for k, keys in SPORT_KEYS.items():
            if k not in sport_ids and any(slug == x or x in slug for x in keys): sport_ids[k] = s["sportId"]
    bms = api("/bookmakers")
    books = {"pinnacle": find_book(bms, "pinnacle", False), **{b: find_book(bms, b) for b in a.books.split(",")}}
    print("Deportes:", sport_ids); print("Casas:", books)
    if not books["pinnacle"]: sys.exit("Pinnacle no disponible en tu plan.")
    soft = {b: s for b, s in books.items() if b != "pinnacle" and s}
    markets = {str(m["marketId"]): m for m in api("/markets")}
    found = []; nfx = 0
    for k, sid in sport_ids.items():
        ts = pick_tournaments(k, api("/tournaments", sportId=sid))
        try: names = api("/participants", sportId=sid, language="es")
        except urllib.error.HTTPError: names = api("/participants", sportId=sid)
        tname = {t["tournamentId"]: t.get("tournamentName") for t in ts}
        ids = [t["tournamentId"] for t in ts]
        print(f"{k}: {len(ids)} competiciones")
        all_books = [books["pinnacle"], *soft.values()]
        def fetch_one_book(batch, bookslug):
            try:
                fx = api("/odds-by-tournaments", tournamentIds=",".join(map(str, batch)), bookmaker=bookslug, verbosity=3)
                return fx if isinstance(fx, list) else list(fx.values())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return []  # sin partidos para esa casa/liga ahora mismo: normal, no reintentar
                print(f"  {bookslug}: competición(es) {batch} omitida(s) ({e.code})"); return []
        def fetch(batch):
            merged = {}
            for bookslug in all_books:
                for f in fetch_one_book(batch, bookslug):
                    fx = merged.setdefault(f["fixtureId"], f)
                    if fx is not f:
                        fx.setdefault("bookmakerOdds", {}).update(f.get("bookmakerOdds", {}))
                    else:
                        fx.setdefault("bookmakerOdds", fx.get("bookmakerOdds", {}))
            return list(merged.values())
        for j in range(0, len(ids), 5):
            for f in fetch(ids[j:j+12]):
                st = datetime.datetime.fromisoformat(f["startTime"].replace("Z", "+00:00")).timestamp()
                if not (now < st < lim) or (f.get("statusId") or 0) >= 2: continue
                nfx += 1
                home = names.get(str(f["participant1Id"]), "Local"); away = names.get(str(f["participant2Id"]), "Visitante")
                base = {"fixtureId": f["fixtureId"], "sport": k, "home": home, "away": away, "league": tname.get(f["tournamentId"], ""), "start": int(st * 1000)}
                by = {}
                for slug, bo in (f.get("bookmakerOdds") or {}).items():
                    if bo.get("suspended"): continue
                    for mid, m in (bo.get("markets") or {}).items():
                        if m.get("marketActive") is False: continue
                        for oid, o in (m.get("outcomes") or {}).items():
                            p = (o.get("players") or {}).get("0")
                            if not p or p.get("active") is False or not (p.get("price") or 0) > 1: continue
                            by.setdefault(mid, {}).setdefault(oid, {})[slug] = {"price": p["price"], "url": bo.get("fixturePath")}
                for mid, outs in by.items():
                    md = markets.get(mid)
                    if not md or md.get("playerProp"): continue
                    oids = list(outs); n_out = md.get("marketLength") or len(oids)
                    def oname(oid):
                        o = next((x for x in md.get("outcomes", []) if str(x["outcomeId"]) == str(oid)), None)
                        nm = o["outcomeName"] if o else oid
                        nm = {"1": home, "2": away, "X": "Empate", "Over": "Más de", "Under": "Menos de", "Yes": "Sí"}.get(nm, nm)
                        if md.get("handicap") and nm in ("Más de", "Menos de"): nm += f" {md['handicap']}"
                        return nm
                    mname = (md.get("marketName") or "").replace("Full Time Result", "Resultado").replace("Over Under Full Time", "Goles totales").replace("Both Teams To Score", "Ambos marcan").replace("Moneyline", "Ganador")
                    pin = [outs[o].get(books["pinnacle"], {}).get("price") for o in oids]
                    if len(pin) == n_out and all(pin):
                        inv = [1 / p for p in pin]; s = sum(inv); fair = [i / s for i in inv]
                        for oid, fp in zip(oids, fair):
                            for b, slug in soft.items():
                                x = outs[oid].get(slug)
                                if not x or x["price"] > a.maxodd: continue
                                ev = x["price"] * fp - 1
                                if ev * 100 >= a.minval:
                                    kelly = max(0, ((x["price"] - 1) * fp - (1 - fp)) / (x["price"] - 1)) * a.kelly * a.bank
                                    found.append({**base, "type": "value", "edge": ev * 100, "market": mname, "sel": oname(oid), "book": b,
                                                  "odds": x["price"], "fair": round(1 / fp, 2), "prob": fp, "url": x["url"],
                                                  "stake": min(kelly, a.bank * a.maxpct / 100)})
                    if len(oids) == n_out and n_out >= 2:
                        best = []
                        for oid in oids:
                            bb = None
                            for b, slug in soft.items():
                                x = outs[oid].get(slug)
                                if x and (not bb or x["price"] > bb["price"]): bb = {**x, "book": b}
                            best.append(bb)
                        if all(best) and len({b["book"] for b in best}) > 1:
                            inv = [1 / b["price"] for b in best]; s = sum(inv); margin = (1 / s - 1) * 100
                            if margin >= a.minsure:
                                total = a.bank * a.maxpct / 100 * 2
                                found.append({**base, "type": "sure", "edge": margin, "market": mname, "stake": total,
                                              "legs": [{"sel": oname(o), "book": b["book"], "odds": b["price"], "url": b["url"], "stake": total * (1 / b["price"]) / s} for o, b in zip(oids, best)]})
    seen = {}
    for p in found:
        key = f'{p["fixtureId"]}|{p["market"]}|{p.get("sel", "SURE")}'
        if key not in seen or seen[key]["edge"] < p["edge"]: seen[key] = p
    picks = sorted(seen.values(), key=lambda p: -p["edge"])
    out = {"at": int(time.time() * 1000), "nfx": nfx, "picks": picks}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "picks.json"), "w") as fh: json.dump(out, fh, ensure_ascii=False)
    print(f"{nfx} partidos analizados → {len(picks)} picks. Top 3:")
    for p in picks[:3]: print(f'  +{p["edge"]:.1f}%  {p["home"]} – {p["away"]}  {p["market"]}  {p.get("sel","surebet")}')

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily"); ap.add_argument("--minval", type=float, default=20); ap.add_argument("--minsure", type=float, default=10)
    ap.add_argument("--bank", type=float, default=1000); ap.add_argument("--kelly", type=float, default=0.25); ap.add_argument("--maxpct", type=float, default=3)
    ap.add_argument("--maxodd", type=float, default=6); ap.add_argument("--hours", type=float, default=48); ap.add_argument("--books", default="bet365,winamax")
    a = ap.parse_args()
    KEY = os.environ.get("ODDSPAPI_KEY") or sys.exit("Define ODDSPAPI_KEY")
    if not a.daily: scan(a); sys.exit()
    hh, mm = map(int, a.daily.split(":"))
    while True:
        nxt = datetime.datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= datetime.datetime.now(): nxt += datetime.timedelta(days=1)
        print("Próximo escaneo:", nxt); time.sleep((nxt - datetime.datetime.now()).total_seconds())
        try: scan(a)
        except Exception as e: print("Error:", e)
