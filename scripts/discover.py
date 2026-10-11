#!/usr/bin/env python3
"""
Scoperte: carte FUORI dalla lista che su CardTrader Zero costano meno di Cardmarket.

Carte considerate (solo foil, prezzo Cardmarket Trend foil >= MIN_CM):
  - Secret Lair e Special Guests
  - full art, borderless, showcase, extended art
  - tutte le carte uscite fino a Visione Futura (maggio 2007) compresa

Per ogni espansione CardTrader che contiene candidati si fa UNA richiesta
(in inglese e una in giapponese, solo foil) invece di una per carta.
Scrive data/discoveries.json.
"""

import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_prices as up  # noqa: E402

MIN_CM = float(os.environ.get("DISCOVER_MIN_CM", "15"))
MAX_CM = float(os.environ.get("DISCOVER_MAX_CM", "80"))   # prezzo Cardmarket massimo considerato
MIN_SAVING = float(os.environ.get("DISCOVER_MIN_SAVING", "5"))  # sconto minimo in euro rispetto a Cardmarket
SUSPICIOUS_PCT = 70  # sconto oltre il 70%: probabile prezzo Cardmarket poco affidabile
OUT_FILE = os.path.join(up.DATA_DIR, "discoveries.json")

SPECIAL_SETS = ("sta", "mul", "brr", "wot", "otp", "big", "soa")
SF_QUERY = ("game:paper is:foil ("
            "set:sld or set:slc or set:slu or set:slp or set:psld or set:spg "
            "or is:fullart or border:borderless or frame:showcase or frame:extendedart "
            "or is:boosterfun or is:textless or is:alternate or (frame:1997 date>=2018) "
            "or " + " or ".join("set:" + x for x in SPECIAL_SETS) + " "
            "or date<=fut)")
LANGS = ("en",)  # scoperte: solo offerte in inglese

log = up.log
SF_STATS = {"incomplete": False}


def scryfall_candidates():
    url = up.SF_API + "/cards/search?" + urllib.parse.urlencode(
        {"q": SF_QUERY, "unique": "prints", "order": "eur"})
    out, pages, total = [], 0, None
    while url:
        res = None
        for attempt in range(6):  # Scryfall a volte rallenta: riprovo con pause crescenti
            res = up.http_json(url)
            if res:
                break
            time.sleep(5 * (attempt + 1))
        if not res:
            log("  ! Scryfall: pagina non letta dopo 6 tentativi, mi fermo a", len(out))
            SF_STATS["incomplete"] = True
            break
        total = res.get("total_cards", total)
        for d in res.get("data", []):
            try:
                eur = float((d.get("prices") or {}).get("eur_foil") or 0)
            except ValueError:
                eur = 0
            if MIN_CM <= eur <= MAX_CM:
                out.append(d)
        pages += 1
        url = res.get("next_page") if res.get("has_more") else None
        time.sleep(0.12)
    SF_STATS.update({"total": total, "pages": pages, "priced": len(out)})
    log("Scryfall: %s stampe foil nei criteri, %d con Cardmarket foil tra €%g e €%g (%d pagine)" % (
        total, len(out), MIN_CM, MAX_CM, pages))
    return out


def own_keys():
    try:
        with open(up.CARDS_FILE, encoding="utf-8") as f:
            cards = up.parse_cards(f.read())
    except OSError:
        return set(), set()
    return ({(c["set"], up.norm_coll(c["collector"])) for c in cards},
            {up.norm_name(c["name"]) + "|" + c["set"] for c in cards})


def category(d):
    s = d.get("set", "")
    if s in ("sld", "slc", "slu", "slp", "psld"):
        return "Secret Lair"
    if s == "spg":
        return "Special Guests"
    fe = d.get("frame_effects") or []
    if d.get("released_at", "9999") <= "2007-05-04" and not (
            d.get("full_art") or d.get("border_color") == "borderless" or "showcase" in fe or "extendedart" in fe):
        return "Foil vintage"
    if d.get("border_color") == "borderless":
        return "Borderless"
    if "showcase" in fe:
        return "Showcase"
    if "extendedart" in fe:
        return "Extended art"
    if d.get("full_art"):
        return "Full art"
    if d.get("released_at", "9999") <= "2007-05-04":
        return "Foil vintage"
    if d.get("frame") == "1997":
        return "Retro frame"
    return "Special art"


def pick_blueprint(d, idx):
    """Blueprint CardTrader della stessa identica stampa (ID Scryfall, poi ID Cardmarket)."""
    want_coll = up.norm_coll(d.get("collector_number"))
    want_name = up.norm_name(d.get("name"))
    cands = {}
    for bp in idx.by_sf.get(d["id"], []):
        cands[bp["id"]] = bp
    if d.get("cardmarket_id"):
        for bp in idx.by_cm.get(d["cardmarket_id"], []):
            cands[bp["id"]] = bp
    best, best_s = None, 0
    for bp in cands.values():
        s = 0
        if bp.get("s") == d["id"]:
            s += 8
        if d.get("cardmarket_id") in (bp.get("c") or []):
            s += 6
        k = up.norm_coll(bp.get("k"))
        if k:
            s += 4 if k == want_coll else -5
        if bp.get("n") and up.norm_name(bp["n"]) != want_name:
            s -= 20
        if s > best_s:
            best, best_s = bp, s
    return best, best_s


def expansion_products(exp_id, lang):
    q = urllib.parse.urlencode({"expansion_id": exp_id, "language": lang, "foil": "true"})
    raw = up.ct_get("/marketplace/products?" + q)
    time.sleep(0.6)
    if raw is None:
        return None
    if isinstance(raw, list):  # formato inatteso: raggruppa per blueprint
        g = {}
        for p in raw:
            g.setdefault(str(p.get("blueprint_id")), []).append(p)
        return g
    return raw


def main():
    started = datetime.now(timezone.utc)
    if not up.CT_TOKEN:
        log("Token CardTrader mancante")
        sys.exit(1)

    cands = scryfall_candidates()
    mine_sc, mine_name = own_keys()
    cands = [d for d in cands
             if (d["set"], up.norm_coll(d["collector_number"])) not in mine_sc
             and up.norm_name(d["name"]) + "|" + d["set"] not in mine_name]
    log("Candidati fuori dalla tua lista: %d" % len(cands))

    idx = up.CTIndex(up.load_ct_index())

    by_exp, matched = {}, 0
    for d in cands:
        bp, sc = pick_blueprint(d, idx)
        if not bp:
            continue
        matched += 1
        by_exp.setdefault(str(bp["e"]), []).append((d, bp, sc))
    log("Abbinati a CardTrader: %d, in %d espansioni" % (matched, len(by_exp)))

    found, failed_exp = [], 0
    for n, (eid, items) in enumerate(sorted(by_exp.items(), key=lambda x: -len(x[1])), 1):
        offers = {}
        for lang in LANGS:
            g = expansion_products(eid, lang)
            if g is None:
                failed_exp += 1
                continue
            for d, bp, sc in items:
                o = up.pick_offer(g.get(str(bp["id"])) or [], True, lang)
                if o:
                    offers.setdefault(bp["id"], {})[lang] = o
        for d, bp, sc in items:
            o = offers.get(bp["id"], {})
            en, jp = o.get("en"), None
            ref, lang = (en, "en") if en else (None, None)
            if not ref:
                continue
            cm = round(float(d["prices"]["eur_foil"]), 2)
            sv = round(cm - ref["price"], 2)
            if sv < MIN_SAVING:
                continue
            pct = round(sv / cm * 100)
            e = idx.exps.get(str(bp.get("e"))) or {}
            found.append({
                "key": "%s-%s-f" % (d["set"], d["collector_number"].lower()),
                "name": d["name"], "set": d["set"], "collector": d["collector_number"], "foil": True, "qty": 1,
                "category": category(d),
                "cm": cm,
                "cm_uri": (d.get("purchase_uris") or {}).get("cardmarket"),
                "sf_img": up.sf_image(d), "sf_uri": d.get("scryfall_uri"), "set_name": d.get("set_name"),
                "variant": ", ".join(filter(None, [
                    "borderless" if d.get("border_color") == "borderless" else None,
                    *[x for x in (d.get("frame_effects") or []) if x in ("showcase", "extendedart", "etched", "inverted")],
                    "full art" if d.get("full_art") else None])) or None,
                "bp": bp["id"], "bp_name": bp.get("n"), "bp_exp": e.get("name"),
                "ct_img": up.ct_image(bp.get("i")),
                "match_score": sc, "match": None,
                "en": en, "jp": jp,
                "ref_lang": lang, "saving": sv, "pct": pct,
                "suspicious": pct > SUSPICIOUS_PCT,
            })
        if n % 25 == 0:
            log("  ...%d/%d espansioni, %d scoperte finora" % (n, len(by_exp), len(found)))

    found.sort(key=lambda r: -r["saving"])
    prev = up.read_json(OUT_FILE, {})
    prev_keys = {c["key"] for c in prev.get("cards", [])}
    for r in found:
        r["new"] = r["key"] not in prev_keys if prev_keys else False

    result = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "duration_s": int((datetime.now(timezone.utc) - started).total_seconds()),
        "min_cm": MIN_CM, "max_cm": MAX_CM,
        "scryfall": SF_STATS,
        "candidates": len(cands), "matched": matched, "expansions": len(by_exp),
        "failed_requests": failed_exp,
        "count": len(found),
        "min_saving": MIN_SAVING,
        "cards": found,
    }
    up.write_json(OUT_FILE, result, compact=True)
    log("Fatto: %d scoperte (sconto >= €%g), %d richieste fallite, %ds" % (
        len(found), MIN_SAVING, failed_exp, result["duration_s"]))


if __name__ == "__main__":
    main()
