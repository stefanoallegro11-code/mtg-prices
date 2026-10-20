#!/usr/bin/env python3
"""
Riepilogo Telegram di tutte le occasioni (lanciato ogni 4 ore).

Occasione = offerta CardTrader Zero inglese sotto il prezzo Cardmarket;
la giapponese conta solo se non c'e' nessuna offerta inglese.
Ordine: dal risparmio piu' alto al piu' basso.
"""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_FILE = os.path.join(ROOT, "data", "prices.json")
PAGE_URL = os.environ.get("PAGE_URL", "https://stefanoallegro11-code.github.io/mtg-prices/")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
MAX_LEN = 3800  # limite Telegram 4096 caratteri per messaggio


def send(text):
    """Invia un messaggio; se Telegram non risponde o da' errore temporaneo, riprova fino a 6 volte (circa 10 minuti)."""
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                data=json.dumps({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                                 "disable_web_page_preview": True}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                res = json.loads(r.read().decode())
            if res.get("ok"):
                time.sleep(1)  # piccola pausa tra un messaggio e l'altro
                return
            last = res
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            last = "HTTP %d %s" % (e.code, body)
            if e.code == 400:  # messaggio non valido: inutile riprovare
                break
        except Exception as e:
            last = e
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(last)


def deals(cards):
    out = []
    for c in cards:
        cm = c.get("cm")
        en, jp = c.get("en"), c.get("jp")
        offer, lang = (en, "EN") if en else ((jp, "JP") if jp else (None, None))
        if cm is None or offer is None or offer["price"] >= cm:
            continue
        sv = round(cm - offer["price"], 2)
        out.append((sv, c, offer, lang))
    out.sort(key=lambda x: -x[0])
    return out


def send_list(head, lines, empty_text):
    foot = "\n🌐 <a href=\"%s\">Apri MTG Price Hunter</a>" % PAGE_URL
    if not lines:
        send(head + "\n\n" + empty_text)
        return 1
    msgs, cur = [], head + "\n"
    for ln in lines:
        if len(cur) + len(ln) + 2 > MAX_LEN:
            msgs.append(cur)
            cur = ""
        cur += "\n" + ln
    cur += "\n" + foot
    msgs.append(cur)
    for m in msgs:
        send(m)
    return len(msgs)


def card_line(i, c, cm, lang, price, sv, cond, extra=""):
    name = html.escape(c["name"])
    title = '<a href="https://www.cardtrader.com/it/cards/%s">%s</a>' % (c["bp"], name) if c.get("bp") else name
    return "%d. %s (%s)%s%s\n    CM €%.2f → %s €%.2f · <b>−€%.2f</b> (%d%%) · %s" % (
        i, title, c["set"].upper(), " ✨" if c.get("foil") else "", extra,
        cm, lang.upper(), price, sv, round(sv / cm * 100), html.escape(cond or ""))


def main_list():
    with open(PRICES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    upd = datetime.fromisoformat(data["updated"]).astimezone(ZoneInfo("Europe/Rome"))
    ds = deals(data.get("cards", []))
    head = "🔥 <b>%d occasioni dalla tua lista</b>\nPrezzi delle %s\n🌐 <a href=\"%s\">Apri MTG Price Hunter</a>" % (
        len(ds), upd.strftime("%H:%M del %d/%m"), PAGE_URL)
    if data.get("ct_error"):
        head += "\n⚠️ " + html.escape(data["ct_error"]) + " Offerte CardTrader dell'aggiornamento precedente."
    lines = [card_line(i, c, c["cm"], lang, o["price"], sv, o.get("cond"))
             for i, (sv, c, o, lang) in enumerate(ds, 1)]
    n = send_list(head, lines, "Nessuna carta sotto il prezzo Cardmarket in questo momento.")
    print("Inviati %d messaggi con %d occasioni" % (n, len(ds)))


def main_discoveries(top=50):
    path = os.path.join(ROOT, "data", "discoveries.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    upd = datetime.fromisoformat(data["updated"]).astimezone(ZoneInfo("Europe/Rome"))
    cards = sorted([c for c in data.get("cards", []) if c.get("saving", 0) > 0], key=lambda c: -c["saving"])
    head = ("💎 <b>Scoperte: le %d più scontate</b>\n%d carte fuori dalla tua lista (Cardmarket €%g–%g) con sconto ≥ €%g\n"
            "Prezzi delle %s\n🌐 <a href=\"%s\">Apri la sezione Scoperte</a>") % (
        min(top, len(cards)), len(cards), data.get("min_cm", 15), data.get("max_cm", 80), data.get("min_saving", 10), upd.strftime("%H:%M del %d/%m"), PAGE_URL + "#scoperte")
    lines = []
    for i, c in enumerate(cards[:top], 1):
        o = c.get(c.get("ref_lang") or "en") or {}
        extra = " · %s" % html.escape(c.get("category") or "")
        if c.get("new"):
            extra += " 🆕"
        if c.get("suspicious"):
            extra += " ⚠️"
        lines.append(card_line(i, c, c["cm"], c.get("ref_lang") or "en", o.get("price", 0), c["saving"], o.get("cond"), extra))
    n = send_list(head, lines, "Nessuna scoperta in questo giro.")
    print("Inviati %d messaggi con %d scoperte" % (n, min(top, len(cards))))


def main():
    if not (TG_TOKEN and TG_CHAT):
        print("Mancano i secret TELEGRAM_TOKEN e/o TELEGRAM_CHAT_ID: nessun messaggio inviato.")
        sys.exit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "scoperte":
        main_discoveries()
    else:
        main_list()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Errore Telegram:", e)
        sys.exit(1)
