#!/usr/bin/env python3
"""
Valore della collezione: per ogni carta di collection.txt (formato Manabox)
prende l'offerta CardTrader Zero piu' bassa (condizione >= SP), in inglese;
la giapponese solo se non c'e' nessuna offerta inglese. Calcola i totali.

Per risparmiare richieste chiede le offerte per espansione (una richiesta per
espansione e per foil/non foil) invece che carta per carta.
Scrive data/collection.json.
"""

import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_prices as up  # noqa: E402

COLL_FILE = os.path.join(up.ROOT, "collection.txt")
OUT_FILE = os.path.join(up.DATA_DIR, "collection.json")
log = up.log


def expansion_products(exp_id, lang, foil):
    q = urllib.parse.urlencode({"expansion_id": exp_id, "language": lang, "foil": "true" if foil else "false"})
    raw = up.ct_get("/marketplace/products?" + q)
    time.sleep(0.6)
    if raw is None:
        return None
    if isinstance(raw, list):
        g = {}
        for p in raw:
            g.setdefault(str(p.get("blueprint_id")), []).append(p)
        return g
    return raw


def main():
    started = datetime.now(timezone.utc)
    if not os.path.exists(COLL_FILE):
        log("collection.txt non presente: niente da fare")
        return
    with open(COLL_FILE, encoding="utf-8") as f:
        cards = up.parse_cards(f.read())
    log("Carte in collezione (righe uniche): %d, copie totali: %d" % (len(cards), sum(c["qty"] for c in cards)))
    if not up.CT_TOKEN:
        log("Token CardTrader mancante")
        sys.exit(1)

    coll = up.sf_collection(cards)
    idx = up.CTIndex(up.load_ct_index())

    rows, groups = [], {}
    for n, card in enumerate(cards, 1):
        sf, alt = up.sf_lookup(card, coll)
        cm = up.sf_price(sf, card["foil"]) or up.sf_price(alt, card["foil"])
        row = {
            "key": card["key"], "qty": card["qty"], "name": card["name"], "set": card["set"],
            "collector": card["collector"], "foil": card["foil"], "coll": True,
            "cm": cm,
            "cm_uri": ((sf or {}).get("purchase_uris") or {}).get("cardmarket"),
            "sf_img": up.sf_image(sf) or up.sf_image(alt),
            "set_name": (sf or {}).get("set_name"),
            "variant": ", ".join(filter(None, [
                "borderless" if (sf or {}).get("border_color") == "borderless" else None,
                *[x for x in ((sf or {}).get("frame_effects") or []) if x in ("showcase", "extendedart", "etched", "inverted")],
                "full art" if (sf or {}).get("full_art") else None])) or None,
            "bp": None, "bp_name": None, "bp_exp": None, "ct_img": None,
            "match": None, "match_score": None, "en": None, "jp": None,
        }
        if not sf:
            row["error"] = "Carta non trovata su Scryfall"
        else:
            bp, sc, why = up.match_blueprint(card, sf, alt, idx)
            if bp:
                e = idx.exps.get(str(bp.get("e"))) or {}
                row.update({"bp": bp["id"], "bp_name": bp.get("n"), "bp_exp": e.get("name"),
                            "ct_img": up.ct_image(bp.get("i")), "match": ", ".join(why), "match_score": sc})
                groups.setdefault((str(bp["e"]), card["foil"]), []).append(row)
            else:
                row["error"] = "Versione non trovata su CardTrader"
        rows.append(row)
        if n % 100 == 0:
            log("  ...abbinate %d/%d" % (n, len(cards)))

    log("Richieste offerte: %d gruppi espansione/foil" % len(groups))
    failed = 0
    for i, ((eid, foil), items) in enumerate(groups.items(), 1):
        g = expansion_products(eid, "en", foil)
        if g is None:
            failed += 1
            for r in items:  # ripiego carta per carta
                r["en"], _ = up.best_offer(r["bp"], foil, "en")
            continue
        for r in items:
            r["en"] = up.pick_offer(g.get(str(r["bp"])) or [], foil, "en")
        if i % 50 == 0:
            log("  ...%d/%d gruppi" % (i, len(groups)))

    # Giapponese solo per le carte senza offerta inglese
    no_en = [r for r in rows if r["bp"] and not r["en"]]
    log("Carte senza offerta inglese, cerco in giapponese: %d" % len(no_en))
    for r in no_en:
        r["jp"], _ = up.best_offer(r["bp"], r["foil"], "jp")

    tot_ct = tot_cm = 0.0
    priced = 0
    for r in rows:
        ref = r["en"] or r["jp"]
        r["ref_lang"] = "en" if r["en"] else ("jp" if r["jp"] else None)
        r["unit"] = ref["price"] if ref else None
        r["value"] = round(ref["price"] * r["qty"], 2) if ref else None
        r["cm_value"] = round(r["cm"] * r["qty"], 2) if r["cm"] is not None else None
        if r["value"] is not None:
            tot_ct += r["value"]
            priced += r["qty"]
        if r["cm_value"] is not None:
            tot_cm += r["cm_value"]
        if r["bp"] and not ref and not r.get("error"):
            r["error"] = "Nessuna offerta CardTrader Zero (SP o meglio)"

    rows.sort(key=lambda r: -(r["value"] or 0))
    result = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "duration_s": int((datetime.now(timezone.utc) - started).total_seconds()),
        "count": len(rows),
        "copies": sum(r["qty"] for r in rows),
        "priced_copies": priced,
        "total_ct": round(tot_ct, 2),
        "total_cm": round(tot_cm, 2),
        "failed_requests": failed,
        "cards": rows,
    }
    up.write_json(OUT_FILE, result, compact=True)
    log("Fatto: %d carte (%d copie), totale CardTrader €%.2f, totale Cardmarket €%.2f, %ds" % (
        len(rows), result["copies"], tot_ct, tot_cm, result["duration_s"]))


if __name__ == "__main__":
    main()
