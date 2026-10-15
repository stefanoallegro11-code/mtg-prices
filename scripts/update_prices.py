#!/usr/bin/env python3
"""
MTG Price Hunter - aggiornamento automatico dei prezzi.

Per ogni carta di cards.txt (formato Manabox):
  1. Scryfall  -> stampa esatta (set + numero), prezzo Cardmarket Trend, immagine
  2. CardTrader -> blueprint della stessa identica variante, verificato con
     scryfall_id, cardmarket_id, numero di collezione e nome (+ TCGTracking)
  3. CardTrader Zero -> offerta piu' economica in Inglese e in Giapponese,
     condizione minima Slightly Played, foil/non foil come in lista

Scrive data/prices.json e data/history.json (letti dalla pagina web).
Usa solo la libreria standard di Python.
"""

import gzip
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_FILE = os.path.join(ROOT, "cards.txt")
DATA_DIR = os.path.join(ROOT, "data")
PRICES_FILE = os.path.join(DATA_DIR, "prices.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
CACHE_DIR = os.path.join(ROOT, ".cache")
CT_INDEX_FILE = os.path.join(CACHE_DIR, "ct_index.json.gz")

CT_API = "https://api.cardtrader.com/api/v2"
SF_API = "https://api.scryfall.com"
TCG_API = "https://tcgtracking.com/tcgapi/v1/1"
UA = "mtg-prices/1.0 (personal price tracker)"

CT_TOKEN = os.environ.get("CARDTRADER_TOKEN", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

OK_CONDITIONS = {"mint", "near mint", "slightly played"}
LANGS = ("en", "jp")
HISTORY_DAYS = 60
INDEX_MAX_AGE_H = 30
ALERT_MIN_SAVING = float(os.environ.get("ALERT_MIN_SAVING", "3"))

LOG = []


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_json(url, headers=None, data=None, tries=4, timeout=60):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return None
            if e.code == 401:
                raise
            if e.code == 403:
                # CardTrader a volte blocca per qualche minuto: aspetto e riprovo
                if attempt < tries - 1:
                    time.sleep(30 * (attempt + 1))
                    continue
                raise
            if e.code == 429 or e.code >= 500:
                time.sleep(1.5 * (attempt + 1) + (3 if e.code == 429 else 0))
                continue
            return None
        except Exception as e:  # rete, timeout, json
            last = e
            time.sleep(1.5 * (attempt + 1))
    log("  ! richiesta fallita:", url.split("?")[0], "-", last)
    return None


def ct_get(path):
    return http_json(CT_API + path, headers={"Authorization": "Bearer " + CT_TOKEN})


# --------------------------------------------------------------------------
# Lista carte (formato Manabox)
# --------------------------------------------------------------------------
LINE_RE = re.compile(r"^(\d+)\s+(.+?)\s+\(([A-Za-z0-9]+)\)\s+(\S+)(.*)$")


def clean_star(s):
    """Ripara i caratteri rovinati dall'export (es. "58â˜…" -> "58★", "PalantÃ­r" -> "Palantír")."""
    if any(ch in s for ch in "ÃÂâ"):
        try:
            s = s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            s = s.replace("\u00e2\u02dc\u2026", "\u2605")
    return s


def parse_cards(text):
    cards, seen = [], {}
    for raw in text.splitlines():
        line = clean_star(raw.strip())
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            log("  ? riga non riconosciuta:", line)
            continue
        flags = {t.strip("*").upper() for t in m.group(5).split()}
        foil = "F" in flags or "E" in flags
        card = {
            "qty": int(m.group(1)),
            "name": m.group(2).strip(),
            "set": m.group(3).lower(),
            "collector": m.group(4).strip(),
            "foil": foil,
            "etched": "E" in flags,
        }
        card["key"] = "%s-%s-%s" % (card["set"], card["collector"].lower(), "f" if foil else "n")
        if card["key"] in seen:
            cards[seen[card["key"]]]["qty"] += card["qty"]
            continue
        seen[card["key"]] = len(cards)
        cards.append(card)
    return cards


def norm_name(s):
    s = (s or "").split(" // ")[0].strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)


def norm_coll(s):
    s = str(s or "").lower().replace("★", "").strip()
    s = re.sub(r"^0+(?=\d)", "", s)
    return s


# --------------------------------------------------------------------------
# Scryfall
# --------------------------------------------------------------------------
def sf_image(d):
    if not d:
        return None
    if d.get("image_uris"):
        return d["image_uris"].get("normal") or d["image_uris"].get("large")
    faces = d.get("card_faces") or []
    if faces and faces[0].get("image_uris"):
        return faces[0]["image_uris"].get("normal")
    return None


def sf_collection(cards):
    """Recupera le stampe esatte in blocchi da 75 (endpoint /cards/collection)."""
    found = {}
    ids = [{"set": c["set"], "collector_number": c["collector"]} for c in cards]
    for i in range(0, len(ids), 75):
        chunk = ids[i:i + 75]
        res = http_json(SF_API + "/cards/collection", data={"identifiers": chunk})
        for d in (res or {}).get("data", []):
            found[(d["set"].lower(), d["collector_number"].lower())] = d
        time.sleep(0.15)
    return found


def sf_lookup(card, coll):
    """Restituisce (stampa scryfall, stampa alternativa senza stella o None)."""
    key = (card["set"], card["collector"].lower())
    d = coll.get(key)
    if d is None:
        d = http_json("%s/cards/%s/%s" % (SF_API, card["set"], urllib.parse.quote(card["collector"])))
        time.sleep(0.12)
    alt = None
    if "★" in card["collector"]:
        plain = card["collector"].replace("★", "")
        alt = coll.get((card["set"], plain.lower())) or http_json(
            "%s/cards/%s/%s" % (SF_API, card["set"], urllib.parse.quote(plain)))
        time.sleep(0.12)
        if d is None:
            d, alt = alt, None
    if d is None:
        q = urllib.parse.urlencode({"exact": card["name"].split(" // ")[0], "set": card["set"]})
        d = http_json(SF_API + "/cards/named?" + q)
        time.sleep(0.12)
    if d and d.get("object") == "error":
        d = None
    return d, alt


def sf_price(d, foil, etched=False):
    if not d:
        return None
    p = d.get("prices") or {}
    v = p.get("eur_foil") if foil else p.get("eur")
    try:
        return round(float(v), 2) if v else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Indice CardTrader (tutti i blueprint Magic, aggiornato 1 volta al giorno)
# --------------------------------------------------------------------------
def bp_collector(bp):
    for k in ("fixed_properties", "properties_hash"):
        v = (bp.get(k) or {}).get("collector_number")
        if v:
            return str(v)
    return str(bp.get("collector_number") or "")


def bp_image(bp):
    img = bp.get("image_url")
    if not img and isinstance(bp.get("image"), dict):
        img = bp["image"].get("url") or (bp["image"].get("show") or {}).get("url")
    return img


def build_ct_index():
    exps = ct_get("/expansions") or []
    exps = [e for e in exps if e.get("game_id") == 1]
    log("CardTrader: %d espansioni Magic, scarico i blueprint..." % len(exps))
    sample_logged = [False]

    def one(e):
        data = ct_get("/blueprints/export?expansion_id=%d" % e["id"]) or []
        out = []
        for bp in data:
            if bp.get("game_id") not in (None, 1):
                continue
            if not sample_logged[0]:
                sample_logged[0] = True
                log("  esempio campi blueprint:", sorted(bp.keys()))
            out.append({
                "id": bp["id"],
                "n": bp.get("name"),
                "e": bp.get("expansion_id") or e["id"],
                "s": bp.get("scryfall_id"),
                "c": [int(x) for x in (bp.get("card_market_ids") or []) if str(x).isdigit()],
                "k": bp_collector(bp),
                "i": bp_image(bp),
                "cat": bp.get("category_id"),
            })
        time.sleep(0.4)
        return out

    bps = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, part in enumerate(ex.map(one, exps)):
            bps.extend(part)
            if (i + 1) % 100 == 0:
                log("  ...%d/%d espansioni" % (i + 1, len(exps)))
    index = {
        "built": datetime.now(timezone.utc).isoformat(),
        "expansions": {str(e["id"]): {"code": e.get("code"), "name": e.get("name")} for e in exps},
        "blueprints": bps,
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with gzip.open(CT_INDEX_FILE, "wt", encoding="utf-8") as f:
        json.dump(index, f)
    log("CardTrader: indice creato con %d blueprint" % len(bps))
    return index


def load_ct_index():
    try:
        with gzip.open(CT_INDEX_FILE, "rt", encoding="utf-8") as f:
            idx = json.load(f)
        age = datetime.now(timezone.utc) - datetime.fromisoformat(idx["built"])
        if age < timedelta(hours=INDEX_MAX_AGE_H) and idx.get("blueprints"):
            log("CardTrader: uso indice in cache (%d blueprint, %.1f ore)" % (
                len(idx["blueprints"]), age.total_seconds() / 3600))
            return idx
    except Exception:
        pass
    return build_ct_index()


class CTIndex:
    def __init__(self, raw):
        self.exps = raw.get("expansions", {})
        self.by_id, self.by_sf, self.by_cm, self.by_exp = {}, {}, {}, {}
        for bp in raw.get("blueprints", []):
            self.by_id[bp["id"]] = bp
            if bp.get("s"):
                self.by_sf.setdefault(bp["s"], []).append(bp)
            for c in bp.get("c") or []:
                self.by_cm.setdefault(c, []).append(bp)
            self.by_exp.setdefault(str(bp["e"]), []).append(bp)
        self.code_to_exp = {}
        for eid, e in self.exps.items():
            if e.get("code"):
                self.code_to_exp.setdefault(e["code"].lower(), eid)


# --------------------------------------------------------------------------
# TCGTracking (seconda fonte per scryfall_id -> blueprint CardTrader)
# --------------------------------------------------------------------------
_tcg_sets, _tcg_products = {}, {}


def tcg_cardtrader_id(scryfall_id, set_code):
    if not scryfall_id:
        return None
    if set_code not in _tcg_sets:
        res = http_json(TCG_API + "/search?q=" + urllib.parse.quote(set_code), tries=2, timeout=30)
        sets = (res or {}).get("sets") or []
        match = next((s for s in sets if (s.get("set_abbr") or "").lower() == set_code), None)
        _tcg_sets[set_code] = match.get("id") if match else None
        if match:
            prods = (http_json("%s/sets/%s" % (TCG_API, match["id"]), tries=2, timeout=60) or {}).get("products") or []
            for p in prods:
                ct = p.get("cardtrader_id")
                if not ct and p.get("cardtrader"):
                    ct = p["cardtrader"][0].get("id")
                if p.get("scryfall_id") and ct:
                    _tcg_products[p["scryfall_id"]] = int(ct)
        time.sleep(0.2)
    return _tcg_products.get(scryfall_id)


# --------------------------------------------------------------------------
# Abbinamento universale stampa Scryfall -> blueprint CardTrader
# --------------------------------------------------------------------------
def match_blueprint(card, sf, alt, idx):
    want_name = norm_name(card["name"])
    want_coll = norm_coll(card["collector"])
    sf_id = sf.get("id") if sf else None
    cm_id = sf.get("cardmarket_id") if sf else None
    alt_id = alt.get("id") if alt else None
    alt_cm = alt.get("cardmarket_id") if alt else None
    tcg_id = tcg_cardtrader_id(sf_id, card["set"]) if sf_id else None

    cands = {}
    for bp in idx.by_sf.get(sf_id, []) if sf_id else []:
        cands[bp["id"]] = bp
    for bp in idx.by_cm.get(cm_id, []) if cm_id else []:
        cands[bp["id"]] = bp
    for bp in (idx.by_sf.get(alt_id, []) if alt_id else []) + (idx.by_cm.get(alt_cm, []) if alt_cm else []):
        cands[bp["id"]] = bp
    if tcg_id:
        cands.setdefault(tcg_id, idx.by_id.get(tcg_id) or {"id": tcg_id})

    def score(bp):
        s, why = 0, []
        if sf_id and bp.get("s") == sf_id:
            s += 8; why.append("scryfall")
        if cm_id and cm_id in (bp.get("c") or []):
            s += 6; why.append("cardmarket")
        if alt_id and bp.get("s") == alt_id:
            s += 3; why.append("scryfall-alt")
        if tcg_id and bp["id"] == tcg_id:
            s += 3; why.append("tcgtracking")
        k = norm_coll(bp.get("k"))
        if k:
            if k == want_coll:
                s += 4; why.append("numero")
            else:
                s -= 5; why.append("numero diverso")
        if bp.get("n"):
            if norm_name(bp["n"]) == want_name:
                s += 1
            else:
                s -= 20; why.append("nome diverso")
        return s, why

    best, best_s, best_why = None, -999, []
    for bp in cands.values():
        s, why = score(bp)
        if s > best_s:
            best, best_s, best_why = bp, s, why
    if best is not None and best_s > 0:
        return best, best_s, best_why

    # Ripiego: espansione con lo stesso codice, per numero e poi per nome
    eid = idx.code_to_exp.get(card["set"])
    pool = idx.by_exp.get(eid, []) if eid else []
    for bp in pool:
        if norm_coll(bp.get("k")) == want_coll and norm_name(bp.get("n")) == want_name:
            return bp, 2, ["ripiego: set+numero"]
    named = [bp for bp in pool if norm_name(bp.get("n")) == want_name]
    if len(named) == 1:
        return named[0], 1, ["ripiego: solo nome (da verificare)"]
    return None, 0, ["non trovato"]


# --------------------------------------------------------------------------
# Offerte CardTrader Zero
# --------------------------------------------------------------------------
def pick_offer(prods, foil, lang):
    """Offerta CardTrader Zero piu' economica tra i prodotti, lingua e foil richiesti, condizione >= SP."""
    pool = []
    for p in prods or []:
        ph = p.get("properties_hash") or {}
        user = p.get("user") or {}
        if user.get("can_sell_via_hub") is not True or p.get("on_vacation"):
            continue
        if bool(ph.get("mtg_foil")) != foil:
            continue
        if (ph.get("mtg_language") or "").lower() != lang:
            continue
        if (ph.get("condition") or "").lower() not in OK_CONDITIONS:
            continue
        cents = p.get("price_cents") or (p.get("price") or {}).get("cents")
        if not cents:
            continue
        pool.append((cents, p))
    if not pool:
        return None
    pool.sort(key=lambda x: x[0])
    cents, p = pool[0]
    ph = p.get("properties_hash") or {}
    return {
        "price": round(cents / 100, 2),
        "cur": (p.get("price") or {}).get("currency") or p.get("price_currency") or "EUR",
        "cond": ph.get("condition"),
        "seller": (p.get("user") or {}).get("username"),
        "country": (p.get("user") or {}).get("country_code"),
        "qty": p.get("quantity"),
        "offers": len(pool),
    }


def best_offer(bp_id, foil, lang):
    q = urllib.parse.urlencode({"blueprint_id": bp_id, "language": lang, "foil": "true" if foil else "false"})
    raw = ct_get("/marketplace/products?" + q)
    time.sleep(0.35)
    if raw is None:
        return None, False
    prods = raw if isinstance(raw, list) else (raw.get(str(bp_id)) or [])
    return pick_offer(prods, foil, lang), True


# --------------------------------------------------------------------------
# Storico, Telegram
# --------------------------------------------------------------------------
def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    http_json("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
              data={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": True}, tries=2)


def ct_image(url):
    if not url:
        return None
    return url if url.startswith("http") else "https://www.cardtrader.com" + url


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    started = datetime.now(timezone.utc)
    with open(CARDS_FILE, encoding="utf-8") as f:
        cards = parse_cards(f.read())
    log("Carte in lista: %d" % len(cards))

    prev = read_json(PRICES_FILE, {})
    prev_by_key = {c["key"]: c for c in prev.get("cards", [])}

    coll = sf_collection(cards)
    log("Scryfall: %d/%d stampe trovate in blocco" % (len(coll), len(cards)))

    idx, ct_error = None, None
    if not CT_TOKEN:
        ct_error = "Token CardTrader mancante (secret CARDTRADER_TOKEN)."
    else:
        try:
            idx = CTIndex(load_ct_index())
        except urllib.error.HTTPError as e:
            ct_error = ("CardTrader ha rifiutato il token (errore 401)." if e.code == 401 else
                        "CardTrader ha bloccato temporaneamente le richieste (errore %d)." % e.code)
        except Exception as e:
            ct_error = "CardTrader non raggiungibile: %s" % e
    if ct_error:
        log("ATTENZIONE:", ct_error)

    out, alerts = [], []
    for n, card in enumerate(cards, 1):
        sf, alt = sf_lookup(card, coll)
        cm = sf_price(sf, card["foil"]) or sf_price(alt, card["foil"])
        row = {
            "key": card["key"], "qty": card["qty"], "name": card["name"], "set": card["set"],
            "collector": card["collector"], "foil": card["foil"],
            "cm": cm,
            "cm_uri": ((sf or {}).get("purchase_uris") or {}).get("cardmarket"),
            "sf_img": sf_image(sf) or sf_image(alt),
            "sf_uri": (sf or {}).get("scryfall_uri"),
            "sf_name": (sf or {}).get("name"),
            "set_name": (sf or {}).get("set_name"),
            "variant": ", ".join(filter(None, [
                (sf or {}).get("border_color") if (sf or {}).get("border_color") == "borderless" else None,
                *(sf or {}).get("frame_effects", []),
                "full art" if (sf or {}).get("full_art") else None,
                "promo" if (sf or {}).get("promo") else None,
            ])) or None,
            "bp": None, "bp_name": None, "bp_exp": None, "ct_img": None,
            "match": None, "match_score": None,
            "en": None, "jp": None,
        }
        if not sf:
            row["error"] = "Carta non trovata su Scryfall"

        if idx is not None and sf:
            try:
                bp, sc, why = match_blueprint(card, sf, alt, idx)
                if bp:
                    row["bp"] = bp["id"]
                    row["bp_name"] = bp.get("n")
                    e = idx.exps.get(str(bp.get("e"))) or {}
                    row["bp_exp"] = e.get("name")
                    row["ct_img"] = ct_image(bp.get("i"))
                    row["match"] = ", ".join(why)
                    row["match_score"] = sc
                    for lang in LANGS:
                        offer, ok = best_offer(bp["id"], card["foil"], lang)
                        row[lang] = offer
                        if not ok:
                            row["error"] = "Offerte CardTrader non lette"
                else:
                    row["error"] = "Versione non trovata su CardTrader"
            except urllib.error.HTTPError as e:
                ct_error = ("CardTrader ha rifiutato il token (errore 401)." if e.code == 401 else
                            "CardTrader ha bloccato temporaneamente le richieste (errore %d)." % e.code)
                log("ATTENZIONE:", ct_error)
                idx = None

        old = prev_by_key.get(card["key"]) or {}
        if ct_error and not row.get("bp") and old.get("bp"):
            # CardTrader non raggiungibile in questo giro: tengo l'ultimo dato valido
            for k in ("bp", "bp_name", "bp_exp", "ct_img", "match", "match_score", "en", "jp"):
                row[k] = old.get(k)
            row["stale"] = True
            row.pop("error", None)
        elif row["en"] is None and row["jp"] is None and row.get("bp") and old.get("bp") == row["bp"] and row.get("error") == "Offerte CardTrader non lette":
            # lettura fallita in questo giro: mostra l'ultimo dato valido
            row["en"], row["jp"] = old.get("en"), old.get("jp")
            row["stale"] = True
        # Offerta da evidenziare: EN se sotto il prezzo medio CM, altrimenti JP se sotto,
        # senza prezzo CM la piu' economica
        en_p = row["en"] and row["en"]["price"]
        jp_p = row["jp"] and row["jp"]["price"]
        pick = None
        if en_p is not None and cm is not None and en_p < cm:
            pick = "en"
        elif jp_p is not None and cm is not None and jp_p < cm:
            pick = "jp"
        elif cm is None and (en_p is not None or jp_p is not None):
            pick = "en" if (en_p is not None and (jp_p is None or en_p <= jp_p)) else "jp"
        prices_ct = [p for p in (en_p, jp_p) if p is not None]
        best = row[pick]["price"] if pick else (min(prices_ct) if prices_ct else None)
        row["pick"] = pick
        row["ct_best"] = best
        row["saving"] = round(cm - best, 2) if (cm is not None and best is not None) else None
        # Occasione: offerta inglese sotto CM; la giapponese conta solo se non c'e' l'inglese
        ref_p = en_p if en_p is not None else jp_p
        row["deal_saving"] = round(cm - ref_p, 2) if (cm is not None and ref_p is not None) else None
        row["deal"] = bool(row["deal_saving"] is not None and row["deal_saving"] > 0)

        if row["deal"]:
            row["deal_since"] = old.get("deal_since") if old.get("deal") else started.isoformat()
            if not old.get("deal") and row["deal_saving"] >= ALERT_MIN_SAVING:
                alerts.append(row)
        else:
            row["deal_since"] = None
        out.append(row)
        log("[%d/%d] %s (%s %s%s) CM=%s EN=%s JP=%s bp=%s [%s]" % (
            n, len(cards), card["name"], card["set"].upper(), card["collector"], " F" if card["foil"] else "",
            cm, row["en"] and row["en"]["price"], row["jp"] and row["jp"]["price"], row["bp"], row["match"]))

    # Storico giornaliero (ultimo valore del giorno)
    hist = read_json(HISTORY_FILE, {})
    today = datetime.now(timezone(timedelta(hours=2))).strftime("%Y-%m-%d")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    for r in out:
        if r["cm"] is None and r["ct_best"] is None:
            continue
        entries = [e for e in hist.get(r["key"], []) if e.get("d", "") >= cutoff and e.get("d") != today]
        entries.append({"d": today, "cm": r["cm"],
                        "en": r["en"] and r["en"]["price"], "jp": r["jp"] and r["jp"]["price"]})
        hist[r["key"]] = entries
    keys = {r["key"] for r in out}
    hist = {k: v for k, v in hist.items() if k in keys}

    result = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "duration_s": int((datetime.now(timezone.utc) - started).total_seconds()),
        "ct_error": ct_error,
        "count": len(out),
        "deals": sum(1 for r in out if r["deal"]),
        "cards": out,
    }
    write_json(PRICES_FILE, result, compact=True)
    write_json(HISTORY_FILE, hist, compact=True)
    log("Fatto: %d carte, %d occasioni, %ds" % (len(out), result["deals"], result["duration_s"]))
    log("Riepilogo: %d con blueprint CardTrader, %d da verificare, %d senza versione CT, %d con offerte Zero" % (
        sum(1 for r in out if r["bp"]),
        sum(1 for r in out if r["bp"] and (r["match_score"] or 0) < 8),
        sum(1 for r in out if not r["bp"]),
        sum(1 for r in out if r["en"] or r["jp"])))

    # Avvisi immediati per nuove occasioni: disattivati (c'e' il riepilogo ogni 4 ore).
    # Per riattivarli: variabile TELEGRAM_NEW_DEAL_ALERTS=1 nel workflow.
    if alerts and os.environ.get("TELEGRAM_NEW_DEAL_ALERTS") == "1":
        alerts.sort(key=lambda r: -r["deal_saving"])
        lines = ["<b>Nuove occasioni su CardTrader Zero</b>"]
        for r in alerts[:15]:
            lines.append("• %s (%s)%s: CM €%.2f → CT €%.2f (−€%.2f)\nhttps://www.cardtrader.com/it/cards/%s" % (
                r["name"], r["set"].upper(), " foil" if r["foil"] else "", r["cm"], r["cm"] - r["deal_saving"], r["deal_saving"], r["bp"]))
        telegram("\n".join(lines))

    if ct_error:
        sys.exit(0)


if __name__ == "__main__":
    main()
