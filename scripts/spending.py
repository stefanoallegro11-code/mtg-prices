#!/usr/bin/env python3
"""
Quanto hai speso su CardTrader per le carte che possiedi.

Legge gli ordini d'acquisto (GET /orders/bought, paginati) con il token
CardTrader, somma i prezzi pagati per blueprint e incrocia con
data/collection.json (le copie che possiedi davvero).

Scrive data/spending.json. Lanciato a mano (workflow spending.yml).
Gli ordini annullati/rimborsati vengono esclusi. Il prezzo e' quello pagato
per copia, spedizione esclusa quando l'API la separa.

Nota: la forma esatta della risposta non e' documentata qui; lo script prova
le varianti piu' comuni e salva nel file l'elenco dei campi trovati, cosi'
si puo' verificare al primo giro.
"""

import json
import os
import sys
import time
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_prices as up  # noqa: E402

OUT_FILE = os.path.join(up.DATA_DIR, "spending.json")
COLL_FILE = os.path.join(up.DATA_DIR, "collection.json")
MAX_PAGES = 200
CANCELLED = ("cancel", "refund", "annull", "rimbors")
DBG = {"order_keys": None, "item_keys": None, "price_shape": None}


def cents_of(v):
    """Converte un prezzo CardTrader (centesimi, o oggetto {cents: ...}) in euro."""
    if isinstance(v, dict):
        c = v.get("cents")
        if c is None:
            c = v.get("price_cents")
        if c is None:
            return None
        return float(c) / 100
    if isinstance(v, (int, float)):
        return float(v) / 100
    return None


def item_price(it):
    # Lato acquirente: buyer_price (in /orders compaiono anche le vendite, con seller_price)
    for key in ("buyer_price", "price", "unit_price", "price_per_unit"):
        if key in it:
            p = cents_of(it[key])
            if p is not None:
                DBG["price_shape"] = DBG["price_shape"] or key
                return p
    for key in ("price_cents", "unit_price_cents"):
        if it.get(key) is not None:
            DBG["price_shape"] = DBG["price_shape"] or key
            return float(it[key]) / 100
    return None


def item_list(order):
    for key in ("order_items", "line_items", "items", "products"):
        if isinstance(order.get(key), list):
            return order[key]
    return []


def item_bp(it):
    for key in ("blueprint_id",):
        if it.get(key) is not None:
            return str(it[key])
    prod = it.get("product") or {}
    if isinstance(prod, dict) and prod.get("blueprint_id") is not None:
        return str(prod["blueprint_id"])
    return None


def item_foil(it):
    props = it.get("properties") or it.get("properties_hash") or {}
    return str(props.get("mtg_foil")).lower() in ("true", "1")


def item_qty(it):
    try:
        return int(it.get("quantity") or it.get("qty") or 1)
    except (TypeError, ValueError):
        return 1


def item_name(it):
    if it.get("name"):
        return it["name"]
    prod = it.get("product") or {}
    if isinstance(prod, dict) and prod.get("name_en"):
        return prod["name_en"]
    bp = it.get("blueprint") or {}
    if isinstance(bp, dict) and bp.get("name"):
        return bp["name"]
    return None


ORDER_PATHS = ["/orders/bought", "/orders", "/orders/buyer"]


def fetch_orders():
    """Scarica gli ordini d'acquisto. Prova i percorsi noti finche' uno risponde."""
    base = None
    for p in ORDER_PATHS:
        raw = up.ct_get(p + "?page=1")
        time.sleep(1.0)
        DBG.setdefault("tried", []).append("%s -> %s" % (p, "none" if raw is None else type(raw).__name__))
        if raw is not None:
            base = p
            first = raw
            break
    if base is None:
        return []
    DBG["path"] = base
    orders, seen = [], set()
    page = 1
    while page <= MAX_PAGES:
        batch = first if page == 1 else up.ct_get("%s?page=%d" % (base, page))
        time.sleep(1.0)
        if page > 1 and batch is None:
            break
        batch = batch.get("orders", batch) if isinstance(batch, dict) else batch
        if not isinstance(batch, list) or not batch:
            break
        new = 0
        for o in batch:
            oid = str(o.get("id"))
            if oid in seen:
                continue
            seen.add(oid)
            orders.append(o)
            new += 1
        if new == 0:
            break
        page += 1
    DBG["pages"] = page
    return orders


def main():
    if not up.CT_TOKEN:
        up.log("Token CardTrader mancante")
        sys.exit(1)
    started = datetime.now(timezone.utc)

    orders = fetch_orders()
    up.log("Ordini d'acquisto letti: %d" % len(orders))
    if orders:
        DBG["order_keys"] = sorted(orders[0].keys())

    per_bp = defaultdict(lambda: {"bp": None, "name": None, "foil": False, "qty": 0, "spent": 0.0})
    total_spent = 0.0
    items_count = 0
    skipped_orders = 0
    currencies = set()

    for o in orders:
        status = str(o.get("state") or o.get("status") or "").lower()
        if o.get("cancelled_at") or any(x in status for x in CANCELLED):
            skipped_orders += 1
            continue
        currencies.add(str(o.get("currency") or o.get("price_currency") or "EUR"))
        DBG.setdefault("states", {})
        st_key = "%s|%s" % (o.get("state"), "cancelled" if o.get("cancelled_at") else "")
        DBG["states"][st_key] = DBG["states"].get(st_key, 0) + 1
        DBG.setdefault("order_as", {})
        oa = str(o.get("order_as"))
        DBG["order_as"][oa] = DBG["order_as"].get(oa, 0) + 1
        if oa == "seller":  # ordine di una mia vendita: non e' una spesa
            skipped_orders += 1
            continue
        for it in item_list(o):
            if DBG["item_keys"] is None:
                DBG["item_keys"] = sorted(it.keys())
            if it.get("deleted_at"):
                continue
            bp = item_bp(it)
            price = item_price(it)
            qty = item_qty(it)
            if bp is None or price is None:
                continue
            foil = item_foil(it)
            key = "%s|%s" % (bp, "f" if foil else "n")
            agg = per_bp[key]
            agg["bp"], agg["foil"] = bp, foil
            agg["name"] = agg["name"] or item_name(it)
            agg["qty"] += qty
            agg["spent"] += price * qty
            total_spent += price * qty
            items_count += qty

    # Incrocio con la collezione: quante copie possiedi ora per ogni blueprint/foil
    owned = defaultdict(int)
    names = {}
    if os.path.exists(COLL_FILE):
        coll = up.read_json(COLL_FILE, {})
        for c in coll.get("cards", []):
            if not c.get("bp"):
                continue
            k = "%s|%s" % (c["bp"], "f" if c.get("foil") else "n")
            owned[k] += int(c.get("qty") or 0)
            names.setdefault(k, c.get("name"))

    rows = []
    spent_owned = 0.0
    owned_copies = 0
    for key, agg in per_bp.items():
        own = owned.get(key, 0)
        bought = agg["qty"]
        avg = agg["spent"] / bought if bought else 0
        kept = min(own, bought)  # copie ancora in collezione, al prezzo medio pagato
        spent_owned += avg * kept
        owned_copies += kept
        rows.append({
            "bp": agg["bp"], "name": agg["name"] or names.get(key), "foil": agg["foil"],
            "bought": bought, "spent": round(agg["spent"], 2), "avg": round(avg, 2),
            "owned": own, "spent_owned": round(avg * kept, 2),
        })
    rows.sort(key=lambda r: -r["spent"])

    result = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "duration_s": int((datetime.now(timezone.utc) - started).total_seconds()),
        "orders": len(orders),
        "orders_skipped": skipped_orders,
        "items": items_count,
        "currencies": sorted(currencies),
        "total_spent": round(total_spent, 2),
        "owned_copies_bought": owned_copies,
        "spent_on_owned": round(spent_owned, 2),
        "debug": DBG,
        "cards": rows,
    }
    up.write_json(OUT_FILE, result, compact=True)
    up.log("Speso su CardTrader: €%.2f su %d copie; per le carte ancora in collezione: €%.2f (%d copie)" % (
        total_spent, items_count, spent_owned, owned_copies))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            up.log("CardTrader ha bloccato o rifiutato la richiesta (errore %d)." % e.code)
            sys.exit(0)
        raise
