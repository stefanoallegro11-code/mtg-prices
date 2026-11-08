#!/usr/bin/env python3
"""
Report one-shot: foil tra MIN_CM e MAX_CM su Cardmarket che NON entrano nelle Scoperte.

Usa la stessa ricerca di discover.py (scryfall_candidates) per sapere cosa entra,
poi prende tutte le foil nella stessa fascia di prezzo e tiene quelle fuori.
Solo Scryfall: nessuna richiesta a CardTrader. Scrive data/excluded.json.
"""

import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_prices as up  # noqa: E402
import discover as dc  # noqa: E402

OUT_FILE = os.path.join(up.DATA_DIR, "excluded.json")
BROAD_Q = "game:paper is:foil eur>=%g eur<=%g" % (dc.MIN_CM, dc.MAX_CM)
MAX_PAGES = 80


def broad_candidates():
    url = up.SF_API + "/cards/search?" + urllib.parse.urlencode(
        {"q": BROAD_Q, "unique": "prints", "order": "eur"})
    out, pages = [], 0
    while url and pages < MAX_PAGES:
        res = None
        for attempt in range(6):
            res = up.http_json(url)
            if res:
                break
            time.sleep(5 * (attempt + 1))
        if not res:
            break
        out.extend(res.get("data", []))
        pages += 1
        url = res.get("next_page") if res.get("has_more") else None
        time.sleep(0.12)
    return out, pages


def reason(d):
    s = d.get("set", "")
    fe = set(d.get("frame_effects") or [])
    pt = set(d.get("promo_types") or [])
    if "prerelease" in pt:
        return "promo prerelease (esclusa apposta)"
    if d.get("border_color") == "borderless" or "borderless" in fe:
        return "borderless ma set non incluso"
    if fe & {"showcase", "extendedart", "inverted"} or d.get("full_art"):
        return "arte speciale ma set non incluso"
    if pt & set(dc.SPECIAL_FOILS):
        return "foil speciale non riconosciuta"
    if s in ("ss1", "ss2", "ss3"):
        return "serie speciale non inclusa"
    return "foil normale o set non in lista"


def main():
    ours_list = dc.scryfall_candidates()
    ours = {d["id"] for d in ours_list}
    log = up.log
    log("Nelle Scoperte: %d stampe" % len(ours))
    broad, pages = broad_candidates()
    log("Foil nella fascia prezzo: %d stampe (%d pagine)" % (len(broad), pages))

    rows = []
    for d in broad:
        if d["id"] in ours:
            continue
        try:
            eur = float((d.get("prices") or {}).get("eur_foil") or 0)
        except ValueError:
            eur = 0
        rows.append({
            "name": d.get("name"), "set": d.get("set"), "set_name": d.get("set_name"),
            "collector": d.get("collector_number"), "foil_eur": eur,
            "reason": reason(d),
        })
    rows.sort(key=lambda r: (r["reason"], -r["foil_eur"]))
    by_reason, by_set = {}, {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        by_set[r["set"]] = by_set.get(r["set"], 0) + 1
    top_sets = sorted(by_set.items(), key=lambda kv: -kv[1])[:80]
    result = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "broad_query": BROAD_Q,
        "in_discoveries": len(ours),
        "total_broad": len(broad),
        "excluded": len(rows),
        "truncated": pages >= MAX_PAGES,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "top_sets": top_sets,
        "cards": rows,
    }
    up.write_json(OUT_FILE, result, compact=True)
    log("Fuori dalle Scoperte: %d stampe" % len(rows))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Errore:", e)
        sys.exit(1)
